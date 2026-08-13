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

# Appended to Indeed-published descriptions via tearsheet 1640 sync (Plan B).
# Do not strip on unpublish / tearsheet remove — once added it stays in Bullhorn.
INDSHOW_TAG = '#INDShow'
INDSHOW_SUFFIX = f'   {INDSHOW_TAG}'  # three ASCII spaces before the tag


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


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


def ensure_indshow_tag(html: str) -> str:
    """Append `   #INDShow` when missing (case-sensitive). Empty input unchanged."""
    text = html or ''
    if not text.strip():
        return text
    if INDSHOW_TAG in text:
        return text
    return f'{text}{INDSHOW_SUFFIX}'


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
    # Do NOT include dateLastModified — Publish itself bumps that field and
    # would cause every member to REPUBLISH on every sync cycle.
    # Fingerprint the final (tagged) description so first publish with #INDShow
    # does not thrash on every subsequent sync.
    payload = '|'.join([
        str(job.get('id') or ''),
        str(job.get('title') or ''),
        ensure_indshow_tag(_job_description_html(job)),
        str(category_id),
        str(response_user_id),
    ])
    return hashlib.sha256(payload.encode('utf-8', errors='ignore')).hexdigest()


def _load_state() -> Dict[str, Any]:
    from models import GlobalSettings
    raw = GlobalSettings.get_value(STATE_KEY, '{}') or '{}'
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            data.setdefault('job_ids', [])
            data.setdefault('fingerprints', {})
            data.setdefault('pending_unpublish', [])
            return data
    except Exception:
        pass
    return {'job_ids': [], 'fingerprints': {}, 'pending_unpublish': []}


def _save_state(state: Dict[str, Any]) -> None:
    from models import GlobalSettings
    state = dict(state)
    state.setdefault('job_ids', [])
    state.setdefault('fingerprints', {})
    state.setdefault('pending_unpublish', [])
    state['updated_at'] = _utc_now()
    GlobalSettings.set_value(
        STATE_KEY,
        json.dumps(state),
        description='Indeed tearsheet 1640 publish membership + fingerprints + pending unpublish',
        category='indeed_publish',
    )


