"""Qualified Indeed leak detection and correction.

Qualified has no free Indeed posting. A job is a leak when Indeed Publication
Status (``get_publication_status``) says Indeed is published and the job is
not on Sponsored - Indeed (tearsheet 2).

``INDEED_LEAK_ENFORCEMENT_MODE`` is ``off``, ``report``, or ``enforce``.
Default is ``report``: detect and digest, do not email recruiters and do not
remove. ``enforce`` sends each recruiter one email listing the open jobs that
just entered the waiting window, then DELETEs Indeed only after that window.
The career portal is never unpublished here.
"""

from __future__ import annotations

import html
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Union
from zoneinfo import ZoneInfo

from utils.job_status import is_job_eligible

from .campaign_tags import department_owns_campaign_tag, strip_campaign_tags
from .config import DEFAULT_NOTIFY_EMAIL

MODE_OFF = 'off'
MODE_REPORT = 'report'
MODE_ENFORCE = 'enforce'
MODES = (MODE_OFF, MODE_REPORT, MODE_ENFORCE)

STATE_KEY = 'indeed_leak_state'
EASTERN = ZoneInfo('America/New_York')
# First scheduled digest. Earlier manual runs do not send.
DIGEST_NOT_BEFORE = datetime(2026, 11, 2, 8, 0, tzinfo=EASTERN)

MEMBERSHIP_DROP_FRACTION = 0.50
STATUS_FAILURE_FRACTION = 0.20
DEEP_SWEEP_LOOKBACK_DAYS = 180
GUARDRAIL_EMAIL_COOLDOWN = timedelta(hours=24)
MISMATCH_AFTER = timedelta(hours=48)
MISMATCH_DELETE_AFTER = timedelta(hours=24)
INDEED_JOB_BOARD_SOURCE = 'Indeed Job Board'
UNASSIGNED_USER_ID = 1

# Query keys whose values must never reach email, saved state, or logs.
_SECRET_QUERY_KEYS = r'BhRestToken|access_token|refresh_token|password|code'
_SECRET_ASSIGN = re.compile(
    rf'(?i)\b({_SECRET_QUERY_KEYS})\b(\s*[=:]\s*)(?:"[^"]*"|\'[^\']*\'|\S+)'
)
_URL_WITH_QUERY = re.compile(r'https?://[^\s\'"<>]+', re.I)
_HTTP_STATUS = re.compile(r'\bHTTP\s+(\d{3})\b', re.I)
_STATUS_ONLY = re.compile(r'(?i)\AHTTP\s+\d{3}\Z')


def sanitize_leak_text(text: str) -> str:
    """Drop URL query strings and redact token, code, and password values."""
    if not text:
        return ''

    def _strip_query(match: re.Match) -> str:
        url = match.group(0).rstrip('.,);]')
        return url.split('?', 1)[0].split('#', 1)[0]

    cleaned = _URL_WITH_QUERY.sub(_strip_query, str(text))
    return _SECRET_ASSIGN.sub(r'\1\2[REDACTED]', cleaned)


_UNSAFE_REASON = re.compile(
    r'(?i)(https?://|\bBhRestToken\b|\baccess_token\b|\brefresh_token\b|'
    r'\bpassword\b|\bcode\b|\[REDACTED\])'
)

# Scout raises these with its own wording. A Bullhorn response body is never
# one of them. Anything else stays type and HTTP status.
_ALLOWED_REASON_EXACT = frozenset({
    'job has empty description/publicDescription',
    'cannot resolve published contact',
    'REST auth failed',
    'missing correlatedCustomText1 (department)',
    'no assigned recruiter or usable job owner',
    'assigned recruiter missing id',
    'job owner missing id',
})
_ALLOWED_REASON_PREFIXES = (
    'unmapped department:',
    'manual-department:',
)
_ALLOWED_CONTACT_EMAIL = re.compile(
    r'\A(?:assigned recruiter|job owner) \d+ missing email\Z'
)


def _http_status_from_exc(exc: BaseException):
    status = None
    response = getattr(exc, 'response', None)
    if response is not None:
        status = getattr(response, 'status_code', None)
    if status is None:
        match = _HTTP_STATUS.search(str(exc))
        status = match.group(1) if match else None
    return status


def _allowed_scout_reason(text: str) -> bool:
    """True when the text is a fixed Scout message, not a response body."""
    if text in _ALLOWED_REASON_EXACT:
        return True
    if text.startswith(_ALLOWED_REASON_PREFIXES):
        return True
    return bool(_ALLOWED_CONTACT_EMAIL.fullmatch(text))


def _safe_reason(exc: BaseException) -> str:
    """A Scout-written reason, dropped when redaction still sees a secret.

    The allowlist runs first. URL and token redaction is the second layer:
    an allowed sentence that still contains a URL, a token, or a redacted
    value is not kept.
    """
    raw = sanitize_leak_text(str(exc) or '').strip()
    if not raw or len(raw) > 240 or not _allowed_scout_reason(raw):
        return ''
    if _UNSAFE_REASON.search(raw) or _URL_WITH_QUERY.search(raw):
        return ''
    if _SECRET_ASSIGN.search(raw):
        return ''
    return raw


