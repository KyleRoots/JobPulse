from __future__ import annotations
"""
Candidate Detection - Discovery of new candidates from Bullhorn and ParsedEmail.

Contains:
- detect_new_applicants: Finds Online Applicant candidates via Bullhorn search
- detect_pandologic_candidates: Finds Pandologic API candidates (owner-based)
- detect_pandologic_note_candidates: Finds re-applicants via Pandologic API notes
- detect_matador_candidates: Finds Matador API candidates (corporate website submissions)
- detect_unvetted_applications: Primary detection via ParsedEmail records
- _should_skip_candidate: Job-aware dedup logic
- _fetch_candidate_details: Fetches full candidate data from Bullhorn
- _fetch_applied_job: Fetches a single job for applied-job injection
- _mark_application_vetted: Marks ParsedEmail as vetted
- get_candidate_resume: Downloads resume file from Bullhorn
- extract_resume_text / _extract_text_from_*: Delegates to vetting.resume_utils
"""

import logging
logger = logging.getLogger(__name__)
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

from app import db
from sqlalchemy import func, case
from models import CandidateVettingLog, ParsedEmail, VettingConfig
from vetting.resume_utils import (
    extract_resume_text as _extract_resume_text,
    extract_text_from_pdf as _extract_text_from_pdf,
    extract_text_from_docx as _extract_text_from_docx,
    extract_text_from_doc as _extract_text_from_doc,
)


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


