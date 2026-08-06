"""Internal email when Scout creates a new AI requirement spec.

Support/sanity only — notifies Kyle (or REQUIREMENTS_SPEC_NOTIFY_EMAIL) so
interpreted requirements can be checked against the Bullhorn JD.

Fired only on first-time create of a JobVettingRequirements row with AI text,
and only once the job is present on the Scout Screening jobs list (active
BullhornMonitor.last_job_snapshot — same source as “My Matches & Jobs” /
Job-Level Settings). Specs saved earlier are deferred until the job appears
in that snapshot; create-only (not regen); fail-soft; no duplicate emails.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Iterable, Optional, Set

logger = logging.getLogger(__name__)

DEFAULT_NOTIFY_EMAIL = 'kroots@myticas.com'
DEFAULT_BH_UI_BASE = 'https://cls45.bullhornstaffing.com'
# Cap body so long JD extracts do not flood the inbox; full text stays in JobPulse.
MAX_REQUIREMENTS_EXCERPT_CHARS = 2500


def env_flag(name: str, default: bool = True) -> bool:
    raw = (os.environ.get(name) or '').strip().lower()
    if not raw:
        return default
    return raw in ('1', 'true', 'yes', 'on')


def notify_config() -> dict:
    return {
        'enabled': env_flag('REQUIREMENTS_SPEC_NOTIFY_ENABLED', True),
        'notify_email': (
            os.environ.get('REQUIREMENTS_SPEC_NOTIFY_EMAIL') or DEFAULT_NOTIFY_EMAIL
        ).strip(),
        'bh_base_url': (
            os.environ.get('BH_UI_BASE_URL') or DEFAULT_BH_UI_BASE
        ).rstrip('/'),
    }


def bullhorn_job_url(job_id: int, bh_base_url: Optional[str] = None) -> str:
    base = (bh_base_url or notify_config()['bh_base_url']).rstrip('/')
    return (
        f"{base}/BullhornSTAFFING/OpenWindow.cfm"
        f"?Entity=JobOrder&id={int(job_id)}"
    )


def _excerpt_requirements(requirements: str, max_chars: int = MAX_REQUIREMENTS_EXCERPT_CHARS) -> str:
    text = (requirements or '').strip()
    if not text:
        return '(empty)'
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + '\n\n… (truncated; full text in JobPulse Screening settings)'


def build_notify_message(
    *,
    job_id: int,
    job_title: Optional[str],
    requirements: str,
    job_location: Optional[str] = None,
    job_work_type: Optional[str] = None,
    created_at: Optional[datetime] = None,
    bh_base_url: Optional[str] = None,
) -> str:
    title = (job_title or '').strip() or f'Job #{job_id}'
    when = created_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    ts = when.strftime('%Y-%m-%d %H:%M:%S UTC')
    job_url = bullhorn_job_url(job_id, bh_base_url)

    lines = [
        'Scout created a new AI requirement spec for screening.',
        '',
        'This is an internal sanity-check email (not a recruiter blast).',
        'Compare the interpreted requirements below to the Bullhorn job description.',
        '',
        f'Job: {title}',
        f'Job ID: {job_id}',
        f'Bullhorn: {job_url}',
    ]
    if job_location:
        lines.append(f'Location: {job_location}')
    if job_work_type:
        lines.append(f'Work type: {job_work_type}')
    lines.extend([
        f'Created: {ts}',
        '',
        '--- Interpreted requirements ---',
        _excerpt_requirements(requirements),
        '',
        '---',
        'JobPulse → Scout Screening → Configure requirements for this job to edit or re-extract.',
    ])
    return '\n'.join(lines)


def screening_snapshot_job_ids() -> Set[int]:
    """Job IDs present on any active monitor's last_job_snapshot.

    Mirrors Scout Screening's job list source (`routes.scout_screening._get_user_job_ids`
    / `_get_user_jobs_with_meta`) for admins: a job is "in the module" once it
    appears in a tearsheet monitor snapshot. Fail-soft → empty set.
    """
    try:
        from models import BullhornMonitor

        monitors = BullhornMonitor.query.filter_by(is_active=True).all()
        job_ids: Set[int] = set()
        for monitor in monitors:
            if not monitor.last_job_snapshot:
                continue
            try:
                jobs = json.loads(monitor.last_job_snapshot)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(jobs, list):
                continue
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                raw_id = job.get('id')
                if raw_id is None:
                    continue
                try:
                    job_ids.add(int(raw_id))
                except (TypeError, ValueError):
                    continue
        return job_ids
    except Exception as exc:
        logger.warning(
            'screening_snapshot_job_ids failed (treating as empty): %s', exc
        )
        return set()


def is_job_on_scout_screening_list(job_id: int) -> bool:
    """True when job_id is in an active tearsheet monitor snapshot (UI-visible)."""
    try:
        return int(job_id) in screening_snapshot_job_ids()
    except (TypeError, ValueError):
        return False


def _mark_notified(job_req, when: Optional[datetime] = None) -> None:
    """Stamp spec_create_notified_at and commit; never raises to callers."""
    try:
        from app import db

        job_req.spec_create_notified_at = when or datetime.utcnow()
        db.session.commit()
    except Exception as exc:
        logger.error(
            'Failed to stamp spec_create_notified_at for job %s: %s',
            getattr(job_req, 'bullhorn_job_id', '?'),
            exc,
            exc_info=True,
        )
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


def notify_new_requirements_spec(
    *,
    job_id: int,
    job_title: Optional[str],
    requirements: str,
    job_location: Optional[str] = None,
    job_work_type: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> bool:
    """Send create-only notify email. Fail-soft: never raises; returns True if sent.

    Callers that care about Scout Screening visibility should use
    ``maybe_notify_new_requirements_spec`` instead — this low-level sender does
    not re-check the snapshot gate.
    """
    try:
        cfg = notify_config()
        if not cfg['enabled']:
            logger.debug(
                'Requirements-spec notify disabled (REQUIREMENTS_SPEC_NOTIFY_ENABLED)'
            )
            return False
        to_email = cfg['notify_email']
        if not to_email:
            logger.warning('Requirements-spec notify skipped — empty notify email')
            return False

        title = (job_title or '').strip() or f'Job #{job_id}'
        subject = f'[JobPulse] New requirement spec: {title} (#{job_id})'
        message = build_notify_message(
            job_id=job_id,
            job_title=job_title,
            requirements=requirements,
            job_location=job_location,
            job_work_type=job_work_type,
            created_at=created_at,
            bh_base_url=cfg['bh_base_url'],
        )

        from utils.bullhorn_helpers import get_email_service

        email_svc = get_email_service()
        if not email_svc:
            logger.warning('Requirements-spec notify skipped — email service unavailable')
            return False

        ok = email_svc.send_notification_email(
            to_email=to_email,
            subject=subject,
            message=message,
            notification_type='requirements_spec_create',
        )
        if ok:
            logger.info(
                'Requirements-spec create notify sent for job %s to %s',
                job_id,
                to_email,
            )
        else:
            logger.warning(
                'Requirements-spec create notify returned False for job %s',
                job_id,
            )
        return bool(ok)
    except Exception as exc:
        logger.error(
            'Requirements-spec create notify failed for job %s: %s',
            job_id,
            exc,
            exc_info=True,
        )
        return False


def maybe_notify_new_requirements_spec(job_req) -> bool:
    """Notify if eligible: create-only pending + on Scout Screening snapshot list.

    Idempotent via ``spec_create_notified_at``. Returns True only when an email
    was sent. Fail-soft.
    """
    try:
        if job_req is None:
            return False
        if getattr(job_req, 'spec_create_notified_at', None):
            return False
        requirements = (job_req.ai_interpreted_requirements or '').strip()
        if not requirements:
            return False

        # Re-check DB stamp so a stale in-memory instance cannot double-send
        # after a concurrent flush already notified.
        from models import JobVettingRequirements

        row_id = getattr(job_req, 'id', None)
        if row_id is not None:
            fresh_stamp = (
                JobVettingRequirements.query.filter_by(id=row_id)
                .with_entities(JobVettingRequirements.spec_create_notified_at)
                .scalar()
            )
            if fresh_stamp:
                job_req.spec_create_notified_at = fresh_stamp
                return False

        job_id = int(job_req.bullhorn_job_id)
        if not is_job_on_scout_screening_list(job_id):
            logger.info(
                'Requirements-spec notify deferred for job %s — not yet on '
                'Scout Screening snapshot list',
                job_id,
            )
            return False

        ok = notify_new_requirements_spec(
            job_id=job_id,
            job_title=job_req.job_title,
            requirements=requirements,
            job_location=job_req.job_location,
            job_work_type=job_req.job_work_type,
            created_at=getattr(job_req, 'created_at', None),
        )
        if ok:
            _mark_notified(job_req)
        return ok
    except Exception as exc:
        logger.error(
            'maybe_notify_new_requirements_spec failed for job %s: %s',
            getattr(job_req, 'bullhorn_job_id', '?'),
            exc,
            exc_info=True,
        )
        return False


def flush_pending_requirements_spec_notifies(
    visible_job_ids: Optional[Iterable[int]] = None,
) -> int:
    """Send deferred create-notifies for specs now visible on the screening list.

    Called after ``last_job_snapshot`` is refreshed so specs extracted before the
    job appeared in Scout Screening still get exactly one email. Returns count sent.
    """
    try:
        from models import JobVettingRequirements

        if visible_job_ids is None:
            ids = screening_snapshot_job_ids()
        else:
            ids = set()
            for raw in visible_job_ids:
                try:
                    ids.add(int(raw))
                except (TypeError, ValueError):
                    continue
        if not ids:
            return 0

        pending = (
            JobVettingRequirements.query.filter(
                JobVettingRequirements.spec_create_notified_at.is_(None),
                JobVettingRequirements.ai_interpreted_requirements.isnot(None),
                JobVettingRequirements.bullhorn_job_id.in_(list(ids)),
            ).all()
        )
        sent = 0
        for row in pending:
            if not (row.ai_interpreted_requirements or '').strip():
                continue
            # Re-check stamp in case of concurrent flush.
            if row.spec_create_notified_at:
                continue
            if maybe_notify_new_requirements_spec(row):
                sent += 1
        if sent:
            logger.info(
                'Flushed %s deferred requirements-spec notify email(s)', sent
            )
        return sent
    except Exception as exc:
        logger.error(
            'flush_pending_requirements_spec_notifies failed: %s',
            exc,
            exc_info=True,
        )
        return 0