def sanitize_leak_error(exc: Union[BaseException, str]) -> str:
    """Type and HTTP status, plus a Scout-written reason when it is safe.

    A Bullhorn response body, a connection URL, or any other message reports
    only the exception type and HTTP status. An empty description and the
    other fixed publish reasons are kept.
    """
    if isinstance(exc, BaseException):
        status = _http_status_from_exc(exc)
        reason = _safe_reason(exc)
        if reason and _STATUS_ONLY.fullmatch(reason):
            reason = ''
        if reason:
            if status and not _HTTP_STATUS.search(reason):
                return f'{reason} HTTP {status}'
            return reason
        text = f'{type(exc).__name__} HTTP {status}' if status else type(exc).__name__
        return sanitize_leak_text(text)
    return sanitize_leak_text(str(exc or ''))


def leak_mode() -> str:
    raw = (os.environ.get('INDEED_LEAK_ENFORCEMENT_MODE') or MODE_REPORT).strip().lower()
    if raw not in MODES:
        return MODE_REPORT
    return raw


def grace_hours() -> float:
    raw = (os.environ.get('INDEED_LEAK_GRACE_HOURS') or '24').strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 24.0
    if value < 0:
        return 24.0
    return value


def notify_email() -> str:
    return (
        (os.environ.get('INDEED_LEAK_NOTIFY_EMAIL') or '').strip()
        or (os.environ.get('INDEED_TEARSHEET_PUBLISH_NOTIFY_EMAIL') or '').strip()
        or DEFAULT_NOTIFY_EMAIL
    )


def leak_bcc_email() -> str:
    """BCC on leak mail to recruiters. Unset defaults to ops. Empty disables it."""
    if 'INDEED_LEAK_BCC_EMAIL' not in os.environ:
        return DEFAULT_NOTIFY_EMAIL
    return (os.environ.get('INDEED_LEAK_BCC_EMAIL') or '').strip()


def leak_bcc_recipients(to_emails: Iterable[str]) -> List[str]:
    """One BCC address, omitted when it is already a To recipient or disabled."""
    bcc = leak_bcc_email()
    if not bcc:
        return []
    seen = set()
    for item in to_emails or []:
        if isinstance(item, str) and '@' in item:
            seen.add(item.strip().lower())
    if bcc.lower() in seen:
        return []
    return [bcc]


def exempt_job_ids() -> Set[int]:
    raw = os.environ.get('INDEED_LEAK_EXEMPT_JOB_IDS') or ''
    ids: Set[int] = set()
    for part in raw.split(','):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def exempt_departments() -> Set[str]:
    raw = os.environ.get('INDEED_LEAK_EXEMPT_DEPARTMENTS') or ''
    return {_norm_department(part) for part in raw.split(',') if part.strip()}


def _norm_department(value: Optional[str]) -> str:
    return ' '.join((value or '').strip().lower().split())


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _iso(now: datetime) -> str:
    return _utc(now).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _epoch_to_dt(value: Any) -> Optional[datetime]:
    """Bullhorn epoch milliseconds (or seconds) to UTC. Zero and blank are absent."""
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return None
    if raw <= 0:
        return None
    if raw < 10_000_000_000:
        raw *= 1000
    return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)


def _format_et(value: Optional[str]) -> str:
    parsed = _parse(value)
    if not parsed:
        return value or ''
    return parsed.astimezone(EASTERN).strftime('%b %-d, %Y %-I:%M %p ET')


def publication_stopped_at(status: Dict[str, Any]) -> Optional[datetime]:
    """When Bullhorn last showed Indeed as published, if it is not published now."""
    if not isinstance(status, dict) or status.get('indeed_published'):
        return None
    return _epoch_to_dt(status.get('indeed_date_last_published'))


def qualifying_mismatch_dates(
    status: Dict[str, Any],
    application_times: Sequence[datetime],
) -> List[datetime]:
    """Indeed Job Board applications that show Indeed is still live.

    A job Bullhorn has never shown as Indeed-published qualifies on any
    application. A job Bullhorn has unpublished qualifies only when an
    application arrived more than 48 hours after that last published time.
    """
    if not isinstance(status, dict) or status.get('indeed_published'):
        return []
    moments = [moment for moment in application_times if isinstance(moment, datetime)]
    if not moments:
        return []
    stopped = publication_stopped_at(status)
    if stopped is None:
        return sorted(_utc(moment) for moment in moments)
    late = [
        _utc(moment) for moment in moments
        if _utc(moment) - stopped > MISMATCH_AFTER
    ]
    return sorted(late)


def indeed_delete_description(job: Dict[str, Any]) -> str:
    """Body Bullhorn requires on an Indeed DELETE. A blank job still sends a space."""
    text = (job.get('publicDescription') or job.get('description') or '').strip()
    return text or ' '


def description_source_field(job: Dict[str, Any]) -> str:
    """Field Scout publishes from: public description when present, else description."""
    if (job.get('publicDescription') or '').strip():
        return 'publicDescription'
    return 'description'


