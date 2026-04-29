"""
Owner Reassignment Task
=======================
Scheduled task: find Bullhorn Candidate records whose owner is a known API
service account (Pandologic, Matador, Myticas, etc.) and reassign ownership to
the human recruiter responsible for the job the candidate applied to.

Configuration is read from VettingConfig at runtime:
  - auto_reassign_owner_enabled  bool   master toggle (default false)
  - api_user_ids                 str    comma-separated Bullhorn CorporateUser IDs
  - reassign_owner_note_enabled  bool   whether to leave a Bullhorn note (default true)

THREAD-SAFETY: Uses standalone requests.get/post — never bh.session.* — because
this runs in a background APScheduler thread and requests.Session is not thread-safe.
"""
import time
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import requests as _requests

from bullhorn_service import BullhornService

logger = logging.getLogger(__name__)

_CANDIDATE_FIELDS = (
    'id,firstName,lastName,email,owner(id,firstName,lastName)'
)
_JOB_FIELDS = (
    'id,title,owner(id,firstName,lastName),'
    'assignedUsers(id,firstName,lastName)'
)


def _get_vetting_config(key: str, default: str = '') -> str:
    """Read a single VettingConfig value. Returns default if key is missing."""
    try:
        from models import VettingConfig
        row = VettingConfig.query.filter_by(setting_key=key).first()
        return row.setting_value if row else default
    except Exception:
        return default


def _parse_api_user_ids(raw: str) -> List[int]:
    """Parse a comma-separated string of Bullhorn CorporateUser IDs to a list of ints."""
    ids = []
    for part in raw.split(','):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


