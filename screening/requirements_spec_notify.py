"""Internal email when Scout creates a new AI requirement spec.

Support/sanity only — notifies Kyle (or REQUIREMENTS_SPEC_NOTIFY_EMAIL) so
interpreted requirements can be checked against the Bullhorn JD. Fired only on
first-time create of a JobVettingRequirements row with AI text, not on update/regen.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

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


def notify_new_requirements_spec(
    *,
    job_id: int,
    job_title: Optional[str],
    requirements: str,
    job_location: Optional[str] = None,
    job_work_type: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> bool:
    """Send create-only notify email. Fail-soft: never raises; returns True if sent."""
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