def _pending_unpublish_ids(state: Dict[str, Any]) -> Set[int]:
    return {int(x) for x in (state.get('pending_unpublish') or [])}


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
        pending_unpublish = _pending_unpublish_ids(state)
        # Re-added to tearsheet → no longer need unpublish retry
        pending_unpublish -= current_ids

        to_add = current_ids - prev_ids
        to_remove = prev_ids - current_ids
        to_check = current_ids & prev_ids
        # Retry failed unpublishes every cycle until success (do not forget them
        # just because they are no longer in job_ids / tearsheet membership).
        unpublish_targets = (to_remove | pending_unpublish) - current_ids

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
            result['errors'].append(f'UI login failed: {exc}')
            _save_last_result(result)
            _notify_failure(
                'Indeed tearsheet publish: Bullhorn UI login failed',
                str(exc),
                self.config.get('notify_email') or '',
            )
            return result

        # Unpublish removals + pending retries first
        for jid in sorted(unpublish_targets):
            try:
                job = self._fetch_job_detail(bh, jid) or {'id': jid}
                self._unpublish_one(ui, bh, job, result)
                fingerprints.pop(str(jid), None)
                pending_unpublish.discard(jid)
            except Exception as exc:
                pending_unpublish.add(jid)
                msg = f'unpublish {jid}: {exc}'
                result['errors'].append(msg)
                _notify_failure(
                    f'Indeed unpublish failed for job {jid}',
                    msg,
                    self.config.get('notify_email') or '',
                )

        # Publish new members
        # Bullhorn JobBoard CFC: after an Unpublish (or from Not Published),
        # operation=PUBLISH returns "will be removed…" and leaves the job
        # unpublished. operation=REPUBLISH is what actually publishes/republishes
        # (matches the live UI network capture). Always use REPUBLISH here.
        for jid in sorted(to_add):
            job = jobs_by_id[jid]
            try:
                fp = self._publish_one(ui, bh, job, result, operation='REPUBLISH')
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

        # job_ids tracks current tearsheet membership for publish/republish.
        # pending_unpublish retains failed removals so the next sync retries
        # instead of forgetting them forever.
        _save_state({
            'job_ids': sorted(current_ids),
            'fingerprints': {
                k: v for k, v in fingerprints.items()
                if int(k) in current_ids
            },
            'pending_unpublish': sorted(pending_unpublish),
        })
        result['pending_unpublish'] = sorted(pending_unpublish)
        _save_last_result(result)
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

    def _persist_indshow_description(self, bh, job: Dict[str, Any], tagged_desc: str) -> None:
        """Write tagged description back to the Bullhorn field we published from."""
        jid = int(job['id'])
        field = _description_source_field(job)
        current = (job.get(field) or '')
        if INDSHOW_TAG in current:
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
                logger.info('Indeed publish: persisted %s with #INDShow on job %s', field, jid)
            else:
                logger.warning(
                    'Indeed publish: Bullhorn REST update of %s failed for job %s',
                    field,
                    jid,
                )
        except Exception as exc:
            logger.warning(
                'Indeed publish: could not persist #INDShow on job %s %s: %s',
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
        cat_id, cat_name, reason = map_published_category(job)
        uid, email, err = self._resolve_response_user(bh, job)
        if err or not uid:
            raise BullhornUIClientError(err or 'cannot resolve published contact')

        raw_desc = _job_description_html(job)
        if not raw_desc:
            raise BullhornUIClientError('job has empty description/publicDescription')
        desc = ensure_indshow_tag(raw_desc)

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
        if desc != raw_desc:
            self._persist_indshow_description(bh, job, desc)
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


def _update_state_after_unpublish(job_id: int, *, success: bool) -> None:
    """Adjust membership / pending_unpublish after a one-off unpublish attempt."""
    state = _load_state()
    jid = int(job_id)
    ids = [int(x) for x in (state.get('job_ids') or []) if int(x) != jid]
    fps = dict(state.get('fingerprints') or {})
    fps.pop(str(jid), None)
    pending = _pending_unpublish_ids(state)
    if success:
        pending.discard(jid)
    else:
        pending.add(jid)
    _save_state({
        'job_ids': ids,
        'fingerprints': fps,
        'pending_unpublish': sorted(pending),
    })


def unpublish_job_after_tearsheet_remove(job_id: int, tearsheet_id: int) -> bool:
    """
    Hook for incremental monitor auto-remove: full Unpublish when leaving 1640.
    Best-effort; failures are logged + emailed and do not raise.
    Failed attempts are retained in pending_unpublish for the next sync retry.
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
        ui.login_with_retry()
        if not ui.current_user_id:
            resolved = svc._resolve_ui_user_id(bh)
            if resolved:
                ui.set_current_user_id(resolved)

        result = {'unpublished': [], 'errors': []}
        svc._unpublish_one(ui, bh, job, result)

        # Drop from membership state so next sync doesn't double-unpublish
        _update_state_after_unpublish(int(job_id), success=True)
        logger.info('indeed auto-unpublish succeeded for job %s', job_id)
        return True
    except Exception as exc:
        logger.error('indeed auto-unpublish failed for job %s: %s', job_id, exc)
        try:
            _update_state_after_unpublish(int(job_id), success=False)
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
    One-off recovery: unpublish specific jobs via Bullhorn UI JobBoard CFC
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
            _update_state_after_unpublish(jid, success=True)
            out['unpublished'].append(jid)
            logger.info('indeed force-unpublish succeeded for job %s', jid)
        except Exception as exc:
            try:
                _update_state_after_unpublish(jid, success=False)
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