class CandidateDetectionMixin:
    """Candidate discovery from Bullhorn and ParsedEmail."""

    def _should_skip_candidate(
        self,
        candidate_id: int,
        applied_job_id: int = None,
        bullhorn=None,
    ) -> bool:
        """
        Job-aware dedup + recruiter-activity gate: decide whether to skip
        a candidate based on their vetting history and recent recruiter touch.

        Dedup rules (DB-only, fast path):
        - Different job → always rescreen (return False)
        - Same job within 24h → skip (return True)
        - Same job 3+ times within 7 days → skip (return True)
        - No applied_job_id context → fall back to 24h global dedup

        Recruiter-activity gate (Bullhorn API, slow path):
        - If `bullhorn` is provided AND the candidate would otherwise be vetted
          (i.e. all dedup checks passed), check Bullhorn for recent Note activity
          by a real human (not the API user). If found → skip with INFO log.
        - Configurable via VettingConfig:
            recruiter_activity_check_enabled  (default 'true')
            recruiter_activity_lookback_minutes  (default '60')
        - Fails open: if the Bullhorn lookup errors out, candidate proceeds
          (we'd rather over-vet than silently drop a candidate).

        Args:
            candidate_id: Bullhorn candidate ID
            applied_job_id: The job ID the candidate applied to (None if unknown)
            bullhorn: Optional authenticated BullhornService for the recruiter-
                activity check. If None, the gate is skipped (DB-only dedup).

        Returns:
            True if candidate should be skipped, False if they should be rescreened
        """
        from datetime import timedelta

        if not applied_job_id:
            # No job context — fall back to 24h global dedup (cross-path safety)
            recent_cutoff = datetime.utcnow() - timedelta(hours=24)
            recent = CandidateVettingLog.query.filter(
                CandidateVettingLog.bullhorn_candidate_id == candidate_id,
                CandidateVettingLog.status.in_(['completed', 'processing']),
                CandidateVettingLog.created_at >= recent_cutoff
            ).first()
            if recent:
                logger.debug(
                    f"Candidate {candidate_id} vetted within 24h (no job context), skipping"
                )
                return True
            # Dedup passed → check recruiter-activity gate before allowing vet
            if self._is_paused_by_recruiter_activity(bullhorn, candidate_id):
                return True
            return False

        # Rule 1: Same job within 24h → skip
        recent_cutoff = datetime.utcnow() - timedelta(hours=24)
        same_job_recent = CandidateVettingLog.query.filter(
            CandidateVettingLog.bullhorn_candidate_id == candidate_id,
            CandidateVettingLog.applied_job_id == applied_job_id,
            CandidateVettingLog.status.in_(['completed', 'processing']),
            CandidateVettingLog.created_at >= recent_cutoff
        ).first()
        if same_job_recent:
            logger.debug(
                f"Candidate {candidate_id} vetted for job {applied_job_id} within 24h, skipping"
            )
            return True

        # Rule 2: Same job 3+ times in 7 days → skip
        week_cutoff = datetime.utcnow() - timedelta(days=7)
        same_job_week_count = CandidateVettingLog.query.filter(
            CandidateVettingLog.bullhorn_candidate_id == candidate_id,
            CandidateVettingLog.applied_job_id == applied_job_id,
            CandidateVettingLog.status.in_(['completed', 'processing']),
            CandidateVettingLog.created_at >= week_cutoff
        ).count()
        if same_job_week_count >= 3:
            logger.debug(
                f"Candidate {candidate_id} vetted for job {applied_job_id} "
                f"{same_job_week_count} times in 7 days, skipping (soft cap)"
            )
            return True

        # Different job or under caps → would normally rescreen.
        # Final gate: defer if a recruiter has been actively working this candidate.
        if self._is_paused_by_recruiter_activity(bullhorn, candidate_id):
            return True

        return False

    def _is_paused_by_recruiter_activity(self, bullhorn, candidate_id: int) -> bool:
        """
        Wrapper for the recruiter-activity gate that respects the
        `recruiter_activity_check_enabled` killswitch and a no-bullhorn safe path.

        Returns True if the candidate should be paused (recent recruiter touch),
        False otherwise (no activity detected, killswitch off, no bullhorn,
        or persistent lookup failure — see _has_recent_recruiter_activity).
        """
        if bullhorn is None:
            return False

        # VettingConfig lookups are wrapped to fail-open: if the DB is
        # momentarily unreachable we'd rather skip the gate (one possible
        # spurious vet) than crash the screening worker for this candidate.
        try:
            enabled_raw = (VettingConfig.get_value('recruiter_activity_check_enabled')
                           or 'true')
            lookback_raw = VettingConfig.get_value('recruiter_activity_lookback_minutes')
        except Exception as cfg_err:
            logger.warning(
                f"⚠️ Recruiter-activity gate: VettingConfig read failed "
                f"({type(cfg_err).__name__}: {cfg_err}); failing open"
            )
            return False

        if str(enabled_raw).strip().lower() not in ('true', '1', 'yes', 'on'):
            return False

        try:
            lookback_min = int((lookback_raw or '60').strip())
        except (ValueError, AttributeError):
            lookback_min = 60
        if lookback_min <= 0:
            return False

        active, minutes_ago = self._has_recent_recruiter_activity(
            bullhorn, candidate_id, lookback_min
        )
        if active:
            logger.info(
                f"👤 Candidate {candidate_id}: recruiter activity within "
                f"{minutes_ago}min (window={lookback_min}min), deferring auto-vet"
            )
            return True
        return False

    def _has_recent_recruiter_activity(
        self,
        bullhorn,
        candidate_id: int,
        lookback_minutes: int,
    ) -> Tuple[bool, Optional[int]]:
        """
        Query Bullhorn for any Note on this candidate authored by a real human
        (commentingPerson.id != bullhorn.user_id) within the lookback window.

        Single retry on transient failures (5xx, network, JSON parse).
        Fail-open on persistent failure: returns (False, None) and logs a
        WARNING so operators can see when this safety net is degraded.

        Args:
            bullhorn: Authenticated BullhornService instance.
            candidate_id: Bullhorn candidate ID.
            lookback_minutes: How far back to look for recruiter notes.

        Returns:
            Tuple of (active, minutes_since_most_recent):
              - (True, N)  → human note found N minutes ago, candidate paused
              - (False, None) → no human notes in window, OR lookup failed
                (caller should not block the vet on lookup failure)
        """
        api_user_id = getattr(bullhorn, 'user_id', None)
        # If we don't know who the AI is, we can't distinguish AI notes from
        # recruiter notes — fail open rather than blocking every vet.
        if not api_user_id:
            logger.debug(
                f"Recruiter-activity check skipped for candidate {candidate_id}: "
                f"bullhorn.user_id not set"
            )
            return (False, None)

        since_dt = datetime.utcnow() - timedelta(minutes=lookback_minutes)
        since_ms = int(since_dt.timestamp() * 1000)
        url = f"{bullhorn.base_url}search/Note"
        params = {
            'query': (
                f'personReference.id:{candidate_id} '
                f'AND dateAdded:[{since_ms} TO *] '
                f'AND isDeleted:false'
            ),
            'fields': 'id,dateAdded,commentingPerson(id)',
            'count': 25,
            'sort': '-dateAdded',
            'BhRestToken': bullhorn.rest_token,
        }

        last_error: Optional[str] = None
        last_status: Optional[int] = None

        for attempt in (1, 2):
            try:
                resp = bullhorn.session.get(url, params=params, timeout=15)
                last_status = resp.status_code

                if resp.status_code == 200:
                    try:
                        notes = resp.json().get('data', []) or []
                    except ValueError as parse_err:
                        last_error = f"JSON parse error: {parse_err}"
                        if attempt == 1:
                            time.sleep(1)
                            continue
                        break

                    now_ms = int(datetime.utcnow().timestamp() * 1000)
                    for note in notes:
                        cp = note.get('commentingPerson') or {}
                        cp_id = cp.get('id')
                        if cp_id is None:
                            # Unknown author — treat conservatively as recruiter
                            # to avoid silently bypassing the gate.
                            note_added = note.get('dateAdded') or now_ms
                            minutes_ago = max(0, int((now_ms - note_added) / 60000))
                            return (True, minutes_ago)
                        try:
                            if int(cp_id) != int(api_user_id):
                                note_added = note.get('dateAdded') or now_ms
                                minutes_ago = max(0, int((now_ms - note_added) / 60000))
                                return (True, minutes_ago)
                        except (TypeError, ValueError):
                            # Non-numeric author id — treat as recruiter (safer)
                            note_added = note.get('dateAdded') or now_ms
                            minutes_ago = max(0, int((now_ms - note_added) / 60000))
                            return (True, minutes_ago)
                    # All notes in window are by the API user (or no notes) → no recruiter touch
                    return (False, None)

                if 500 <= resp.status_code < 600:
                    last_error = f"HTTP {resp.status_code}"
                    if attempt == 1:
                        time.sleep(1)
                        continue
                    break

                # 4xx — non-transient, do not retry
                last_error = f"HTTP {resp.status_code}"
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
            f"⚠️ Recruiter-activity lookup failed for candidate {candidate_id} "
            f"after retry ({last_error}, status={last_status}); "
            f"failing open — candidate will proceed to vet (gate degraded)"
        )
        return (False, None)
    
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

    def detect_new_applicants(self, since_minutes: int = 5) -> List[Dict]:
        """
        Find new candidates with "Online Applicant" status that haven't been processed yet.
        Uses dateLastModified filter to catch both new and returning candidates.
        
        Args:
            since_minutes: Only look at candidates created/updated in the last N minutes
            
        Returns:
            List of candidate dictionaries from Bullhorn
        """
        bullhorn = self._get_bullhorn_service()
        if not bullhorn:
            return []
        
        if not bullhorn.authenticate():
            logger.error("Failed to authenticate with Bullhorn for candidate detection")
            return []
        
        try:
            # Determine the since timestamp - use last run or fallback to since_minutes
            last_run = self._get_last_run_timestamp()
            if last_run:
                since_time = last_run
                logger.info(f"Using last run timestamp for detection: {since_time}")
            else:
                # First run - only look at very recent candidates (prevent historical processing)
                since_time = datetime.utcnow() - timedelta(minutes=since_minutes)
                logger.info(f"First run - only detecting candidates from last {since_minutes} minutes")
            
            since_timestamp = int(since_time.timestamp() * 1000)  # Bullhorn uses milliseconds
            
            # Use dateLastModified to catch returning candidates who reapply to new jobs
            # (dateAdded only reflects candidate creation, not new applications)
            url = f"{bullhorn.base_url}search/Candidate"
            params = {
                'query': f'status:"Online Applicant" AND dateLastModified:[{since_timestamp} TO *]',
                'fields': 'id,firstName,lastName,email,phone,status,dateAdded,dateLastModified,source,occupation,description,address(address1,city,state,countryName)',
                'count': 50,  # Limit batch size for performance
                'sort': '-dateLastModified',  # Most recently modified first
                'BhRestToken': bullhorn.rest_token
            }
            
            response = bullhorn.session.get(url, params=params, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"Failed to search for applicants: {response.status_code}")
                return []
            
            data = response.json()
            candidates = data.get('data', [])
            
            logger.info(f"Bullhorn returned {len(candidates)} candidates since {since_time}")
            
            # Job-aware dedup: allow rescreening for different jobs
            new_candidates = []
            for candidate in candidates:
                candidate_id = candidate.get('id')
                if not candidate_id:
                    continue
                
                # For Online Applicants detected via Bullhorn search, we don't have the
                # applied job ID at this stage. Use global 24h dedup as a safety net.
                # The ParsedEmail path (primary) already handles job-aware dedup properly.
                # Pass bullhorn so the recruiter-activity gate can fire for this path.
                if self._should_skip_candidate(candidate_id, bullhorn=bullhorn):
                    logger.debug(f"Candidate {candidate_id} vetted recently, skipping")
                else:
                    new_candidates.append(candidate)
                    logger.info(f"New applicant detected: {candidate.get('firstName')} {candidate.get('lastName')} (ID: {candidate_id})")
            
            logger.info(f"Found {len(new_candidates)} new applicants to process out of {len(candidates)} recent online applicants")
            return new_candidates
            
        except Exception as e:
            logger.error(f"Error detecting new applicants: {str(e)}")
            return []
    
    def detect_pandologic_candidates(self, since_minutes: int = 5) -> List[Dict]:
        """
        Find candidates from Pandologic API that haven't been vetted recently.
        Pandologic feeds candidates directly into Bullhorn with owner='Pandologic API'.
        
        Uses dateLastModified to catch returning candidates who reapply to new jobs
        (dateAdded only reflects candidate creation, not new applications).
        
        Job-aware dedup: candidates applying to different jobs are always rescreened.
        
        Args:
            since_minutes: Only look at candidates modified in the last N minutes (fallback)
            
        Returns:
            List of candidate dictionaries from Bullhorn
        """
        bullhorn = self._get_bullhorn_service()
        if not bullhorn:
            return []
        
        if not bullhorn.authenticate():
            logger.error("Failed to authenticate with Bullhorn for Pandologic detection")
            return []
        
        try:
            # Use same timestamp logic as detect_new_applicants
            last_run = self._get_last_run_timestamp()
            if last_run:
                since_time = last_run
            else:
                since_time = datetime.utcnow() - timedelta(minutes=since_minutes)
            
            since_timestamp = int(since_time.timestamp() * 1000)
            
            # Use dateLastModified to catch returning candidates who reapply to new jobs
            # (dateAdded only reflects candidate creation, not new applications)
            url = f"{bullhorn.base_url}search/Candidate"
            params = {
                'query': f'owner.name:"Pandologic API" AND dateLastModified:[{since_timestamp} TO *]',
                'fields': 'id,firstName,lastName,email,phone,status,dateAdded,dateLastModified,source,occupation,description,address(address1,city,state,countryName),owner(name)',
                'count': 50,
                'sort': '-dateLastModified',
                'BhRestToken': bullhorn.rest_token
            }
            
            response = bullhorn.session.get(url, params=params, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"Failed to search for Pandologic candidates: {response.status_code}")
                return []
            
            data = response.json()
            candidates = data.get('data', [])
            
            logger.info(f"🔍 Pandologic: Found {len(candidates)} candidates since {since_time}")
            
            # Job-aware dedup: get latest JobSubmission for each candidate to check
            # if they were already vetted for THIS specific job
            new_candidates = []
            for candidate in candidates:
                candidate_id = candidate.get('id')
                if not candidate_id:
                    continue
                
                # Get latest job submission to determine which job they applied to.
                # Helper retries once on transient failures and logs a WARNING if
                # the lookup ultimately fails (so missed re-applications stay visible).
                applied_job_id, applied_job_title, _lookup_ok = self._fetch_latest_job_submission(
                    bullhorn, candidate_id
                )
                if applied_job_id is not None:
                    candidate['_applied_job_id'] = applied_job_id
                    candidate['_applied_job_title'] = applied_job_title or ''

                # Job-aware dedup + recruiter-activity gate
                if self._should_skip_candidate(candidate_id, applied_job_id, bullhorn=bullhorn):
                    logger.debug(
                        f"Pandologic candidate {candidate_id} skipped by job-aware dedup "
                        f"(applied_job={applied_job_id})"
                    )
                else:
                    new_candidates.append(candidate)
                    job_info = f" for job {applied_job_id}" if applied_job_id else ""
                    logger.info(
                        f"🔵 Pandologic candidate detected: "
                        f"{candidate.get('firstName')} {candidate.get('lastName')} "
                        f"(ID: {candidate_id}{job_info})"
                    )
            
            logger.info(f"🔍 Pandologic: {len(new_candidates)} candidates to vet out of {len(candidates)} total")
            return new_candidates
            
        except Exception as e:
            logger.error(f"Error detecting Pandologic candidates: {str(e)}")
            return []
    
    def detect_matador_candidates(self, since_minutes: int = 5) -> List[Dict]:
        """
        Find candidates from Matador API that haven't been vetted recently.
        Matador feeds candidates directly into Bullhorn with owner='Matador API'
        when applicants submit through the corporate website. These candidates
        land in Bullhorn with status='New Lead' (not 'Online Applicant'), so the
        status-based detection in detect_new_applicants will not catch them, and
        they bypass the inbound email parser entirely. Owner-based detection is
        the only reliable path — same pattern as detect_pandologic_candidates.
        
        Uses dateLastModified to catch returning candidates who reapply to new
        jobs (dateAdded only reflects candidate creation, not new applications).
        
        Job-aware dedup: candidates applying to different jobs are always
        rescreened (delegated to _should_skip_candidate).
        
        Args:
            since_minutes: Only look at candidates modified in the last N
                minutes (fallback when no last-run timestamp is available).
            
        Returns:
            List of candidate dictionaries from Bullhorn, each enriched with
            `_applied_job_id` / `_applied_job_title` when a JobSubmission is
            found.
        """
        bullhorn = self._get_bullhorn_service()
        if not bullhorn:
            return []
        
        if not bullhorn.authenticate():
            logger.error("Failed to authenticate with Bullhorn for Matador detection")
            return []
        
        try:
            # Use same timestamp logic as detect_pandologic_candidates
            last_run = self._get_last_run_timestamp()
            if last_run:
                since_time = last_run
            else:
                since_time = datetime.utcnow() - timedelta(minutes=since_minutes)
            
            since_timestamp = int(since_time.timestamp() * 1000)
            
            # Use dateLastModified to catch returning candidates who reapply to new jobs
            url = f"{bullhorn.base_url}search/Candidate"
            params = {
                'query': f'owner.name:"Matador API" AND dateLastModified:[{since_timestamp} TO *]',
                'fields': 'id,firstName,lastName,email,phone,status,dateAdded,dateLastModified,source,occupation,description,address(address1,city,state,countryName),owner(name)',
                'count': 50,
                'sort': '-dateLastModified',
                'BhRestToken': bullhorn.rest_token
            }
            
            response = bullhorn.session.get(url, params=params, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"Failed to search for Matador candidates: {response.status_code}")
                return []
            
            data = response.json()
            candidates = data.get('data', [])
            
            logger.info(f"🔍 Matador: Found {len(candidates)} candidates since {since_time}")
            
            # Job-aware dedup: get latest JobSubmission for each candidate to check
            # if they were already vetted for THIS specific job
            new_candidates = []
            for candidate in candidates:
                candidate_id = candidate.get('id')
                if not candidate_id:
                    continue
                
                # Get latest job submission to determine which job they applied to.
                # Helper retries once on transient failures and logs a WARNING if
                # the lookup ultimately fails (so missed re-applications stay visible).
                applied_job_id, applied_job_title, _lookup_ok = self._fetch_latest_job_submission(
                    bullhorn, candidate_id
                )
                if applied_job_id is not None:
                    candidate['_applied_job_id'] = applied_job_id
                    candidate['_applied_job_title'] = applied_job_title or ''

                # Job-aware dedup + recruiter-activity gate
                if self._should_skip_candidate(candidate_id, applied_job_id, bullhorn=bullhorn):
                    logger.debug(
                        f"Matador candidate {candidate_id} skipped by job-aware dedup "
                        f"(applied_job={applied_job_id})"
                    )
                else:
                    new_candidates.append(candidate)
                    job_info = f" for job {applied_job_id}" if applied_job_id else ""
                    logger.info(
                        f"🟣 Matador candidate detected: "
                        f"{candidate.get('firstName')} {candidate.get('lastName')} "
                        f"(ID: {candidate_id}{job_info})"
                    )
            
            logger.info(f"🔍 Matador: {len(new_candidates)} candidates to vet out of {len(candidates)} total")
            return new_candidates
            
        except Exception as e:
            logger.error(f"Error detecting Matador candidates: {str(e)}")
            return []

    def _resolve_pandologic_user_id(self, bullhorn) -> Optional[int]:
        """
        Resolve and cache the Bullhorn CorporateUser ID for the 'Pandologic API'
        account, used by detect_pandologic_note_candidates.

        Caching: stored in VettingConfig as 'pandologic_api_user_id' after the
        first successful lookup. Subsequent calls return immediately from the
        cache (one Bullhorn round-trip ever, per environment).

        Returns None on persistent lookup failure — caller should skip note-based
        detection for this cycle and try again next minute.
        """
        cached = VettingConfig.get_value('pandologic_api_user_id')
        if cached:
            try:
                return int(cached)
            except (ValueError, TypeError):
                logger.warning(
                    f"Cached pandologic_api_user_id is malformed ({cached!r}); "
                    f"re-resolving from Bullhorn"
                )

        url = f"{bullhorn.base_url}query/CorporateUser"
        params = {
            'where': "name='Pandologic API'",
            'fields': 'id,name',
            'count': 1,
            'BhRestToken': bullhorn.rest_token,
        }
        try:
            resp = bullhorn.session.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text[:500]
                logger.warning(
                    f"Pandologic CorporateUser lookup failed: HTTP {resp.status_code} — "
                    f"body: {body} — note-based detector will no-op this cycle"
                )
                return None
            users = resp.json().get('data', []) or []
            if not users:
                logger.warning(
                    "Pandologic API CorporateUser not found in Bullhorn — "
                    "note-based detector will no-op until the user appears"
                )
                return None
            user_id = users[0].get('id')
            if not user_id:
                return None
            try:
                VettingConfig.set_value(
                    'pandologic_api_user_id',
                    str(user_id),
                    description=(
                        'Bullhorn CorporateUser ID for the "Pandologic API" account. '
                        'Used by detect_pandologic_note_candidates to discover '
                        're-applicants whose owner did not change to Pandologic API. '
                        'Auto-resolved on first detector run; safe to delete to '
                        'force re-resolution.'
                    ),
                )
                logger.info(f"✅ Cached pandologic_api_user_id: {user_id}")
            except Exception as cache_err:
                logger.warning(
                    f"Could not cache pandologic_api_user_id: {cache_err} "
                    f"(detector will still work, just slower next cycle)"
                )
            return int(user_id)
        except Exception as e:
            logger.warning(f"Error resolving Pandologic API user_id: {e}")
            return None

    def detect_pandologic_note_candidates(self, since_minutes: int = 5) -> List[Dict]:
        """
        Find candidates whose Bullhorn record received a NEW Note from the
        'Pandologic API' user — catches existing/returning candidates whose
        parent Candidate.owner did NOT change to 'Pandologic API' (so the
        primary detect_pandologic_candidates detector misses them).

        Background: when an existing Bullhorn candidate re-applies via
        PandoLogic, PandoLogic creates a fresh JobSubmission and posts a note
        to the candidate ("New application delivered by PandoLogic to job
        id#NNNN") but does NOT change the parent Candidate's owner or status.
        The owner-based detector only finds brand-new candidates (owner =
        'Pandologic API'), so re-applicants fall through every other channel
        (no email forward, no status flip, no owner change). This note-based
        detector closes that gap by watching for notes authored by the
        PandoLogic API CorporateUser.

        Same dedup rules + recruiter-activity gate as the other detectors.

        Args:
            since_minutes: Fallback window when no last-run timestamp is available.

        Returns:
            List of candidate dicts (each enriched with `_applied_job_id` and
            `_applied_job_title` when a JobSubmission lookup succeeds).
        """
        bullhorn = self._get_bullhorn_service()
        if not bullhorn:
            return []

        if not bullhorn.authenticate():
            logger.error("Failed to authenticate with Bullhorn for Pandologic-note detection")
            return []

        user_id = self._resolve_pandologic_user_id(bullhorn)
        if not user_id:
            # Resolver already logged at WARNING level — just no-op this cycle.
            return []

        try:
            last_run = self._get_last_run_timestamp()
            if last_run:
                since_time = last_run
            else:
                since_time = datetime.utcnow() - timedelta(minutes=since_minutes)
            since_ms = int(since_time.timestamp() * 1000)

            url = f"{bullhorn.base_url}search/Note"
            # Note is a Lucene-indexed entity — must use search/ not query/.
            # commentingPerson.id is NOT in the Lucene index so it cannot be
            # used as a filter here; we fetch all recent notes and filter by
            # commentingPerson.id in Python after the response.
            params = {
                'query': (
                    f'dateAdded:[{since_ms} TO *] '
                    f'AND isDeleted:false'
                ),
                'fields': (
                    'id,dateAdded,commentingPerson(id),'
                    'personReference(id,firstName,lastName,email,phone,status)'
                ),
                'count': 200,
                'sort': '-dateAdded',
                'BhRestToken': bullhorn.rest_token,
            }

            resp = bullhorn.session.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text[:500]
                logger.error(
                    f"Pandologic note search failed: HTTP {resp.status_code} — "
                    f"body: {body}"
                )
                return []

            all_notes = resp.json().get('data', []) or []
            # Keep only notes authored by the Pandologic API user.
            notes = [
                n for n in all_notes
                if (n.get('commentingPerson') or {}).get('id') == user_id
            ]
            logger.info(
                f"📝 Pandologic notes: Found {len(notes)} note(s) by user "
                f"{user_id} (out of {len(all_notes)} total) since {since_time}"
            )

            # One candidate may have multiple notes in the window — dedup by ID.
            seen_candidate_ids = set()
            new_candidates = []
            for note in notes:
                person_ref = note.get('personReference') or {}
                candidate_id = person_ref.get('id')
                if not candidate_id or candidate_id in seen_candidate_ids:
                    continue
                seen_candidate_ids.add(candidate_id)

                applied_job_id, applied_job_title, _lookup_ok = self._fetch_latest_job_submission(
                    bullhorn, candidate_id
                )

                # Job-aware dedup + recruiter-activity gate (same as other detectors)
                if self._should_skip_candidate(candidate_id, applied_job_id, bullhorn=bullhorn):
                    logger.debug(
                        f"Pandologic-note candidate {candidate_id} skipped by dedup "
                        f"(applied_job={applied_job_id})"
                    )
                    continue

                # Build candidate dict from the expanded personReference. Mirrors
                # the shape returned by the other detectors so downstream code
                # is uniform.
                candidate = dict(person_ref)
                if applied_job_id is not None:
                    candidate['_applied_job_id'] = applied_job_id
                    candidate['_applied_job_title'] = applied_job_title or ''

                new_candidates.append(candidate)
                job_info = f" for job {applied_job_id}" if applied_job_id else ""
                logger.info(
                    f"📝 Pandologic-note candidate detected: "
                    f"{candidate.get('firstName')} {candidate.get('lastName')} "
                    f"(ID: {candidate_id}{job_info})"
                )

            logger.info(
                f"📝 Pandologic notes: {len(new_candidates)} candidate(s) to vet "
                f"out of {len(notes)} note(s)"
            )
            return new_candidates

        except Exception as e:
            logger.error(f"Error detecting Pandologic-note candidates: {str(e)}")
            return []

    def detect_unvetted_applications(self, limit: int = 25) -> List[Dict]:
        """
        Find candidates from ParsedEmail records that have been successfully processed
        but not yet vetted. This captures ALL inbound applicants (both new and existing
        candidates) since email parsing is the entry point for all applications.
        
        Database query runs FIRST (no external API needed), and Bullhorn auth is only
        attempted when there are actual candidates to fetch details for.
        
        Args:
            limit: Maximum number of candidates to return (configurable batch size)
            
        Returns:
            List of candidate dictionaries ready for vetting
        """
        try:
            # ── Step 1: Query local database FIRST (no API call needed) ──
            from sqlalchemy import func, case

            # Resolve cutoff up-front so the stats query can report the
            # truly-actionable backlog (post-cutoff) separately from the
            # historical pre-cutoff records that will never be processed.
            cutoff_dt = _resolve_vetting_cutoff()

            unvetted_predicate = (
                (ParsedEmail.status == 'completed')
                & (ParsedEmail.bullhorn_candidate_id.isnot(None))
                & (ParsedEmail.vetted_at.is_(None))
            )
            if cutoff_dt is not None:
                # Explicitly exclude NULL received_at — matches the actual
                # WHERE-clause filter behavior below, and protects the stat
                # against SQL backends that may evaluate CASE differently for
                # 3-valued logic.
                pending_eligible_predicate = (
                    unvetted_predicate
                    & ParsedEmail.received_at.isnot(None)
                    & (ParsedEmail.received_at >= cutoff_dt)
                )
            else:
                pending_eligible_predicate = unvetted_predicate

            stats = db.session.query(
                func.count(ParsedEmail.id).label('total'),
                func.count(case((ParsedEmail.status == 'completed', 1))).label('completed'),
                func.count(case((
                    (ParsedEmail.status == 'completed') & (ParsedEmail.bullhorn_candidate_id.isnot(None)),
                    1
                ))).label('with_candidate'),
                func.count(case((
                    (ParsedEmail.status == 'completed') & (ParsedEmail.bullhorn_candidate_id.isnot(None)) & (ParsedEmail.vetted_at.isnot(None)),
                    1
                ))).label('already_vetted'),
                func.count(case((pending_eligible_predicate, 1))).label('pending_eligible'),
            ).first()

            total_unvetted = stats.with_candidate - stats.already_vetted
            pre_cutoff_excluded = total_unvetted - stats.pending_eligible

            logger.info(
                f"📊 ParsedEmail stats: total={stats.total}, completed={stats.completed}, "
                f"with_candidate_id={stats.with_candidate}, already_vetted={stats.already_vetted}, "
                f"pending_eligible={stats.pending_eligible} (actionable), "
                f"pre_cutoff_excluded={pre_cutoff_excluded} (skipped by cutoff), "
                f"total_unvetted={total_unvetted}"
            )

            # DEBUG: Show most recent 5 ParsedEmail records (only at DEBUG level)
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                recent_emails = ParsedEmail.query.order_by(ParsedEmail.received_at.desc()).limit(5).all()
                for pe in recent_emails:
                    logger.debug(f"  📧 Recent ParsedEmail id={pe.id}: candidate='{pe.candidate_name}', "
                                f"status={pe.status}, bh_id={pe.bullhorn_candidate_id}, "
                                f"vetted_at={'SET' if pe.vetted_at else 'NULL'}, received={pe.received_at}")

            # Query ParsedEmail for completed applications that haven't been vetted
            # Apply cutoff date if configured (skip historical backlog)
            if cutoff_dt is not None:
                logger.info(f"📅 Vetting cutoff active: only processing applicants received after {cutoff_dt} UTC")

            filters = [
                ParsedEmail.status == 'completed',
                ParsedEmail.vetted_at.is_(None),
                ParsedEmail.bullhorn_candidate_id.isnot(None),
            ]
            if cutoff_dt:
                filters.append(ParsedEmail.received_at >= cutoff_dt)
            
            unvetted_emails = ParsedEmail.query.filter(
                *filters
            ).order_by(
                ParsedEmail.processed_at.asc()  # Process oldest first (FIFO)
            ).limit(limit).all()
            
            if not unvetted_emails:
                logger.info("No unvetted applications found in ParsedEmail records")
                return []
            
            logger.info(f"Found {len(unvetted_emails)} unvetted applications from email parsing")
            
            # Build candidate list from ParsedEmail records
            candidates_to_vet = []
            already_vetted_ids = []
            
            # BATCH: Pre-fetch vetting logs linked to these specific ParsedEmail IDs
            batch_email_ids = [pe.id for pe in unvetted_emails]
            
            vetted_email_ids = set()
            if batch_email_ids:
                # Check if a vetting log already exists for these specific ParsedEmail IDs
                # This is the key dedup: same ParsedEmail.id = duplicate loop, different = valid re-application
                existing_logs = CandidateVettingLog.query.filter(
                    CandidateVettingLog.parsed_email_id.in_(batch_email_ids),
                    CandidateVettingLog.status.in_(['completed', 'failed', 'processing'])
                ).all()
                vetted_email_ids = {log.parsed_email_id for log in existing_logs}
                if vetted_email_ids:
                    logger.info(f"Found {len(vetted_email_ids)} ParsedEmails already linked to vetting logs")
            
            # Filter out already-vetted before making any Bullhorn API calls
            candidates_needing_details = []
            for parsed_email in unvetted_emails:
                candidate_id = parsed_email.bullhorn_candidate_id
                
                # Dedup: skip if a vetting log already exists for THIS specific ParsedEmail
                if parsed_email.id in vetted_email_ids:
                    already_vetted_ids.append(parsed_email.id)
                    logger.info(f"Candidate {candidate_id} already vetted for ParsedEmail {parsed_email.id}, skipping (duplicate loop prevention)")
                    continue
                
                candidates_needing_details.append(parsed_email)
            
            # Batch update already-vetted records in single transaction
            if already_vetted_ids:
                try:
                    ParsedEmail.query.filter(ParsedEmail.id.in_(already_vetted_ids)).update(
                        {'vetted_at': datetime.utcnow()},
                        synchronize_session=False
                    )
                    db.session.commit()
                    logger.info(f"Marked {len(already_vetted_ids)} already-vetted applications")
                except Exception as e:
                    db.session.rollback()
                    logger.error(f"Error updating already-vetted applications: {str(e)}")
            
            # ── Step 2: Only authenticate with Bullhorn if we have candidates to fetch ──
            if not candidates_needing_details:
                logger.info("All unvetted candidates were already processed or skipped")
                return []
            
            logger.info(f"Need Bullhorn details for {len(candidates_needing_details)} candidates")
            
            bullhorn = self._get_bullhorn_service()
            if not bullhorn:
                logger.warning(f"⚠️ Bullhorn service unavailable — {len(candidates_needing_details)} candidates waiting for vetting")
                return []
            
            if not bullhorn.authenticate():
                logger.warning(f"⚠️ Bullhorn authentication failed (possible rate limit) — "
                              f"{len(candidates_needing_details)} candidates waiting for vetting. "
                              f"Will retry next cycle.")
                return []
            
            # ── Step 3: Fetch candidate details from Bullhorn ──
            for parsed_email in candidates_needing_details:
                candidate_id = parsed_email.bullhorn_candidate_id
                candidate_data = self._fetch_candidate_details(bullhorn, candidate_id)
                
                if candidate_data:
                    # Attach the ParsedEmail ID for tracking
                    candidate_data['_parsed_email_id'] = parsed_email.id
                    candidate_data['_applied_job_id'] = parsed_email.bullhorn_job_id
                    candidate_data['_is_duplicate'] = parsed_email.is_duplicate_candidate
                    candidates_to_vet.append(candidate_data)
                    logger.info(f"Queued for vetting: {candidate_data.get('firstName')} {candidate_data.get('lastName')} (ID: {candidate_id}, Applied to Job: {parsed_email.bullhorn_job_id})")
            
            logger.info(f"Prepared {len(candidates_to_vet)} candidates for vetting from email parsing")
            return candidates_to_vet
            
        except Exception as e:
            logger.error(f"Error detecting unvetted applications: {str(e)}")
            db.session.rollback()
            return []
    
    def _fetch_candidate_details(self, bullhorn: BullhornService, candidate_id: int) -> Optional[Dict]:
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
    
    def _fetch_applied_job(self, bullhorn: 'BullhornService', job_id: int) -> Optional[Dict]:
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
            
            # Only return open jobs. Use shared eligibility helper so the
            # screening filter cannot drift from the dashboard/feed filters.
            # NOTE: a job is ineligible if EITHER isOpen is false OR status is
            # in the ineligible set — recruiters sometimes flip isOpen=false
            # but leave the status as 'Accepting Candidates' (e.g., job 31896).
            from utils.job_status import is_job_eligible
            if not is_job_eligible(job_data):
                logger.info(
                    f"Applied job {job_id} is closed "
                    f"(isOpen={job_data.get('isOpen')}, "
                    f"status={job_data.get('status')}) — skipping injection"
                )
                return None
            
            # Enrich with user emails (same pattern as get_active_jobs_from_tearsheets)
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
            
            # Mark as injected for audit trail
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
            # First, get the list of files attached to the candidate
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
            
            # Find the resume file (prioritize files with "Resume" type or name)
            resume_file = None
            for file_info in files:
                file_type = file_info.get('type', '').lower()
                file_name = file_info.get('name', '').lower()
                
                if 'resume' in file_type or 'resume' in file_name:
                    resume_file = file_info
                    break
            
            # If no explicit resume, use the first file (often the resume)
            if not resume_file and files:
                resume_file = files[0]
            
            if not resume_file:
                return None, None
            
            # Download the file content
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

