"""Tearsheet membership sync → Bullhorn native Indeed Publish/Unpublish."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from .campaign_tags import (
    MYTICAS_CAMPAIGN_TAG,
    apply_campaign_tag,
    campaign_tag_for_job,
    is_manual_campaign_reason,
)
from .category_mapper import map_published_category
from .config import (
    LAST_RESULT_KEY,
    NOTIFY_STAMP_KEY,
    STATE_KEY,
    TEARSHEET_ID,
    config_from_env,
)
from .leak import sanitize_leak_error
from .leak_runtime import _rest_get
from .ui_client import BullhornPublishUnavailable, BullhornUIClient, BullhornUIClientError
from feeds.feed_config import is_qualified_tenant

logger = logging.getLogger(__name__)

EASTERN = ZoneInfo('America/New_York')
MEMBERSHIP_PAGE_SIZE = 200


def _association_404_is_empty(response) -> bool:
    """True only when a 404 body says this tearsheet association is empty.

    An invalid path, HTML, or any other 404 body is not absence. Live Qualified
    returned HTTP 400 ``errors.badCommand`` for ``.../tearsheets/{id}``, so that
    body must not DELETE.
    """
    try:
        payload = response.json()
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    key = str(payload.get('errorMessageKey') or '').strip().lower()
    message = str(payload.get('errorMessage') or '').strip().lower()
    if key == 'errors.badcommand' or 'badly structured' in message:
        return False
    if payload.get('data') == []:
        return True
    if key == 'errors.associationnotfound':
        return True
    if 'association' in message and ('empty' in message or 'not found' in message):
        return True
    return False
# A real tearsheet does not lose this many jobs in one 5-minute cycle.
MASS_DROP_MIN_JOBS = 5
MASS_DROP_FRACTION = 0.10
GUARDRAIL_EMAIL_AFTER = timedelta(hours=24)
SELF_HEAL_REPORT_AFTER = timedelta(days=2)


class TearsheetMembershipError(RuntimeError):
    """The tearsheet read is not a complete membership. Do not unpublish from it."""

# Back-compat for tests / older imports
INDSHOW_TAG = MYTICAS_CAMPAIGN_TAG
INDSHOW_SUFFIX = f'   {MYTICAS_CAMPAIGN_TAG}'


def ensure_indshow_tag(html: str) -> str:
    """Myticas/STSI helper: append ``   #INDShow`` when missing."""
    return apply_campaign_tag(html, MYTICAS_CAMPAIGN_TAG)


def _cfg_state_key(cfg: Optional[dict] = None) -> str:
    cfg = cfg or config_from_env()
    return (cfg.get('state_key') or STATE_KEY).strip() or STATE_KEY


def _cfg_last_result_key(cfg: Optional[dict] = None) -> str:
    cfg = cfg or config_from_env()
    return (cfg.get('last_result_key') or LAST_RESULT_KEY).strip() or LAST_RESULT_KEY


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _eastern_now() -> datetime:
    return datetime.now(EASTERN)


def _job_description_html(job: Dict[str, Any]) -> str:
    public = (job.get('publicDescription') or '').strip()
    if public:
        return public
    return (job.get('description') or '').strip()


def _description_source_field(job: Dict[str, Any]) -> str:
    """Field `_job_description_html` would prefer (publicDescription, else description)."""
    if (job.get('publicDescription') or '').strip():
        return 'publicDescription'
    return 'description'


