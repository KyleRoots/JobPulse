import logging
logger = logging.getLogger(__name__)
import json
import os
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

DEFAULT_PLATFORM_AGE_CEILINGS = {
    'databricks': 8.0,
    'delta lake': 7.0,
    'azure synapse': 6.0,
    'azure synapse analytics': 6.0,
    'microsoft fabric': 3.0,
    'snowflake': 10.0,
    'dbt': 8.0,
    'data build tool': 8.0,
    'apache flink': 10.0,
    'kubernetes': 10.0,
    'apache kafka': 14.0,
    'terraform': 10.0,
    'docker': 12.0,
}

DEFAULT_AUDITOR_MODEL = 'gpt-5.4'
DEFAULT_QUALIFIED_SAMPLE_RATE = 0
DEFAULT_REVET_CAP_PER_24H = 2
DEFAULT_REVET_SCORE_TOLERANCE = 5.0
DEFAULT_AUDIT_COOLDOWN_HOURS = 6


def get_platform_age_ceilings() -> Dict[str, float]:
    """Load PLATFORM_AGE_CEILINGS from VettingConfig with safe fallback.

    Looks up the `platform_age_ceilings` key (JSON-encoded dict of
    platform_name -> max_years_float). Returns the in-file defaults if
    the row is missing, the value is empty, or the JSON is malformed.
    """
    try:
        from models import VettingConfig
        raw = VettingConfig.get_value('platform_age_ceilings', None)
        if not raw or not isinstance(raw, str) or not raw.strip():
            return DEFAULT_PLATFORM_AGE_CEILINGS
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or not parsed:
            return DEFAULT_PLATFORM_AGE_CEILINGS
        cleaned: Dict[str, float] = {}
        for k, v in parsed.items():
            try:
                f = float(v)
            except (ValueError, TypeError):
                continue
            if f != f or f <= 0 or f > 100:
                logger.warning(
                    f"⚠️ platform_age_ceilings: dropping out-of-range value "
                    f"{k!r}={v!r} (must be > 0 and <= 100 years)"
                )
                continue
            cleaned[str(k).lower()] = f
        return cleaned if cleaned else DEFAULT_PLATFORM_AGE_CEILINGS
    except Exception as e:
        logger.warning(
            f"⚠️ platform_age_ceilings load failed ({e!r}) — using in-file defaults"
        )
        return DEFAULT_PLATFORM_AGE_CEILINGS


def get_auditor_model() -> str:
    """Load the auditor model name from VettingConfig with safe fallback."""
    try:
        from models import VettingConfig
        raw = VettingConfig.get_value('quality_auditor_model', None)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    except Exception as e:
        logger.warning(
            f"⚠️ quality_auditor_model load failed ({e!r}) — using default"
        )
    return DEFAULT_AUDITOR_MODEL


class RevetResetDeferred(RuntimeError):
    """A reset would delete the vetting log of a run that is still in flight."""


def _parsed_email_recency_key(parsed_email):
    # Newest received first; NULL received_at sorts oldest; id breaks ties.
    received = parsed_email.received_at
    return (received is not None, received or datetime.min, parsed_email.id or 0)


def select_revet_parsed_email(candidate_id: int, audit_job_id: Optional[int] = None):
    """Pick the one ParsedEmail a re-vet should re-queue.

    Only emails the ParsedEmail detector can pick up are considered: status
    'completed' and, when a vetting cutoff is active, ``received_at`` at or
    after it.

    Returns ``(to_reset, already_pending)``:
      - ``already_pending``: newest eligible email with ``vetted_at`` still
        NULL. It will trigger the analysis by itself, so nothing is reset.
      - ``to_reset``: otherwise the newest eligible email for the audited job,
        falling back to the newest eligible email of any job.
    Both are None when the candidate has no eligible email (non-email intake
    or everything predates the cutoff); the pending-revet detector re-enqueues
    those directly.
    """
    from models import ParsedEmail
    from screening.candidate_data import _resolve_vetting_cutoff

    query = ParsedEmail.query.populate_existing().filter(
        ParsedEmail.bullhorn_candidate_id == candidate_id,
        ParsedEmail.status == 'completed',
    )
    cutoff = _resolve_vetting_cutoff()
    if cutoff is not None:
        query = query.filter(
            ParsedEmail.received_at.isnot(None),
            ParsedEmail.received_at >= cutoff,
        )
    eligible = query.all()

    pending = [pe for pe in eligible if pe.vetted_at is None]
    if pending:
        return None, max(pending, key=_parsed_email_recency_key)
    if not eligible:
        return None, None
    if audit_job_id is not None:
        same_job = [
            pe for pe in eligible
            if pe.bullhorn_job_id is not None
            and int(pe.bullhorn_job_id) == int(audit_job_id)
        ]
        if same_job:
            return max(same_job, key=_parsed_email_recency_key), None
    return max(eligible, key=_parsed_email_recency_key), None


