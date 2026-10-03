"""Qualified-only snapshot note: which job, company, owner, when, and how they applied.

Kept separate from the AI Resume Summary. Recruiters read this on Overview / Notes
instead of opening More → Pipeline → Response.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

APPLICATION_NOTE_ACTION = 'Application Received'
SNAPSHOT_JOB_FIELDS = (
    'id,title,clientCorporation(id,name),'
    'owner(id,firstName,lastName),'
    'responseUser(id,firstName,lastName),'
    'assignedUsers(id,firstName,lastName)'
)
_DISPLAY_TZ = ZoneInfo('America/New_York')


def applicant_label_for_source(source: Optional[str]) -> str:
    """Lead-in Kevin and Emily asked for, keyed off the Bullhorn source string."""
    text = (source or '').strip().lower()
    if 'indeed' in text:
        return 'Indeed Applicant'
    if 'zip' in text:
        return 'ZipRecruiter Applicant'
    if 'linkedin' in text:
        return 'LinkedIn Applicant'
    return 'Web Applicant'


def source_display_line(source: Optional[str]) -> str:
    line = (source or '').strip()
    return line or 'Corporate Website'


def format_applied_at(applied_at: Optional[Any]) -> str:
    """Thursday, October 1, 2026 at 7:18 PM in US Eastern."""
    dt = _as_eastern(applied_at)
    if dt is None:
        dt = datetime.now(_DISPLAY_TZ)
    hour = dt.strftime('%I').lstrip('0') or '0'
    minute = dt.strftime('%M')
    ampm = dt.strftime('%p')
    return f"{dt.strftime('%A, %B')} {dt.day}, {dt.strftime('%Y')} at {hour}:{minute} {ampm}"


def person_name(value: Any) -> str:
    if not isinstance(value, dict):
        return ''
    first = str(value.get('firstName') or '').strip()
    last = str(value.get('lastName') or '').strip()
    return ' '.join(part for part in (first, last) if part)


def company_from_job(job: Optional[Dict[str, Any]]) -> str:
    if not isinstance(job, dict):
        return ''
    corp = job.get('clientCorporation')
    if isinstance(corp, dict):
        return str(corp.get('name') or '').strip()
    return ''


def recruiter_from_job(job: Optional[Dict[str, Any]]) -> str:
    """Prefer assigned recruiter, then response user, then owner."""
    if not isinstance(job, dict):
        return ''
    assigned = job.get('assignedUsers')
    rows: List[Any] = []
    if isinstance(assigned, dict):
        data = assigned.get('data')
        if isinstance(data, list):
            rows = data
        elif assigned.get('firstName') or assigned.get('lastName'):
            rows = [assigned]
    elif isinstance(assigned, list):
        rows = assigned
    for row in rows:
        name = person_name(row)
        if name:
            return name
    for key in ('responseUser', 'owner'):
        name = person_name(job.get(key))
        if name:
            return name
    return ''


def build_application_note_text(
    *,
    source: Optional[str],
    title: Optional[str],
    company: Optional[str] = None,
    recruiter: Optional[str] = None,
    applied_at: Optional[Any] = None,
) -> str:
    label = html.escape(applicant_label_for_source(source))
    job_title = html.escape((title or 'this job').strip() or 'this job')
    pieces = [f'<b>{label}</b> for <b>{job_title}</b>']
    company_clean = (company or '').strip()
    if company_clean:
        pieces.append(f' at <b>{html.escape(company_clean)}</b>')
    recruiter_clean = (recruiter or '').strip()
    if recruiter_clean:
        pieces.append(f' under <b>{html.escape(recruiter_clean)}</b>')
    pieces.append(f' on {html.escape(format_applied_at(applied_at))}')
    first_line = ''.join(pieces)
    second = html.escape(source_display_line(source))
    return f'{first_line}\n\n{second}'


def snapshot_already_present(
    notes: Optional[List[Dict[str, Any]]],
    *,
    title: Optional[str],
    company: Optional[str] = None,
) -> bool:
    job_title = (title or '').strip()
    if not job_title:
        return False
    needle = f'for <b>{html.escape(job_title)}</b>'
    company_needle = html.escape((company or '').strip()) if (company or '').strip() else ''
    for note in notes or []:
        if (note.get('action') or '') != APPLICATION_NOTE_ACTION:
            continue
        comments = note.get('comments') or ''
        if needle not in comments:
            continue
        if company_needle and company_needle not in comments:
            continue
        return True
    return False


def write_application_received_note(
    bullhorn,
    candidate_id: int,
    job_id: int,
    *,
    source: Optional[str],
    applied_at: Optional[Any] = None,
) -> Optional[int]:
    """Create the snapshot when Qualified knows the applied job. None if skipped or failed."""
    from feeds.feed_config import is_qualified_tenant

    if not is_qualified_tenant():
        return None
    if not candidate_id or not job_id:
        return None
    try:
        job = bullhorn.get_entity('JobOrder', int(job_id), fields=SNAPSHOT_JOB_FIELDS) or {}
        title = str(job.get('title') or '').strip()
        if not title:
            logger.warning(
                'application snapshot: job %s has no title; skipping candidate %s',
                job_id, candidate_id,
            )
            return None
        company = company_from_job(job)
        recruiter = recruiter_from_job(job)
        existing = bullhorn.get_candidate_notes(
            int(candidate_id),
            action_filter=[APPLICATION_NOTE_ACTION],
            count=50,
        )
        if snapshot_already_present(existing, title=title, company=company):
            logger.info(
                'application snapshot: already present for candidate %s job %s',
                candidate_id, job_id,
            )
            return None
        text = build_application_note_text(
            source=source,
            title=title,
            company=company,
            recruiter=recruiter,
            applied_at=applied_at,
        )
        note_id = bullhorn.create_candidate_note(
            int(candidate_id), text, APPLICATION_NOTE_ACTION
        )
        if note_id:
            logger.info(
                'application snapshot: note %s on candidate %s for job %s',
                note_id, candidate_id, job_id,
            )
        return note_id
    except Exception as exc:
        logger.warning(
            'application snapshot: failed for candidate %s job %s: %s',
            candidate_id, job_id, exc,
        )
        return None


def _as_eastern(applied_at: Optional[Any]) -> Optional[datetime]:
    if applied_at is None:
        return None
    if isinstance(applied_at, datetime):
        dt = applied_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_DISPLAY_TZ)
    if isinstance(applied_at, (int, float)):
        ms = float(applied_at)
        if ms > 10_000_000_000:
            ms = ms / 1000.0
        return datetime.fromtimestamp(ms, tz=timezone.utc).astimezone(_DISPLAY_TZ)
    return None