def stripped_description_update(job: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Return a Bullhorn update that removes a campaign tag, or None.

    Departments that type their own tag (Appleton) are left unchanged.
    """
    if department_owns_campaign_tag(job.get('correlatedCustomText1')):
        return None
    field = description_source_field(job)
    current = job.get(field) or ''
    stripped = strip_campaign_tags(current)
    if stripped == current:
        return None
    return {field: stripped}


def _person_name(user: Dict[str, Any]) -> str:
    return f"{user.get('firstName') or ''} {user.get('lastName') or ''}".strip().lower()


def _usable_email(user: Any) -> Optional[str]:
    if not isinstance(user, dict):
        return None
    try:
        uid = int(user.get('id') or 0)
    except (TypeError, ValueError):
        uid = 0
    if uid == UNASSIGNED_USER_ID or 'unassigned' in _person_name(user):
        return None
    email = (user.get('email') or '').strip()
    if '@' not in email:
        return None
    return email


def _assigned_users(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    users = job.get('assignedUsers') or {}
    data = users.get('data') if isinstance(users, dict) else users
    if not isinstance(data, list):
        return []
    return [user for user in data if isinstance(user, dict)]


def notice_recipients(job: Dict[str, Any], fallback_email: str) -> List[str]:
    """Assigned recruiters, then owner, then the ops fallback.

    Every usable assigned recruiter is included. Unassigned placeholders and
    users without an email are skipped. Owner is used only when no assigned
    recruiter is usable. The ops address is used only when the owner is not.
    """
    found: List[str] = []
    seen: Set[str] = set()
    for user in _assigned_users(job):
        email = _usable_email(user)
        if not email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(email)
    if found:
        return found
    owner_email = _usable_email(job.get('owner') if isinstance(job.get('owner'), dict) else None)
    if owner_email:
        return [owner_email]
    fallback = (fallback_email or '').strip()
    return [fallback] if fallback else []


def is_exempt(job: Dict[str, Any], job_ids: Set[int], departments: Set[str]) -> bool:
    try:
        jid = int(job.get('id') or 0)
    except (TypeError, ValueError):
        jid = 0
    if jid and jid in job_ids:
        return True
    return _norm_department(job.get('correlatedCustomText1')) in departments


_NOTICE_CLOSING = (
    'Add the job to the Sponsored - Indeed tearsheet before the deadline to keep it on Indeed. '
    'Otherwise it will be removed from Indeed automatically. '
    'Your careers site posting is not affected.'
)


def notice_subject(count: int) -> str:
    """Subject for one recruiter's combined warning. Singular when count is 1."""
    count = int(count)
    noun = 'posting' if count == 1 else 'postings'
    return f'Action needed: {count} Indeed {noun} outside Sponsored - Indeed'


def notice_deadline_text(deadline: datetime) -> str:
    """Removal time in Eastern, for example 'Tue Nov 3, 9:00 AM ET'."""
    local = _utc(deadline).astimezone(EASTERN)
    return local.strftime('%a %b %-d, %-I:%M %p ET')


def _notice_intro(count: int) -> str:
    if count == 1:
        return (
            'This job is posted on Indeed and is not on the Sponsored - Indeed tearsheet.'
        )
    return (
        'These jobs are posted on Indeed and are not on the Sponsored - Indeed tearsheet.'
    )


def _notice_rows(jobs: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    rows = []
    for job in jobs:
        title = (job.get('title') or '').strip() or '(no title)'
        department = (job.get('correlatedCustomText1') or '').strip() or '(no department)'
        rows.append({
            'id': str(job.get('id') if job.get('id') is not None else ''),
            'title': title,
            'department': department,
        })
    return rows


def notice_plain_text(jobs: Sequence[Dict[str, Any]], deadline: datetime) -> str:
    rows = _notice_rows(jobs)
    when = notice_deadline_text(deadline)
    lines = [_notice_intro(len(rows)), '', 'Job ID | Title | Department | Removal deadline']
    for row in rows:
        lines.append(f"{row['id']} | {row['title']} | {row['department']} | {when}")
    lines.extend(['', _NOTICE_CLOSING])
    return '\n'.join(lines)


def notice_html(jobs: Sequence[Dict[str, Any]], deadline: datetime) -> str:
    """HTML body. Every Bullhorn field is escaped."""
    rows = _notice_rows(jobs)
    when = html.escape(notice_deadline_text(deadline))
    body = []
    for row in rows:
        body.append(
            '<tr>'
            f"<td>{html.escape(row['id'])}</td>"
            f"<td>{html.escape(row['title'])}</td>"
            f"<td>{html.escape(row['department'])}</td>"
            f'<td>{when}</td>'
            '</tr>'
        )
    table = (
        '<table border="1" cellpadding="6" cellspacing="0">'
        '<thead><tr>'
        '<th>Job ID</th><th>Title</th><th>Department</th><th>Removal deadline</th>'
        '</tr></thead><tbody>'
        + ''.join(body)
        + '</tbody></table>'
    )
    return (
        f"<p>{html.escape(_notice_intro(len(rows)))}</p>"
        f'{table}'
        f'<p>{html.escape(_NOTICE_CLOSING)}</p>'
    )


def _blank_state() -> Dict[str, Any]:
    return {
        'jobs': {},
        'last_successful_tearsheet_count': None,
        'last_successful_at': None,
        'last_digest_at': None,
        'guardrail_notified': {},
        'last_status_failures': [],
        'last_run': {},
    }


def _state_jobs(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    jobs = state.get('jobs')
    if not isinstance(jobs, dict):
        jobs = {}
        state['jobs'] = jobs
    return jobs


def _append_event(record: Dict[str, Any], now: datetime, action: str, result: str, detail: str = '') -> None:
    events = record.setdefault('events', [])
    events.append({
        'at': _iso(now),
        'action': action,
        'result': sanitize_leak_text(result),
        'detail': sanitize_leak_text(detail)[:500],
    })
    if len(events) > 30:
        del events[:-30]
    record['last_action'] = action
    record['last_result'] = result


def _touch_record(state: Dict[str, Any], job: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    jid = str(int(job['id']))
    jobs = _state_jobs(state)
    record = jobs.get(jid)
    if record is None:
        record = {
            'job_id': int(job['id']),
            'events': [],
            'grace_deadline': None,
            'notice_sent_at': None,
            'removed_at': None,
            'resolved_at': None,
        }
        jobs[jid] = record
        record['first_seen'] = _iso(now)
        _append_event(record, now, 'detected', 'open')
    elif not record.get('open'):
        record['first_seen'] = _iso(now)
        record['grace_deadline'] = None
        record['notice_sent_at'] = None
        record['removed_at'] = None
        record['resolved_at'] = None
        record['recipients'] = []
        _append_event(record, now, 'detected', 'open again')
    record['open'] = True
    record['title'] = job.get('title') or record.get('title') or ''
    record['department'] = job.get('correlatedCustomText1') or record.get('department') or ''
    record['status'] = job.get('status') or record.get('status') or ''
    record['last_seen'] = _iso(now)
    record['assigned_users'] = _assigned_users(job)
    record['owner'] = job.get('owner') if isinstance(job.get('owner'), dict) else None
    record['recipients'] = notice_recipients(job, '')
    record['resolved_at'] = None
    return record


def _resolve(record: Dict[str, Any], now: datetime, result: str) -> None:
    if record.get('resolved_at') or not record.get('open', True):
        record['open'] = False
        return
    record['open'] = False
    record['resolved_at'] = _iso(now)
    record['grace_deadline'] = None
    _append_event(record, now, 'resolved', result)


class LeakRun:
    """One detection pass. IO is injected so the rules can be tested without Bullhorn."""

    def __init__(
        self,
        *,
        mode: str,
        now: datetime,
        state: Dict[str, Any],
        grace_hours: float = 24,
        exempt_job_ids: Optional[Set[int]] = None,
        exempt_departments: Optional[Set[str]] = None,
        notify_email: str = DEFAULT_NOTIFY_EMAIL,
        send_email: Optional[Callable[[str, str, str], bool]] = None,
        remove_indeed: Optional[Callable[[Dict[str, Any]], None]] = None,
        publication_status: Optional[Callable[[int], Dict[str, Any]]] = None,
        job_on_tearsheet: Optional[Callable[[int], bool]] = None,
    ):
        self.mode = mode if mode in MODES else MODE_REPORT
        self.now = _utc(now)
        self.state = _blank_state() if state is None else state
        for key, value in _blank_state().items():
            self.state.setdefault(key, value)
        self.grace_hours = grace_hours
        self.exempt_job_ids = exempt_job_ids or set()
        self.exempt_departments = exempt_departments or set()
        self.notify_email = notify_email
        self.send_email = send_email or (lambda *_a, **_k: False)
        self.remove_indeed = remove_indeed or (lambda _job: None)
        self._pending_notices: List[Dict[str, Any]] = []
        self.publication_status = publication_status or (lambda _jid: {'indeed_published': False})
        self.job_on_tearsheet = job_on_tearsheet or (lambda _jid: False)
        self.acted: Set[int] = set()
        # Fast path only: Indeed Job Board application times per job id.
        self.mismatch_applications: Dict[int, List[datetime]] = {}
        self.result: Dict[str, Any] = {
            'mode': self.mode,
            'aborted': False,
            'guardrail': None,
            'checked': 0,
            'status_failures': 0,
            'leaks': [],
            'mismatches': [],
            'mismatch_deletes': [],
            'noticed': [],
            'removed': [],
            'resolved': [],
            'exempt': [],
            'failed': [],
            'skipped': [],
        }

    def abort_for_guardrail(self, kind: str, detail: str) -> Dict[str, Any]:
        """No removals and no grace mail. Email ops at most once per kind per day."""
        detail = sanitize_leak_text(detail)
        self.result['aborted'] = True
        self.result['guardrail'] = kind
        self.result['guardrail_detail'] = detail
        notified = self.state.setdefault('guardrail_notified', {})
        last = _parse(notified.get(kind) if isinstance(notified, dict) else None)
        if self.notify_email and (last is None or self.now - last >= GUARDRAIL_EMAIL_COOLDOWN):
            sent = False
            try:
                sent = bool(self.send_email(
                    self.notify_email,
                    f'Indeed leak check stopped: {kind}',
                    (
                        'This Indeed leak run took no action because its inputs look broken.\n\n'
                        f'{detail}\n\n'
                        'No jobs were removed and no grace notices were sent.'
                    ),
                ))
            except Exception as exc:
                self.result['guardrail_email_error'] = sanitize_leak_error(exc)
            if sent and isinstance(notified, dict):
                notified[kind] = _iso(self.now)
        return self.result

    def guard_tearsheet(self, tearsheet_ids: Optional[Iterable[int]], *, read_error: str = '') -> bool:
        """Return True when the run must stop. Zero jobs and a failed read both stop."""
        if read_error:
            self.abort_for_guardrail('tearsheet_read_failed', read_error)
            return True
        ids = []
        try:
            ids = [int(x) for x in (tearsheet_ids or [])]
        except (TypeError, ValueError):
            self.abort_for_guardrail('tearsheet_read_failed', 'tearsheet membership was not a list of job ids')
            return True
        if not ids:
            self.abort_for_guardrail(
                'tearsheet_empty',
                'Sponsored - Indeed (tearsheet 2) returned zero jobs.',
            )
            return True
        previous = self.state.get('last_successful_tearsheet_count')
        try:
            previous_count = int(previous) if previous is not None else None
        except (TypeError, ValueError):
            previous_count = None
        if previous_count and len(ids) < previous_count * (1 - MEMBERSHIP_DROP_FRACTION):
            self.abort_for_guardrail(
                'tearsheet_membership_drop',
                (
                    f'Sponsored - Indeed membership dropped from {previous_count} '
                    f'to {len(ids)} (more than 50%).'
                ),
            )
            return True
        self._tearsheet_count = len(ids)
        self._tearsheet_ids = set(ids)
        return False

    def consider(self, jobs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """Status-check jobs that are not on the tearsheet, then apply the mode."""
        if self.mode == MODE_OFF:
            self.result['skipped'] = ['mode off']
            return self.result
        if self.result.get('aborted'):
            return self.result
        for key, record in list(_state_jobs(self.state).items()):
            if not record.get('open'):
                continue
            try:
                seen_id = int(key)
            except (TypeError, ValueError):
                continue
            if seen_id in getattr(self, '_tearsheet_ids', set()):
                self._resolve_if_open(seen_id, 'on tearsheet')
        unique: List[Dict[str, Any]] = []
        for job in jobs:
            try:
                jid = int(job.get('id') or 0)
            except (TypeError, ValueError):
                continue
            if not jid or jid in self.acted:
                continue
            self.acted.add(jid)
            if jid in getattr(self, '_tearsheet_ids', set()):
                self._resolve_if_open(jid, 'on tearsheet')
                continue
            unique.append(job)

        failures = 0
        leaks: List[Dict[str, Any]] = []
        for job in unique:
            jid = int(job['id'])
            try:
                status = self.publication_status(jid) or {}
            except Exception as exc:
                failures += 1
                self.result['failed'].append({
                    'job_id': jid,
                    'result': f'status: {sanitize_leak_error(exc)}',
                })
                continue
            published = bool(status.get('indeed_published'))
            if not published:
                applications = self.mismatch_applications.get(jid) or []
                if qualifying_mismatch_dates(status, applications):
                    self._act_mismatch(job, status, applications)
                    continue
                self._resolve_if_open(jid, 'Indeed not published')
                continue
            leaks.append(job)

        attempts = len(unique)
        self.result['checked'] = attempts
        self.result['status_failures'] = failures
        if attempts and (failures / attempts) > STATUS_FAILURE_FRACTION:
            self.result['leaks'] = []
            self.result['failed'] = []
            self.abort_for_guardrail(
                'status_failure_rate',
                (
                    f'{failures} of {attempts} Indeed publication status calls failed '
                    f'(more than 20%).'
                ),
            )
            return self.result

        if attempts:
            self.state['last_status_failures'] = [
                item for item in self.result['failed']
                if str(item.get('result', '')).startswith('status:')
            ]
        self.state['last_successful_tearsheet_count'] = getattr(self, '_tearsheet_count', None)
        self.state['last_successful_at'] = _iso(self.now)

        for job in leaks:
            self._act(job)
        self._deliver_notices()
        self.state['last_run'] = {
            'at': _iso(self.now),
            'mode': self.mode,
            'checked': attempts,
            'leaks': [int(job['id']) for job in leaks],
            'mismatches': list(self.result['mismatches']),
            'removed': list(self.result['removed']),
            'noticed': list(self.result['noticed']),
        }
        return self.result

    def _resolve_if_open(self, job_id: int, reason: str) -> None:
        record = _state_jobs(self.state).get(str(job_id))
        if not record or not record.get('open'):
            return
        # Bullhorn already says these are off Indeed. A later sweep must not
        # clear them, or the Monday ticket list disappears.
        if record.get('last_action') == 'mismatch' and reason == 'Indeed not published':
            return
        _resolve(record, self.now, reason)
        self.result['resolved'].append(job_id)

    def _act(self, job: Dict[str, Any]) -> None:
        jid = int(job['id'])
        record = _touch_record(self.state, job, self.now)
        self.result['leaks'].append(jid)
        if is_exempt(job, self.exempt_job_ids, self.exempt_departments):
            if record.get('last_action') != 'exempt':
                _append_event(record, self.now, 'exempt', 'skipped')
            self.result['exempt'].append(jid)
            return
        if self.mode != MODE_ENFORCE:
            if record.get('last_action') not in ('detected', 'exempt'):
                _append_event(record, self.now, 'detected', self.mode)
            return

        if not is_job_eligible(job):
            self._remove(job, record, reason='ineligible')
            return

        deadline = _parse(record.get('grace_deadline'))
        if deadline is None:
            self._start_grace(job, record)
            return
        if self.now >= deadline:
            self._remove(job, record, reason='grace ended')
            return
        self.result['skipped'].append(jid)

    def _start_grace(self, job: Dict[str, Any], record: Dict[str, Any]) -> None:
        """Queue a warning. Delivery is one email per recruiter after the run."""
        jid = int(job['id'])
        recipients = notice_recipients(job, self.notify_email)
        if not recipients:
            _append_event(record, self.now, 'failed', 'no recipient')
            self.result['failed'].append({'job_id': jid, 'result': 'no recipient'})
            return
        self._pending_notices.append({
            'job': job,
            'record': record,
            'recipients': recipients,
        })

    def _deliver_notices(self) -> None:
        """Send one email per recipient, then start the window where a send landed."""
        pending = self._pending_notices
        self._pending_notices = []
        if not pending:
            return
        deadline = self.now + timedelta(hours=self.grace_hours)
        groups: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for item in pending:
            for email in item['recipients']:
                key = email.strip().lower()
                if key not in groups:
                    groups[key] = {'email': email.strip(), 'jobs': []}
                    order.append(key)
                groups[key]['jobs'].append(item['job'])
        sent: Dict[str, bool] = {}
        errors: Dict[str, str] = {}
        for key in order:
            group = groups[key]
            subject = notice_subject(len(group['jobs']))
            text = notice_plain_text(group['jobs'], deadline)
            html_body = notice_html(group['jobs'], deadline)
            try:
                ok = bool(self.send_email([group['email']], subject, text, html_body))
            except Exception as exc:
                ok = False
                errors[key] = sanitize_leak_error(exc)
            sent[key] = ok
        for item in pending:
            job = item['job']
            record = item['record']
            recipients = item['recipients']
            jid = int(job['id'])
            keys = [email.strip().lower() for email in recipients]
            if any(sent.get(key) for key in keys):
                record['notice_sent_at'] = _iso(self.now)
                record['grace_deadline'] = _iso(deadline)
                record['recipients'] = recipients
                _append_event(record, self.now, 'notice', 'grace started', ', '.join(recipients))
                self.result['noticed'].append(jid)
                continue
            detail = ', '.join(errors[key] for key in keys if errors.get(key))
            _append_event(record, self.now, 'failed', 'notice email failed', detail)
            self.result['failed'].append({'job_id': jid, 'result': 'notice email failed'})

    def _act_mismatch(
        self,
        job: Dict[str, Any],
        status: Dict[str, Any],
        application_times: Sequence[datetime],
    ) -> None:
        """Bullhorn says Indeed is off, but an Indeed Job Board application arrived.

        Report mode records it. Enforce mode re-sends the Indeed DELETE at most
        once per day. The record stays open for the Monday ops digest.
        """
        jid = int(job['id'])
        record = _touch_record(self.state, job, self.now)
        late = qualifying_mismatch_dates(status, application_times)
        stopped = publication_stopped_at(status)
        record['never_published'] = stopped is None
        record['unpublished_at'] = _iso(stopped) if stopped else None
        merged = [str(item) for item in (record.get('application_dates') or []) if item]
        seen = set(merged)
        for moment in late:
            stamp = _iso(moment)
            if stamp not in seen:
                seen.add(stamp)
                merged.append(stamp)
        record['application_dates'] = merged
        record['open'] = True
        self.result['mismatches'].append(jid)
        if self.mode != MODE_ENFORCE:
            if record.get('last_action') != 'mismatch':
                _append_event(record, self.now, 'mismatch', 'recorded')
            else:
                record['last_action'] = 'mismatch'
            return
        last_delete = _parse(record.get('mismatch_delete_at'))
        if last_delete and self.now - last_delete < MISMATCH_DELETE_AFTER:
            record['last_action'] = 'mismatch'
            return
        try:
            if self.job_on_tearsheet(jid):
                _resolve(record, self.now, 'on tearsheet before mismatch delete')
                self.result['resolved'].append(jid)
                return
            again = self.publication_status(jid) or {}
        except Exception as exc:
            safe = sanitize_leak_error(exc)
            _append_event(record, self.now, 'mismatch', f'recheck: {safe}')
            self.result['failed'].append({'job_id': jid, 'result': f'recheck: {safe}'})
            return
        if again.get('indeed_published'):
            self._act(job)
            return
        record['mismatch_delete_at'] = _iso(self.now)
        try:
            self.remove_indeed(job)
        except Exception as exc:
            safe = sanitize_leak_error(exc)
            _append_event(record, self.now, 'mismatch', f'delete failed: {safe}')
            self.result['failed'].append({'job_id': jid, 'result': f'mismatch delete: {safe}'})
            return
        _append_event(record, self.now, 'mismatch', 'indeed delete resent')
        self.result['mismatch_deletes'].append(jid)

    def _remove(self, job: Dict[str, Any], record: Dict[str, Any], *, reason: str) -> None:
        jid = int(job['id'])
        try:
            if self.job_on_tearsheet(jid):
                _resolve(record, self.now, 'on tearsheet before removal')
                self.result['resolved'].append(jid)
                return
            status = self.publication_status(jid) or {}
        except Exception as exc:
            safe = sanitize_leak_error(exc)
            _append_event(record, self.now, 'failed', f'recheck: {safe}')
            self.result['failed'].append({'job_id': jid, 'result': f'recheck: {safe}'})
            return
        if not status.get('indeed_published'):
            _resolve(record, self.now, 'Indeed not published before removal')
            self.result['resolved'].append(jid)
            return
        try:
            self.remove_indeed(job)
        except Exception as exc:
            safe = sanitize_leak_error(exc)
            _append_event(record, self.now, 'failed', f'remove: {safe}')
            self.result['failed'].append({'job_id': jid, 'result': f'remove: {safe}'})
            return
        record['open'] = False
        record['removed_at'] = _iso(self.now)
        record['grace_deadline'] = None
        _append_event(record, self.now, 'removed', reason)
        self.result['removed'].append(jid)


def select_sweep_jobs(
    open_jobs: Sequence[Dict[str, Any]],
    recent_jobs: Sequence[Dict[str, Any]],
    tearsheet_ids: Set[int],
) -> List[Dict[str, Any]]:
    """Open jobs, plus anything closed or modified recently, excluding tearsheet members."""
    chosen: Dict[int, Dict[str, Any]] = {}
    for job in list(open_jobs) + list(recent_jobs):
        try:
            jid = int(job.get('id') or 0)
        except (TypeError, ValueError):
            continue
        if not jid or jid in tearsheet_ids:
            continue
        chosen[jid] = job
    return [chosen[jid] for jid in sorted(chosen)]


def deep_sweep_where(now: datetime) -> str:
    """Closed, not deleted, closed or last modified within 180 days.

    ``dateClosed`` is often empty on Qualified. The last-modified half of the
    OR still covers a job that was filled or held and then left untouched.
    """
    cutoff_ms = int(
        (_utc(now) - timedelta(days=DEEP_SWEEP_LOOKBACK_DAYS)).timestamp() * 1000
    )
    return (
        'isDeleted=false AND isOpen=false AND '
        f'(dateLastModified>={cutoff_ms} OR dateClosed>={cutoff_ms})'
    )


def _is_closed_job(job: Dict[str, Any]) -> bool:
    if job.get('isDeleted') in (True, 1):
        return False
    return job.get('isOpen') in (False, 0)


def select_deep_sweep_jobs(
    closed_jobs: Sequence[Dict[str, Any]],
    tearsheet_ids: Set[int],
) -> List[Dict[str, Any]]:
    """Closed jobs from the 180-day query, excluding Sponsored - Indeed members."""
    chosen: Dict[int, Dict[str, Any]] = {}
    for job in closed_jobs:
        try:
            jid = int(job.get('id') or 0)
        except (TypeError, ValueError):
            continue
        if not jid or jid in tearsheet_ids or not _is_closed_job(job):
            continue
        chosen[jid] = job
    return [chosen[jid] for jid in sorted(chosen)]


def jobs_from_applicant_submissions(
    submissions_by_candidate: Dict[int, Sequence[Dict[str, Any]]],
) -> List[int]:
    """Job ids on Indeed Job Board submissions. Order is stable, duplicates dropped."""
    found: List[int] = []
    seen: Set[int] = set()
    for _cid, rows in submissions_by_candidate.items():
        for row in rows or []:
            job = row.get('jobOrder') if isinstance(row, dict) else None
            if not isinstance(job, dict) or not job.get('id'):
                continue
            try:
                jid = int(job['id'])
            except (TypeError, ValueError):
                continue
            if jid in seen:
                continue
            seen.add(jid)
            found.append(jid)
    return found


def cannot_publish_reason(job: Dict[str, Any]) -> Optional[str]:
    """Why a tearsheet job cannot be published: empty description and/or no response user."""
    reasons = []
    public = (job.get('publicDescription') or '').strip()
    private = (job.get('description') or '').strip()
    if not public and not private:
        reasons.append('empty description')
    if not notice_recipients(job, ''):
        reasons.append('no response user')
    if not reasons:
        return None
    return ', '.join(reasons)


def _record_recipients(record: Dict[str, Any], fallback: str) -> List[str]:
    stored = record.get('recipients')
    if isinstance(stored, list) and stored:
        return [str(item) for item in stored if str(item).strip()]
    job = {
        'id': record.get('job_id'),
        'assignedUsers': {'data': record.get('assigned_users') or []},
        'owner': record.get('owner'),
    }
    return notice_recipients(job, fallback)


def digest_is_due(now: datetime, state: Dict[str, Any]) -> bool:
    current = _utc(now).astimezone(EASTERN)
    if current < DIGEST_NOT_BEFORE:
        return False
    last = _parse(state.get('last_digest_at') if isinstance(state, dict) else None)
    if last and _utc(now) - last < timedelta(days=6):
        return False
    return True


def _section_lines(title: str, rows: Sequence[str]) -> List[str]:
    if not rows:
        return [f'{title}: 0']
    return [f'{title}: {len(rows)}', *[f'- {row}' for row in rows]]


def mismatch_digest_detail(record: Dict[str, Any]) -> str:
    """Unpublish time and Indeed Job Board application times for a support ticket."""
    if record.get('never_published') or not record.get('unpublished_at'):
        when = 'never'
    else:
        when = _format_et(record.get('unpublished_at')) or 'unknown'
    apps = [_format_et(str(item)) for item in (record.get('application_dates') or [])]
    app_text = ', '.join(item for item in apps if item) or 'none recorded'
    return f'unpublished {when}; applications {app_text}'


def indeed_board_applications(
    submissions: Sequence[Dict[str, Any]],
) -> Dict[int, List[datetime]]:
    """Job id to Indeed Job Board application times. Other sources are ignored."""
    found: Dict[int, List[datetime]] = {}
    for row in submissions or []:
        if not isinstance(row, dict):
            continue
        if (row.get('source') or '') != INDEED_JOB_BOARD_SOURCE:
            continue
        job = row.get('jobOrder')
        if not isinstance(job, dict) or not job.get('id'):
            continue
        moment = _epoch_to_dt(row.get('dateAdded'))
        if moment is None:
            continue
        try:
            jid = int(job['id'])
        except (TypeError, ValueError):
            continue
        found.setdefault(jid, []).append(moment)
    return found


def jobs_with_recent_indeed_applications(
    applications: Dict[int, Sequence[datetime]],
    now: datetime,
    *,
    lookback_minutes: float,
) -> Dict[int, List[datetime]]:
    """Keep jobs that received an Indeed Job Board application inside the lookback.

    Older applications on that same job stay in the list so the digest can
    show them. A candidate edit with no new application does not qualify.
    """
    cutoff = _utc(now) - timedelta(minutes=lookback_minutes)
    kept: Dict[int, List[datetime]] = {}
    for job_id, moments in (applications or {}).items():
        parsed = [_utc(moment) for moment in moments if isinstance(moment, datetime)]
        if any(moment >= cutoff for moment in parsed):
            kept[int(job_id)] = parsed
    return kept


def render_digest(
    *,
    now: datetime,
    mode: str,
    live_tearsheet: Sequence[Dict[str, Any]],
    leaks: Sequence[Dict[str, Any]],
    removed: Sequence[Dict[str, Any]],
    in_grace: Sequence[Dict[str, Any]],
    failed: Sequence[Dict[str, Any]],
    cannot_publish: Sequence[Dict[str, Any]],
    exempt: Sequence[Dict[str, Any]] = (),
    not_live: Sequence[Dict[str, Any]] = (),
    mismatches: Sequence[Dict[str, Any]] = (),
    audience: Optional[str] = None,
) -> Optional[str]:
    """Plain-text digest. ``audience`` keeps only rows for that recipient.

    Returns None when the audience has nothing to report. The full digest
    (audience None) is always returned.
    """
    def keep(row: Dict[str, Any]) -> bool:
        if not audience:
            return True
        emails = [str(item).lower() for item in (row.get('recipients') or [])]
        return audience.lower() in emails

    def line(row: Dict[str, Any]) -> str:
        jid = row.get('job_id') or row.get('id')
        title = row.get('title') or ''
        department = row.get('department') or row.get('correlatedCustomText1') or ''
        extra = row.get('detail') or row.get('reason') or ''
        base = f'{jid} {title} ({department})'.strip()
        return f'{base} — {extra}' if extra else base

    groups = [
        ('Live via Sponsored - Indeed', [line(row) for row in live_tearsheet if keep(row)]),
        ('Leaks found', [line(row) for row in leaks if keep(row)]),
        ('Removed', [line(row) for row in removed if keep(row)]),
        ('In grace', [line(row) for row in in_grace if keep(row)]),
        ('Failed actions', [line(row) for row in failed if keep(row)]),
        ('Tearsheet jobs that cannot publish', [line(row) for row in cannot_publish if keep(row)]),
        (
            'On Sponsored - Indeed but not live on Indeed',
            [line(row) for row in not_live if keep(row)],
        ),
    ]
    if audience and not any(rows for _title, rows in groups):
        return None
    if not audience:
        groups.append((
            'Live on Indeed but Bullhorn says unpublished',
            [line(row) for row in mismatches],
        ))
        groups.append(('Exempt', [line(row) for row in exempt]))
    when = _utc(now).astimezone(EASTERN).strftime('%b %-d, %Y %-I:%M %p ET')
    who = audience or notify_email()
    header = [
        f'Indeed leak digest — {when}',
        f'Mode: {mode}',
        f'To: {who}',
        '',
    ]
    body: List[str] = []
    for title, rows in groups:
        body.extend(_section_lines(title, rows))
        body.append('')
    return '\n'.join(header + body).rstrip() + '\n'


def digest_buckets(state: Dict[str, Any], now: datetime) -> Dict[str, List[Dict[str, Any]]]:
    """Split persisted records into digest sections since the previous digest."""
    current = _utc(now)
    last = _parse(state.get('last_digest_at'))
    leaks, removed, in_grace, failed, exempt, mismatches = [], [], [], [], [], []
    for record in _state_jobs(state).values():
        row = dict(record)
        row['recipients'] = _record_recipients(row, '')
        if record.get('open') and record.get('last_action') == 'mismatch':
            row['detail'] = mismatch_digest_detail(record)
            mismatches.append(row)
            continue
        if record.get('last_action') == 'exempt' and record.get('open'):
            exempt.append(row)
        if record.get('open') and record.get('last_action') != 'exempt':
            leaks.append(row)
            deadline = _parse(record.get('grace_deadline'))
            if deadline and deadline > current and not record.get('removed_at'):
                in_grace.append(row)
        for event in record.get('events') or []:
            at = _parse(event.get('at'))
            if last and at and at <= last:
                continue
            if last is None and at and current - at > timedelta(days=7):
                continue
            action = event.get('action')
            item = dict(row)
            item['detail'] = event.get('result') or ''
            item['recipients'] = row['recipients']
            if action == 'removed':
                removed.append(item)
            elif action == 'failed':
                failed.append(item)
    for item in state.get('last_status_failures') or []:
        failed.append({
            'job_id': item.get('job_id'),
            'title': '',
            'department': '',
            'detail': item.get('result') or 'status failed',
            'recipients': [],
        })
    return {
        'leaks': leaks,
        'removed': removed,
        'in_grace': in_grace,
        'failed': failed,
        'exempt': exempt,
        'mismatches': mismatches,
    }