def clear_candidate_vetting_state(
    candidate_id: int,
    audit_job_id: Optional[int] = None,
    reset_parsed_email: bool = True,
) -> Dict[str, Optional[int]]:
    """Delete the candidate's prior vetting state so the next vetting cycle
    re-scores them from scratch.

    At most one ParsedEmail is re-queued (see ``select_revet_parsed_email``).
    Re-queuing every completed email made the ParsedEmail detector run one
    full multi-job analysis per email on successive cycles. Pass
    ``reset_parsed_email=False`` when the caller enqueues the candidate
    itself in the same cycle.

    Runs under the candidate run lock (see ``run_claim``) and commits.
    Raises ``RevetResetDeferred`` while any vetting run for the candidate is
    still ``processing``, whatever its age, and ``CandidateLockUnavailable``
    when the lock cannot be taken. Nothing is deleted in either case.

    Shared by ``RevetMixin._trigger_revet`` (front-line auditor revet path)
    and ``CandidateDetectionMixin.detect_pending_revet_candidates`` (the
    audit-log-as-queue safety net that re-enqueues candidates whose revet
    was triggered but never landed). Both call sites need the same cascade
    semantics: drop matches → drop embedding/escalation logs → drop
    CandidateVettingLog → reset ParsedEmail.vetted_at.

    The previous in-line implementation in ``_trigger_revet`` filtered the
    delete by ``CandidateVettingLog.parsed_email_id IN (...)`` which only
    caught parsed_email-path candidates. PandoLogic-note / Matador /
    Bullhorn-search-legacy intake stores ``parsed_email_id=NULL`` on the
    vetting log, so those rows survived the delete and the
    ``_self_screen_cooldown_active`` gate then blocked re-vetting for the
    full cooldown window — leaving the audit row stuck as
    ``revet_triggered`` / ``revet_new_score=NULL`` indefinitely.

    Filtering by ``bullhorn_candidate_id`` instead of ``parsed_email_id``
    closes that gap.

    Returns a dict with the per-table delete counts for logging.
    """
    from app import db
    from .run_claim import acquire_candidate_lock

    acquire_candidate_lock(candidate_id)
    try:
        stats = reset_candidate_vetting_state_locked(
            candidate_id, audit_job_id, reset_parsed_email,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return stats


def reset_candidate_vetting_state_locked(
    candidate_id: int,
    audit_job_id: Optional[int] = None,
    reset_parsed_email: bool = True,
) -> Dict[str, Optional[int]]:
    """``clear_candidate_vetting_state`` body. The caller holds the candidate
    run lock in the current transaction and commits or rolls back."""
    from app import db
    from sqlalchemy import or_
    from models import (
        CandidateVettingLog, CandidateJobMatch,
        EmbeddingFilterLog, EscalationLog, ScoutVettingSession,
    )

    vetting_logs = (
        db.session.query(CandidateVettingLog.id, CandidateVettingLog.status)
        .filter(CandidateVettingLog.bullhorn_candidate_id == candidate_id)
        .all()
    )
    inflight_ids = [row_id for row_id, status in vetting_logs if status == 'processing']
    if inflight_ids:
        raise RevetResetDeferred(
            f"clear_candidate_vetting_state deferred: candidate "
            f"{candidate_id} has vetting run(s) in flight "
            f"(vetting_log ids {inflight_ids})"
        )
    log_ids = [row_id for row_id, _status in vetting_logs]

    # FK-safety pre-check (May 2026): ScoutVettingSession.vetting_log_id
    # is a NOT NULL FK to candidate_vetting_log.id with NO ON DELETE
    # CASCADE in migration c7e2a4f3b9d1. ANY session row pointing at a
    # vlog we're about to delete (active OR terminal: qualified,
    # not_qualified, declined, unresponsive) will raise IntegrityError
    # at commit. Query for the actual FK refs that would block the
    # delete — not just "active" sessions — and abort if any exist.
    # Caller catches RuntimeError and reclassifies the audit row.
    if log_ids:
        blocking_session_count = (
            ScoutVettingSession.query
            .filter(ScoutVettingSession.vetting_log_id.in_(log_ids))
            .count()
        )
        if blocking_session_count > 0:
            # Differentiate active vs terminal in the error message so
            # operators can tell whether a manual session-archive +
            # retry is reasonable or whether the conversation is live.
            ACTIVE_SESSION_STATES = (
                'pending', 'queued', 'outreach_sent', 'in_progress',
            )
            active_count = (
                ScoutVettingSession.query
                .filter(
                    ScoutVettingSession.vetting_log_id.in_(log_ids),
                    ScoutVettingSession.status.in_(ACTIVE_SESSION_STATES),
                )
                .count()
            )
            terminal_count = blocking_session_count - active_count
            raise RuntimeError(
                f"clear_candidate_vetting_state blocked: candidate "
                f"{candidate_id} has {blocking_session_count} Scout "
                f"vetting session(s) referencing its vetting logs "
                f"({active_count} active, {terminal_count} terminal) — "
                f"FK to candidate_vetting_log.id is NOT NULL without "
                f"CASCADE; deleting would raise IntegrityError"
            )

    to_reset = pending = None
    if reset_parsed_email:
        to_reset, pending = select_revet_parsed_email(candidate_id, audit_job_id)
        if to_reset is not None:
            to_reset.vetted_at = None

    if log_ids:
        EmbeddingFilterLog.query.filter(
            EmbeddingFilterLog.vetting_log_id.in_(log_ids)
        ).delete(synchronize_session=False)
        EscalationLog.query.filter(
            EscalationLog.vetting_log_id.in_(log_ids)
        ).delete(synchronize_session=False)
        CandidateJobMatch.query.filter(
            CandidateJobMatch.vetting_log_id.in_(log_ids)
        ).delete(synchronize_session=False)
        deleted = CandidateVettingLog.query.filter(
            CandidateVettingLog.id.in_(log_ids),
            or_(
                CandidateVettingLog.status.is_(None),
                CandidateVettingLog.status != 'processing',
            ),
        ).delete(synchronize_session=False)
        if deleted != len(log_ids):
            raise RevetResetDeferred(
                f"clear_candidate_vetting_state deferred: candidate "
                f"{candidate_id} vetting logs changed during the reset "
                f"({deleted} of {len(log_ids)} deletable)"
            )

    return {
        'vetting_logs_deleted': len(log_ids),
        'parsed_emails_reset': 1 if to_reset is not None else 0,
        'parsed_email_id': to_reset.id if to_reset is not None else None,
        'pending_parsed_email_id': pending.id if pending is not None else None,
    }


def backfill_revet_new_score(
    candidate_id: int,
    vetting_log=None,
) -> int:
    """Back-fill ``VettingAuditLog.revet_new_score`` after a re-vet completes.

    ``_trigger_revet`` is asynchronous: it deletes the candidate's vetting
    logs and lets the next vetting cycle re-score them, so it cannot
    return the new score synchronously and the audit row is written with
    ``revet_new_score=NULL``. This helper is invoked from the vetting
    service once the new ``CandidateJobMatch`` rows have been committed;
    it locates the audit rows bound to that run (``revet_vetting_log_id``,
    set when the run was claimed) that are still waiting on a score and
    copies the newly-scored ``match_score`` for the same job. The status and
    binding reads and the writes run in one transaction under the candidate
    run lock, and each write re-checks the binding and the null score.

    Parameters
    ----------
    candidate_id:
        The Bullhorn candidate id whose audit rows should be back-filled.
    vetting_log:
        Optional ``CandidateVettingLog`` instance whose
        ``CandidateJobMatch`` rows should be used as the source of new
        scores. When omitted, the helper resolves the most recent
        completed vetting log for the candidate and reads its matches.

    Returns
    -------
    int
        Number of audit log rows that were updated.
    """
    from app import db
    from models import VettingAuditLog, CandidateJobMatch, CandidateVettingLog
    from .run_claim import acquire_candidate_lock, write_bound_score

    try:
        if vetting_log is not None and getattr(vetting_log, 'id', None):
            run_id = vetting_log.id
        else:
            newest = (
                db.session.query(CandidateVettingLog.id)
                .filter_by(
                    bullhorn_candidate_id=candidate_id,
                    status='completed',
                )
                .order_by(CandidateVettingLog.id.desc())
                .first()
            )
            if newest is None:
                db.session.rollback()
                return 0
            run_id = newest[0]

        # Reset, claim (rebind) and detector repairs take the same lock, so
        # the run status and bindings read below cannot change before the
        # conditional writes commit.
        acquire_candidate_lock(candidate_id)
        run_status = (
            db.session.query(CandidateVettingLog.status)
            .filter(CandidateVettingLog.id == run_id)
            .scalar()
        )
        if run_status != 'completed':
            db.session.rollback()
            return 0

        # Only audits bound to this run at claim time (see run_claim): an
        # audit created after the run started is a later request, and an
        # older run's score must not satisfy it. I7: 'revet_skipped_stable'
        # rows are back-filled too, so score-drift reporting is not skewed.
        pending = (
            db.session.query(VettingAuditLog.id, VettingAuditLog.job_id)
            .filter(
                VettingAuditLog.bullhorn_candidate_id == candidate_id,
                VettingAuditLog.action_taken.in_(['revet_triggered', 'revet_skipped_stable']),
                VettingAuditLog.revet_new_score.is_(None),
                VettingAuditLog.revet_vetting_log_id == run_id,
            )
            .all()
        )

        if not pending:
            db.session.rollback()
            return 0

        matches = CandidateJobMatch.query.filter_by(vetting_log_id=run_id).all()

        score_by_job: Dict[int, float] = {}
        for m in matches:
            if m.bullhorn_job_id is None or m.match_score is None:
                continue
            score_by_job[int(m.bullhorn_job_id)] = float(m.match_score)

        if not score_by_job:
            db.session.rollback()
            return 0

        backfilled = 0
        for audit_id, job_id in pending:
            if job_id is None:
                continue
            new_score = score_by_job.get(int(job_id))
            if new_score is None:
                continue
            if write_bound_score(audit_id, run_id, new_score):
                backfilled += 1

        if backfilled == 0:
            db.session.rollback()
            return 0

        db.session.commit()
        logger.info(
            f"🔁 Back-filled revet_new_score on {backfilled} audit row(s) "
            f"for candidate {candidate_id}"
        )
        return backfilled

    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning(
            f"⚠️ Failed to back-fill revet_new_score for candidate "
            f"{candidate_id}: {e!r}"
        )
        return 0


def get_qualified_sample_rate() -> int:
    """Load the Qualified false-positive sample rate (percent, 0-100).

    Returns the configured percentage of Qualified results to audit per
    cycle. 0 disables the Qualified audit branch entirely (Phase 2).
    Defaults to DEFAULT_QUALIFIED_SAMPLE_RATE (0 = disabled) if the
    config row is missing or malformed.

    Note (May 2026 — S6 cost optimization): seed default and code-level
    fallback both lowered to 0 after 30-day prod data showed Phase 2
    produced ~zero operational value (724 audits → 1 revet, 0
    qualification flips). Phase 1 auto-trigger heuristic checks remain
    at 100% and catch all flips. Aligning the fallback with the seed
    default ensures a missing/malformed row cannot silently re-enable
    Phase 2.
    """
    try:
        from models import VettingConfig
        raw = VettingConfig.get_value('qualified_audit_sample_rate', None)
        if raw is not None:
            value = int(str(raw).strip())
            if 0 <= value <= 100:
                return value
    except (ValueError, TypeError, Exception) as e:
        logger.warning(
            f"⚠️ qualified_audit_sample_rate load failed ({e!r}) — using default"
        )
    return DEFAULT_QUALIFIED_SAMPLE_RATE


def get_revet_cap_per_24h() -> int:
    """Maximum number of re-vets the auditor may trigger for the same
    (candidate, job) pair within any rolling 24-hour window.

    Reads ``auditor_revet_cap_per_24h`` from VettingConfig. Falls back to
    ``DEFAULT_REVET_CAP_PER_24H`` when the row is missing or malformed.
    Values < 1 are clamped to 1 (a value of 0 would disable the auditor's
    re-vet action entirely, which is what existing config flags are for).
    """
    try:
        from models import VettingConfig
        raw = VettingConfig.get_value('auditor_revet_cap_per_24h', None)
        if raw is not None:
            value = int(str(raw).strip())
            if value >= 1:
                return value
            return 1
    except (ValueError, TypeError, Exception) as e:
        logger.warning(
            f"⚠️ auditor_revet_cap_per_24h load failed ({e!r}) — using default"
        )
    return DEFAULT_REVET_CAP_PER_24H


def get_audit_cooldown_hours() -> int:
    """Minimum hours that must elapse before the auditor re-examines the same
    (candidate, job) pair after a non-actionable outcome (no_action or any
    revet_skipped_* result).

    Reads ``auditor_cooldown_hours`` from VettingConfig. Falls back to
    ``DEFAULT_AUDIT_COOLDOWN_HOURS`` (6) when missing or malformed.
    A value of 0 disables the cooldown entirely.
    """
    try:
        from models import VettingConfig
        raw = VettingConfig.get_value('auditor_cooldown_hours', None)
        if raw is not None:
            value = int(str(raw).strip())
            if value >= 0:
                return value
    except (ValueError, TypeError, Exception) as e:
        logger.warning(
            f"⚠️ auditor_cooldown_hours load failed ({e!r}) — using default"
        )
    return DEFAULT_AUDIT_COOLDOWN_HOURS


def get_revet_score_tolerance() -> float:
    """Score-stability tolerance (in match-score points) used by the auditor
    to decide that a re-vet's new result is "close enough" to the original
    score that further re-vets are unlikely to change the verdict.

    Reads ``auditor_revet_score_tolerance`` from VettingConfig. Falls back
    to ``DEFAULT_REVET_SCORE_TOLERANCE`` (5.0) when missing or malformed.
    Negative values are clamped to 0.0.
    """
    try:
        from models import VettingConfig
        raw = VettingConfig.get_value('auditor_revet_score_tolerance', None)
        if raw is not None:
            value = float(str(raw).strip())
            if value >= 0:
                return value
            return 0.0
    except (ValueError, TypeError, Exception) as e:
        logger.warning(
            f"⚠️ auditor_revet_score_tolerance load failed ({e!r}) — using default"
        )
    return DEFAULT_REVET_SCORE_TOLERANCE


PLATFORM_AGE_CEILINGS = DEFAULT_PLATFORM_AGE_CEILINGS

DOMAIN_KEYWORDS = [
    'data engineer', 'azure data', 'databricks', 'spark', 'etl', 'pipeline',
    'data lake', 'synapse', 'snowflake', 'cloud engineer', 'big data',
    'data warehouse', 'kafka', 'airflow', 'dbt', 'analytics engineer',
    'machine learning', 'ml engineer', 'ai engineer', 'software engineer',
    'devops', 'platform engineer', 'backend engineer', 'data architect',
    'full stack', 'frontend engineer', 'cloud architect', 'sre',
    'infrastructure engineer', 'data scientist', 'business intelligence',
]
