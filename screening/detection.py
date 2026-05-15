from __future__ import annotations
"""
Candidate Detection - Discovery of new candidates from Bullhorn and ParsedEmail.

Contains:
- CandidateDetectionMixin: Candidate source detectors (composes dedup + data access)
  - detect_new_applicants: Finds Online Applicant candidates via Bullhorn search
  - detect_pandologic_candidates: Finds Pandologic API candidates (owner-based)
  - detect_pandologic_note_candidates: Finds re-applicants via Pandologic API notes
  - detect_matador_candidates: Finds Matador API candidates (corporate website submissions)
  - detect_unvetted_applications: Primary detection via ParsedEmail records
  - _resolve_pandologic_user_id: Resolves and caches Pandologic CorporateUser ID

Sub-modules:
- screening.dedup: Job-aware dedup logic and recruiter-activity gating
- screening.candidate_data: Bullhorn data access, resume handling, _resolve_vetting_cutoff
"""

import logging
logger = logging.getLogger(__name__)
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app import db
from sqlalchemy import func, case
from models import CandidateVettingLog, ParsedEmail, VettingConfig
from screening.dedup import CandidateDeduplicationMixin
from screening.candidate_data import CandidateDataAccessMixin, _resolve_vetting_cutoff


# ─────────────────────────────────────────────────────────────────────────────
# Human-owner skip helpers
# ─────────────────────────────────────────────────────────────────────────────
# Background: the 5-minute screening cycle was re-screening candidates that
# had already been transferred to a human recruiter. Bullhorn's search index
# can lag behind a freshly-added note by ~1 minute; during that window the
# `dateLastModified` advances, the candidate matches the detection query,
# but the dedup check still sees no recruiter-activity yet — so the system
# re-vets a candidate that is already being actively worked.
#
# Fix: once a candidate's owner is a human (i.e., NOT one of the configured
# `api_user_ids` API-service accounts), the screening cycle skips them.
# This is gated by the `screening_skip_human_owned` VettingConfig kill switch
# (default ON) so it can be disabled without a deploy if it ever misfires.

def _parse_api_user_ids_for_screening() -> List[int]:
    """
    Read the `api_user_ids` VettingConfig setting (a comma-separated list of
    Bullhorn CorporateUser IDs that represent API-service accounts) and
    return them as a list of ints. Returns [] if missing or malformed.
    Mirrors `tasks.owner_reassignment._parse_api_user_ids` semantics.
    """
    try:
        row = VettingConfig.query.filter_by(setting_key='api_user_ids').first()
    except Exception:
        return []
    raw = (row.setting_value if row else '') or ''
    out: List[int] = []
    for part in raw.split(','):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def _screening_skip_human_owned() -> bool:
    """
    Read the `screening_skip_human_owned` VettingConfig kill switch.
    Defaults to True (skip enabled) when missing, so the safer behavior is
    the default even on a fresh DB.
    """
    try:
        row = VettingConfig.query.filter_by(
            setting_key='screening_skip_human_owned'
        ).first()
    except Exception:
        return True
    if row is None or row.setting_value is None:
        return True
    return str(row.setting_value).strip().lower() in ('true', '1', 'yes', 'on')


def _is_human_owned(candidate: Dict, api_user_ids: List[int]) -> bool:
    """
    Return True iff `candidate.owner.id` is set AND is NOT one of the
    configured API-service-account IDs. A missing owner or an owner whose
    ID is in `api_user_ids` returns False (i.e., the candidate is still
    eligible for screening).
    """
    owner = candidate.get('owner') or {}
    owner_id = owner.get('id')
    if owner_id is None:
        return False
    try:
        return int(owner_id) not in api_user_ids
    except (TypeError, ValueError):
        return False


