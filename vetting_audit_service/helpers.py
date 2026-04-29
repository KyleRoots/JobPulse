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
DEFAULT_QUALIFIED_SAMPLE_RATE = 10
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
        pending = VettingAuditLog.query.filter(
            VettingAuditLog.bullhorn_candidate_id == candidate_id,
            VettingAuditLog.action_taken == 'revet_triggered',
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
    cycle. 0 disables the Qualified audit branch entirely. Defaults to
    DEFAULT_QUALIFIED_SAMPLE_RATE (10%) if missing or malformed.
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
