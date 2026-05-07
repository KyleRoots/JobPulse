"""
Candidate Deduplication & Recruiter Activity Gating.

Contains:
- CandidateDeduplicationMixin: Job-aware dedup logic and recruiter-activity gate
  - _should_skip_candidate: Job-aware dedup rules (24h window, 3x/7d cap)
  - _self_screen_cooldown_active: Short-window cooldown to kill back-to-back loop bugs
  - _is_paused_by_recruiter_decision: Per-job recruiter-decisioned skip
  - _is_paused_by_recruiter_activity: Recruiter-activity gate wrapper
  - _has_recent_recruiter_activity: Bullhorn Note search for recruiter touches
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple

import requests

from models import CandidateVettingLog, CandidateJobMatch, VettingConfig

logger = logging.getLogger(__name__)

_SCOUT_SCREEN_ACTION_PREFIX = "Scout Screen"


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
            recruiter_activity_lookback_minutes  (default '1440' = 24 hours)
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

        # Self-screen cooldown gate (May 2026) — runs FIRST and is candidate-
        # level (no applied_job_id required). Catches the back-to-back loop
        # where every 5-min cycle re-detects the same applicant and creates
        # a fresh vetting_log against ALL tearsheet jobs (Hemanth bug).
        if self._self_screen_cooldown_active(candidate_id):
            return True

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

        # Recruiter-decisioned full-skip gate (May 2026) — only fires for
        # (candidate × job) pairs we've previously screened. New jobs are
        # always evaluated so cross-role discovery (e.g. CSec PM rejected
        # for generalist PM, then re-applies to CSec PM) is preserved.
        if self._is_paused_by_recruiter_decision(bullhorn, candidate_id, applied_job_id):
            return True

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

    def _self_screen_cooldown_active(self, candidate_id: int) -> bool:
        """
        Self-screen cooldown gate (May 2026) — block re-screening of any
        candidate within `self_screen_cooldown_minutes` of the previous
        vetting_log row for that candidate, REGARDLESS of applied_job_id.

        This is the surgical fix for the back-to-back loop where the
        5-min cycle re-detects the same applicant via ParsedEmail or
        Bullhorn search and creates a fresh vetting_log against ALL
        tearsheet jobs each time. The 24h same-job dedup misses this
        because each cycle's per-job analysis runs against jobs that
        weren't `applied_job_id`.

        Bypass mechanics:
          - Vetting Sandbox runs its own analysis/write flow and never
            enters `_should_skip_candidate`, so this gate is a no-op there.
          - Quality-Auditor revets call `reset_candidate_for_revet` first,
            which deletes the candidate's prior CandidateVettingLog and
            CandidateJobMatch rows. The next detection pass therefore sees
            no recent log and proceeds.

        Returns:
            True → candidate vetted within cooldown window, skip.
            False → no cooldown active, killswitch off, or config error.
        """
        try:
            cooldown_raw = VettingConfig.get_value('self_screen_cooldown_minutes')
        except Exception as cfg_err:
            logger.warning(
                f"⚠️ Self-screen cooldown gate: VettingConfig read failed "
                f"({type(cfg_err).__name__}: {cfg_err}); failing open"
            )
            return False
        try:
            cooldown_min = int((cooldown_raw or '60').strip())
        except (ValueError, AttributeError, TypeError):
            cooldown_min = 60
        if cooldown_min <= 0:
            return False

        cutoff = datetime.utcnow() - timedelta(minutes=cooldown_min)
        recent = CandidateVettingLog.query.filter(
            CandidateVettingLog.bullhorn_candidate_id == candidate_id,
            CandidateVettingLog.status.in_(['completed', 'processing']),
            CandidateVettingLog.created_at >= cutoff,
        ).order_by(CandidateVettingLog.created_at.desc()).first()
        if recent:
            mins_since = max(0, int((datetime.utcnow() - recent.created_at).total_seconds() / 60))
            logger.info(
                f"⏱️ SELF-SCREEN COOLDOWN: candidate {candidate_id} re-screen blocked — "
                f"prior vetting_log id={recent.id} created {mins_since}min ago "
                f"(cooldown={cooldown_min}min)"
            )
            return True
        return False

    def _is_paused_by_recruiter_decision(
        self,
        bullhorn,
        candidate_id: int,
        applied_job_id: int,
    ) -> bool:
        """
        Recruiter-decisioned full-skip gate (May 2026).

        Skip the entire re-screen of a (candidate × job) pair when:
          1. Killswitch `recruiter_decision_skip_enabled` is ON.
          2. We have previously screened this exact (candidate × job) pair
             — proven by an existing CandidateJobMatch row. NEW jobs
             always proceed (cross-role discovery preserved).
          3. Bullhorn shows at least one human-authored note (commenter
             not in the configured api_user_ids set) dated AFTER the most
             recent "Scout Screen" note on this candidate.

        Fail-open: any error reading config / querying Bullhorn returns
        False (candidate proceeds). We'd rather over-vet than silently
        drop. The candidate-level cooldown (above) still protects against
        the loop bug even if this gate degrades.

        Returns:
            True → recruiter has decisioned this candidate × job pair, skip.
            False → not previously screened for this job, killswitch off,
                no human note since last screen, or lookup failed.
        """
        if bullhorn is None or not applied_job_id:
            return False

        try:
            enabled_raw = (VettingConfig.get_value('recruiter_decision_skip_enabled')
                           or 'true')
            api_user_ids_raw = VettingConfig.get_value('api_user_ids') or ''
        except Exception as cfg_err:
            logger.warning(
                f"⚠️ Recruiter-decision gate: VettingConfig read failed "
                f"({type(cfg_err).__name__}: {cfg_err}); failing open"
            )
            return False
        if str(enabled_raw).strip().lower() not in ('true', '1', 'yes', 'on'):
            return False

        # Step 1: cheap local check — have we ever screened this exact pair?
        try:
            existing = CandidateJobMatch.query.join(
                CandidateVettingLog,
                CandidateJobMatch.vetting_log_id == CandidateVettingLog.id,
            ).filter(
                CandidateVettingLog.bullhorn_candidate_id == candidate_id,
                CandidateJobMatch.bullhorn_job_id == applied_job_id,
            ).first()
        except Exception as db_err:
            logger.warning(
                f"⚠️ Recruiter-decision gate: prior-match lookup failed "
                f"({type(db_err).__name__}: {db_err}); failing open"
            )
            return False
        if not existing:
            return False  # Brand new (candidate × job) pair — always screen.

        # Step 2: build the api_user_ids exclusion set (mirrors
        # _has_recent_recruiter_activity behavior — auth user always added).
        api_user_id_set = set()
        api_user_id = getattr(bullhorn, 'user_id', None)
        try:
            if api_user_id is not None:
                api_user_id_set.add(int(api_user_id))
        except (TypeError, ValueError):
            pass
        for part in str(api_user_ids_raw).split(','):
            part = part.strip()
            if part.isdigit():
                api_user_id_set.add(int(part))

        # Step 3: pull notes (action + commenter + date) and find the most
        # recent Scout Screen note. Then check for any human note after it.
        url = f"{bullhorn.base_url}entity/Candidate/{candidate_id}"
        params = {
            'fields': 'notes(id,dateAdded,action,commentingPerson(id))',
            'BhRestToken': bullhorn.rest_token,
        }
        try:
            resp = bullhorn.session.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                logger.warning(
                    f"⚠️ Recruiter-decision gate: Bullhorn HTTP {resp.status_code} "
                    f"for candidate {candidate_id}; failing open"
                )
                return False
            body = resp.json() or {}
        except (requests.RequestException, ValueError) as req_err:
            logger.warning(
                f"⚠️ Recruiter-decision gate: Bullhorn lookup failed for candidate "
                f"{candidate_id} ({type(req_err).__name__}: {req_err}); failing open"
            )
            return False

        candidate_data = body.get('data') if isinstance(body, dict) else None
        if not isinstance(candidate_data, dict):
            return False
        notes_assoc = candidate_data.get('notes')
        if isinstance(notes_assoc, dict):
            notes_raw = notes_assoc.get('data')
        elif isinstance(notes_assoc, list):
            notes_raw = notes_assoc
        else:
            notes_raw = []
        if not isinstance(notes_raw, list):
            return False
        notes = [n for n in notes_raw if isinstance(n, dict)]
        if not notes:
            return False

        def _date_key(n):
            try:
                return int(n.get('dateAdded') or 0)
            except (TypeError, ValueError, AttributeError):
                return 0

        # Find the most-recent Scout Screen note.
        scout_screen_dates = [
            _date_key(n) for n in notes
            if str(n.get('action') or '').startswith(_SCOUT_SCREEN_ACTION_PREFIX)
        ]
        if not scout_screen_dates:
            # We've matched this job before but no Scout Screen note found —
            # could be old data; don't second-guess, let it screen.
            return False
        latest_screen_ms = max(scout_screen_dates)

        # Look for a human-authored note dated AFTER the latest Scout Screen.
        for note in notes:
            note_ms = _date_key(note)
            if note_ms <= latest_screen_ms:
                continue
            action = str(note.get('action') or '')
            if action.startswith(_SCOUT_SCREEN_ACTION_PREFIX):
                continue  # another AI screen, not a recruiter decision
            cp = note.get('commentingPerson') or {}
            cp_id = cp.get('id')
            is_human = False
            if cp_id is None:
                is_human = True
            else:
                try:
                    is_human = int(cp_id) not in api_user_id_set
                except (TypeError, ValueError):
                    is_human = True
            if is_human:
                hours_since = max(0, int((datetime.utcnow().timestamp() * 1000 - note_ms) / 3600000))
                logger.info(
                    f"🛑 RECRUITER-DECISIONED SKIP: candidate {candidate_id} × job "
                    f"{applied_job_id} — human note id={note.get('id')} action='{action}' "
                    f"posted ~{hours_since}h ago (after latest Scout Screen). "
                    f"Skipping re-screen for this job."
                )
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
            api_user_ids_raw = VettingConfig.get_value('api_user_ids') or ''
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

        # Bug #5c (May 2026): the recruiter-activity check must exclude
        # ALL configured api_user_ids (e.g. Pandologic API, Myticas API
        # User, etc.), not just the single Bullhorn auth user. Otherwise
        # any candidate whose only note is from another API integration
        # (e.g. PandoLogic's "New application delivered" note) gets
        # mis-classified as having recent recruiter activity and is
        # silently blocked from auto-vetting indefinitely.
        api_user_ids = []
        for part in str(api_user_ids_raw).split(','):
            part = part.strip()
            if part.isdigit():
                api_user_ids.append(int(part))

        active, minutes_ago = self._has_recent_recruiter_activity(
            bullhorn, candidate_id, lookback_min, api_user_ids=api_user_ids
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
        api_user_ids: Optional[list] = None,
    ) -> Tuple[bool, Optional[int]]:
        """
        Check whether a real human (commentingPerson.id NOT in the configured
        api_user_ids set) has added a Note on this candidate within the
        lookback window.

        Bug #5b (May 2026): switched from the broken
        ``search/Note?query=personReference.id:X`` Lucene index — which
        returned ``total=0`` for candidates whose notes WERE visible in
        the Bullhorn UI (UI-added notes link via the ``candidates``
        to-many association rather than ``personReference``) — to the
        canonical ``entity/Candidate/{id}?fields=notes(...)`` association
        lookup. This is the same pattern owner_reassignment uses
        (``_find_first_human_interactor``) and reads the live
        association table, so manually-added recruiter notes are no
        longer invisible. Without this fix the gate silently failed open
        and candidates whose owner was still an API user got
        re-screened despite recent recruiter activity.

        Bug #5c (May 2026): the "is human" check now compares against the
        FULL configured ``api_user_ids`` set (Pandologic API, Myticas API
        User, etc.) rather than only the single Bullhorn auth user. The
        prior single-user check mis-classified notes from other API
        integrations (e.g. PandoLogic's "New application delivered" note,
        author = Pandologic API id=4582033) as human recruiter activity
        and silently blocked every PandoLogic-sourced candidate from
        auto-vetting forever. The auth user (``bullhorn.user_id``) is
        always added to the exclusion set so back-compat is preserved
        even when ``api_user_ids`` is empty/unset.

        Single retry on transient failures (5xx, network, JSON parse).
        Fail-open on persistent failure: returns (False, None) and logs a
        WARNING so operators can see when this safety net is degraded.

        Args:
            bullhorn: Authenticated BullhornService instance.
            candidate_id: Bullhorn candidate ID.
            lookback_minutes: How far back to look for recruiter notes.
            api_user_ids: Optional list of configured API user IDs to
                exclude from the "is human" check. The auth user
                (``bullhorn.user_id``) is always added to this set. If
                None or empty, only the auth user is excluded
                (preserves prior behavior for back-compat / tests).

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

        # Build the union of {auth user} ∪ {configured api_user_ids}.
        # Defensive int coercion — any non-numeric entry is dropped
        # rather than crashing the gate.
        api_user_id_set = set()
        try:
            api_user_id_set.add(int(api_user_id))
        except (TypeError, ValueError):
            pass
        for uid in (api_user_ids or []):
            try:
                api_user_id_set.add(int(uid))
            except (TypeError, ValueError):
                continue

        since_dt = datetime.utcnow() - timedelta(minutes=lookback_minutes)
        since_ms = int(since_dt.timestamp() * 1000)
        url = f"{bullhorn.base_url}entity/Candidate/{candidate_id}"
        params = {
            'fields': 'notes(id,dateAdded,commentingPerson(id))',
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
                        body = resp.json() or {}
                    except ValueError as parse_err:
                        last_error = f"JSON parse error: {parse_err}"
                        if attempt == 1:
                            time.sleep(1)
                            continue
                        break

                    candidate_data = body.get('data')
                    if not isinstance(candidate_data, dict):
                        candidate_data = {}
                    notes_assoc = candidate_data.get('notes')
                    # Bullhorn returns to-many associations as either a
                    # wrapped object ({'data': [...], 'total': N}) or, on
                    # some endpoints/versions, a bare list. Handle both.
                    if isinstance(notes_assoc, dict):
                        notes_raw = notes_assoc.get('data')
                    elif isinstance(notes_assoc, list):
                        notes_raw = notes_assoc
                    else:
                        notes_raw = []
                    # Defensive: enforce list-of-dicts. Anything else (a
                    # malformed upstream payload like a dict/string in
                    # `notes.data`, or non-dict note items) is treated as
                    # "no notes" — preserving the fail-open guarantee
                    # rather than raising AttributeError below.
                    if not isinstance(notes_raw, list):
                        notes = []
                    else:
                        notes = [n for n in notes_raw if isinstance(n, dict)]

                    now_ms = int(datetime.utcnow().timestamp() * 1000)

                    # Sort newest-first so we report the MOST recent
                    # recruiter touch (matches the prior search-based
                    # `sort=-dateAdded` semantics).
                    def _date_key(n):
                        try:
                            return int(n.get('dateAdded') or 0)
                        except (TypeError, ValueError, AttributeError):
                            return 0
                    notes_sorted = sorted(notes, key=_date_key, reverse=True)

                    for note in notes_sorted:
                        note_added = _date_key(note)
                        # Filter to the lookback window in code (entity
                        # endpoint doesn't support a date filter on the
                        # association). Newest-first iteration means we
                        # can break once we fall out of the window.
                        if note_added and note_added < since_ms:
                            break
                        cp = note.get('commentingPerson') or {}
                        cp_id = cp.get('id')
                        is_human = False
                        if cp_id is None:
                            is_human = True  # conservative: unknown author = recruiter
                        else:
                            try:
                                is_human = int(cp_id) not in api_user_id_set
                            except (TypeError, ValueError):
                                is_human = True
                        if is_human:
                            effective_added = note_added or now_ms
                            minutes_ago = max(0, int((now_ms - effective_added) / 60000))
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
