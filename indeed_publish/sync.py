"""Tearsheet 1640 membership sync → Bullhorn native Indeed Publish/Unpublish."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from feeds.feed_config import TEARSHEET_STSI_INDEED
from .category_mapper import map_published_category
from .config import LAST_RESULT_KEY, STATE_KEY, config_from_env
from .ui_client import BullhornUIClient, BullhornUIClientError

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _job_description_html(job: Dict[str, Any]) -> str:
    public = (job.get('publicDescription') or '').strip()
    if public:
        return public
    return (job.get('description') or '').strip()


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


def _fingerprint(job: Dict[str, Any], category_id: int, response_user_id: int) -> str:
    payload = '|'.join([
        str(job.get('id') or ''),
        str(job.get('title') or ''),
        _job_description_html(job),
        str(category_id),
        str(response_user_id),
        str(job.get('dateLastModified') or ''),
    ])
    return hashlib.sha256(payload.encode('utf-8', errors='ignore')).hexdigest()


def _load_state() -> Dict[str, Any]:
    from models import GlobalSettings
    raw = GlobalSettings.get_value(STATE_KEY, '{}') or '{}'
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {'job_ids': [], 'fingerprints': {}}


def _save_state(state: Dict[str, Any]) -> None:
    from models import GlobalSettings
    state = dict(state)
    state['updated_at'] = _utc_now()
    GlobalSettings.set_value(
        STATE_KEY,
        json.dumps(state),
        description='Indeed tearsheet 1640 publish membership + fingerprints',
        category='indeed_publish',
    )


def _save_last_result(result: Dict[str, Any]) -> None:
    from models import GlobalSettings
    GlobalSettings.set_value(
        LAST_RESULT_KEY,
        json.dumps(result),
        description='Last Indeed tearsheet publish sync result',
        category='indeed_publish',
    )


def _notify_failure(subject: str, message: str, notify_email: str) -> None:
    if not notify_email:
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
    except Exception as exc:
        logger.error('Indeed publish failure notify failed: %s', exc)


class IndeedTearsheetPublishService:
    """Diff tearsheet 1640 membership and drive CFC Publish / Unpublish."""

    def __init__(self, config: Optional[dict] = None, ui_client: Optional[BullhornUIClient] = None):
        self.config = config or config_from_env()
        self.ui_client = ui_client
        self.tearsheet_id = int(self.config.get('tearsheet_id') or TEARSHEET_STSI_INDEED)

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
        tmpl = self.config.get('job_url_template') or 'https://myticas.com/jobs/{job_id}'
        return tmpl.format(job_id=job_id)

    def _fetch_tearsheet_jobs(self, bh) -> List[Dict[str, Any]]:
        members = bh.get_tearsheet_members(self.tearsheet_id) or []
        ids: List[int] = []
        for m in members:
            if isinstance(m, dict) and m.get('id'):
                ids.append(int(m['id']))
            elif isinstance(m, int):
                ids.append(m)
        if not ids:
            # Fallback: full tearsheet jobs (works for empty / small sheets)
            return bh.get_tearsheet_jobs(self.tearsheet_id) or []

        jobs: List[Dict[str, Any]] = []
        for jid in ids:
            job = self._fetch_job_detail(bh, jid)
            if job:
                jobs.append(job)
        return jobs

    def _fetch_job_detail(self, bh, job_id: int) -> Optional[Dict[str, Any]]:
        if not bh.base_url or not bh.rest_token:
            if not bh.authenticate():
                return None
        fields = (
            'id,title,description,publicDescription,dateLastModified,status,isOpen,'
            'isJobcastPublished,categories(id,name),'
            'assignedUsers(id,firstName,lastName,email),'
            'publishedCategory(id,name),responseUser(id,firstName,lastName,email)'
        )
        try:
            import requests as _requests
            url = f'{bh.base_url}entity/JobOrder/{job_id}'
            resp = _requests.get(
                url,
                params={'fields': fields, 'BhRestToken': bh.rest_token},
                timeout=45,
            )
            if resp.status_code != 200:
                logger.warning('Indeed publish: job %s fetch HTTP %s', job_id, resp.status_code)
                return None
            data = resp.json().get('data') or {}
            return data if data.get('id') else None
        except Exception as exc:
            logger.warning('Indeed publish: job %s fetch error: %s', job_id, exc)
            return None

    def _resolve_response_user(
        self, bh, job: Dict[str, Any]
    ) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        """Return (user_id, email, error_reason)."""
        user = _first_assigned_recruiter(job)
        if not user:
            return None, None, 'no assigned recruiter'

        uid = user.get('id')
        email = (user.get('email') or '').strip()
        if not email and uid and hasattr(bh, 'get_user_emails'):
            try:
                emails = bh.get_user_emails([int(uid)]) or {}
                info = emails.get(int(uid)) or emails.get(str(uid))
                if isinstance(info, dict):
                    email = (info.get('email') or '').strip()
                elif isinstance(info, str):
                    email = info.strip()
            except Exception:
                pass
        if not uid:
            return None, None, 'assigned recruiter missing id'
        if not email or '@' not in email:
            return None, None, f'assigned recruiter {uid} missing email'
        return int(uid), email, None

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
            _save_last_result(result)
            logger.info('indeed_tearsheet_publish: disabled — skipping')
            return result

        if not self.config.get('username') or not self.config.get('password'):
            result['errors'].append('missing BH_UI_USERNAME / BH_UI_PASSWORD')
            _save_last_result(result)
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
            _save_last_result(result)
            return result

        try:
            jobs = self._fetch_tearsheet_jobs(bh)
        except Exception as exc:
            result['errors'].append(f'tearsheet fetch failed: {exc}')
            _save_last_result(result)
            return result

        current_ids: Set[int] = set()
        jobs_by_id: Dict[int, Dict[str, Any]] = {}
        for job in jobs:
            jid = job.get('id')
            if not jid:
                continue
            jid = int(jid)
            current_ids.add(jid)
            jobs_by_id[jid] = job

        state = _load_state()
        prev_ids = {int(x) for x in (state.get('job_ids') or [])}
        fingerprints = {
            str(k): v for k, v in (state.get('fingerprints') or {}).items()
        }

        to_add = current_ids - prev_ids
        to_remove = prev_ids - current_ids
        to_check = current_ids & prev_ids

        ui = self._build_ui_client()
        try:
            ui.login()
            if self.config.get('current_user_id'):
                ui.set_current_user_id(self.config['current_user_id'])
            elif not ui.current_user_id:
                # Resolve CorporateUser id via REST username match when possible
                resolved = self._resolve_ui_user_id(bh)
                if resolved:
                    ui.set_current_user_id(resolved)
        except BullhornUIClientError as exc:
            result['errors'].append(f'UI login failed: {exc}')
            _save_last_result(result)
            _notify_failure(
                'Indeed tearsheet publish: Bullhorn UI login failed',
                str(exc),
                self.config.get('notify_email') or '',
            )
            return result

        # Unpublish removals first
        for jid in sorted(to_remove):
            try:
                job = self._fetch_job_detail(bh, jid) or {'id': jid}
                self._unpublish_one(ui, bh, job, result)
                fingerprints.pop(str(jid), None)
            except Exception as exc:
                msg = f'unpublish {jid}: {exc}'
                result['errors'].append(msg)
                _notify_failure(
                    f'Indeed unpublish failed for job {jid}',
                    msg,
                    self.config.get('notify_email') or '',
                )

        # Publish new members
        for jid in sorted(to_add):
            job = jobs_by_id[jid]
            try:
                fp = self._publish_one(ui, bh, job, result, operation='PUBLISH')
                if fp:
                    fingerprints[str(jid)] = fp
                    result['published'].append(jid)
            except Exception as exc:
                msg = f'publish {jid}: {exc}'
                result['errors'].append(msg)
                result['skipped'].append({'job_id': jid, 'reason': str(exc)})
                _notify_failure(
                    f'Indeed publish failed for job {jid}',
                    msg,
                    self.config.get('notify_email') or '',
                )

        # Republish when fingerprint changed
        for jid in sorted(to_check):
            job = jobs_by_id[jid]
            try:
                cat_id, _, _ = map_published_category(job)
                uid, _, err = self._resolve_response_user(bh, job)
                if err or not uid:
                    result['skipped'].append({'job_id': jid, 'reason': err or 'no recruiter'})
                    continue
                fp = _fingerprint(job, cat_id, uid)
                if fingerprints.get(str(jid)) == fp:
                    continue
                new_fp = self._publish_one(ui, bh, job, result, operation='REPUBLISH')
                if new_fp:
                    fingerprints[str(jid)] = new_fp
                    result['republished'].append(jid)
            except Exception as exc:
                msg = f'republish {jid}: {exc}'
                result['errors'].append(msg)
                _notify_failure(
                    f'Indeed republish failed for job {jid}',
                    msg,
                    self.config.get('notify_email') or '',
                )

        _save_state({
            'job_ids': sorted(current_ids),
            'fingerprints': fingerprints,
        })
        _save_last_result(result)
        logger.info(
            'indeed_tearsheet_publish: done published=%s republished=%s unpublished=%s errors=%s',
            len(result['published']),
            len(result['republished']),
            len(result['unpublished']),
            len(result['errors']),
        )
        return result

    def _resolve_ui_user_id(self, bh) -> Optional[int]:
        username = (self.config.get('username') or '').strip()
        if not username or not bh.base_url or not bh.rest_token:
            return None
        try:
            import requests as _requests
            # CorporateUser username often matches UI login
            url = f'{bh.base_url}query/CorporateUser'
            resp = _requests.get(
                url,
                params={
                    'where': f"username='{username.replace(chr(39), '')}'",
                    'fields': 'id,username,email',
                    'count': 5,
                    'BhRestToken': bh.rest_token,
                },
                timeout=30,
            )
            if resp.status_code != 200:
                return None
            rows = resp.json().get('data') or []
            if rows and rows[0].get('id'):
                return int(rows[0]['id'])
        except Exception as exc:
            logger.warning('Could not resolve BH_UI current user id: %s', exc)
        return None

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
        cat_id, cat_name, reason = map_published_category(job)
        uid, email, err = self._resolve_response_user(bh, job)
        if err or not uid:
            raise BullhornUIClientError(err or 'cannot resolve published contact')

        desc = _job_description_html(job)
        if not desc:
            raise BullhornUIClientError('job has empty description/publicDescription')

        logger.info(
            'Indeed %s job %s category=%s (%s) contact=%s <%s> via %s',
            operation,
            jid,
            cat_name,
            cat_id,
            uid,
            email,
            reason,
        )
        ui.publish_boards(
            job_id=jid,
            published_category_id=cat_id,
            response_user_id=uid,
            description_html=desc,
            job_url=self._job_url(jid),
            operation=operation,
        )
        time.sleep(0.2)
        return _fingerprint(job, cat_id, uid)

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
            cat_id, _, _ = map_published_category(job)
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

        ui.unpublish_boards(
            job_id=jid,
            published_category_id=cat_id,
            response_user_id=uid,
            description_html=_job_description_html(job),
            job_url=self._job_url(jid),
        )
        result['unpublished'].append(jid)
        time.sleep(0.2)


def unpublish_job_after_tearsheet_remove(job_id: int, tearsheet_id: int) -> bool:
    """
    Hook for incremental monitor auto-remove: full Unpublish when leaving 1640.
    Best-effort; failures are logged + emailed and do not raise.
    """
    if int(tearsheet_id) != int(TEARSHEET_STSI_INDEED):
        return False

    cfg = config_from_env()
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
        ui.login()
        if not ui.current_user_id:
            resolved = svc._resolve_ui_user_id(bh)
            if resolved:
                ui.set_current_user_id(resolved)

        result = {'unpublished': [], 'errors': []}
        svc._unpublish_one(ui, bh, job, result)

        # Drop from membership state so next sync doesn't double-unpublish
        state = _load_state()
        ids = [int(x) for x in (state.get('job_ids') or []) if int(x) != int(job_id)]
        fps = dict(state.get('fingerprints') or {})
        fps.pop(str(job_id), None)
        _save_state({'job_ids': ids, 'fingerprints': fps})
        logger.info('indeed auto-unpublish succeeded for job %s', job_id)
        return True
    except Exception as exc:
        logger.error('indeed auto-unpublish failed for job %s: %s', job_id, exc)
        _notify_failure(
            f'Indeed auto-unpublish failed for job {job_id}',
            str(exc),
            cfg.get('notify_email') or '',
        )
        return False
