"""
Candidate Data Access — Bullhorn data fetching, resume handling, and utilities.

Contains:
- _resolve_vetting_cutoff: Standalone utility to parse the configured cutoff datetime
- CandidateDataAccessMixin: Bullhorn data access methods
  - _fetch_latest_job_submission: Latest JobSubmission lookup with retry
  - _fetch_candidate_details: Full candidate entity fetch
  - _fetch_applied_job: Single job fetch for applied-job injection
  - _mark_application_vetted: Mark ParsedEmail as vetted
  - get_candidate_resume: Download resume file from Bullhorn
  - extract_resume_text / _extract_text_from_*: Delegate to vetting.resume_utils
"""

import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

from app import db
from models import ParsedEmail, VettingConfig
from vetting.resume_utils import (
    extract_resume_text as _extract_resume_text,
    extract_text_from_pdf as _extract_text_from_pdf,
    extract_text_from_docx as _extract_text_from_docx,
    extract_text_from_doc as _extract_text_from_doc,
)

logger = logging.getLogger(__name__)


def _resolve_vetting_cutoff() -> Optional[datetime]:
    """
    Resolve the configured vetting cutoff datetime from VettingConfig.

    Returns the parsed datetime if configured + valid, else None.
    Logs a warning if the configured value is malformed (cutoff disabled).
    Accepts both 'YYYY-MM-DD HH:MM:SS' and ISO 'YYYY-MM-DDTHH:MM:SS' formats.
    """
    cutoff_raw = VettingConfig.get_value('vetting_cutoff_date')
    if not cutoff_raw:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(cutoff_raw.strip(), fmt)
        except ValueError:
            continue
    logger.error(
        f"❌ Invalid vetting_cutoff_date format: '{cutoff_raw}' — expected "
        f"'YYYY-MM-DD HH:MM:SS' or ISO format. Cutoff DISABLED — entire "
        f"backlog will be processed!"
    )
    return None


