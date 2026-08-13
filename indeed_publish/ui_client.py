"""Bullhorn classic UI session + JobBoard CFC Publish/Unpublish client."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urljoin

import requests

logger = logging.getLogger(__name__)

DEFAULT_UNIVERSAL_LOGIN_URL = 'https://universal-east.bullhornstaffing.com/universal-login'


class BullhornUIClientError(RuntimeError):
    pass


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


class BullhornUIClient:
    """
    Authenticates via Bullhorn Universal Login (same path as CheckBHLogin.cfm JS),
    then POSTs JobBoard CFC endpoints for Corporate + Indeed publish/unpublish.
    """

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        private_label_id: str,
        encryption_key: str = 'novo',
        current_user_id: Optional[str] = None,
        universal_login_url: str = DEFAULT_UNIVERSAL_LOGIN_URL,
        session: Optional[requests.Session] = None,
    ):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.private_label_id = str(private_label_id)
        self.encryption_key = encryption_key
        self.current_user_id = str(current_user_id) if current_user_id else None
        self.universal_login_url = (universal_login_url or DEFAULT_UNIVERSAL_LOGIN_URL).rstrip('/')
        self.session = session or requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
        })
        self._logged_in = False

    def login(self) -> None:
        if not self.username or not self.password:
            raise BullhornUIClientError('BH_UI_USERNAME / BH_UI_PASSWORD not configured')

        tz = _eastern_tz_fields()

        # Seed CF cookies / private-label context (matches browser first hop)
        login_page = urljoin(self.base_url + '/', 'BullhornSTAFFING/bhlogin.cfm')
        self.session.get(login_page, timeout=45)
        bootstrap = urljoin(self.base_url + '/', 'BullhornStaffing/CheckBHLogin.cfm')
        self.session.post(
            bootstrap,
            data={
                'username': self.username,
                'password': self.password,
                'goToURL': '/BullhornStaffing/default.cfm',
                'source': 'ats',
                'clientLogin': 'TRUE',
                'closeOnFinish': 'FALSE',
                'privateLabelID': self.private_label_id,
                **tz,
            },
            timeout=60,
            allow_redirects=True,
        )

        # Universal Login (authoritative session for JobBoard CFC)
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
        # Browser XHR uses POST with credentials (query string carries the fields)
        resp = self.session.post(login_url, timeout=60)
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

        identity = data.get('identity') or {}
        if identity.get('userId') and not self.current_user_id:
            self.current_user_id = str(identity['userId'])

        # Bridge into ATS CF session (LoadNovo / default.cfm)
        redirect_host = (data.get('redirectUrl') or self.base_url).rstrip('/')
        bridge = self.session.get(
            f'{redirect_host}/BullhornStaffing/default.cfm',
            timeout=60,
            allow_redirects=True,
        )
        if bridge.status_code >= 400:
            logger.warning('ATS bridge HTTP %s — continuing with universal cookies', bridge.status_code)

        if not self.current_user_id:
            self.current_user_id = self._discover_current_user_id(bridge.text or '')

        if not self.current_user_id:
            raise BullhornUIClientError(
                'currentUserID unknown after login — set BH_UI_CURRENT_USER_ID'
            )

        self._logged_in = True
        logger.info(
            'Bullhorn UI session established for Indeed publish automation (user_id=%s)',
            self.current_user_id,
        )

    def login_with_retry(
        self,
        *,
        max_attempts: int = 2,
        backoff_seconds: float = 3.0,
    ) -> None:
        """Login once; on transient 401/5xx, fresh session + backoff then retry."""
        last_exc: Optional[BullhornUIClientError] = None
        for attempt in range(1, max_attempts + 1):
            try:
                if attempt > 1:
                    time.sleep(backoff_seconds)
                    self.session = requests.Session()
                    self.session.headers.update({
                        'User-Agent': (
                            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                            'AppleWebKit/537.36 (KHTML, like Gecko) '
                            'Chrome/120.0.0.0 Safari/537.36'
                        ),
                    })
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

    def _discover_current_user_id(self, html: str) -> Optional[str]:
        patterns = (
            r'currentUserID["\s:=]+(\d+)',
            r'userID["\s:=]+(\d+)',
            r'"userId"\s*:\s*(\d+)',
            r'userId:\s*(\d+)',
        )
        for pat in patterns:
            for m in re.finditer(pat, html, re.I):
                uid = m.group(1)
                if uid not in ('0', '-1'):
                    return uid
        return None

    def set_current_user_id(self, user_id: Any) -> None:
        if user_id is not None:
            self.current_user_id = str(user_id)

    def _cfc_url(self, board: str) -> str:
        return f'{self.base_url}/BullhornSTAFFING/JobBoard/API/BHJobBoard_{board}.cfc'

    def publish_boards(
        self,
        *,
        job_id: int,
        published_category_id: int,
        response_user_id: int,
        description_html: str,
        job_url: str,
        operation: str = 'PUBLISH',
        boards: tuple = ('Corporate', 'Indeed'),
    ) -> Dict[str, Any]:
        """Publish/Republish to career portal + Indeed (same order as the UI)."""
        self.ensure_login()
        if not self.current_user_id:
            raise BullhornUIClientError(
                'currentUserID unknown — set BH_UI_CURRENT_USER_ID after first login'
            )

        results = {}
        for board in boards:
            results[board] = self._post_cfc(
                board=board,
                method='Publish',
                operation=operation,
                job_id=job_id,
                published_category_id=published_category_id,
                response_user_id=response_user_id,
                description_html=description_html,
                job_url=job_url,
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
        boards: tuple = ('Corporate', 'Indeed'),
    ) -> Dict[str, Any]:
        """Full Unpublish (portal + Indeed)."""
        self.ensure_login()
        if not self.current_user_id:
            raise BullhornUIClientError(
                'currentUserID unknown — set BH_UI_CURRENT_USER_ID after first login'
            )

        # Bullhorn UI uses the same CFC `method=Publish` with operation=UNPUBLISH
        # (method=Unpublish returns HTTP 500). Confirmed Jul 2026 on cls45.
        results = {}
        for board in boards:
            results[board] = self._post_cfc(
                board=board,
                method='Publish',
                operation='UNPUBLISH',
                job_id=job_id,
                published_category_id=published_category_id or 0,
                response_user_id=response_user_id or 0,
                description_html=description_html or '',
                job_url=job_url or '',
            )
        return results

    def _post_cfc(
        self,
        *,
        board: str,
        method: str,
        operation: str,
        job_id: int,
        published_category_id: int,
        response_user_id: int,
        description_html: str,
        job_url: str,
    ) -> Dict[str, Any]:
        url = self._cfc_url(board)
        data = {
            'method': method,
            'operation': operation,
            'jobPostingID': str(job_id),
            'publishedCategoryID': str(published_category_id),
            'responseUserID': str(response_user_id),
            'description': description_html or '',
            'currentUserID': str(self.current_user_id),
            'privateLabelId': self.private_label_id,
            'jobUrl': job_url or '',
            'jsonResult': 'true',
            'privateLabelEncryptionKey': self.encryption_key,
            'externalAccountID': '-1',
        }
        resp = self.session.post(
            url,
            data=data,
            timeout=90,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )
        text = resp.text or ''
        if resp.status_code >= 400:
            raise BullhornUIClientError(
                f'{board} {method} HTTP {resp.status_code}: {text[:300]}'
            )
        lowered = text.lower()
        if '"return":"error"' in lowered or '"success":false' in lowered:
            raise BullhornUIClientError(f'{board} {method} failed: {text[:400]}')
        if 'exception' in lowered and 'error' in lowered and 'success' not in lowered:
            raise BullhornUIClientError(f'{board} {method} failed: {text[:400]}')
        return {
            'status_code': resp.status_code,
            'body_preview': text[:500],
            'board': board,
            'method': method,
            'operation': operation,
        }
