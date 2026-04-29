"""
Candidate Deduplication & Recruiter Activity Gating.

Contains:
- CandidateDeduplicationMixin: Job-aware dedup logic and recruiter-activity gate
  - _should_skip_candidate: Job-aware dedup rules (24h window, 3x/7d cap)
  - _is_paused_by_recruiter_activity: Recruiter-activity gate wrapper
  - _has_recent_recruiter_activity: Bullhorn Note search for recruiter touches
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple

import requests

from models import CandidateVettingLog, VettingConfig

logger = logging.getLogger(__name__)


class CandidateDeduplicationMixin:
    """Job-aware dedup and recruiter-activity gating for candidate detection."""

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
            if self._is_paused_by_recruiter_activity(bullhorn, candidate_id):
                return True
            return False

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
                            note_added = note.get('dateAdded') or now_ms
                            minutes_ago = max(0, int((now_ms - note_added) / 60000))
                            return (True, minutes_ago)
                        try:
                            if int(cp_id) != int(api_user_id):
                                note_added = note.get('dateAdded') or now_ms
                                minutes_ago = max(0, int((now_ms - note_added) / 60000))
                                return (True, minutes_ago)
                        except (TypeError, ValueError):
                            note_added = note.get('dateAdded') or now_ms
                            minutes_ago = max(0, int((now_ms - note_added) / 60000))
                            return (True, minutes_ago)
                    return (False, None)

                if 500 <= resp.status_code < 600:
                    last_error = f"HTTP {resp.status_code}"
                    if attempt == 1:
                        time.sleep(1)
                        continue
                    break

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