def _fetch_candidate_job(
    base_url: str,
    headers: dict,
    candidate_id: int,
) -> Tuple[Optional[int], Optional[str], Optional[int]]:
    """
    Find the most recent job a candidate applied to and return:
      (job_id, job_title, job_owner_corporate_user_id)

    Returns (None, None, None) if no submission is found or the lookup fails.
    """
    sub_url = f"{base_url}search/JobSubmission"
    params = {
        'query': f'candidate.id:{candidate_id}',
        'fields': f'id,jobOrder({_JOB_FIELDS}),dateAdded',
        'count': 1,
        'sort': '-dateAdded',
    }

    for attempt in range(2):
        try:
            resp = _requests.get(sub_url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                submissions = resp.json().get('data', [])
                if not submissions:
                    return (None, None, None)
                job = submissions[0].get('jobOrder') or {}
                job_id = job.get('id')
                job_title = job.get('title', '')
                owner = job.get('owner') or {}
                owner_id = owner.get('id')
                if not owner_id:
                    assigned = job.get('assignedUsers', {})
                    users = assigned.get('data', []) if isinstance(assigned, dict) else assigned
                    if users:
                        owner_id = users[0].get('id')
                return (job_id, job_title, owner_id)
            if 500 <= resp.status_code < 600 and attempt == 0:
                time.sleep(1)
                continue
            logger.warning(
                f"Job submission lookup for candidate {candidate_id}: "
                f"HTTP {resp.status_code}"
            )
            return (None, None, None)
        except Exception as exc:
            if attempt == 0:
                time.sleep(1)
                continue
            logger.warning(
                f"Job submission lookup exception for candidate {candidate_id}: {exc}"
            )
            return (None, None, None)

    return (None, None, None)


def _build_note_text(
    candidate_first: str,
    candidate_last: str,
    old_owner_first: str,
    old_owner_last: str,
    new_owner_first: str,
    new_owner_last: str,
    job_title: str,
    job_id: Optional[int],
) -> str:
    old_name = f"{old_owner_first} {old_owner_last}".strip() or "API User"
    new_name = f"{new_owner_first} {new_owner_last}".strip() or "Unknown Recruiter"
    job_ref = f"{job_title} (ID: {job_id})" if job_id else job_title or "their applied job"
    return (
        f"Owner Reassigned — {candidate_first} {candidate_last}\n\n"
        f"Previous owner: {old_name}\n"
        f"New owner: {new_name}\n"
        f"Reason: API service account detected; ownership transferred to the "
        f"recruiter responsible for {job_ref}.\n\n"
        f"This change was made automatically by Scout Genius."
    )


def reassign_api_user_candidates(since_minutes: int = 30) -> None:
    """
    Main entry point for the scheduled task.

    Runs inside an app context (APScheduler worker). Reads VettingConfig for
    the toggle, the configured API user IDs, and the note toggle. For each
    candidate owned by an API user account, derives the correct human recruiter
    from their most recent job submission and updates the Bullhorn record.
    """
    from app import app

    with app.app_context():
        try:
            if _get_vetting_config('auto_reassign_owner_enabled', 'false').lower() != 'true':
                logger.debug("owner_reassignment: feature disabled — skipping run")
                return

            api_user_ids = _parse_api_user_ids(
                _get_vetting_config('api_user_ids', '')
            )
            if not api_user_ids:
                logger.info(
                    "owner_reassignment: no API user IDs configured — skipping run. "
                    "Add Bullhorn CorporateUser IDs to the 'api_user_ids' setting."
                )
                return

            note_enabled = (
                _get_vetting_config('reassign_owner_note_enabled', 'true').lower() == 'true'
            )

            bh = BullhornService()
            if not bh.authenticate():
                logger.warning("owner_reassignment: Bullhorn authentication failed — skipping run")
                return

            base_url = bh.base_url
            rest_token = bh.rest_token
            headers = {
                'BhRestToken': rest_token,
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            }

            since_time = datetime.utcnow() - timedelta(minutes=since_minutes)
            since_ts = int(since_time.timestamp() * 1000)

            if len(api_user_ids) == 1:
                owner_clause = f'owner.id:{api_user_ids[0]}'
            else:
                owner_clause = '(' + ' OR '.join(f'owner.id:{uid}' for uid in api_user_ids) + ')'

            query = f'{owner_clause} AND dateLastModified:[{since_ts} TO *]'

            search_url = f"{base_url}search/Candidate"
            page_size = 100
            start = 0
            candidates: list = []

            while True:
                search_params = {
                    'query': query,
                    'fields': _CANDIDATE_FIELDS,
                    'count': page_size,
                    'start': start,
                    'sort': '-dateLastModified',
                }
                resp = _requests.get(
                    search_url, headers=headers, params=search_params, timeout=30
                )
                if resp.status_code != 200:
                    logger.error(
                        f"owner_reassignment: candidate search failed "
                        f"HTTP {resp.status_code}: {resp.text[:300]}"
                    )
                    return

                page_data = resp.json()
                page_candidates = page_data.get('data', [])
                candidates.extend(page_candidates)

                total = page_data.get('total', len(candidates))
                start += len(page_candidates)
                if start >= total or len(page_candidates) < page_size:
                    break

                time.sleep(0.1)

            if not candidates:
                logger.info(
                    f"owner_reassignment: no API-owned candidates found in the last "
                    f"{since_minutes} minutes — nothing to do"
                )
                return

            logger.info(
                f"owner_reassignment: found {len(candidates)} API-owned candidate(s) "
                f"to evaluate (owner IDs: {api_user_ids})"
            )

            reassigned = 0
            skipped_no_job = 0
            skipped_no_recruiter = 0
            failed = 0

            for candidate in candidates:
                candidate_id = candidate.get('id')
                if not candidate_id:
                    continue

                c_first = candidate.get('firstName', '')
                c_last = candidate.get('lastName', '')
                old_owner = candidate.get('owner') or {}
                old_owner_first = old_owner.get('firstName', '')
                old_owner_last = old_owner.get('lastName', '')

                job_id, job_title, recruiter_id = _fetch_candidate_job(
                    base_url, headers, candidate_id
                )

                if job_id is None:
                    logger.info(
                        f"owner_reassignment: skipping candidate {candidate_id} "
                        f"({c_first} {c_last}) — no job submission found"
                    )
                    skipped_no_job += 1
                    continue

                if not recruiter_id:
                    logger.info(
                        f"owner_reassignment: skipping candidate {candidate_id} "
                        f"({c_first} {c_last}) — job {job_id} has no resolvable owner"
                    )
                    skipped_no_recruiter += 1
                    continue

                if int(recruiter_id) in api_user_ids:
                    logger.info(
                        f"owner_reassignment: skipping candidate {candidate_id} "
                        f"({c_first} {c_last}) — job owner is also an API user ({recruiter_id})"
                    )
                    skipped_no_recruiter += 1
                    continue

                try:
                    upd = _requests.post(
                        f"{base_url}entity/Candidate/{candidate_id}",
                        headers=headers,
                        json={'owner': {'id': int(recruiter_id)}},
                        timeout=15,
                    )
                    body = {}
                    try:
                        body = upd.json()
                    except Exception:
                        pass

                    if (upd.status_code in (200, 201)
                            and not body.get('errorCode')
                            and not body.get('errors')
                            and (body.get('changeType') == 'UPDATE'
                                 or body.get('changedEntityId') is not None)):

                        new_owner_first = ''
                        new_owner_last = ''
                        try:
                            user_resp = _requests.get(
                                f"{base_url}entity/CorporateUser/{recruiter_id}",
                                headers=headers,
                                params={'fields': 'id,firstName,lastName'},
                                timeout=10,
                            )
                            if user_resp.status_code == 200:
                                ud = user_resp.json().get('data', {})
                                new_owner_first = ud.get('firstName', '')
                                new_owner_last = ud.get('lastName', '')
                        except Exception:
                            pass

                        old_name = f"{old_owner_first} {old_owner_last}".strip() or "API User"
                        new_name = f"{new_owner_first} {new_owner_last}".strip() or str(recruiter_id)
                        logger.info(
                            f"✅ owner_reassignment: candidate {candidate_id} "
                            f"({c_first} {c_last}) reassigned "
                            f"{old_name} → {new_name} "
                            f"(job: {job_title or job_id})"
                        )
                        reassigned += 1

                        if note_enabled:
                            try:
                                note_text = _build_note_text(
                                    c_first, c_last,
                                    old_owner_first, old_owner_last,
                                    new_owner_first, new_owner_last,
                                    job_title or '', job_id,
                                )
                                note_url = f"{base_url}entity/Note"
                                note_data = {
                                    'personReference': {'id': int(candidate_id)},
                                    'action': 'AI Resume Summary',
                                    'comments': note_text,
                                    'isDeleted': False,
                                    'candidates': [{'id': int(candidate_id)}],
                                }
                                _requests.put(
                                    note_url,
                                    headers=headers,
                                    json=note_data,
                                    timeout=30,
                                )
                            except Exception as note_err:
                                logger.warning(
                                    f"owner_reassignment: note creation failed for "
                                    f"candidate {candidate_id}: {note_err}"
                                )
                    else:
                        logger.warning(
                            f"owner_reassignment: update failed for candidate {candidate_id}: "
                            f"HTTP {upd.status_code} — {body}"
                        )
                        failed += 1

                except Exception as rec_err:
                    logger.error(
                        f"owner_reassignment: error processing candidate {candidate_id}: {rec_err}"
                    )
                    failed += 1

                time.sleep(0.1)

            logger.info(
                f"owner_reassignment: complete — {reassigned} reassigned, "
                f"{skipped_no_job} skipped (no job), "
                f"{skipped_no_recruiter} skipped (no recruiter), "
                f"{failed} failed"
            )

        except Exception as e:
            logger.error(f"owner_reassignment: unexpected error — {e}", exc_info=True)