class CandidateDetectionMixin(CandidateDeduplicationMixin, CandidateDataAccessMixin):
    """Candidate discovery from Bullhorn and ParsedEmail."""

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
            last_run = self._get_last_run_timestamp()
            if last_run:
                since_time = last_run
                logger.info(f"Using last run timestamp for detection: {since_time}")
            else:
                since_time = datetime.utcnow() - timedelta(minutes=since_minutes)
                logger.info(f"First run - only detecting candidates from last {since_minutes} minutes")
            
            since_timestamp = int(since_time.timestamp() * 1000)
            
            url = f"{bullhorn.base_url}search/Candidate"
            params = {
                'query': f'status:"Online Applicant" AND dateLastModified:[{since_timestamp} TO *]',
                'fields': 'id,firstName,lastName,email,phone,status,dateAdded,dateLastModified,source,occupation,description,address(address1,city,state,countryName),owner(id,name)',
                'count': 50,
                'sort': '-dateLastModified',
                'BhRestToken': bullhorn.rest_token
            }
            
            response = bullhorn.session.get(url, params=params, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"Failed to search for applicants: {response.status_code}")
                return []
            
            data = response.json()
            candidates = data.get('data', [])
            
            logger.info(f"Bullhorn returned {len(candidates)} candidates since {since_time}")

            # Resolve human-ownership skip configuration once per cycle. If
            # the kill switch is on AND we have configured API user IDs,
            # any candidate whose owner is NOT one of them is being
            # actively worked by a recruiter and must not be re-screened.
            skip_human_owned = _screening_skip_human_owned()
            api_user_ids = (
                _parse_api_user_ids_for_screening() if skip_human_owned else []
            )
            human_owned_skipped = 0

            new_candidates = []
            for candidate in candidates:
                candidate_id = candidate.get('id')
                if not candidate_id:
                    continue

                if (skip_human_owned
                        and api_user_ids
                        and _is_human_owned(candidate, api_user_ids)):
                    owner = candidate.get('owner') or {}
                    logger.info(
                        f"Candidate {candidate_id} "
                        f"({candidate.get('firstName')} {candidate.get('lastName')}) "
                        f"has human owner (id={owner.get('id')}, "
                        f"name={owner.get('name', '?')}); skipping re-screen"
                    )
                    human_owned_skipped += 1
                    continue

                if self._should_skip_candidate(candidate_id, bullhorn=bullhorn):
                    logger.debug(f"Candidate {candidate_id} vetted recently, skipping")
                else:
                    new_candidates.append(candidate)
                    logger.info(f"New applicant detected: {candidate.get('firstName')} {candidate.get('lastName')} (ID: {candidate_id})")

            logger.info(
                f"Found {len(new_candidates)} new applicants to process out of "
                f"{len(candidates)} recent online applicants "
                f"({human_owned_skipped} skipped — human-owned)"
            )
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
            last_run = self._get_last_run_timestamp()
            if last_run:
                since_time = last_run
            else:
                since_time = datetime.utcnow() - timedelta(minutes=since_minutes)
            
            since_timestamp = int(since_time.timestamp() * 1000)
            
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
            
            new_candidates = []
            for candidate in candidates:
                candidate_id = candidate.get('id')
                if not candidate_id:
                    continue
                
                applied_job_id, applied_job_title, _lookup_ok = self._fetch_latest_job_submission(
                    bullhorn, candidate_id
                )

                # Job submission gate: skip candidates with no confirmed application.
                # If the lookup succeeded (_lookup_ok=True) but returned no submission,
                # the candidate was sourced/added without applying to a role — do not vet.
                # If the lookup failed transiently (_lookup_ok=False), fail open and
                # proceed so a Bullhorn API hiccup never silently drops an applicant.
                if _lookup_ok and applied_job_id is None:
                    logger.debug(
                        f"Pandologic candidate {candidate_id} skipped — "
                        f"no JobSubmission found (sourced, not applied)"
                    )
                    continue

                if applied_job_id is not None:
                    candidate['_applied_job_id'] = applied_job_id
                    candidate['_applied_job_title'] = applied_job_title or ''

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
            last_run = self._get_last_run_timestamp()
            if last_run:
                since_time = last_run
            else:
                since_time = datetime.utcnow() - timedelta(minutes=since_minutes)
            
            since_timestamp = int(since_time.timestamp() * 1000)
            
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
            
            new_candidates = []
            for candidate in candidates:
                candidate_id = candidate.get('id')
                if not candidate_id:
                    continue
                
                applied_job_id, applied_job_title, _lookup_ok = self._fetch_latest_job_submission(
                    bullhorn, candidate_id
                )

                # Job submission gate: skip candidates with no confirmed application.
                # Mirrors the identical gate in detect_pandologic_candidates.
                if _lookup_ok and applied_job_id is None:
                    logger.debug(
                        f"Matador candidate {candidate_id} skipped — "
                        f"no JobSubmission found (sourced, not applied)"
                    )
                    continue

                if applied_job_id is not None:
                    candidate['_applied_job_id'] = applied_job_id
                    candidate['_applied_job_title'] = applied_job_title or ''

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
            return []

        try:
            last_run = self._get_last_run_timestamp()
            if last_run:
                since_time = last_run
            else:
                since_time = datetime.utcnow() - timedelta(minutes=since_minutes)
            since_ms = int(since_time.timestamp() * 1000)

            url = f"{bullhorn.base_url}search/Note"
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
            notes = [
                n for n in all_notes
                if (n.get('commentingPerson') or {}).get('id') == user_id
            ]
            logger.info(
                f"📝 Pandologic notes: Found {len(notes)} note(s) by user "
                f"{user_id} (out of {len(all_notes)} total) since {since_time}"
            )

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

                if self._should_skip_candidate(candidate_id, applied_job_id, bullhorn=bullhorn):
                    logger.debug(
                        f"Pandologic-note candidate {candidate_id} skipped by dedup "
                        f"(applied_job={applied_job_id})"
                    )
                    continue

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

    def detect_pending_revet_candidates(
        self,
        lookback_days: int = 7,
        max_candidates: int = 10,
    ) -> List[Dict]:
        """Re-enqueue candidates whose audit log fired ``revet_triggered``
        but whose ``revet_new_score`` is still NULL because no fresh
        ``CandidateVettingLog`` has landed since the audit row.

        Background — why this is needed even though the auditor already
        calls ``_trigger_revet``:

        ``RevetMixin._trigger_revet`` deletes the candidate's prior
        vetting state and expects the next vetting cycle to re-score them.
        For parsed_email-path candidates that works because
        ``detect_unvetted_applications`` re-picks them up via
        ``vetted_at IS NULL``. For non-parsed_email-path candidates
        (PandoLogic notes / Matador / Bullhorn-search legacy), the upstream
        detectors only look at the last 5–10 minutes of activity — so a
        candidate revet that was triggered hours ago is never re-discovered,
        leaving the audit row stuck as ``revet_triggered`` /
        ``revet_new_score=NULL`` indefinitely. The May-2026 stuck-row
        cluster (rows 6242, 6251, 6387 + ~4 unidentified) all fit this
        pattern.

        This detector uses the audit log itself as the durable revet
        queue: any audit row still missing ``revet_new_score`` after
        ``_trigger_revet`` ran is grounds to re-enqueue the candidate.
        It also calls ``clear_candidate_vetting_state`` to fix orphaned
        rows whose original ``_trigger_revet`` ran before the
        ``parsed_email_id``→``bullhorn_candidate_id`` filter fix and
        therefore left stale CandidateVettingLog rows behind (those
        stale rows would otherwise trip ``_self_screen_cooldown_active``
        and block the re-vet again).

        Args:
            lookback_days: How far back to scan the audit log. 7 days
                covers the auditor's stuck-row SLA window without
                pulling in ancient rows that should have been
                reclassified by hand.
            max_candidates: Per-cycle cap so a backlog of stuck rows
                cannot starve regular pipeline work.

        Returns:
            List of candidate dicts shaped like the other detectors so
            ``run_vetting_cycle`` can dedupe and process them through the
            standard ``process_candidate`` path.
        """
        from app import db
        from models import VettingAuditLog, CandidateVettingLog, CandidateJobMatch
        from vetting_audit_service import clear_candidate_vetting_state

        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        try:
            pending_audits = (
                VettingAuditLog.query
                .filter(
                    VettingAuditLog.action_taken == 'revet_triggered',
                    VettingAuditLog.revet_new_score.is_(None),
                    VettingAuditLog.created_at >= cutoff,
                )
                .order_by(VettingAuditLog.created_at.asc())
                .all()
            )
        except Exception as e:
            logger.warning(
                f"⚠️ Pending-revet detector: audit log query failed "
                f"({type(e).__name__}: {e}); skipping this cycle"
            )
            return []

        if not pending_audits:
            return []

        seen_candidate_ids = set()
        candidates_to_enqueue: List[Dict] = []
        skipped_already_revetted = 0
        skipped_active_session = 0
        skipped_clear_failed = 0

        for audit in pending_audits:
            if len(candidates_to_enqueue) >= max_candidates:
                break

            candidate_id = audit.bullhorn_candidate_id
            if not candidate_id or candidate_id in seen_candidate_ids:
                continue
            seen_candidate_ids.add(candidate_id)

            # If a fresh vlog has landed AFTER the audit row AND it
            # produced a CandidateJobMatch for the SAME job the audit
            # row references, then the re-vet actually ran for the
            # right job and only the back-fill failed. Skip and let
            # ``backfill_revet_new_score`` retry on the next vlog
            # commit. Candidate-level only would over-skip: a vlog
            # for a *different* job (e.g. a later application by the
            # same candidate) does not satisfy this audit row, so we
            # require the match to be keyed to ``audit.job_id``.
            # When ``audit.job_id`` is NULL, fall back to a strict
            # candidate-level guard since we have nothing finer to
            # filter on.
            try:
                if audit.job_id is not None:
                    post_audit_match_exists = db.session.query(
                        CandidateJobMatch.id
                    ).join(
                        CandidateVettingLog,
                        CandidateJobMatch.vetting_log_id == CandidateVettingLog.id,
                    ).filter(
                        CandidateVettingLog.bullhorn_candidate_id == candidate_id,
                        CandidateVettingLog.status.in_(['completed', 'processing']),
                        CandidateVettingLog.created_at > audit.created_at,
                        CandidateJobMatch.bullhorn_job_id == audit.job_id,
                    ).first() is not None
                else:
                    post_audit_match_exists = (
                        CandidateVettingLog.query
                        .filter(
                            CandidateVettingLog.bullhorn_candidate_id == candidate_id,
                            CandidateVettingLog.status.in_(['completed', 'processing']),
                            CandidateVettingLog.created_at > audit.created_at,
                        )
                        .first()
                    ) is not None
            except Exception as e:
                logger.warning(
                    f"⚠️ Pending-revet detector: post-audit lookup failed for "
                    f"candidate {candidate_id} ({type(e).__name__}: {e}); skipping"
                )
                continue
            if post_audit_match_exists:
                skipped_already_revetted += 1
                continue

            # Clear stale vetting state so ``_self_screen_cooldown_active``
            # doesn't block the next cycle. Note: we deliberately do NOT
            # call ``_should_skip_candidate`` here — that gate consults
            # the same stale vlogs we are about to delete, and would
            # self-block recovery for the very rows this detector exists
            # to recover. The auditor's revet decision was the human-
            # signal checkpoint; the post-audit-match guard above is the
            # loop guard. ``clear_candidate_vetting_state`` raises
            # ``RuntimeError`` when an active ``ScoutVettingSession``
            # would be orphaned by the delete — treat that as a separate
            # skip class so it shows up clearly in telemetry.
            try:
                stats = clear_candidate_vetting_state(candidate_id)
            except RuntimeError as e:
                from app import db
                try:
                    db.session.rollback()
                except Exception:
                    pass
                logger.info(
                    f"⏸️ Pending-revet detector: candidate {candidate_id} "
                    f"audit row {audit.id} skipped — {e}"
                )
                skipped_active_session += 1
                continue
            except Exception as e:
                from app import db
                try:
                    db.session.rollback()
                except Exception:
                    pass
                logger.warning(
                    f"⚠️ Pending-revet detector: clear_candidate_vetting_state "
                    f"failed for candidate {candidate_id} "
                    f"({type(e).__name__}: {e}); skipping enqueue"
                )
                skipped_clear_failed += 1
                continue

            name = audit.candidate_name or ''
            first_name = name.split(' ', 1)[0] if name else None
            last_name = name.split(' ', 1)[1] if ' ' in name else None

            cand_dict: Dict = {
                'id': candidate_id,
                'firstName': first_name,
                'lastName': last_name,
                '_pending_revet_audit_id': audit.id,
            }
            if audit.job_id is not None:
                cand_dict['_applied_job_id'] = audit.job_id
                cand_dict['_applied_job_title'] = audit.job_title or ''
            candidates_to_enqueue.append(cand_dict)

            audit_age_h = (datetime.utcnow() - audit.created_at).total_seconds() / 3600
            logger.info(
                f"🔁 Pending-revet re-enqueue: candidate {candidate_id} "
                f"({audit.candidate_name or 'unknown'}) audit row {audit.id} "
                f"finding={audit.finding_type} original_score={audit.original_score} "
                f"audit_age_hours={audit_age_h:.1f} cleared_vlogs="
                f"{stats['vetting_logs_deleted']}"
            )

        if (
            candidates_to_enqueue
            or skipped_already_revetted
            or skipped_active_session
            or skipped_clear_failed
        ):
            logger.info(
                f"🔁 Pending revets: {len(candidates_to_enqueue)} re-enqueued, "
                f"{skipped_already_revetted} already-revetted (back-fill pending), "
                f"{skipped_active_session} blocked by active Scout session, "
                f"{skipped_clear_failed} failed clear, "
                f"out of {len(pending_audits)} stuck audit row(s) in last "
                f"{lookback_days}d"
            )
        return candidates_to_enqueue

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
            from sqlalchemy import func, case

            cutoff_dt = _resolve_vetting_cutoff()

            unvetted_predicate = (
                (ParsedEmail.status == 'completed')
                & (ParsedEmail.bullhorn_candidate_id.isnot(None))
                & (ParsedEmail.vetted_at.is_(None))
            )
            if cutoff_dt is not None:
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

            if logging.getLogger().isEnabledFor(logging.DEBUG):
                recent_emails = ParsedEmail.query.order_by(ParsedEmail.received_at.desc()).limit(5).all()
                for pe in recent_emails:
                    logger.debug(f"  📧 Recent ParsedEmail id={pe.id}: candidate='{pe.candidate_name}', "
                                f"status={pe.status}, bh_id={pe.bullhorn_candidate_id}, "
                                f"vetted_at={'SET' if pe.vetted_at else 'NULL'}, received={pe.received_at}")

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
                ParsedEmail.processed_at.asc()
            ).limit(limit).all()
            
            if not unvetted_emails:
                logger.info("No unvetted applications found in ParsedEmail records")
                return []
            
            logger.info(f"Found {len(unvetted_emails)} unvetted applications from email parsing")
            
            candidates_to_vet = []
            already_vetted_ids = []
            
            batch_email_ids = [pe.id for pe in unvetted_emails]
            
            vetted_email_ids = set()
            if batch_email_ids:
                existing_logs = CandidateVettingLog.query.filter(
                    CandidateVettingLog.parsed_email_id.in_(batch_email_ids),
                    CandidateVettingLog.status.in_(['completed', 'failed', 'processing'])
                ).all()
                vetted_email_ids = {log.parsed_email_id for log in existing_logs}
                if vetted_email_ids:
                    logger.info(f"Found {len(vetted_email_ids)} ParsedEmails already linked to vetting logs")
            
            candidates_needing_details = []
            for parsed_email in unvetted_emails:
                candidate_id = parsed_email.bullhorn_candidate_id
                
                if parsed_email.id in vetted_email_ids:
                    already_vetted_ids.append(parsed_email.id)
                    logger.info(f"Candidate {candidate_id} already vetted for ParsedEmail {parsed_email.id}, skipping (duplicate loop prevention)")
                    continue
                
                candidates_needing_details.append(parsed_email)
            
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
            
            for parsed_email in candidates_needing_details:
                candidate_id = parsed_email.bullhorn_candidate_id
                candidate_data = self._fetch_candidate_details(bullhorn, candidate_id)
                
                if candidate_data:
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
