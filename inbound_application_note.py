"""Qualified-only application note: job snapshot first, then the AI résumé summary.

Recruiters read this on Overview / Notes instead of opening More → Pipeline → Response.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

APPLICATION_NOTE_ACTION = 'Application Received'
SUMMARY_ACTION = 'AI Resume Summary'
SUMMARY_HEADING = 'AI-Generated Resume Summary:'
BH_OPEN_WINDOW = (
    'https://www.bullhornstaffing.com/BullhornStaffing/OpenWindow.cfm'
)
SNAPSHOT_JOB_FIELDS = (
    'id,title,clientCorporation(id,name),'
    'owner(id,firstName,lastName),'
    'responseUser(id,firstName,lastName),'
    'assignedUsers(id,firstName,lastName)'
)
_DISPLAY_TZ = ZoneInfo('America/New_York')
_JOB_ORDER_ID_RE = re.compile(
    r'entity=JobOrder(?:&amp;|&)id=(\d+)',
    re.IGNORECASE,
)
_APPLIED_AT_RE = re.compile(
    r' on (?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), '
    r'[A-Za-z]+ \d+, \d{4} at \d+:\d{2} [AP]M',
    re.IGNORECASE,
)


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


def _record_id(value: Any) -> Optional[int]:
    if not isinstance(value, dict):
        return None
    try:
        raw = value.get('id')
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _linked_name(label: str, entity: str, entity_id: Optional[int]) -> str:
    escaped = html.escape(label)
    if not entity_id:
        return f'<b>{escaped}</b>'
    url = html.escape(
        f'{BH_OPEN_WINDOW}?entity={entity}&id={int(entity_id)}',
        quote=True,
    )
    return f'<b><a href="{url}">{escaped}</a></b>'


def company_record_from_job(job: Optional[Dict[str, Any]]) -> tuple:
    if not isinstance(job, dict):
        return '', None
    corp = job.get('clientCorporation')
    if not isinstance(corp, dict):
        return '', None
    return str(corp.get('name') or '').strip(), _record_id(corp)


def company_from_job(job: Optional[Dict[str, Any]]) -> str:
    name, _company_id = company_record_from_job(job)
    return name


def recruiter_record_from_job(job: Optional[Dict[str, Any]]) -> tuple:
    """Prefer assigned recruiter, then response user, then owner."""
    if not isinstance(job, dict):
        return '', None
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
            return name, _record_id(row)
    for key in ('responseUser', 'owner'):
        person = job.get(key)
        name = person_name(person)
        if name:
            return name, _record_id(person)
    return '', None


def recruiter_from_job(job: Optional[Dict[str, Any]]) -> str:
    name, _recruiter_id = recruiter_record_from_job(job)
    return name


def build_application_note_text(
    *,
    source: Optional[str],
    title: Optional[str],
    company: Optional[str] = None,
    recruiter: Optional[str] = None,
    applied_at: Optional[Any] = None,
    job_id: Optional[int] = None,
    company_id: Optional[int] = None,
    recruiter_id: Optional[int] = None,
) -> str:
    label = html.escape(applicant_label_for_source(source))
    job_title = (title or 'this job').strip() or 'this job'
    pieces = [
        f'<b>{label}</b> for ',
        _linked_name(job_title, 'JobOrder', job_id),
    ]
    company_clean = (company or '').strip()
    if company_clean:
        pieces.append(' at ')
        pieces.append(_linked_name(company_clean, 'ClientCorporation', company_id))
    recruiter_clean = (recruiter or '').strip()
    if recruiter_clean:
        pieces.append(' under ')
        # CorporateUser is not an OpenWindow entity in Novo (job/company are).
        pieces.append(f'<b>{html.escape(recruiter_clean)}</b>')
    pieces.append(f' on {html.escape(format_applied_at(applied_at))}')
    first_line = ''.join(pieces)
    second = html.escape(source_display_line(source))
    return f'{first_line}<br>{second}'


def resume_summary_body(resume_data: Optional[Dict[str, Any]]) -> str:
    """Same résumé-summary body as mailbox ingest, without a separate note action."""
    summary = str((resume_data or {}).get('summary') or '').strip()
    if not summary:
        return ''
    parts = [f'{SUMMARY_HEADING}\n\n{summary}']
    skills = (resume_data or {}).get('skills') or []
    if isinstance(skills, list) and skills:
        parts.append('\n\nKey Skills: ' + ', '.join(str(s) for s in skills[:10]))
    years = (resume_data or {}).get('years_experience')
    if years:
        parts.append(f'\n\nExperience: {years} years')
    return ''.join(parts)


def combine_application_note_text(
    snapshot: str,
    summary_text: Optional[str] = None,
) -> str:
    """Application details first, AI summary underneath. Empty summary is omitted."""
    snapshot = (snapshot or '').strip()
    summary_text = (summary_text or '').strip()
    if not summary_text:
        return snapshot
    if SUMMARY_HEADING in snapshot:
        return snapshot
    return f'{snapshot}<br><br>{summary_text}'


def snapshot_already_present(
    notes: Optional[List[Dict[str, Any]]],
    *,
    title: Optional[str],
    company: Optional[str] = None,
    job_id: Optional[int] = None,
    applied_at: Optional[Any] = None,
) -> bool:
    return matching_snapshot_note(
        notes,
        title=title,
        company=company,
        job_id=job_id,
        applied_at=applied_at,
    ) is not None


def application_note_job_ids(comments: Optional[str]) -> List[int]:
    found: List[int] = []
    for raw in _JOB_ORDER_ID_RE.findall(comments or ''):
        try:
            found.append(int(raw))
        except (TypeError, ValueError):
            continue
    return found


def application_note_applied_at_phrase(comments: Optional[str]) -> str:
    match = _APPLIED_AT_RE.search(comments or '')
    return match.group(0).strip().lower() if match else ''


def expected_applied_at_phrase(applied_at: Optional[Any]) -> str:
    """Same 'on Weekday, Month D, YYYY at h:mm AM/PM' clause written into the note."""
    if applied_at is None:
        return ''
    return f'on {format_applied_at(applied_at)}'.strip().lower()


def _same_applied_at(comments: str, applied_at: Optional[Any]) -> bool:
    expected = expected_applied_at_phrase(applied_at)
    if not expected:
        return True
    phrase = application_note_applied_at_phrase(comments)
    if not phrase:
        return False
    return phrase == expected


def _job_order_linked(comments: str, job_id: int) -> bool:
    return int(job_id) in application_note_job_ids(comments)


def duplicate_application_note_groups(
    notes: Optional[List[Dict[str, Any]]],
) -> List[List[Dict[str, Any]]]:
    """Notes for the same job at the same apply time. Those are Scout duplicates."""
    buckets: Dict[tuple, List[Dict[str, Any]]] = {}
    for note in notes or []:
        if (note.get('action') or '') != APPLICATION_NOTE_ACTION:
            continue
        comments = note.get('comments') or ''
        job_ids = application_note_job_ids(comments)
        when = application_note_applied_at_phrase(comments)
        if not job_ids or not when:
            continue
        key = (job_ids[0], when)
        buckets.setdefault(key, []).append(note)
    return [rows for rows in buckets.values() if len(rows) >= 2]


def _comments_include(haystack: str, needle: str) -> bool:
    text = (needle or '').strip()
    if not text:
        return False
    if text in haystack or html.escape(text) in haystack:
        return True
    decoded = html.unescape(haystack)
    return text in decoded or html.escape(text) in decoded


def _pick_snapshot_keeper(notes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not notes:
        return None
    with_summary = [
        note for note in notes
        if SUMMARY_HEADING in (note.get('comments') or '')
    ]
    pool = with_summary or notes
    return sorted(
        pool,
        key=lambda note: (note.get('dateAdded') or 0, note.get('id') or 0),
    )[0]


def matching_snapshot_notes(
    notes: Optional[List[Dict[str, Any]]],
    *,
    title: Optional[str],
    company: Optional[str] = None,
    job_id: Optional[int] = None,
    applied_at: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    job_title = (title or '').strip()
    has_job_id = False
    try:
        job_id_int = int(job_id) if job_id is not None else 0
        has_job_id = job_id_int > 0
    except (TypeError, ValueError):
        job_id_int = 0
    if not job_title and not has_job_id:
        return []
    found: List[Dict[str, Any]] = []
    for note in notes or []:
        if (note.get('action') or '') != APPLICATION_NOTE_ACTION:
            continue
        comments = note.get('comments') or ''
        if has_job_id and _job_order_linked(comments, job_id_int):
            if _same_applied_at(comments, applied_at):
                found.append(note)
            continue
        if not job_title or not _comments_include(comments, job_title):
            continue
        company_clean = (company or '').strip()
        if company_clean and not _comments_include(comments, company_clean):
            continue
        if not _same_applied_at(comments, applied_at):
            continue
        found.append(note)
    return found


def matching_snapshot_note(
    notes: Optional[List[Dict[str, Any]]],
    *,
    title: Optional[str],
    company: Optional[str] = None,
    job_id: Optional[int] = None,
    applied_at: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    return _pick_snapshot_keeper(
        matching_snapshot_notes(
            notes,
            title=title,
            company=company,
            job_id=job_id,
            applied_at=applied_at,
        )
    )


def write_application_received_note(
    bullhorn,
    candidate_id: int,
    job_id: int,
    *,
    source: Optional[str],
    applied_at: Optional[Any] = None,
    summary_text: Optional[str] = None,
) -> Optional[int]:
    """Create or update the combined application note. None if skipped or failed."""
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
        company, company_id = company_record_from_job(job)
        recruiter, recruiter_id = recruiter_record_from_job(job)
        existing = bullhorn.get_candidate_notes(
            int(candidate_id),
            action_filter=[APPLICATION_NOTE_ACTION, SUMMARY_ACTION],
            count=50,
        )
        summary_text = (summary_text or '').strip()
        if not summary_text:
            for note in existing or []:
                comments = note.get('comments') or ''
                if (note.get('action') or '') == SUMMARY_ACTION and SUMMARY_HEADING in comments:
                    summary_text = comments.strip()
                    break
        snapshot = build_application_note_text(
            source=source,
            title=title,
            company=company,
            recruiter=recruiter,
            applied_at=applied_at,
            job_id=int(job_id),
            company_id=company_id,
            recruiter_id=recruiter_id,
        )
        combined = combine_application_note_text(snapshot, summary_text)
        matches = matching_snapshot_notes(
            existing,
            title=title,
            company=company,
            job_id=int(job_id),
            applied_at=applied_at,
        )
        match = _pick_snapshot_keeper(matches)
        if match:
            comments = match.get('comments') or ''
            job_linked = _job_order_linked(comments, int(job_id))
            summary_present = SUMMARY_HEADING in comments
            has_user_link = 'entity=CorporateUser' in comments
            needs_rewrite = (
                (not job_linked)
                or (bool(summary_text) and not summary_present)
                or has_user_link
            )
            match_id = int(match['id'])
            extras = list(matches)
            for group in duplicate_application_note_groups(existing):
                if any(int(n.get('id') or 0) == match_id for n in group):
                    extras.extend(group)
            if needs_rewrite:
                updated = bullhorn.update_entity(
                    'Note', match_id, {'comments': combined}
                )
                if updated:
                    _soft_delete_standalone_summaries(
                        bullhorn, existing, kept_id=match_id
                    )
                    _soft_delete_duplicate_snapshots(
                        bullhorn, extras, kept_id=match_id
                    )
                    logger.info(
                        'application snapshot: updated note %s on candidate %s',
                        match['id'], candidate_id,
                    )
                    return match_id
            if summary_present:
                _soft_delete_standalone_summaries(
                    bullhorn, existing, kept_id=match_id
                )
            _soft_delete_duplicate_snapshots(bullhorn, extras, kept_id=match_id)
            logger.info(
                'application snapshot: already present for candidate %s job %s',
                candidate_id, job_id,
            )
            return None
        text = combined
        note_id = bullhorn.create_candidate_note(
            int(candidate_id), text, APPLICATION_NOTE_ACTION
        )
        if note_id:
            _soft_delete_standalone_summaries(bullhorn, existing, kept_id=int(note_id))
            extras = []
            for group in duplicate_application_note_groups(existing):
                comments = (group[0].get('comments') if group else '') or ''
                if _job_order_linked(comments, int(job_id)) and _same_applied_at(
                    comments, applied_at
                ):
                    extras.extend(group)
            _soft_delete_duplicate_snapshots(
                bullhorn, extras, kept_id=int(note_id)
            )
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


def _soft_delete_duplicate_snapshots(bullhorn, notes, *, kept_id: int) -> None:
    """Remove extra Application Received notes for the same job after one keeper remains."""
    for note in notes or []:
        if (note.get('action') or '') != APPLICATION_NOTE_ACTION:
            continue
        note_id = note.get('id')
        if not note_id or int(note_id) == int(kept_id):
            continue
        try:
            bullhorn.delete_entity('Note', int(note_id), soft_delete=True)
            logger.info(
                'application snapshot: removed duplicate Application Received note %s',
                note_id,
            )
        except Exception as exc:
            logger.warning(
                'application snapshot: could not remove duplicate note %s: %s',
                note_id, exc,
            )


def _soft_delete_standalone_summaries(bullhorn, notes, *, kept_id: int) -> None:
    """Remove leftover AI Resume Summary notes after the combined note is in place."""
    for note in notes or []:
        if (note.get('action') or '') != SUMMARY_ACTION:
            continue
        note_id = note.get('id')
        if not note_id or int(note_id) == int(kept_id):
            continue
        comments = note.get('comments') or ''
        if SUMMARY_HEADING not in comments:
            continue
        try:
            bullhorn.delete_entity('Note', int(note_id), soft_delete=True)
            logger.info('application snapshot: removed standalone AI Resume Summary note %s', note_id)
        except Exception as exc:
            logger.warning(
                'application snapshot: could not remove standalone summary note %s: %s',
                note_id, exc,
            )


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
