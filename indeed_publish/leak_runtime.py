"""Bullhorn and scheduler wiring for the Qualified Indeed leak check.

Myticas/STSI is unchanged: the sweep, the applicant fast path, and the Monday
digest return immediately unless this process is the Qualified tenant.
"""

from __future__ import annotations

import fcntl
import html
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set

import requests

from feeds.feed_config import QUALIFIED_TEARSHEET_INDEED, is_qualified_tenant

from .config import DEFAULT_NOTIFY_EMAIL, config_from_env
from .leak import (
    STATE_KEY,
    INDEED_JOB_BOARD_SOURCE,
    LeakRun,
    cannot_publish_reason,
    indeed_board_applications,
    indeed_delete_description,
    description_source_field,
    digest_buckets,
    digest_is_due,
    exempt_departments,
    exempt_job_ids,
    grace_hours,
    jobs_with_recent_indeed_applications,
    deep_sweep_where,
    leak_bcc_recipients,
    leak_mode,
    notify_email,
    render_digest,
    sanitize_leak_error,
    select_deep_sweep_jobs,
    select_sweep_jobs,
    stripped_description_update,
)
from .ui_client import BullhornUIClient

logger = logging.getLogger(__name__)

JOB_FIELDS = (
    'id,title,status,isOpen,isDeleted,dateLastModified,correlatedCustomText1,'
    'description,publicDescription,'
    'owner(id,firstName,lastName),'
    'assignedUsers(id,firstName,lastName),'
    'dateClosed'
)
FAST_PATH_LOOKBACK_MINUTES = 15
# Sweeps wait this long for another leak run, then skip without saving.
SWEEP_LOCK_WAIT_SECONDS = 120
# Stable Postgres advisory-lock key for indeed_leak_state. Not a secret.
_ADVISORY_LOCK_KEY = 441702021701
_LOCK_LOCAL = threading.local()


def qualified_leak_active() -> bool:
    return is_qualified_tenant() and leak_mode() != 'off'


def strip_campaign_tag_on_tearsheet_leave(bh, job: Dict[str, Any], tearsheet_id: int) -> bool:
    """Remove the campaign tag when a Qualified job leaves tearsheet 2.

    Enforce mode only. Appleton keeps the tag it types itself. Report mode
    does not write. A failed write is logged and does not raise.
    """
    if leak_mode() != 'enforce' or not is_qualified_tenant():
        return False
    try:
        if int(tearsheet_id) != int(QUALIFIED_TEARSHEET_INDEED):
            return False
    except (TypeError, ValueError):
        return False
    if not isinstance(job, dict) or not job.get('id'):
        return False
    update = stripped_description_update(job)
    if not update:
        return False
    if not hasattr(bh, 'update_job_order'):
        logger.warning('Indeed leak: cannot strip campaign tag on job %s', job.get('id'))
        return False
    field = description_source_field(job)
    try:
        ok = bool(bh.update_job_order(int(job['id']), update))
    except Exception as exc:
        logger.warning(
            'Indeed leak: campaign tag strip failed for job %s: %s',
            job.get('id'),
            sanitize_leak_error(exc),
        )
        return False
    if ok:
        job[field] = update[field]
        logger.info('Indeed leak: stripped campaign tag from %s on job %s', field, job.get('id'))
    return ok


def _load_state() -> Dict[str, Any]:
    import json
    from models.scheduling import GlobalSettings

    raw = GlobalSettings.get_value(STATE_KEY, '') or ''
    if not raw:
        return {'jobs': {}}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {'jobs': {}}
    return data if isinstance(data, dict) else {'jobs': {}}


def _save_state(state: Dict[str, Any]) -> None:
    import json
    from models.scheduling import GlobalSettings

    GlobalSettings.set_value(
        STATE_KEY,
        json.dumps(state),
        description='Qualified Indeed leak detections and actions',
        category='indeed_publish',
    )