class CandidateDataAccessMixin:
    """Bullhorn data access and resume handling for candidate detection."""

    def _fetch_latest_job_submission(
        self,
        bullhorn,
        candidate_id: int,
    ) -> Tuple[Optional[int], Optional[str], bool]:
        """
        Fetch the most recent JobSubmission for a candidate from Bullhorn,
        with a single retry on transient failures (network errors, 5xx).

        Used by Pandologic and Matador detectors so the JobSubmission lookup
        path stays consistent and any improvement applies to both.

        Args:
            bullhorn: Authenticated BullhornService instance.
            candidate_id: Bullhorn candidate ID.

        Returns:
            Tuple of (applied_job_id, applied_job_title, lookup_succeeded):
              - On 200 with a submission → (id, title, True)
              - On 200 with no submissions → (None, None, True)
                (legitimate empty result, e.g. resume-only candidate)
              - On persistent failure (5xx, network error, JSON parse error,
                non-200 after retry) → (None, None, False) and a WARNING is
                logged. Caller should treat this as "applied job unknown"
                and may fall back to global dedup.
        """
        sub_url = f"{bullhorn.base_url}search/JobSubmission"
        sub_params = {
            'query': f'candidate.id:{candidate_id}',
            'fields': 'id,jobOrder(id,title),dateAdded',
            'count': 1,
            'sort': '-dateAdded',
            'BhRestToken': bullhorn.rest_token,
        }

        last_error: Optional[str] = None
        last_status: Optional[int] = None

        for attempt in (1, 2):
            try:
                sub_response = bullhorn.session.get(sub_url, params=sub_params, timeout=15)
                last_status = sub_response.status_code

                if sub_response.status_code == 200:
                    try:
                        submissions = sub_response.json().get('data', [])
                    except ValueError as parse_err:
                        last_error = f"JSON parse error: {parse_err}"
                        if attempt == 1:
                            time.sleep(1)
                            continue
                        break

                    if not submissions:
                        return (None, None, True)

                    job_order = submissions[0].get('jobOrder') or {}
                    return (
                        job_order.get('id'),
                        job_order.get('title', ''),
                        True,
                    )

                if 500 <= sub_response.status_code < 600:
                    last_error = f"HTTP {sub_response.status_code}"
                    if attempt == 1:
                        time.sleep(1)
                        continue
                    break

                last_error = f"HTTP {sub_response.status_code}"
                break

            except (requests.Timeout, requests.ConnectionError) as net_err:
                last_error = f"{type(net_err).__name__}: {net_err}"
                if attempt == 1:
                    time.sleep(1)
                    continue
                break
            except requests.RequestException as req_err:
                last_error = f"{type(req_err).__name__}: {req_err}"
                if attempt == 1:
                    time.sleep(1)
                    continue
                break

        logger.warning(
            f"⚠️ JobSubmission lookup failed for candidate {candidate_id} "
            f"after retry ({last_error}, status={last_status}); "
            f"falling back to global 24h dedup — possible missed re-application "
            f"to a different job within the dedup window"
        )
        return (None, None, False)

    def _fetch_candidate_details(self, bullhorn, candidate_id: int) -> Optional[Dict]:
        """
        Fetch full candidate details from Bullhorn by ID.

        Args:
            bullhorn: Authenticated Bullhorn service
            candidate_id: Bullhorn candidate ID

        Returns:
            Candidate data dictionary or None
        """
        try:
            url = f"{bullhorn.base_url}entity/Candidate/{candidate_id}"
            params = {
                'fields': 'id,firstName,lastName,email,phone,address,status,dateAdded,dateLastModified,source,occupation,description',
                'BhRestToken': bullhorn.rest_token
            }

            response = bullhorn.session.get(url, params=params, timeout=30)

            if response.status_code == 200:
                data = response.json()
                return data.get('data', {})
            else:
                logger.warning(f"Failed to fetch candidate {candidate_id}: {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"Error fetching candidate {candidate_id}: {str(e)}")
            return None

    def _fetch_applied_job(self, bullhorn, job_id: int) -> Optional[Dict]:
        """
        Fetch a single job by ID from Bullhorn for applied-job injection.

        Used when the applied job isn't in a monitored tearsheet. Returns the
        job dict in the same format as get_active_jobs_from_tearsheets() so it
        can be seamlessly added to the job list.

        Only returns jobs with status 'Accepting Candidates' or where isOpen=True.
        Returns None for closed/deleted/invalid jobs.

        Args:
            bullhorn: Authenticated Bullhorn service
            job_id: Bullhorn job order ID

        Returns:
            Job dictionary matching tearsheet format, or None if closed/invalid
        """
        if not bullhorn or not bullhorn.rest_token:
            return None

        try:
            url = f"{bullhorn.base_url}entity/JobOrder/{job_id}"
            params = {
                'fields': (
                    'id,title,isOpen,status,dateAdded,dateLastModified,'
                    'clientCorporation(name),description,publicDescription,'
                    'address(address1,city,state,countryName),'
                    'employmentType,onSite,'
                    'assignedUsers(id,firstName,lastName,email),'
                    'responseUser(firstName,lastName),owner(firstName,lastName)'
                ),
                'BhRestToken': bullhorn.rest_token
            }

            response = bullhorn.session.get(url, params=params, timeout=30)

            if response.status_code != 200:
                logger.warning(
                    f"Bullhorn returned {response.status_code} for job {job_id}"
                )
                return None

            job_data = response.json().get('data', {})

            if not job_data or not job_data.get('id'):
                return None

            from utils.job_status import is_job_eligible
            if not is_job_eligible(job_data):
                logger.info(
                    f"Applied job {job_id} is closed "
                    f"(isOpen={job_data.get('isOpen')}, "
                    f"status={job_data.get('status')}) — skipping injection"
                )
                return None

            assigned_users = job_data.get('assignedUsers', {})
            if isinstance(assigned_users, dict):
                users_list = assigned_users.get('data', [])
            elif isinstance(assigned_users, list):
                users_list = assigned_users
            else:
                users_list = []

            user_ids = [u.get('id') for u in users_list if isinstance(u, dict) and u.get('id')]
            if user_ids:
                user_email_map = bullhorn.get_user_emails(user_ids)
                for user in users_list:
                    if isinstance(user, dict) and user.get('id') in user_email_map:
                        user['email'] = user_email_map[user['id']].get('email', '')

            job_data['_injected_applied_job'] = True

            return job_data

        except Exception as e:
            logger.error(f"Error fetching applied job {job_id}: {str(e)}")
            return None

    def _mark_application_vetted(self, parsed_email_id: int, success: bool = True):
        """Mark a ParsedEmail record as vetted. Only reset retry counter on genuine success."""
        try:
            parsed_email = ParsedEmail.query.get(parsed_email_id)
            if parsed_email:
                parsed_email.vetted_at = datetime.utcnow()
                if success and parsed_email.vetting_retry_count > 0:
                    parsed_email.vetting_retry_count = 0
                db.session.commit()
                logger.debug(f"Marked ParsedEmail {parsed_email_id} as vetted (success={success})")
        except Exception as e:
            logger.error(f"Error marking application vetted: {str(e)}")

    def get_candidate_resume(self, candidate_id: int) -> Tuple[Optional[bytes], Optional[str]]:
        """
        Download the candidate's resume file from Bullhorn.

        Args:
            candidate_id: Bullhorn candidate ID

        Returns:
            Tuple of (file_content_bytes, filename) or (None, None) if not found
        """
        bullhorn = self._get_bullhorn_service()
        if not bullhorn or not bullhorn.base_url:
            return None, None

        try:
            url = f"{bullhorn.base_url}entityFiles/Candidate/{candidate_id}"
            params = {'BhRestToken': bullhorn.rest_token}

            response = bullhorn.session.get(url, params=params, timeout=30)

            if response.status_code != 200:
                logger.warning(f"Failed to get files for candidate {candidate_id}: {response.status_code}")
                return None, None

            data = response.json()
            files = data.get('EntityFiles', [])

            if not files:
                logger.info(f"No files found for candidate {candidate_id}")
                return None, None

            resume_file = None
            for file_info in files:
                file_type = file_info.get('type', '').lower()
                file_name = file_info.get('name', '').lower()

                if 'resume' in file_type or 'resume' in file_name:
                    resume_file = file_info
                    break

            if not resume_file and files:
                resume_file = files[0]

            if not resume_file:
                return None, None

            file_id = resume_file.get('id')
            filename = resume_file.get('name', f'resume_{candidate_id}')

            download_url = f"{bullhorn.base_url}file/Candidate/{candidate_id}/{file_id}"

            download_response = bullhorn.session.get(download_url, params=params, timeout=60)

            if download_response.status_code == 200:
                content = download_response.content
                content_type = download_response.headers.get('Content-Type', 'unknown')
                content_length = len(content) if content else 0
                first_bytes = content[:50] if content else b''
                logger.info(f"Downloaded resume for candidate {candidate_id}: {filename}")
                logger.info(f"  Content-Type: {content_type}, Size: {content_length} bytes, First bytes: {first_bytes[:30]}")

                if content and content.lstrip()[:1] == b'{' and b'"File"' in content[:200]:
                    try:
                        import json
                        import base64
                        json_data = json.loads(content)
                        file_obj = json_data.get('File', {})
                        b64_content = file_obj.get('fileContent', '')
                        if b64_content:
                            content = base64.b64decode(b64_content)
                            logger.info(f"📦 Unwrapped JSON-enveloped file for candidate {candidate_id}: {len(content)} bytes decoded from base64")
                        else:
                            logger.warning(f"JSON envelope found but fileContent is empty for candidate {candidate_id} — Bullhorn returned no file data")
                            return None, None
                    except (json.JSONDecodeError, Exception) as e:
                        logger.warning(f"Failed to unwrap JSON envelope for candidate {candidate_id}: {e}")

                return content, filename
            else:
                logger.warning(f"Failed to download file {file_id}: {download_response.status_code}")
                return None, None

        except Exception as e:
            logger.error(f"Error getting resume for candidate {candidate_id}: {str(e)}")
            return None, None

    def extract_resume_text(self, file_content: bytes, filename: str) -> Optional[str]:
        return _extract_resume_text(file_content, filename)

    def _extract_text_from_pdf(self, file_content: bytes) -> Optional[str]:
        return _extract_text_from_pdf(file_content)

    def _extract_text_from_docx(self, file_content: bytes) -> Optional[str]:
        return _extract_text_from_docx(file_content)

    def _extract_text_from_doc(self, file_content: bytes) -> Optional[str]:
        return _extract_text_from_doc(file_content)
