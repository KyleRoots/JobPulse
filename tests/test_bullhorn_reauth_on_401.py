"""Regression tests for Bullhorn re-authentication after an HTTP 401.

`BullhornService.authenticate()` short-circuits and returns True whenever a
token is already set:

    if self.rest_token and self.base_url:
        return True

That makes the usual "got a 401, re-authenticate and retry once" recovery a
silent no-op unless the caller drops the stale token first — the retry re-sends
the token the server just rejected and fails identically.

Observed in production Jul 2026: `owner_reassignment` logged
"re-authenticating and retrying once" immediately followed by another 401,
every 35 minutes for days. The retry completed in ~130ms, far too fast for a
real login round trip, because no login happened.
"""
import pathlib
import re

from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

AUTH_CALL = re.compile(r'\b(?:self|bh|bullhorn|bh_service|service)\.authenticate\(\)')
SKIP_DIRS = {'.venv', 'node_modules', 'tests', '.git', 'alembic'}


def _iter_source_files():
    for path in REPO_ROOT.rglob('*.py'):
        if SKIP_DIRS & set(path.relative_to(REPO_ROOT).parts):
            continue
        yield path


def _reauth_sites_missing_token_clear():
    """Every 401-triggered re-auth must drop the rejected token first."""
    offenders = []
    for path in _iter_source_files():
        lines = path.read_text(errors='ignore').splitlines()
        for idx, line in enumerate(lines):
            if not AUTH_CALL.search(line):
                continue
            window = "\n".join(lines[max(0, idx - 6):idx])
            if '401' not in window:
                continue
            if 'rest_token = None' in window:
                continue
            offenders.append(
                f"{path.relative_to(REPO_ROOT)}:{idx + 1}: {line.strip()}"
            )
    return offenders


class TestReauthConvention:
    def test_no_401_retry_reuses_the_rejected_token(self):
        offenders = _reauth_sites_missing_token_clear()
        assert not offenders, (
            "These 401 retries call authenticate() without first setting "
            "rest_token = None. authenticate() returns True immediately when a "
            "token is present, so the retry re-sends the rejected token and "
            "fails the same way:\n  " + "\n  ".join(offenders)
        )


class TestAuthenticateShortCircuit:
    """Pin the short-circuit itself, since the convention above depends on it."""

    def _service(self):
        from bullhorn_service import BullhornService

        svc = BullhornService.__new__(BullhornService)
        svc.rest_token = 'stale-token'
        svc.base_url = 'https://rest.bullhorn.example/rest-services/abc/'
        svc._auth_in_progress = False
        svc._last_auth_attempt = None
        svc.client_id = 'cid'
        svc.client_secret = 'secret'
        svc.username = 'user'
        svc.password = 'pass'
        svc.use_bullhorn_one = True
        return svc

    def test_existing_token_skips_login(self):
        svc = self._service()
        with patch.object(svc, '_direct_login', MagicMock(return_value=True)) as login:
            assert svc.authenticate() is True
        login.assert_not_called()

    def test_cleared_token_forces_a_real_login(self):
        svc = self._service()
        svc.rest_token = None
        with patch.object(svc, '_direct_login', MagicMock(return_value=True)) as login:
            assert svc.authenticate() is True
        login.assert_called_once()


class TestOwnerReassignmentRetry:
    """The task that surfaced this in production."""

    def test_401_retry_issues_a_fresh_login(self):
        import tasks.owner_reassignment as mod

        source = pathlib.Path(mod.__file__).read_text()
        match = re.search(
            r'candidate search HTTP 401.*?bh\.authenticate\(\)',
            source,
            re.DOTALL,
        )
        assert match, 'the 401 retry block should still exist'
        assert 'bh.rest_token = None' in match.group(0), (
            'owner_reassignment must drop the rejected token before '
            're-authenticating, or the retry is a no-op'
        )