def _send_email(to_email, subject: str, message: str) -> bool:
    """One email to every To recipient. Recruiters are BCC'd once, via send_html_email."""
    from utils.bullhorn_helpers import get_email_service

    if isinstance(to_email, (list, tuple)):
        recipients = [str(item).strip() for item in to_email if str(item or '').strip()]
    else:
        recipients = [str(to_email).strip()] if str(to_email or '').strip() else []
    if not recipients:
        return False
    email_svc = get_email_service()
    if not email_svc:
        logger.warning('Indeed leak email skipped — email service unavailable')
        return False
    bcc = leak_bcc_recipients(recipients)
    summary = f'bcc:{bcc[0]}' if bcc else None
    safe_body = html.escape(message or '')
    result = email_svc.send_html_email(
        to_email=recipients,
        subject=subject,
        html_content=f'<pre>{safe_body}</pre>',
        notification_type='indeed_leak',
        bcc_emails=bcc or None,
        changes_summary=summary,
    )
    if isinstance(result, dict):
        return bool(result.get('success'))
    return bool(result)


class _HeldLock:
    def __init__(self) -> None:
        self.kind = ''
        self.conn = None
        self.fd = None


def _lock_file_path() -> str:
    configured = (os.environ.get('INDEED_LEAK_LOCK_FILE') or '').strip()
    if configured:
        return configured
    return os.path.join(tempfile.gettempdir(), 'indeed_leak_state.lock')


def _postgres_engine():
    """Postgres engine when this process is on the app database. Else None."""
    try:
        from extensions import db
        engine = db.engine
    except Exception:
        return None
    try:
        if engine.dialect.name != 'postgresql':
            return None
    except Exception:
        return None
    return engine


def _acquire_leak_lock(*, blocking: bool, timeout: float) -> Optional[_HeldLock]:
    """Postgres advisory lock across containers. File lock on sqlite and in tests."""
    engine = _postgres_engine()
    if engine is not None:
        return _acquire_postgres_lock(engine, blocking=blocking, timeout=timeout)
    return _acquire_file_lock(blocking=blocking, timeout=timeout)


def _acquire_postgres_lock(engine, *, blocking: bool, timeout: float) -> Optional[_HeldLock]:
    from sqlalchemy import text

    conn = engine.connect()
    held = _HeldLock()
    held.kind = 'postgres'
    held.conn = conn
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        while True:
            got = conn.execute(
                text('SELECT pg_try_advisory_lock(:key)'),
                {'key': _ADVISORY_LOCK_KEY},
            ).scalar()
            if got:
                _commit_quietly(conn)
                return held
            if not blocking or time.monotonic() >= deadline:
                conn.close()
                held.conn = None
                return None
            time.sleep(0.05)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        held.conn = None
        raise


def _acquire_file_lock(*, blocking: bool, timeout: float) -> Optional[_HeldLock]:
    fd = os.open(_lock_file_path(), os.O_CREAT | os.O_RDWR, 0o644)
    held = _HeldLock()
    held.kind = 'file'
    held.fd = fd
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return held
        except BlockingIOError:
            if not blocking or time.monotonic() >= deadline:
                os.close(fd)
                held.fd = None
                return None
            time.sleep(0.05)


def _commit_quietly(conn) -> None:
    commit = getattr(conn, 'commit', None)
    if not commit:
        return
    try:
        commit()
    except Exception:
        pass


