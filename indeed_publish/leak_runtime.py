"""Bullhorn and scheduler wiring for the Qualified Indeed leak check.

Myticas/STSI is unchanged: the sweep, the applicant fast path, and the Monday
digest return immediately unless this process is the Qualified tenant.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

import requests

from feeds.feed_config import QUALIFIED_TEARSHEET_INDEED, is_qualified_tenant

from .config import DEFAULT_NOTIFY_EMAIL, config_from_env
from .leak import (
    STATE_KEY,
    LeakRun,
    cannot_publish_reason,
    indeed_delete_description,
    description_source_field,
    digest_buckets,
    digest_is_due,
    exempt_departments,
    exempt_job_ids,
    grace_hours,
    jobs_from_applicant_submissions,
    deep_sweep_where,
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
    'assignedUsers(id,firstName,lastName)'
)
FAST_PATH_LOOKBACK_MINUTES = 15


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


def _send_email(to_email: str, subject: str, message: str) -> bool:
    from utils.bullhorn_helpers import get_email_service

    email_svc = get_email_service()
    if not email_svc:
        logger.warning('Indeed leak email skipped — email service unavailable')
        return False
    return bool(email_svc.send_notification_email(
        to_email=to_email,
        subject=subject,
        message=message,
        notification_type='indeed_leak',
    ))


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


def check_jobs(job_ids: Iterable[int]) -> Dict[str, Any]:
    """Fast path: status-check these jobs when they are off tearsheet 2."""
    ids = []
    for job_id in job_ids:
        try:
            ids.append(int(job_id))
        except (TypeError, ValueError):
            continue
    if not is_qualified_tenant() or leak_mode() == 'off' or not ids:
        return {'skipped': True, 'mode': leak_mode()}
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
    if read_error:
        run.abort_for_guardrail('tearsheet_read_failed', read_error)
        _save_state(run.state)
        return run.result
    return _finish(run, tearsheet_ids, jobs)


def check_recent_indeed_applicants() -> Dict[str, Any]:
    """From the 5-minute Indeed inbound cycle: a new Indeed Job Board application."""
    if not qualified_leak_active():
        return {'skipped': True}
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
    submissions = {}
    for candidate in candidates:
        cid = candidate.get('id')
        if not cid:
            continue
        try:
            submissions[int(cid)] = bh.get_candidate_submissions(int(cid), count=5) or []
        except Exception as exc:
            logger.warning(
                'Indeed leak fast path: submissions for %s failed: %s',
                cid,
                sanitize_leak_error(exc),
            )
    job_ids = jobs_from_applicant_submissions(submissions)
    if not job_ids:
        return {'skipped': True, 'applicants': len(candidates)}
    result = check_jobs(job_ids)
    result['applicants'] = len(candidates)
    return result


def _tearsheet_digest_rows(bh, ui: BullhornUIClient) -> Dict[str, List[Dict[str, Any]]]:
    ids = _tearsheet_ids(bh)
    live = []
    blocked = []
    for job_id in sorted(ids):
        job = _fetch_job(bh, job_id) or {'id': job_id}
        _hydrate_emails(bh, [job])
        reason = cannot_publish_reason(job)
        recipients = []
        from .leak import notice_recipients
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
            logger.warning(
                'Indeed leak digest: status failed for tearsheet job %s: %s',
                job_id,
                sanitize_leak_error(exc),
            )
            continue
        if status.get('indeed_published'):
            live.append(dict(row))
    return {'live': live, 'cannot_publish': blocked}


def run_weekly_digest() -> Dict[str, Any]:
    if not is_qualified_tenant():
        return {'skipped': 'not qualified'}
    if leak_mode() == 'off':
        return {'skipped': 'mode off'}
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
        sheets['live'], sheets['cannot_publish'], buckets['leaks'],
        buckets['removed'], buckets['in_grace'], buckets['failed'],
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
