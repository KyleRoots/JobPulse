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


def clear_candidate_vetting_state(candidate_id: int) -> Dict[str, int]:
    """Delete the candidate's prior vetting state so the next vetting cycle
    re-scores them from scratch.

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
    Raises on failure — the caller is responsible for rolling back its
    transaction.
    """
    from app import db
    from models import (
        CandidateVettingLog, CandidateJobMatch, ParsedEmail,
        EmbeddingFilterLog, EscalationLog, ScoutVettingSession,
    )

    # FK-safety pre-check (May 2026): ScoutVettingSession.vetting_log_id
    # is a NOT NULL FK to candidate_vetting_log.id with NO ON DELETE
    # CASCADE in migration c7e2a4f3b9d1. ANY session row pointing at a
    # vlog we're about to delete (active OR terminal: qualified,
    # not_qualified, declined, unresponsive) will raise IntegrityError
    # at commit. Query for the actual FK refs that would block the
    # delete — not just "active" sessions — and abort if any exist.
    # Caller catches RuntimeError and reclassifies the audit row.
    vetting_logs_for_check = CandidateVettingLog.query.filter(
        CandidateVettingLog.bullhorn_candidate_id == candidate_id,
    ).all()
    log_ids_for_check = [vl.id for vl in vetting_logs_for_check]
    if log_ids_for_check:
        blocking_session_count = (
            ScoutVettingSession.query
            .filter(ScoutVettingSession.vetting_log_id.in_(log_ids_for_check))
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
                    ScoutVettingSession.vetting_log_id.in_(log_ids_for_check),
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

    parsed_emails = ParsedEmail.query.filter(
        ParsedEmail.bullhorn_candidate_id == candidate_id,
        ParsedEmail.status == 'completed',
    ).all()
    pe_count = len(parsed_emails)
    for pe in parsed_emails:
        pe.vetted_at = None

    # Re-fetch (in case anything changed between the pre-check and now)
    vetting_logs = CandidateVettingLog.query.filter(
        CandidateVettingLog.bullhorn_candidate_id == candidate_id,
    ).all()
    log_ids = [vl.id for vl in vetting_logs]

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
        CandidateVettingLog.query.filter(
            CandidateVettingLog.id.in_(log_ids)
        ).delete(synchronize_session=False)

    db.session.commit()

    return {
        'vetting_logs_deleted': len(log_ids),
        'parsed_emails_reset': pe_count,
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
    it locates every audit row for this candidate that was waiting on a
    re-vet score and copies the newly-scored ``match_score`` for the
    same applied job.

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

    try:
        # I7: Include 'revet_skipped_stable' rows so when an auditor cycle
        # later confirms the original score on a previously-stable case, the
        # stored revet_new_score gets back-filled too (otherwise these rows
        # remain permanently null and skew "score drift" reporting).
        pending = VettingAuditLog.query.filter(
            VettingAuditLog.bullhorn_candidate_id == candidate_id,
            VettingAuditLog.action_taken.in_(['revet_triggered', 'revet_skipped_stable']),
            VettingAuditLog.revet_new_score.is_(None),
        ).all()

        if not pending:
            return 0

        if vetting_log is not None and getattr(vetting_log, 'id', None):
            matches = CandidateJobMatch.query.filter_by(
                vetting_log_id=vetting_log.id
            ).all()
        else:
            latest = (
                CandidateVettingLog.query
                .filter_by(
                    bullhorn_candidate_id=candidate_id,
                    status='completed',
                )
                .order_by(CandidateVettingLog.id.desc())
                .first()
            )
            if not latest:
                return 0
            matches = CandidateJobMatch.query.filter_by(
                vetting_log_id=latest.id
            ).all()

        score_by_job: Dict[int, float] = {}
        for m in matches:
            if m.bullhorn_job_id is None or m.match_score is None:
                continue
            score_by_job[int(m.bullhorn_job_id)] = float(m.match_score)

        if not score_by_job:
            return 0

        backfilled = 0
        for audit_row in pending:
            if audit_row.job_id is None:
                continue
            new_score = score_by_job.get(int(audit_row.job_id))
            if new_score is None:
                continue
            audit_row.revet_new_score = new_score
            backfilled += 1

        if backfilled == 0:
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