def _release_leak_lock(held: _HeldLock) -> None:
    if held.kind == 'postgres' and held.conn is not None:
        from sqlalchemy import text
        try:
            held.conn.execute(
                text('SELECT pg_advisory_unlock(:key)'),
                {'key': _ADVISORY_LOCK_KEY},
            )
            _commit_quietly(held.conn)
        except Exception as exc:
            logger.warning('Indeed leak lock release failed: %s', sanitize_leak_error(exc))
        try:
            held.conn.close()
        except Exception:
            pass
        held.conn = None
        return
    if held.fd is not None:
        try:
            fcntl.flock(held.fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(held.fd)
        except OSError:
            pass
        held.fd = None


@contextmanager
def leak_state_lock(*, blocking: bool, timeout: Optional[float] = None) -> Iterator[bool]:
    """Serialize load-then-save of indeed_leak_state.

    Re-entrant on the thread that already holds it, so the fast path can call
    the job check without unlocking. A second process waits only when
    ``blocking`` is set, and only until ``timeout``.
    """
    depth = int(getattr(_LOCK_LOCAL, 'depth', 0) or 0)
    if depth > 0:
        _LOCK_LOCAL.depth = depth + 1
        try:
            yield True
        finally:
            _LOCK_LOCAL.depth = depth
        return
    if timeout is None:
        timeout = SWEEP_LOCK_WAIT_SECONDS
    held = _acquire_leak_lock(blocking=blocking, timeout=0 if not blocking else timeout)
    if held is None:
        yield False
        return
    _LOCK_LOCAL.depth = 1
    try:
        yield True
    finally:
        _LOCK_LOCAL.depth = 0
        _release_leak_lock(held)


def _ui_client() -> BullhornUIClient:
    cfg = config_from_env()
    client = BullhornUIClient(
        base_url=cfg['base_url'],
        username=cfg['username'],
        password=cfg['password'],
        private_label_id=cfg['private_label_id'],
        encryption_key=cfg['encryption_key'],
        current_user_id=cfg.get('current_user_id'),
    )
    client.login_with_retry()
    return client


def _bh():
    from utils.bullhorn_helpers import get_bullhorn_service

    bh = get_bullhorn_service()
    if not bh or not bh.authenticate():
        raise RuntimeError('Bullhorn REST auth failed')
    return bh


def _rest_get(bh, path: str, params: Dict[str, Any], timeout: int):
    """GET a Bullhorn REST path. BhRestToken is a header, never a query param."""
    headers = {'Accept': 'application/json'}
    token = getattr(bh, 'rest_token', None)
    if token:
        headers['BhRestToken'] = token
    return requests.get(
        f'{bh.base_url}{path.lstrip("/")}',
        params=params,
        headers=headers,
        timeout=timeout,
    )


def _query_jobs(bh, where: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start = 0
    while True:
        response = _rest_get(
            bh,
            'query/JobOrder',
            {
                'where': where,
                'fields': JOB_FIELDS,
                'count': 200,
                'start': start,
                'orderBy': 'id',
                'showTotalMatched': 'true',
            },
            90,
        )
        if response.status_code != 200:
            raise RuntimeError(f'JobOrder query HTTP {response.status_code}')
        payload = response.json()
        batch = payload.get('data') or []
        rows.extend(batch)
        total = int(payload.get('total') or 0)
        start += len(batch)
        if not batch or start >= total:
            break
    return rows


def _tearsheet_ids(bh) -> Set[int]:
    ids: Set[int] = set()
    start = 0
    while True:
        response = _rest_get(
            bh,
            f'entity/Tearsheet/{QUALIFIED_TEARSHEET_INDEED}/jobOrders',
            {
                'fields': 'id',
                'count': 200,
                'start': start,
            },
            60,
        )
        if response.status_code != 200:
            raise RuntimeError(f'tearsheet 2 read HTTP {response.status_code}')
        payload = response.json()
        batch = payload.get('data') or []
        for row in batch:
            if isinstance(row, dict) and row.get('id'):
                ids.add(int(row['id']))
        total = payload.get('total')
        start += len(batch)
        if not batch:
            break
        if total is not None and start >= int(total):
            break
        if len(batch) < 200:
            break
    return ids


def _hydrate_emails(bh, jobs: Iterable[Dict[str, Any]]) -> None:
    needed = []
    pending = []
    for job in jobs:
        users = []
        assigned = (job.get('assignedUsers') or {}).get('data') or []
        if isinstance(assigned, list):
            users.extend(assigned)
        owner = job.get('owner')
        if isinstance(owner, dict):
            users.append(owner)
        for user in users:
            if not isinstance(user, dict) or not user.get('id'):
                continue
            if '@' in (user.get('email') or ''):
                continue
            pending.append(user)
            needed.append(int(user['id']))
    if not needed or not hasattr(bh, 'get_user_emails'):
        return
    try:
        found = bh.get_user_emails(list(set(needed))) or {}
    except Exception as exc:
        logger.warning('Indeed leak: recruiter email lookup failed: %s', sanitize_leak_error(exc))
        return
    for user in pending:
        info = found.get(int(user['id'])) or found.get(str(user['id'])) or {}
        if isinstance(info, dict) and info.get('email'):
            user['email'] = info['email']


def _fetch_job(bh, job_id: int) -> Optional[Dict[str, Any]]:
    response = _rest_get(
        bh,
        f'entity/JobOrder/{int(job_id)}',
        {'fields': JOB_FIELDS},
        45,
    )
    if response.status_code != 200:
        return None
    data = (response.json() or {}).get('data') or {}
    return data if data.get('id') else None


def _make_run(bh, ui: BullhornUIClient, tearsheet_ids: Set[int]) -> LeakRun:
    def on_tearsheet(job_id: int) -> bool:
        return int(job_id) in _tearsheet_ids(bh)

    def status(job_id: int) -> Dict[str, Any]:
        return ui.get_publication_status(int(job_id))

    def remove(job: Dict[str, Any]) -> None:
        ui.unpublish_indeed(
            job_id=int(job['id']),
            description_html=indeed_delete_description(job),
        )

    return LeakRun(
        mode=leak_mode(),
        now=datetime.now(timezone.utc),
        state=_load_state(),
        grace_hours=grace_hours(),
        exempt_job_ids=exempt_job_ids(),
        exempt_departments=exempt_departments(),
        notify_email=notify_email(),
        send_email=_send_email,
        remove_indeed=remove,
        publication_status=status,
        job_on_tearsheet=on_tearsheet,
    )


def _finish(run: LeakRun, tearsheet_ids: Set[int], jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if run.guard_tearsheet(tearsheet_ids):
        _save_state(run.state)
        logger.warning('Indeed leak run stopped: %s', run.result.get('guardrail'))
        return run.result
    result = run.consider(jobs)
    _save_state(run.state)
    logger.info(
        'Indeed leak run mode=%s checked=%s leaks=%s noticed=%s removed=%s failed=%s',
        result.get('mode'),
        result.get('checked'),
        result.get('leaks'),
        result.get('noticed'),
        result.get('removed'),
        result.get('failed'),
    )
    return result


def run_sweep() -> Dict[str, Any]:
    if not is_qualified_tenant():
        return {'skipped': 'not qualified'}
    if leak_mode() == 'off':
        return {'skipped': 'mode off'}
    with leak_state_lock(blocking=True) as acquired:
        if not acquired:
            logger.warning('Indeed leak sweep skipped: another leak run holds the lock')
            return {'skipped': 'lock held'}
        return _run_sweep_locked()


def _run_sweep_locked() -> Dict[str, Any]:
    bh = _bh()
    ui = _ui_client()
    try:
        tearsheet_ids = _tearsheet_ids(bh)
        read_error = ''
    except Exception as exc:
        tearsheet_ids = set()
        read_error = sanitize_leak_error(exc)
    now = datetime.now(timezone.utc)
    cutoff_ms = int((now - timedelta(days=7)).timestamp() * 1000)
    if read_error:
        open_jobs, recent_jobs = [], []
    else:
        open_jobs = _query_jobs(bh, 'isOpen=true AND isDeleted=false')
        recent_jobs = _query_jobs(
            bh, f'isDeleted=false AND dateLastModified>={cutoff_ms}'
        )
    jobs = select_sweep_jobs(open_jobs, recent_jobs, tearsheet_ids)
    _hydrate_emails(bh, jobs)
    run = _make_run(bh, ui, tearsheet_ids)
    if read_error:
        run.abort_for_guardrail('tearsheet_read_failed', read_error)
        _save_state(run.state)
        return run.result
    return _finish(run, tearsheet_ids, jobs)


def run_deep_sweep() -> Dict[str, Any]:
    """Daily pass over closed jobs the hourly 7-day window never sees.

    Same mode switch, tearsheet guardrails, and status-failure guardrail as
    the hourly sweep. Closed jobs are ineligible, so enforce removes them
    immediately. Report mode only records them.
    """
    if not is_qualified_tenant():
        return {'skipped': 'not qualified'}
    if leak_mode() == 'off':
        return {'skipped': 'mode off'}
    with leak_state_lock(blocking=True) as acquired:
        if not acquired:
            logger.warning('Indeed leak deep sweep skipped: another leak run holds the lock')
            return {'skipped': 'lock held'}
        return _run_deep_sweep_locked()


def _run_deep_sweep_locked() -> Dict[str, Any]:
    bh = _bh()
    ui = _ui_client()
    try:
        tearsheet_ids = _tearsheet_ids(bh)
        read_error = ''
    except Exception as exc:
        tearsheet_ids = set()
        read_error = sanitize_leak_error(exc)
    if read_error:
        closed_jobs: List[Dict[str, Any]] = []
    else:
        closed_jobs = _query_jobs(bh, deep_sweep_where(datetime.now(timezone.utc)))
    jobs = select_deep_sweep_jobs(closed_jobs, tearsheet_ids)
    _hydrate_emails(bh, jobs)
    run = _make_run(bh, ui, tearsheet_ids)
    if read_error:
        run.abort_for_guardrail('tearsheet_read_failed', read_error)
        _save_state(run.state)
        return run.result
    result = _finish(run, tearsheet_ids, jobs)
    result['sweep'] = 'deep'
    return result


def check_jobs(
    job_ids: Iterable[int],
    mismatch_applications: Optional[Dict[int, List[datetime]]] = None,
) -> Dict[str, Any]:
    """Fast path: status-check these jobs when they are off tearsheet 2."""
    ids = []
    for job_id in job_ids:
        try:
            ids.append(int(job_id))
        except (TypeError, ValueError):
            continue
    if not is_qualified_tenant() or leak_mode() == 'off' or not ids:
        return {'skipped': True, 'mode': leak_mode()}
    with leak_state_lock(blocking=False) as acquired:
        if not acquired:
            logger.info('Indeed leak fast path skipped: a sweep holds the lock')
            return {'skipped': 'lock held'}
        return _check_jobs_locked(ids, mismatch_applications or {})


def _check_jobs_locked(
    ids: List[int],
    mismatch_applications: Dict[int, List[datetime]],
) -> Dict[str, Any]:
    bh = _bh()
    ui = _ui_client()
    try:
        tearsheet_ids = _tearsheet_ids(bh)
        read_error = ''
    except Exception as exc:
        tearsheet_ids = set()
        read_error = sanitize_leak_error(exc)
    jobs = []
    if not read_error:
        for job_id in ids:
            if job_id in tearsheet_ids:
                continue
            job = _fetch_job(bh, job_id)
            if job:
                jobs.append(job)
        _hydrate_emails(bh, jobs)
    run = _make_run(bh, ui, tearsheet_ids)
    run.mismatch_applications = {
        int(job_id): list(moments)
        for job_id, moments in (mismatch_applications or {}).items()
    }
    if read_error:
        run.abort_for_guardrail('tearsheet_read_failed', read_error)
        _save_state(run.state)
        return run.result
    return _finish(run, tearsheet_ids, jobs)


def _indeed_board_submissions(bh, candidate_id: int) -> List[Dict[str, Any]]:
    response = _rest_get(
        bh,
        'search/JobSubmission',
        {
            'query': (
                f'candidate.id:{int(candidate_id)} AND '
                f'source:"{INDEED_JOB_BOARD_SOURCE}"'
            ),
            'fields': 'id,dateAdded,source,jobOrder(id)',
            'count': 10,
            'sort': '-dateAdded',
        },
        45,
    )
    if response.status_code != 200:
        logger.warning(
            'Indeed leak fast path: submissions for %s HTTP %s',
            candidate_id,
            response.status_code,
        )
        return []
    return (response.json() or {}).get('data') or []


def check_recent_indeed_applicants() -> Dict[str, Any]:
    """From the 5-minute Indeed inbound cycle: a new Indeed Job Board application."""
    if not qualified_leak_active():
        return {'skipped': True}
    with leak_state_lock(blocking=False) as acquired:
        if not acquired:
            logger.info('Indeed leak fast path skipped: a sweep holds the lock')
            return {'skipped': 'lock held'}
        return _check_recent_indeed_applicants_locked()


def _check_recent_indeed_applicants_locked() -> Dict[str, Any]:
    bh = _bh()
    since_ms = int(
        (datetime.now(timezone.utc) - timedelta(minutes=FAST_PATH_LOOKBACK_MINUTES)).timestamp()
        * 1000
    )
    query = (
        'source:"Indeed Job Board" AND isDeleted:false '
        f'AND dateLastModified:[{since_ms} TO *]'
    )
    response = _rest_get(
        bh,
        'search/Candidate',
        {
            'query': query,
            'fields': 'id',
            'count': 50,
            'sort': '-dateLastModified',
        },
        45,
    )
    if response.status_code != 200:
        logger.warning('Indeed leak fast path: candidate search HTTP %s', response.status_code)
        return {'skipped': True, 'error': f'search HTTP {response.status_code}'}
    candidates = (response.json() or {}).get('data') or []
    by_job: Dict[int, List[datetime]] = {}
    for candidate in candidates:
        cid = candidate.get('id')
        if not cid:
            continue
        try:
            rows = _indeed_board_submissions(bh, int(cid))
        except Exception as exc:
            logger.warning(
                'Indeed leak fast path: submissions for %s failed: %s',
                cid,
                sanitize_leak_error(exc),
            )
            continue
        for job_id, moments in indeed_board_applications(rows).items():
            by_job.setdefault(job_id, []).extend(moments)
    by_job = jobs_with_recent_indeed_applications(
        by_job,
        datetime.now(timezone.utc),
        lookback_minutes=FAST_PATH_LOOKBACK_MINUTES + 5,
    )
    if not by_job:
        return {'skipped': True, 'applicants': len(candidates)}
    result = check_jobs(by_job.keys(), by_job)
    result['applicants'] = len(candidates)
    return result


def _tearsheet_digest_rows(bh, ui: BullhornUIClient) -> Dict[str, List[Dict[str, Any]]]:
    from .leak import notice_recipients

    ids = _tearsheet_ids(bh)
    live = []
    blocked = []
    not_live = []
    for job_id in sorted(ids):
        job = _fetch_job(bh, job_id) or {'id': job_id}
        _hydrate_emails(bh, [job])
        reason = cannot_publish_reason(job)
        recipients = notice_recipients(job, '')
        row = {
            'job_id': job_id,
            'title': job.get('title') or '',
            'department': job.get('correlatedCustomText1') or '',
            'recipients': recipients,
        }
        if reason:
            row['detail'] = reason
            blocked.append(row)
        try:
            status = ui.get_publication_status(job_id)
        except Exception as exc:
            safe = sanitize_leak_error(exc)
            logger.warning(
                'Indeed leak digest: status failed for tearsheet job %s: %s',
                job_id,
                safe,
            )
            missed = dict(row)
            missed['detail'] = safe or 'status check failed'
            not_live.append(missed)
            continue
        if status.get('indeed_published'):
            live.append(dict(row))
            continue
        missed = dict(row)
        missed['detail'] = reason or 'Indeed not published'
        not_live.append(missed)
    return {'live': live, 'cannot_publish': blocked, 'not_live': not_live}


def run_weekly_digest() -> Dict[str, Any]:
    if not is_qualified_tenant():
        return {'skipped': 'not qualified'}
    if leak_mode() == 'off':
        return {'skipped': 'mode off'}
    with leak_state_lock(blocking=True) as acquired:
        if not acquired:
            logger.warning('Indeed leak digest skipped: another leak run holds the lock')
            return {'skipped': 'lock held'}
        return _run_weekly_digest_locked()


def _run_weekly_digest_locked() -> Dict[str, Any]:
    state = _load_state()
    now = datetime.now(timezone.utc)
    if not digest_is_due(now, state):
        return {'skipped': 'not due'}
    bh = _bh()
    ui = _ui_client()
    sheets = _tearsheet_digest_rows(bh, ui)
    buckets = digest_buckets(state, now)
    mode = leak_mode()
    full = render_digest(
        now=now,
        mode=mode,
        live_tearsheet=sheets['live'],
        leaks=buckets['leaks'],
        removed=buckets['removed'],
        in_grace=buckets['in_grace'],
        failed=buckets['failed'],
        cannot_publish=sheets['cannot_publish'],
        exempt=buckets['exempt'],
        not_live=sheets['not_live'],
        mismatches=buckets['mismatches'],
    )
    ops = notify_email() or DEFAULT_NOTIFY_EMAIL
    sent = []
    ops_sent = False
    if full and ops:
        ops_sent = bool(_send_email(ops, 'Indeed leak digest', full))
        if ops_sent:
            sent.append(ops)
    audiences = set()
    for group in (
        sheets['live'], sheets['cannot_publish'], sheets['not_live'],
        buckets['leaks'], buckets['removed'], buckets['in_grace'], buckets['failed'],
    ):
        for row in group:
            for email in row.get('recipients') or []:
                if email and email.lower() != ops.lower():
                    audiences.add(email)
    for email in sorted(audiences):
        body = render_digest(
            now=now,
            mode=mode,
            live_tearsheet=sheets['live'],
            leaks=buckets['leaks'],
            removed=buckets['removed'],
            in_grace=buckets['in_grace'],
            failed=buckets['failed'],
            cannot_publish=sheets['cannot_publish'],
            not_live=sheets['not_live'],
            audience=email,
        )
        if not body:
            continue
        if _send_email(email, 'Your Indeed leak digest', body):
            sent.append(email)
    if ops_sent or not ops:
        state['last_digest_at'] = now.strftime('%Y-%m-%dT%H:%M:%SZ')
        _save_state(state)
    logger.info('Indeed leak digest sent to %s', sent)
    return {'sent': sent}


def survey_closed_jobs_no_floor() -> Dict[str, Any]:
    """Read-only closed-job Indeed check with no date floor.

    Does not write leak state, send email, or DELETE. Indeed-live jobs whose
    close and last-modified times are both outside the 180-day sweep window
    are listed for the report.
    """
    started = time.monotonic()
    if not is_qualified_tenant():
        return {'skipped': 'not qualified'}
    bh = _bh()
    ui = _ui_client()
    tearsheet_ids = _tearsheet_ids(bh)
    closed_jobs = _query_jobs(bh, 'isDeleted=false AND isOpen=false')
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=180)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    checked = 0
    failures = 0
    live = 0
    older: List[Dict[str, Any]] = []
    for job in closed_jobs:
        try:
            jid = int(job.get('id') or 0)
        except (TypeError, ValueError):
            continue
        if not jid or jid in tearsheet_ids:
            continue
        checked += 1
        try:
            status = ui.get_publication_status(jid) or {}
        except Exception as exc:
            failures += 1
            logger.warning(
                'Indeed leak survey: status failed for job %s: %s',
                jid,
                sanitize_leak_error(exc),
            )
            continue
        if not status.get('indeed_published'):
            continue
        live += 1
        modified = int(job.get('dateLastModified') or 0)
        closed_raw = job.get('dateClosed') or 0
        try:
            closed_ms = int(closed_raw or 0)
        except (TypeError, ValueError):
            closed_ms = 0
        if modified >= cutoff_ms or closed_ms >= cutoff_ms:
            continue
        older.append({
            'job_id': jid,
            'title': job.get('title') or '',
            'status': job.get('status') or '',
            'date_last_modified': modified or None,
            'date_closed': closed_ms or None,
            'indeed_date_last_published': status.get('indeed_date_last_published'),
        })
    return {
        'queried': len(closed_jobs),
        'tearsheet_members': len(tearsheet_ids),
        'checked': checked,
        'status_failures': failures,
        'indeed_live': live,
        'indeed_live_older_than_180_days': older,
        'duration_seconds': round(time.monotonic() - started, 1),
    }