def _first_assigned_recruiter(job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    users = job.get('assignedUsers') or {}
    data = users.get('data') if isinstance(users, dict) else users
    if not isinstance(data, list):
        return None
    for user in data:
        if not isinstance(user, dict):
            continue
        uid = user.get('id')
        email = (user.get('email') or '').strip()
        if uid and email and '@' in email:
            return user
        if uid:
            # email may be filled later via get_user_emails
            return user
    return None


def _job_owner_user(job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return job Owner when usable as Indeed response-user fallback.

    Skips blank / Unassigned-style placeholders so we do not publish with a
    dead response contact.
    """
    owner = job.get('owner')
    if not isinstance(owner, dict):
        return None
    uid = owner.get('id')
    if not uid:
        return None
    name = f"{owner.get('firstName') or ''} {owner.get('lastName') or ''}".strip().lower()
    if 'unassigned' in name:
        return None
    return owner


def _fingerprint(job: Dict[str, Any], category_id: int, response_user_id: int, campaign_tag: str) -> str:
    # Do NOT include dateLastModified — Publish itself bumps that field and
    # would cause every member to REPUBLISH on every sync cycle.
    # Fingerprint the final (tagged) description so first publish with a
    # campaign tag does not thrash on every subsequent sync.
    payload = '|'.join([
        str(job.get('id') or ''),
        str(job.get('title') or ''),
        apply_campaign_tag(_job_description_html(job), campaign_tag),
        str(category_id),
        str(response_user_id),
        str(campaign_tag or ''),
    ])
    return hashlib.sha256(payload.encode('utf-8', errors='ignore')).hexdigest()


def _load_state(cfg: Optional[dict] = None) -> Dict[str, Any]:
    from models import GlobalSettings
    raw = GlobalSettings.get_value(_cfg_state_key(cfg), '{}') or '{}'
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            data.setdefault('job_ids', [])
            data.setdefault('fingerprints', {})
            data.setdefault('pending_unpublish', [])
            data.setdefault('absent_once', [])
            data.setdefault('self_heal', {})
            return data
    except Exception:
        pass
    return {
        'job_ids': [],
        'fingerprints': {},
        'pending_unpublish': [],
        'absent_once': [],
        'self_heal': {},
    }


def _save_state(state: Dict[str, Any], cfg: Optional[dict] = None) -> None:
    from models import GlobalSettings
    state = dict(state)
    state.setdefault('job_ids', [])
    state.setdefault('fingerprints', {})
    state.setdefault('pending_unpublish', [])
    state.setdefault('absent_once', [])
    state.setdefault('self_heal', {})
    state['updated_at'] = _utc_now()
    key = _cfg_state_key(cfg)
    GlobalSettings.set_value(
        key,
        json.dumps(state),
        description=f'Indeed tearsheet publish membership + fingerprints ({key})',
        category='indeed_publish',
    )


def _pending_unpublish_ids(state: Dict[str, Any]) -> Set[int]:
    return {int(x) for x in (state.get('pending_unpublish') or [])}


def _absent_once_ids(state: Dict[str, Any]) -> Set[int]:
    return {int(x) for x in (state.get('absent_once') or [])}


def _guardrail_email_due(state: Dict[str, Any]) -> bool:
    then = _parse_utc(state.get('guardrail_email_at') or '')
    if not then:
        return True
    return datetime.now(timezone.utc) - then >= GUARDRAIL_EMAIL_AFTER


def _save_last_result(result: Dict[str, Any], cfg: Optional[dict] = None) -> None:
    from models import GlobalSettings
    GlobalSettings.set_value(
        _cfg_last_result_key(cfg),
        json.dumps(result),
        description='Last Indeed tearsheet publish sync result',
        category='indeed_publish',
    )


NOTIFY_COOLDOWN_SECONDS = 30 * 60
# A job-level problem (empty description, no recruiter) needs a person to fix
# the job in Bullhorn, so repeating it every 30 minutes is just noise.
JOB_NOTIFY_COOLDOWN_SECONDS = 24 * 60 * 60
_JOB_SUBJECT_RE = re.compile(r'\bjob \d+\b', re.IGNORECASE)


def _cooldown_seconds(subject: str) -> int:
    if _JOB_SUBJECT_RE.search(subject or ''):
        return JOB_NOTIFY_COOLDOWN_SECONDS
    return NOTIFY_COOLDOWN_SECONDS


def _parse_utc(at: str) -> Optional[datetime]:
    try:
        then = datetime.fromisoformat(str(at).strip().replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return then


def _load_notify_stamps() -> Dict[str, str]:
    """Subject → last-sent UTC timestamp. Accepts the old single-subject shape."""
    from models import GlobalSettings
    raw = GlobalSettings.get_value(NOTIFY_STAMP_KEY)
    if not raw:
        return {}
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get('sent'), dict):
        return {str(k): str(v) for k, v in data['sent'].items()}
    if data.get('subject') and data.get('at'):
        return {str(data['subject']): str(data['at'])}
    return {}


def _notify_on_cooldown(subject: str) -> bool:
    """True when this subject was already emailed within its cooldown window."""
    try:
        then = _parse_utc(_load_notify_stamps().get(subject) or '')
        if not then:
            return False
        age = (datetime.now(timezone.utc) - then).total_seconds()
        return age < _cooldown_seconds(subject)
    except Exception:
        return False


def _stamp_notify(subject: str) -> None:
    try:
        from models import GlobalSettings
        now = datetime.now(timezone.utc)
        stamps = {}
        for subj, at in _load_notify_stamps().items():
            then = _parse_utc(at)
            if then and (now - then).total_seconds() < JOB_NOTIFY_COOLDOWN_SECONDS:
                stamps[subj] = at
        stamps[subject] = _utc_now()
        GlobalSettings.set_value(
            NOTIFY_STAMP_KEY,
            json.dumps({'sent': stamps}),
            description='Indeed tearsheet publish failure emails sent (per-subject cooldown)',
            category='indeed_publish',
        )
    except Exception as exc:
        logger.warning('Indeed publish notify stamp failed: %s', exc)


def _notify_failure(subject: str, message: str, notify_email: str) -> None:
    if not notify_email:
        return
    if _notify_on_cooldown(subject):
        logger.info('Indeed publish notify skipped (cooldown): %s', subject)
        return
    try:
        from utils.bullhorn_helpers import get_email_service
        email_svc = get_email_service()
        if not email_svc:
            logger.warning('Indeed publish notify skipped — email service unavailable')
            return
        email_svc.send_notification_email(
            to_email=notify_email,
            subject=subject,
            message=message,
            notification_type='indeed_tearsheet_publish',
        )
        _stamp_notify(subject)
    except Exception as exc:
        logger.error('Indeed publish failure notify failed: %s', exc)


class IndeedTearsheetPublishService:
    """Diff Indeed tearsheet membership and drive JobPublish publish / unpublish."""

    def __init__(self, config: Optional[dict] = None, ui_client: Optional[BullhornUIClient] = None):
        self.config = config or config_from_env()
        self.ui_client = ui_client
        self.tearsheet_id = int(self.config.get('tearsheet_id') or TEARSHEET_ID)

    def _build_ui_client(self) -> BullhornUIClient:
        if self.ui_client:
            return self.ui_client
        return BullhornUIClient(
            base_url=self.config['base_url'],
            username=self.config['username'],
            password=self.config['password'],
            private_label_id=self.config['private_label_id'],
            encryption_key=self.config['encryption_key'],
            current_user_id=self.config.get('current_user_id'),
        )

    def _job_url(self, job_id: int) -> str:
        tmpl = self.config.get('job_url_template')
        if tmpl is None:
            tmpl = 'https://myticas.com/jobs/{job_id}'
        tmpl = str(tmpl).strip()
        if not tmpl:
            return ''
        return tmpl.format(job_id=job_id)

    def _read_member_ids(self, bh) -> Set[int]:
        """Paginate tearsheet jobOrders and require the row count to match total.

        A short page, a missing total, or an HTTP error raises
        ``TearsheetMembershipError``. Callers must not unpublish from that read.
        """
        ids: List[int] = []
        start = 0
        expected: Optional[int] = None
        pages = 0
        while True:
            pages += 1
            if pages > 50:
                raise TearsheetMembershipError('tearsheet read exceeded page limit')
            try:
                response = _rest_get(
                    bh,
                    f'entity/Tearsheet/{int(self.tearsheet_id)}/jobOrders',
                    {
                        'fields': 'id',
                        'count': MEMBERSHIP_PAGE_SIZE,
                        'start': start,
                        'showTotalMatched': 'true',
                    },
                    60,
                )
            except Exception as exc:
                raise TearsheetMembershipError(sanitize_leak_error(exc)) from None
            if response.status_code != 200:
                raise TearsheetMembershipError(
                    f'tearsheet read HTTP {response.status_code}'
                )
            try:
                payload = response.json()
            except Exception as exc:
                raise TearsheetMembershipError(sanitize_leak_error(exc)) from None
            if not isinstance(payload, dict) or payload.get('total') is None:
                raise TearsheetMembershipError('tearsheet read missing total')
            try:
                total = int(payload.get('total'))
            except (TypeError, ValueError):
                raise TearsheetMembershipError('tearsheet read missing total') from None
            if expected is None:
                expected = total
            elif total != expected:
                raise TearsheetMembershipError('tearsheet total changed during read')
            batch = payload.get('data') or []
            if not isinstance(batch, list):
                raise TearsheetMembershipError('tearsheet read malformed')
            if not batch:
                if start >= total:
                    break
                raise TearsheetMembershipError('tearsheet read ended before total')
            if len(batch) < MEMBERSHIP_PAGE_SIZE and start + len(batch) < total:
                raise TearsheetMembershipError('tearsheet read short page')
            for row in batch:
                if isinstance(row, dict) and row.get('id'):
                    ids.append(int(row['id']))
            start += len(batch)
            if start >= total:
                break
        unique: List[int] = []
        seen: Set[int] = set()
        for jid in ids:
            if jid not in seen:
                seen.add(jid)
                unique.append(jid)
        if len(unique) != expected:
            raise TearsheetMembershipError(
                f'tearsheet read count {len(unique)} != total {expected}'
            )
        return set(unique)

    def _fetch_tearsheet_jobs(self, bh) -> List[Dict[str, Any]]:
        """Return job payloads. Membership ids stay authoritative when a detail fetch fails."""
        member_ids = self._read_member_ids(bh)
        self._membership_ids = set(member_ids)
        failed: Set[int] = set()
        jobs: List[Dict[str, Any]] = []
        for jid in sorted(member_ids):
            job = self._fetch_job_detail(bh, jid)
            if job:
                jobs.append(job)
            else:
                failed.add(jid)
        self._detail_failed = failed
        return jobs

    def _fetch_job_detail(self, bh, job_id: int) -> Optional[Dict[str, Any]]:
        if not bh.base_url or not bh.rest_token:
            if not bh.authenticate():
                return None
        fields = (
            'id,title,description,publicDescription,dateLastModified,status,isOpen,'
            'isJobcastPublished,categories(id,name),'
            'assignedUsers(id,firstName,lastName,email),'
            'owner(id,firstName,lastName,email),'
            'publishedCategory(id,name),responseUser(id,firstName,lastName,email),'
            'correlatedCustomText1'
        )
        try:
            resp = _rest_get(
                bh,
                f'entity/JobOrder/{int(job_id)}',
                {'fields': fields},
                45,
            )
            if resp.status_code != 200:
                logger.warning('Indeed publish: job %s fetch HTTP %s', job_id, resp.status_code)
                return None
            data = resp.json().get('data') or {}
            return data if data.get('id') else None
        except Exception as exc:
            logger.warning(
                'Indeed publish: job %s fetch error: %s',
                job_id,
                sanitize_leak_error(exc),
            )
            return None

    def _confirm_absent(self, bh, job_id: int) -> Optional[bool]:
        """True when the job is not on this tearsheet.

        False when it is. None when the check failed. The caller must not DELETE.

        Bullhorn rejects ``entity/JobOrder/{id}/tearsheets/{tearsheetId}`` as a
        bad command for members and non-members (live Qualified check, Oct 30:
        HTTP 400 ``errors.badCommand`` on 79512, 79367, and 49606). The job's
        tearsheets association can tell them apart: 79512 included tearsheet 2,
        49606 did not.
        """
        start = 0
        page_size = 100
        seen: Set[int] = set()
        while start <= 500:
            try:
                response = _rest_get(
                    bh,
                    f'entity/JobOrder/{int(job_id)}/tearsheets',
                    {'fields': 'id', 'count': page_size, 'start': start},
                    30,
                )
            except Exception as exc:
                logger.warning(
                    'Indeed publish: membership reconfirm failed for job %s: %s',
                    job_id,
                    sanitize_leak_error(exc),
                )
                return None
            if response.status_code == 404:
                if start == 0 and _association_404_is_empty(response):
                    return True
                logger.warning(
                    'Indeed publish: membership reconfirm HTTP 404 for job %s',
                    job_id,
                )
                return None
            if response.status_code != 200:
                logger.warning(
                    'Indeed publish: membership reconfirm HTTP %s for job %s',
                    response.status_code,
                    job_id,
                )
                return None
            try:
                payload = response.json()
            except Exception as exc:
                logger.warning(
                    'Indeed publish: membership reconfirm unreadable for job %s: %s',
                    job_id,
                    sanitize_leak_error(exc),
                )
                return None
            data = payload.get('data') if isinstance(payload, dict) else None
            if not isinstance(data, list):
                return None
            for row in data:
                if not isinstance(row, dict) or row.get('id') in (None, ''):
                    continue
                try:
                    seen.add(int(row['id']))
                except (TypeError, ValueError):
                    return None
            if len(data) < page_size:
                return int(self.tearsheet_id) not in seen
            start += len(data)
        logger.warning(
            'Indeed publish: membership reconfirm for job %s did not finish',
            job_id,
        )
        return None

    def _save_membership_state(
        self,
        state: Dict[str, Any],
        saved_ids: Set[int],
        fingerprints: Dict[str, str],
        pending_unpublish: Set[int],
        absent_once: Set[int],
    ) -> None:
        payload = dict(state)
        payload.update({
            'job_ids': sorted(saved_ids),
            'fingerprints': {
                k: v for k, v in fingerprints.items()
                if int(k) in saved_ids
            },
            'pending_unpublish': sorted(pending_unpublish),
            'absent_once': sorted(absent_once),
        })
        _save_state(payload, self.config)

    def _self_heal_enabled(self) -> bool:
        from feeds.feed_config import QUALIFIED_TEARSHEET_INDEED
        return (
            is_qualified_tenant()
            and int(self.tearsheet_id) == int(QUALIFIED_TEARSHEET_INDEED)
        )

    def _self_heal_due(self, state: Dict[str, Any]) -> bool:
        heal = state.get('self_heal') if isinstance(state.get('self_heal'), dict) else {}
        then = _parse_utc((heal or {}).get('last_run_at') or '')
        if not then:
            return True
        return then.astimezone(EASTERN).date() != _eastern_now().date()

    def _maybe_email_guardrail(
        self, state: Dict[str, Any], missing: Set[int], prev_count: int
    ) -> None:
        if not _guardrail_email_due(state):
            return
        ids = ', '.join(str(jid) for jid in sorted(missing))
        _notify_failure(
            'Indeed tearsheet publish: skipped a large membership drop',
            (
                f'Tearsheet {self.tearsheet_id}: {len(missing)} of {prev_count} previously '
                'tracked jobs were missing on one read (more than 10% and at least 5). '
                'No jobs were unpublished this cycle. Additions and republishes still ran.\n\n'
                f'Missing job ids: {ids}'
            ),
            self.config.get('notify_email') or '',
        )
        state['guardrail_email_at'] = _utc_now()

    def _self_heal(
        self,
        ui: BullhornUIClient,
        bh,
        jobs_by_id: Dict[int, Dict[str, Any]],
        state: Dict[str, Any],
        fingerprints: Dict[str, str],
        result: Dict[str, Any],
    ) -> None:
        """Once a day, ADDCHANGE tearsheet 2 jobs Bullhorn says are not on Indeed."""
        if not self._self_heal_enabled() or not self._self_heal_due(state):
            return
        heal = state.get('self_heal')
        if not isinstance(heal, dict):
            heal = {}
        jobs_state = heal.get('jobs')
        if not isinstance(jobs_state, dict):
            jobs_state = {}
        heal['jobs'] = jobs_state
        state['self_heal'] = heal
        sent: List[int] = []
        reported: List[int] = []
        now = _utc_now()
        now_dt = _parse_utc(now)
        today = _eastern_now().date()
        for jid in sorted(jobs_by_id):
            if jid in result['published'] or jid in result['republished']:
                continue
            try:
                status = ui.get_publication_status(jid)
            except Exception as exc:
                logger.warning(
                    'Indeed publish: self-heal status failed for job %s: %s',
                    jid,
                    sanitize_leak_error(exc),
                )
                continue
            if not isinstance(status, dict) or 'indeed_published' not in status:
                continue
            key = str(jid)
            if status.get('indeed_published'):
                jobs_state.pop(key, None)
                continue
            record = jobs_state.get(key)
            if not isinstance(record, dict):
                record = {}
            if not record.get('first_not_published_at'):
                record['first_not_published_at'] = now
            last_send = _parse_utc(record.get('last_addchange_at') or '')
            already_today = bool(
                last_send and last_send.astimezone(EASTERN).date() == today
            )
            if not already_today:
                try:
                    fp = self._publish_one(
                        ui, bh, jobs_by_id[jid], result, operation='ADDCHANGE'
                    )
                except BullhornPublishUnavailable:
                    jobs_state[key] = record
                    raise
                except Exception as exc:
                    record['last_error'] = sanitize_leak_error(exc)
                    jobs_state[key] = record
                    result['errors'].append(
                        f'self-heal {jid}: {sanitize_leak_error(exc)}'
                    )
                    continue
                if fp:
                    fingerprints[str(jid)] = fp
                record['last_addchange_at'] = now
                record.pop('last_error', None)
                sent.append(jid)
            first = _parse_utc(record.get('first_not_published_at') or '')
            if (
                record.get('last_addchange_at')
                and first
                and now_dt
                and now_dt - first >= SELF_HEAL_REPORT_AFTER
            ):
                reported.append(jid)
            jobs_state[key] = record
        heal['last_run_at'] = now
        result['self_heal_sent'] = sent
        result['self_heal_reported'] = reported
        if not reported:
            return
        lines = []
        for jid in reported:
            rec = jobs_state.get(str(jid)) or {}
            lines.append(
                f"Job {jid}: not published since {rec.get('first_not_published_at')}, "
                f"last ADDCHANGE {rec.get('last_addchange_at')}."
            )
        _notify_failure(
            'Indeed tearsheet: jobs still not published',
            (
                'These Sponsored - Indeed jobs are still not published on Indeed after '
                '2 days. Scout re-sent ADDCHANGE. This points at the Bullhorn-to-Indeed '
                'connection.\n\n' + '\n'.join(lines)
            ),
            self.config.get('notify_email') or '',
        )

    def _resolve_user_email(self, bh, user: Dict[str, Any]) -> str:
        email = (user.get('email') or '').strip()
        uid = user.get('id')
        if email and '@' in email:
            return email
        if not uid or not hasattr(bh, 'get_user_emails'):
            return email
        try:
            emails = bh.get_user_emails([int(uid)]) or {}
            info = emails.get(int(uid)) or emails.get(str(uid))
            if isinstance(info, dict):
                return (info.get('email') or '').strip()
            if isinstance(info, str):
                return info.strip()
        except Exception:
            pass
        return email

    def _resolve_response_user(
        self, bh, job: Dict[str, Any]
    ) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        """Return (user_id, email, error_reason).

        Prefer the first assigned recruiter. If none, fall back to job Owner so
        tearsheet jobs can still publish to Indeed when Recruiter is blank
        (Recruiter is not mandatory in Bullhorn).
        """
        candidates: List[Tuple[str, Dict[str, Any]]] = []
        recruiter = _first_assigned_recruiter(job)
        if recruiter:
            candidates.append(('assigned recruiter', recruiter))
        owner = _job_owner_user(job)
        if owner:
            # Avoid listing the same person twice when they are both recruiter and owner.
            recruiter_id = (recruiter or {}).get('id')
            if not recruiter_id or int(owner.get('id')) != int(recruiter_id):
                candidates.append(('job owner', owner))

        if not candidates:
            return None, None, 'no assigned recruiter or usable job owner'

        last_err = 'no assigned recruiter or usable job owner'
        for role, user in candidates:
            uid = user.get('id')
            if not uid:
                last_err = f'{role} missing id'
                continue
            try:
                uid_int = int(uid)
            except (TypeError, ValueError):
                last_err = f'{role} missing id'
                continue
            email = self._resolve_user_email(bh, user)
            if not email or '@' not in email:
                last_err = f'{role} {uid_int} missing email'
                continue
            if role == 'job owner':
                logger.info(
                    'Indeed publish: job %s using job owner %s as response user '
                    '(no assigned recruiter)',
                    job.get('id'),
                    uid_int,
                )
            return uid_int, email, None
        return None, None, last_err

    def run_sync(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            'timestamp': _utc_now(),
            'enabled': bool(self.config.get('enabled')),
            'published': [],
            'republished': [],
            'unpublished': [],
            'skipped': [],
            'errors': [],
        }

        if not self.config.get('enabled'):
            result['message'] = 'disabled (INDEED_TEARSHEET_PUBLISH_ENABLED=false)'
            _save_last_result(result, self.config)
            logger.info('indeed_tearsheet_publish: disabled — skipping')
            return result

        if not self.config.get('username') or not self.config.get('password'):
            result['errors'].append('missing BH_UI_USERNAME / BH_UI_PASSWORD')
            _save_last_result(result, self.config)
            _notify_failure(
                'Indeed tearsheet publish: missing UI credentials',
                'Set BH_UI_USERNAME and BH_UI_PASSWORD on Railway.',
                self.config.get('notify_email') or '',
            )
            return result

        from utils.bullhorn_helpers import get_bullhorn_service
        bh = get_bullhorn_service()
        if not bh or not bh.authenticate():
            result['errors'].append('Bullhorn REST auth failed')
            _save_last_result(result, self.config)
            return result

        self._membership_ids = None
        self._detail_failed = set()
        try:
            jobs = self._fetch_tearsheet_jobs(bh)
        except Exception as exc:
            result['errors'].append(
                f'tearsheet fetch failed: {sanitize_leak_error(exc)}'
            )
            result['membership_read'] = 'failed'
            _save_last_result(result, self.config)
            logger.warning(
                'indeed_tearsheet_publish: membership read failed: %s',
                sanitize_leak_error(exc),
            )
            return result

        jobs_by_id: Dict[int, Dict[str, Any]] = {}
        fetched_ids: Set[int] = set()
        for job in jobs:
            jid = job.get('id')
            if not jid:
                continue
            jid = int(jid)
            fetched_ids.add(jid)
            jobs_by_id[jid] = job

        state = _load_state(self.config)
        prev_ids = {int(x) for x in (state.get('job_ids') or [])}
        fingerprints = {
            str(k): v for k, v in (state.get('fingerprints') or {}).items()
        }
        pending_unpublish = _pending_unpublish_ids(state)
        absent_once = _absent_once_ids(state)
        if self._membership_ids is None:
            # Tests and callers that supply the job list directly.
            member_ids = set(fetched_ids)
            detail_failed: Set[int] = set()
        else:
            member_ids = set(self._membership_ids)
            detail_failed = set(self._detail_failed or set())
        # A failed per-job fetch stays a member. It is not a removal.
        visible = (member_ids - detail_failed) | (detail_failed & prev_ids)
        result['membership_read'] = 'ok'
        result['detail_failed'] = sorted(detail_failed)

        # Back on the tearsheet: drop the absence mark and any queued DELETE.
        absent_once -= visible
        pending_unpublish -= visible

        missing = prev_ids - visible
        guardrail = (
            len(missing) >= MASS_DROP_MIN_JOBS
            and len(missing) > MASS_DROP_FRACTION * len(prev_ids)
        )
        to_add = (member_ids - detail_failed) - prev_ids
        to_check = (visible & prev_ids) - detail_failed
        unpublish_targets: Set[int] = set()
        if guardrail:
            # Do not record the drop as a first absence. The next cycle must
            # not DELETE just because this read was short.
            result['guardrail'] = 'membership_drop'
            result['guardrail_missing'] = sorted(missing)
            self._maybe_email_guardrail(state, missing, len(prev_ids))
            saved_ids = set(prev_ids) | visible
        else:
            first_absence = missing - absent_once
            confirmed = missing & absent_once
            absent_once |= first_absence
            kept: Set[int] = set()
            for jid in sorted(confirmed | set(pending_unpublish)):
                verdict = self._confirm_absent(bh, jid)
                if verdict is None:
                    result['skipped'].append({
                        'job_id': jid,
                        'reason': 'membership reconfirm failed',
                    })
                    if jid not in pending_unpublish:
                        kept.add(jid)
                    continue
                if verdict is False:
                    absent_once.discard(jid)
                    pending_unpublish.discard(jid)
                    kept.add(jid)
                    continue
                absent_once.discard(jid)
                unpublish_targets.add(jid)
            saved_ids = set(visible) | first_absence | kept
            saved_ids -= pending_unpublish

        ui = self._build_ui_client()
        try:
            ui.login_with_retry()
            if self.config.get('current_user_id'):
                ui.set_current_user_id(self.config['current_user_id'])
            elif not ui.current_user_id:
                # Resolve CorporateUser id via REST username match when possible
                resolved = self._resolve_ui_user_id(bh)
                if resolved:
                    ui.set_current_user_id(resolved)
        except BullhornUIClientError as exc:
            # The membership read succeeded. Keep the absence mark so the next
            # cycle can confirm a real removal; do not unpublish without a login.
            result['errors'].append(f'UI login failed: {sanitize_leak_error(exc)}')
            self._save_membership_state(
                state, saved_ids, fingerprints, pending_unpublish, absent_once
            )
            _save_last_result(result, self.config)
            _notify_failure(
                'Indeed tearsheet publish: Bullhorn UI login failed',
                sanitize_leak_error(exc),
                self.config.get('notify_email') or '',
            )
            return result

        outage: Optional[BullhornPublishUnavailable] = None

        # Unpublish removals + pending retries first
        for jid in sorted(unpublish_targets):
            if outage:
                pending_unpublish.add(jid)
                continue
            try:
                job = self._fetch_job_detail(bh, jid) or {'id': jid}
                self._unpublish_one(ui, bh, job, result)
                fingerprints.pop(str(jid), None)
                pending_unpublish.discard(jid)
            except BullhornPublishUnavailable as exc:
                outage = exc
                pending_unpublish.add(jid)
            except Exception as exc:
                pending_unpublish.add(jid)
                msg = f'unpublish {jid}: {sanitize_leak_error(exc)}'
                result['errors'].append(msg)
                _notify_failure(
                    f'Indeed unpublish failed for job {jid}',
                    msg,
                    self.config.get('notify_email') or '',
                )

        # Publish new members.
        # Myticas: REPUBLISH for membership adds (PUBLISH leaves jobs unpublished).
        # Qualified: ADDCHANGE for first publish / return after unpublish (live capture).
        add_op = (
            self.config.get('membership_publish_operation') or 'REPUBLISH'
        ).strip() or 'REPUBLISH'
        republish_op = (
            self.config.get('republish_operation') or 'REPUBLISH'
        ).strip() or 'REPUBLISH'
        for jid in sorted(to_add):
            if outage:
                break
            job = jobs_by_id[jid]
            try:
                fp = self._publish_one(ui, bh, job, result, operation=add_op)
                if fp:
                    fingerprints[str(jid)] = fp
                    result['published'].append(jid)
            except BullhornPublishUnavailable as exc:
                outage = exc
            except Exception as exc:
                msg = f'publish {jid}: {sanitize_leak_error(exc)}'
                result['errors'].append(msg)
                result['skipped'].append({
                    'job_id': jid,
                    'reason': sanitize_leak_error(exc),
                })
                _notify_failure(
                    f'Indeed publish failed for job {jid}',
                    msg,
                    self.config.get('notify_email') or '',
                )

        # Republish when fingerprint changed
        for jid in sorted(to_check):
            if outage:
                break
            job = jobs_by_id[jid]
            try:
                tag, tag_reason = campaign_tag_for_job(
                    job, qualified=is_qualified_tenant()
                )
                manual_tag = is_manual_campaign_reason(tag_reason)
                if not tag and not manual_tag:
                    result['skipped'].append({'job_id': jid, 'reason': tag_reason})
                    continue
                if manual_tag:
                    tag = ''
                cat_id, _, _ = map_published_category(
                    job, qualified=is_qualified_tenant()
                )
                uid, _, err = self._resolve_response_user(bh, job)
                if err or not uid:
                    result['skipped'].append({'job_id': jid, 'reason': err or 'no recruiter'})
                    continue
                fp = _fingerprint(job, cat_id, uid, tag)
                if fingerprints.get(str(jid)) == fp:
                    continue
                new_fp = self._publish_one(ui, bh, job, result, operation=republish_op)
                if new_fp:
                    fingerprints[str(jid)] = new_fp
                    result['republished'].append(jid)
            except BullhornPublishUnavailable as exc:
                outage = exc
            except Exception as exc:
                msg = f'republish {jid}: {sanitize_leak_error(exc)}'
                result['errors'].append(msg)
                _notify_failure(
                    f'Indeed republish failed for job {jid}',
                    msg,
                    self.config.get('notify_email') or '',
                )

        if outage is None and not guardrail:
            heal_jobs = {
                jid: job for jid, job in jobs_by_id.items()
                if jid in saved_ids and jid not in unpublish_targets
            }
            try:
                self._self_heal(ui, bh, heal_jobs, state, fingerprints, result)
            except BullhornPublishUnavailable as exc:
                outage = exc

        if outage:
            # Unprocessed adds have no fingerprint yet, so the next cycle
            # republishes them; unprocessed removals sit in pending_unpublish.
            result['errors'].append(
                'Bullhorn publishing unavailable — cycle stopped: '
                + sanitize_leak_error(outage)
            )
            result['outage'] = True
            _notify_failure(
                'Indeed tearsheet publish: Bullhorn publishing unavailable',
                'Stopped this cycle after the first failure; will retry next cycle.\n\n'
                + sanitize_leak_error(outage),
                self.config.get('notify_email') or '',
            )

        # Failed DELETEs leave the job out of job_ids and in pending_unpublish.
        # A first absence stays in job_ids so the next full read is not a re-ADD.
        saved_ids -= {
            jid for jid in unpublish_targets
            if jid not in pending_unpublish or jid in result['unpublished']
        }
        saved_ids -= set(result['unpublished'])
        saved_ids -= pending_unpublish
        self._save_membership_state(
            state, saved_ids, fingerprints, pending_unpublish, absent_once
        )
        result['pending_unpublish'] = sorted(pending_unpublish)
        result['absent_once'] = sorted(absent_once)
        result['tearsheet_id'] = self.tearsheet_id
        _save_last_result(result, self.config)
        logger.info(
            'indeed_tearsheet_publish: done published=%s republished=%s unpublished=%s '
            'pending_unpublish=%s errors=%s',
            len(result['published']),
            len(result['republished']),
            len(result['unpublished']),
            len(pending_unpublish),
            len(result['errors']),
        )
        return result

    def _resolve_ui_user_id(self, bh) -> Optional[int]:
        username = (self.config.get('username') or '').strip()
        if not username or not bh.base_url or not bh.rest_token:
            return None
        try:
            safe_username = username.replace(chr(39), '')
            resp = _rest_get(
                bh,
                'query/CorporateUser',
                {
                    'where': f"username='{safe_username}'",
                    'fields': 'id,username,email',
                    'count': 5,
                },
                30,
            )
            if resp.status_code != 200:
                return None
            rows = resp.json().get('data') or []
            if rows and rows[0].get('id'):
                return int(rows[0]['id'])
        except Exception as exc:
            logger.warning(
                'Could not resolve BH_UI current user id: %s',
                sanitize_leak_error(exc),
            )
        return None

    def _persist_campaign_description(
        self, bh, job: Dict[str, Any], tagged_desc: str, campaign_tag: str
    ) -> None:
        """Write tagged description back to the Bullhorn field we published from."""
        jid = int(job['id'])
        field = _description_source_field(job)
        current = (job.get(field) or '')
        # Skip when already correctly tagged. Force rewrite if a department
        # tag is required but a leftover Myticas #INDShow is still present.
        stale_myticas = (
            bool(campaign_tag)
            and campaign_tag != INDSHOW_TAG
            and INDSHOW_TAG in current
        )
        if (
            campaign_tag
            and current.rstrip().endswith(campaign_tag)
            and not stale_myticas
        ):
            return
        if not hasattr(bh, 'update_job_order'):
            logger.warning(
                'Indeed publish: cannot persist %s on job %s — no update_job_order',
                field,
                jid,
            )
            return
        try:
            ok = bh.update_job_order(jid, {field: tagged_desc})
            if ok:
                job[field] = tagged_desc
                logger.info(
                    'Indeed publish: persisted %s with %s on job %s',
                    field,
                    campaign_tag,
                    jid,
                )
            else:
                logger.warning(
                    'Indeed publish: Bullhorn REST update of %s failed for job %s',
                    field,
                    jid,
                )
        except Exception as exc:
            logger.warning(
                'Indeed publish: could not persist %s on job %s %s: %s',
                campaign_tag,
                jid,
                field,
                exc,
            )

    def _publish_one(
        self,
        ui: BullhornUIClient,
        bh,
        job: Dict[str, Any],
        result: Dict[str, Any],
        *,
        operation: str,
    ) -> Optional[str]:
        jid = int(job['id'])
        campaign_tag, tag_reason = campaign_tag_for_job(
            job, qualified=is_qualified_tenant()
        )
        manual_tag = is_manual_campaign_reason(tag_reason)
        if not campaign_tag and not manual_tag:
            raise BullhornUIClientError(tag_reason)
        if manual_tag:
            campaign_tag = ''

        cat_id, cat_name, reason = map_published_category(
            job, qualified=is_qualified_tenant()
        )
        uid, email, err = self._resolve_response_user(bh, job)
        if err or not uid:
            raise BullhornUIClientError(err or 'cannot resolve published contact')

        raw_desc = _job_description_html(job)
        if not raw_desc:
            raise BullhornUIClientError('job has empty description/publicDescription')
        desc = raw_desc if manual_tag else apply_campaign_tag(raw_desc, campaign_tag)

        logger.info(
            'Indeed %s job %s category=%s (%s) contact=%s <%s> via %s tag=%s (%s)',
            operation,
            jid,
            cat_name,
            cat_id,
            uid,
            email,
            reason,
            campaign_tag,
            tag_reason,
        )
        ui.publish_boards(
            job_id=jid,
            published_category_id=cat_id,
            response_user_id=uid,
            description_html=desc,
            job_url=self._job_url(jid),
            operation=operation,
        )
        if not manual_tag and (desc != raw_desc or INDSHOW_TAG in raw_desc):
            self._persist_campaign_description(bh, job, desc, campaign_tag)
        time.sleep(0.2)
        return _fingerprint(job, cat_id, uid, campaign_tag)

    def _unpublish_one(
        self,
        ui: BullhornUIClient,
        bh,
        job: Dict[str, Any],
        result: Dict[str, Any],
    ) -> None:
        jid = int(job.get('id'))
        cat_id = 0
        uid = 0
        try:
            cat_id, _, _ = map_published_category(
                job, qualified=is_qualified_tenant()
            )
            resolved_uid, _, _ = self._resolve_response_user(bh, job)
            uid = resolved_uid or 0
        except Exception:
            pass
        pub = job.get('publishedCategory') or {}
        if isinstance(pub, dict) and pub.get('id'):
            cat_id = int(pub['id'])
        resp_user = job.get('responseUser') or {}
        if isinstance(resp_user, dict) and resp_user.get('id'):
            uid = int(resp_user['id'])

        try:
            from indeed_publish.leak_runtime import strip_campaign_tag_on_tearsheet_leave
            strip_campaign_tag_on_tearsheet_leave(bh, job, self.tearsheet_id)
        except Exception as exc:
            logger.warning('Indeed leak: campaign tag strip skipped for job %s: %s', jid, exc)
        ui.unpublish_boards(
            job_id=jid,
            published_category_id=cat_id,
            response_user_id=uid,
            description_html=_job_description_html(job),
            job_url=self._job_url(jid),
        )
        result['unpublished'].append(jid)
        time.sleep(0.2)


def _update_state_after_unpublish(
    job_id: int, *, success: bool, cfg: Optional[dict] = None
) -> None:
    """Adjust membership / pending_unpublish after a one-off unpublish attempt."""
    cfg = cfg or config_from_env()
    state = _load_state(cfg)
    jid = int(job_id)
    ids = [int(x) for x in (state.get('job_ids') or []) if int(x) != jid]
    fps = dict(state.get('fingerprints') or {})
    fps.pop(str(jid), None)
    pending = _pending_unpublish_ids(state)
    if success:
        pending.discard(jid)
    else:
        pending.add(jid)
    absent = _absent_once_ids(state)
    absent.discard(jid)
    state['job_ids'] = ids
    state['fingerprints'] = fps
    state['pending_unpublish'] = sorted(pending)
    state['absent_once'] = sorted(absent)
    _save_state(state, cfg)


def unpublish_job_after_tearsheet_remove(job_id: int, tearsheet_id: int) -> bool:
    """
    Hook for incremental monitor auto-remove: full Unpublish when leaving the
    tenant's Indeed tearsheet (1640 Myticas/STSI, 2 Qualified).
    Best-effort; failures are logged + emailed and do not raise.
    Failed attempts are retained in pending_unpublish for the next sync retry.
    """
    cfg = config_from_env()
    if int(tearsheet_id) != int(cfg.get('tearsheet_id') or TEARSHEET_ID):
        return False

    try:
        from feeds.feed_config import QUALIFIED_TEARSHEET_INDEED, is_qualified_tenant
        from indeed_publish.leak import MODE_ENFORCE, leak_mode
        if (
            leak_mode() == MODE_ENFORCE
            and is_qualified_tenant()
            and int(tearsheet_id) == int(QUALIFIED_TEARSHEET_INDEED)
        ):
            from indeed_publish.leak_runtime import strip_campaign_tag_on_tearsheet_leave
            from utils.bullhorn_helpers import get_bullhorn_service
            bh = get_bullhorn_service()
            if bh and bh.authenticate():
                svc = IndeedTearsheetPublishService(config=cfg)
                job = svc._fetch_job_detail(bh, int(job_id)) or {'id': int(job_id)}
                strip_campaign_tag_on_tearsheet_leave(bh, job, int(tearsheet_id))
    except Exception as exc:
        logger.warning(
            'Indeed leak: campaign tag strip skipped for job %s: %s', job_id, exc
        )

    if not cfg.get('enabled'):
        logger.info(
            'indeed auto-unpublish skipped for job %s — feature disabled', job_id
        )
        return False
    if not cfg.get('username') or not cfg.get('password'):
        logger.warning('indeed auto-unpublish skipped — UI credentials missing')
        return False

    try:
        from utils.bullhorn_helpers import get_bullhorn_service
        bh = get_bullhorn_service()
        if not bh or not bh.authenticate():
            raise BullhornUIClientError('REST auth failed')

        svc = IndeedTearsheetPublishService(config=cfg)
        job = svc._fetch_job_detail(bh, int(job_id)) or {'id': int(job_id)}
        ui = svc._build_ui_client()
        ui.login_with_retry()
        if not ui.current_user_id:
            resolved = svc._resolve_ui_user_id(bh)
            if resolved:
                ui.set_current_user_id(resolved)

        result = {'unpublished': [], 'errors': []}
        svc._unpublish_one(ui, bh, job, result)

        # Drop from membership state so next sync doesn't double-unpublish
        _update_state_after_unpublish(int(job_id), success=True, cfg=cfg)
        logger.info('indeed auto-unpublish succeeded for job %s', job_id)
        return True
    except Exception as exc:
        logger.error('indeed auto-unpublish failed for job %s: %s', job_id, exc)
        try:
            _update_state_after_unpublish(int(job_id), success=False, cfg=cfg)
        except Exception as state_exc:
            logger.error(
                'indeed auto-unpublish could not persist pending retry for %s: %s',
                job_id,
                state_exc,
            )
        _notify_failure(
            f'Indeed auto-unpublish failed for job {job_id}',
            str(exc),
            cfg.get('notify_email') or '',
        )
        return False


def force_unpublish_jobs(job_ids: List[int]) -> Dict[str, Any]:
    """
    One-off recovery: unpublish specific jobs via Bullhorn JobPublish
    and clear them from membership / pending_unpublish state on success.

    Use when jobs were published to Indeed but never recorded in (or were
    dropped from) sync state — e.g. manual Manage Tearsheets removal that
    raced a failed unpublish before pending_unpublish existed.
    """
    cfg = config_from_env()
    out: Dict[str, Any] = {
        'timestamp': _utc_now(),
        'requested': [int(x) for x in job_ids],
        'unpublished': [],
        'errors': [],
        'skipped': [],
    }
    if not cfg.get('enabled'):
        out['skipped'].append('feature disabled (INDEED_TEARSHEET_PUBLISH_ENABLED=false)')
        return out
    if not cfg.get('username') or not cfg.get('password'):
        out['errors'].append('missing BH_UI_USERNAME / BH_UI_PASSWORD')
        return out

    from utils.bullhorn_helpers import get_bullhorn_service
    bh = get_bullhorn_service()
    if not bh or not bh.authenticate():
        out['errors'].append('Bullhorn REST auth failed')
        return out

    svc = IndeedTearsheetPublishService(config=cfg)
    ui = svc._build_ui_client()
    try:
        ui.login_with_retry()
        if cfg.get('current_user_id'):
            ui.set_current_user_id(cfg['current_user_id'])
        elif not ui.current_user_id:
            resolved = svc._resolve_ui_user_id(bh)
            if resolved:
                ui.set_current_user_id(resolved)
    except BullhornUIClientError as exc:
        out['errors'].append(f'UI login failed: {exc}')
        return out

    for raw_id in job_ids:
        jid = int(raw_id)
        try:
            job = svc._fetch_job_detail(bh, jid) or {'id': jid}
            result = {'unpublished': [], 'errors': []}
            svc._unpublish_one(ui, bh, job, result)
            _update_state_after_unpublish(jid, success=True, cfg=cfg)
            out['unpublished'].append(jid)
            logger.info('indeed force-unpublish succeeded for job %s', jid)
        except Exception as exc:
            try:
                _update_state_after_unpublish(jid, success=False, cfg=cfg)
            except Exception:
                pass
            msg = f'unpublish {jid}: {exc}'
            out['errors'].append(msg)
            logger.error('indeed force-unpublish failed for job %s: %s', jid, exc)
            _notify_failure(
                f'Indeed force-unpublish failed for job {jid}',
                msg,
                cfg.get('notify_email') or '',
            )
    return out
