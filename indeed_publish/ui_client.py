"""Bullhorn Universal Login session + JobPublish REST client (career portal + Indeed)."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

DEFAULT_UNIVERSAL_LOGIN_URL = 'https://universal-east.bullhornstaffing.com/universal-login'

BOARD_CAREER_PORTAL = 'careerPortal'
BOARD_INDEED = 'indeed'
DEFAULT_BOARDS = (BOARD_CAREER_PORTAL, BOARD_INDEED)

# Callers still pass the old JobBoard CFC names / operations.
_BOARD_ALIASES = {
    'corporate': BOARD_CAREER_PORTAL,
    'careerportal': BOARD_CAREER_PORTAL,
    'indeed': BOARD_INDEED,
}
# JobPublish only knows ADDCHANGE (publish / republish) and DELETE (unpublish).
_OPERATION_ALIASES = {
    'PUBLISH': 'ADDCHANGE',
    'REPUBLISH': 'ADDCHANGE',
    'ADDCHANGE': 'ADDCHANGE',
    'UNPUBLISH': 'DELETE',
    'DELETE': 'DELETE',
}

_BROWSER_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)
_APP_ORIGIN = 'https://app.bullhornstaffing.com'


class BullhornUIClientError(RuntimeError):
    pass


class BullhornPublishUnavailable(BullhornUIClientError):
    """Bullhorn itself is down (5xx / unreachable) — retrying other jobs is pointless."""


def _is_retryable_login_error(exc: BullhornUIClientError) -> bool:
    """Transient Bullhorn auth glitches (401, 5xx) — not config/MFA failures."""
    msg = str(exc)
    if '401' in msg:
        return True
    if 'Universal login HTTP 5' in msg:
        return True
    return False


def _eastern_tz_fields() -> Dict[str, str]:
    """Approximate values produced by Bullhorn timezoneUtils.collectTzInfo (US/Eastern)."""
    return {
        'gmtOffsetMins': '-300',  # EST standard offset minutes
        'recognizesDst': 'true',
        'dstStart': '3/8',
        'dstEnd': '11/1',
        'capturedTimezone': 'true',
    }


def normalize_board(board: str) -> str:
    key = (board or '').strip().lower()
    if key not in _BOARD_ALIASES:
        raise BullhornUIClientError(f'unknown publish board: {board!r}')
    return _BOARD_ALIASES[key]


def _account_named(accounts: list, *names: str) -> Optional[Dict[str, Any]]:
    wanted = {name.lower() for name in names}
    for account in accounts:
        if (account.get('name') or '').lower() in wanted:
            return account
    return None


def normalize_operation(operation: str) -> str:
    key = (operation or '').strip().upper()
    if key not in _OPERATION_ALIASES:
        raise BullhornUIClientError(f'unknown publish operation: {operation!r}')
    return _OPERATION_ALIASES[key]


class BullhornUIClient:
    """
    Authenticates via Bullhorn Universal Login, then calls the same
    JobPublish REST endpoints the Bullhorn UI uses from Publish Job.

    Publish and unpublish POST ``services/JobPublish/{careerPortal,indeed}``.
    Publication status is a separate read-only GET,
    ``services/JobPublish/boards?jobPostingID=``. The Indeed account's
    ``isPublished`` flag is what Publish Job shows as Indeed Publication Status.
    """

    def __init__(
        self,
        *,
        username: str,
        password: str,
        base_url: str = '',
        private_label_id: str = '',
        encryption_key: str = 'novo',
        current_user_id: Optional[str] = None,
        universal_login_url: str = DEFAULT_UNIVERSAL_LOGIN_URL,
        session: Optional[requests.Session] = None,
    ):
        # base_url / private_label_id / encryption_key only fed the retired
        # cls45 JobBoard CFC; kept so existing config wiring keeps working.
        self.base_url = (base_url or '').rstrip('/')
        self.username = username
        self.password = password
        self.private_label_id = str(private_label_id or '')
        self.encryption_key = encryption_key
        self.current_user_id = str(current_user_id) if current_user_id else None
        self.universal_login_url = (universal_login_url or DEFAULT_UNIVERSAL_LOGIN_URL).rstrip('/')
        self.session = session or self._new_session()
        self.rest_url: Optional[str] = None
        self.rest_token: Optional[str] = None
        self._logged_in = False

    @staticmethod
    def _new_session() -> requests.Session:
        session = requests.Session()
        session.headers.update({'User-Agent': _BROWSER_UA})
        return session

    def login(self) -> None:
        if not self.username or not self.password:
            raise BullhornUIClientError('BH_UI_USERNAME / BH_UI_PASSWORD not configured')

        tz = _eastern_tz_fields()
        try:
            check = self.session.post(
                f'{self.universal_login_url}/session/checkForRedirect',
                data={'username': self.username},
                timeout=45,
            )
            redir_base = self.universal_login_url
            if check.status_code == 200 and check.text:
                try:
                    payload = check.json()
                    if payload.get('redirect'):
                        redir_base = str(payload['redirect']).rstrip('/')
                except Exception:
                    pass

            login_url = (
                f'{redir_base}/session/login?'
                + urlencode({
                    'username': self.username,
                    'password': self.password,
                    'app': 'ats',
                    'source': 'ColdFusion',
                    **tz,
                })
            )
            resp = self.session.post(login_url, timeout=60)
        except requests.RequestException as exc:
            # urllib3 messages echo the URL, and the login URL carries the password.
            raise BullhornUIClientError(
                f'Universal login HTTP 503 (unreachable: {type(exc).__name__})'
            ) from None

        if resp.status_code == 401:
            raise BullhornUIClientError('UI login rejected (401 — check credentials / password expiry)')
        if resp.status_code >= 400:
            raise BullhornUIClientError(f'Universal login HTTP {resp.status_code}')

        try:
            data = resp.json()
        except Exception as exc:
            raise BullhornUIClientError(f'Universal login returned non-JSON: {exc}') from exc

        if data.get('mfaCallbackCode'):
            raise BullhornUIClientError(
                'UI login requires MFA — use a service account without MFA for automation'
            )

        rest = {}
        for entry in data.get('sessions') or []:
            if isinstance(entry, dict) and entry.get('name') == 'rest':
                rest = entry.get('value') or {}
                break
        endpoint = (rest.get('endpoint') or '').strip()
        token = (rest.get('token') or '').strip()
        if not endpoint or not token:
            raise BullhornUIClientError('Universal login returned no REST session')

        identity = data.get('identity') or {}
        if identity.get('userId') and not self.current_user_id:
            self.current_user_id = str(identity['userId'])

        self.rest_url = endpoint.rstrip('/') + '/'
        self.rest_token = token
        self._logged_in = True
        logger.info(
            'Bullhorn UI session established for Indeed publish automation (user_id=%s)',
            self.current_user_id,
        )

    def login_with_retry(
        self,
        *,
        max_attempts: int = 3,
        backoff_seconds: float = 5.0,
    ) -> None:
        """Login; on transient 401/5xx, fresh session + backoff, up to 3 tries."""
        last_exc: Optional[BullhornUIClientError] = None
        for attempt in range(1, max_attempts + 1):
            try:
                if attempt > 1:
                    time.sleep(backoff_seconds)
                    self.session = self._new_session()
                    self._logged_in = False
                    logger.warning(
                        'Bullhorn UI login retry %s/%s after: %s',
                        attempt,
                        max_attempts,
                        last_exc,
                    )
                self.login()
                return
            except BullhornUIClientError as exc:
                last_exc = exc
                if attempt >= max_attempts or not _is_retryable_login_error(exc):
                    raise
        if last_exc:
            raise last_exc

    def ensure_login(self) -> None:
        if not self._logged_in:
            self.login()

    def set_current_user_id(self, user_id: Any) -> None:
        if user_id is not None:
            self.current_user_id = str(user_id)

    def get_publication_status(self, job_id: int) -> Dict[str, Any]:
        """Read Publish Job board status. Does not publish or unpublish.

        Bullhorn's Publish Job page loads this with
        ``GET services/JobPublish/boards?jobPostingID=``. ``jobBoardList`` and
        ``isJobcastPublished`` are not that status: a job can have
        ``isJobcastPublished`` set while the Indeed account is not published.
        """
        self.ensure_login()
        resp = self._get_publish_boards(int(job_id))
        if resp.status_code == 401:
            self._logged_in = False
            resp = self._get_publish_boards(int(job_id))
        return self._parse_publish_boards(int(job_id), resp)

    def publish_boards(
        self,
        *,
        job_id: int,
        published_category_id: int,
        response_user_id: int,
        description_html: str,
        job_url: str = '',
        operation: str = 'ADDCHANGE',
        boards: tuple = DEFAULT_BOARDS,
    ) -> Dict[str, Any]:
        """Publish/Republish to career portal + Indeed (same order as the UI)."""
        op = normalize_operation(operation)
        results = {}
        for board in boards:
            name = normalize_board(board)
            results[name] = self._post_job_publish(
                board=name,
                operation=op,
                job_id=job_id,
                published_category_id=published_category_id,
                response_user_id=response_user_id,
                description_html=description_html,
            )
        return results

    def unpublish_boards(
        self,
        *,
        job_id: int,
        published_category_id: int = 0,
        response_user_id: int = 0,
        description_html: str = '',
        job_url: str = '',
        boards: tuple = DEFAULT_BOARDS,
    ) -> Dict[str, Any]:
        """Full Unpublish (career portal + Indeed).

        The Bullhorn UI Unpublish button only DELETEs from careerPortal, which
        leaves the Indeed posting live; send Indeed DELETE explicitly.
        """
        results = {}
        for board in boards:
            name = normalize_board(board)
            results[name] = self._post_job_publish(
                board=name,
                operation='DELETE',
                job_id=job_id,
                published_category_id=published_category_id or 0,
                response_user_id=response_user_id or 0,
                description_html=description_html or '',
            )
        return results

    def unpublish_indeed(
        self,
        *,
        job_id: int,
        published_category_id: int = 0,
        response_user_id: int = 0,
        description_html: str = '',
    ) -> Dict[str, Any]:
        """DELETE the Indeed board only. The career portal posting is left up."""
        return self.unpublish_boards(
            job_id=job_id,
            published_category_id=published_category_id,
            response_user_id=response_user_id,
            description_html=description_html,
            boards=(BOARD_INDEED,),
        )

    def _post_job_publish(
        self,
        *,
        board: str,
        operation: str,
        job_id: int,
        published_category_id: int,
        response_user_id: int,
        description_html: str,
    ) -> Dict[str, Any]:
        body = json.dumps({
            'jobPostingID': int(job_id),
            'description': description_html or '',
            'publishedCategoryID': int(published_category_id or 0),
            'responseUserID': int(response_user_id or 0),
            'operation': operation,
            'externalAccountID': -1,
        })
        resp = self._send(board, body)
        if resp.status_code == 401:
            # REST session expired mid-cycle — one fresh login, then retry.
            self._logged_in = False
            resp = self._send(board, body)

        text = resp.text or ''
        if resp.status_code >= 500:
            raise BullhornPublishUnavailable(
                f'{board} {operation} HTTP {resp.status_code}: {text[:300]}'
            )
        if resp.status_code >= 400:
            raise BullhornUIClientError(
                f'{board} {operation} HTTP {resp.status_code}: {text[:300]}'
            )
        try:
            payload = resp.json()
        except Exception:
            payload = None
        if not isinstance(payload, dict) or payload.get('success') is not True:
            raise BullhornUIClientError(f'{board} {operation} failed: {text[:400]}')
        return {
            'status_code': resp.status_code,
            'message': payload.get('message') or '',
            'board': board,
            'operation': operation,
        }

    def _get_publish_boards(self, job_id: int) -> requests.Response:
        self.ensure_login()
        try:
            return self.session.get(
                f'{self.rest_url}services/JobPublish/boards',
                params={'jobPostingID': int(job_id)},
                headers={
                    'BhRestToken': self.rest_token,
                    'Accept': 'application/json, text/plain, */*',
                    'Origin': _APP_ORIGIN,
                    'Referer': f'{_APP_ORIGIN}/',
                },
                timeout=60,
            )
        except requests.RequestException as exc:
            raise BullhornPublishUnavailable(
                f'JobPublish boards unreachable ({type(exc).__name__})'
            ) from None

    @staticmethod
    def _parse_publish_boards(job_id: int, resp: requests.Response) -> Dict[str, Any]:
        text = resp.text or ''
        if resp.status_code >= 500:
            raise BullhornPublishUnavailable(
                f'JobPublish boards HTTP {resp.status_code}: {text[:300]}'
            )
        if resp.status_code >= 400:
            raise BullhornUIClientError(
                f'JobPublish boards HTTP {resp.status_code}: {text[:300]}'
            )
        try:
            payload = resp.json()
        except Exception:
            payload = None
        if not isinstance(payload, dict) or 'accounts' not in payload:
            raise BullhornUIClientError(
                f'JobPublish boards unexpected response: {text[:300]}'
            )
        accounts = []
        for raw in payload.get('accounts') or []:
            if not isinstance(raw, dict):
                continue
            accounts.append({
                'name': raw.get('name') or '',
                'isPublished': bool(raw.get('isPublished')),
                'dateLastPublished': raw.get('dateLastPublished'),
            })
        indeed = _account_named(accounts, 'indeed')
        career = _account_named(accounts, 'corporate', 'careerportal')
        return {
            'job_id': int(job_id),
            'indeed_published': bool(indeed and indeed['isPublished']),
            'indeed_date_last_published': (
                indeed.get('dateLastPublished') if indeed else None
            ),
            'career_portal_published': bool(career and career['isPublished']),
            'career_portal_date_last_published': (
                career.get('dateLastPublished') if career else None
            ),
            'accounts': accounts,
        }

    def _send(self, board: str, body: str) -> requests.Response:
        self.ensure_login()
        try:
            return self.session.post(
                f'{self.rest_url}services/JobPublish/{board}',
                params={
                    'uniqueCallId': str(uuid.uuid4()),
                    'highLevelCallStack': '/content/actions/publish-job',
                },
                data=body,
                headers={
                    'BhRestToken': self.rest_token,
                    'Content-Type': 'application/json',
                    'Accept': 'application/json, text/plain, */*',
                    'Origin': _APP_ORIGIN,
                    'Referer': f'{_APP_ORIGIN}/',
                },
                timeout=90,
            )
        except requests.RequestException as exc:
            raise BullhornPublishUnavailable(
                f'{board} unreachable ({type(exc).__name__})'
            ) from None
