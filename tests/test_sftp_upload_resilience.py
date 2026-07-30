"""Regression tests for SFTP connect retry and per-cycle connection reuse.

Jul 30 2026: an upload cycle uploaded the v2 feed successfully, then had the
next two feeds rejected with "Authentication failed" one second later, with the
remote server tarpitting the third connection for 21s before rejecting it. The
credentials were unchanged and the following cycle succeeded, so the failure was
remote-side throttling of three connect-and-auth handshakes in quick succession.

These tests pin the two behaviours that make that cycle survivable: a cycle
authenticates once, and a transient connect rejection is retried rather than
reported to an operator as "Manual Action Required".
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from ftp_service import FTPService
from tasks.xml_feeds import _upload_single_file


def _service(**kwargs):
    defaults = dict(
        hostname='sftp.example.com',
        username='feeduser',
        password='secret',
        target_directory='/',
        port=2222,
        use_sftp=True,
        connect_retry_delay=0.0,
    )
    defaults.update(kwargs)
    return FTPService(**defaults)


@pytest.fixture
def fake_ssh_factory():
    """Patch paramiko.SSHClient with a factory whose connects follow a script.

    Each entry in `outcomes` is either None (connect succeeds) or an exception
    instance to raise from connect().
    """
    def build(outcomes):
        clients = []

        def make_client():
            client = MagicMock()
            sftp = MagicMock()
            # Uploaded files always verify: size checks are covered elsewhere.
            sftp.stat.return_value = SimpleNamespace(st_size=11)
            client.open_sftp.return_value = sftp

            def connect(*_args, **_kwargs):
                outcome = outcomes[len(clients) - 1]
                if outcome is not None:
                    raise outcome

            client.connect.side_effect = connect
            clients.append(client)
            return client

        patcher = patch('paramiko.SSHClient', side_effect=make_client)
        return patcher, clients

    return build


@pytest.fixture
def local_file(tmp_path):
    path = tmp_path / 'feed.xml'
    path.write_text('<rss></rss>')
    return str(path)


class TestConnectRetry:
    def test_transient_auth_rejection_is_retried(self, fake_ssh_factory, local_file):
        """The exact Jul 30 failure: auth rejected once, fine on the retry."""
        patcher, clients = fake_ssh_factory(
            [paramiko.AuthenticationException('Authentication failed.'), None]
        )
        svc = _service()

        with patcher:
            assert svc.upload_file(local_file, 'feed.xml') is True

        assert len(clients) == 2, 'should have retried after the rejection'
        assert svc.last_error is None

    def test_gives_up_after_configured_attempts(self, fake_ssh_factory, local_file):
        patcher, clients = fake_ssh_factory(
            [paramiko.AuthenticationException('Authentication failed.')] * 3
        )
        svc = _service(connect_attempts=3)

        with patcher:
            assert svc.upload_file(local_file, 'feed.xml') is False

        assert len(clients) == 3, 'must be bounded, not unlimited'
        assert 'authentication failed' in (svc.last_error or '').lower()

    def test_retry_backs_off_between_attempts(self, fake_ssh_factory, local_file):
        """Backoff must grow so a throttling server gets room to recover."""
        patcher, _clients = fake_ssh_factory(
            [paramiko.SSHException('banner error'),
             paramiko.SSHException('banner error'),
             None]
        )
        svc = _service(connect_retry_delay=2.0)

        with patcher, patch('ftp_service.time.sleep') as mock_sleep:
            assert svc.upload_file(local_file, 'feed.xml') is True

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [2.0, 6.0]

    def test_timeouts_are_treated_as_transient(self, fake_ssh_factory, local_file):
        import socket

        patcher, clients = fake_ssh_factory([socket.timeout('timed out'), None])
        svc = _service()

        with patcher:
            assert svc.upload_file(local_file, 'feed.xml') is True

        assert len(clients) == 2


class TestSessionReuse:
    def test_one_authentication_for_the_whole_cycle(self, fake_ssh_factory, local_file):
        """Three feeds, one handshake — this removes the throttling trigger."""
        patcher, clients = fake_ssh_factory([None])
        svc = _service()

        with patcher:
            with svc.sftp_session():
                for name in ('v2.xml', 'indeed.xml', 'ziprecruiter.xml'):
                    assert svc.upload_file(local_file, name) is True

        assert len(clients) == 1, 'a cycle must authenticate once, not per file'
        assert clients[0].open_sftp.return_value.put.call_count == 3

    def test_session_closes_its_connection(self, fake_ssh_factory, local_file):
        patcher, clients = fake_ssh_factory([None])
        svc = _service()

        with patcher:
            with svc.sftp_session():
                svc.upload_file(local_file, 'v2.xml')

        clients[0].close.assert_called_once()
        assert svc._session_sftp is None

    def test_session_is_released_when_an_upload_raises(self, fake_ssh_factory, local_file):
        patcher, clients = fake_ssh_factory([None])
        svc = _service()

        with patcher:
            with svc.sftp_session():
                clients[0].open_sftp.return_value.put.side_effect = IOError('disk full')
                assert svc.upload_file(local_file, 'v2.xml') is False

        assert svc._session_sftp is None, 'a failed upload must not leak the session'
        clients[0].close.assert_called_once()

    def test_one_failed_feed_does_not_abort_the_rest(self, fake_ssh_factory, local_file):
        """A per-file error should not cost the cycle its other feeds."""
        patcher, clients = fake_ssh_factory([None])
        svc = _service()

        with patcher:
            with svc.sftp_session():
                sftp = clients[0].open_sftp.return_value
                sftp.put.side_effect = [IOError('permission denied'), None, None]
                results = [
                    svc.upload_file(local_file, name)
                    for name in ('v2.xml', 'indeed.xml', 'ziprecruiter.xml')
                ]

        assert results == [False, True, True]

    def test_connect_failure_propagates_out_of_the_session(self, fake_ssh_factory):
        """Callers need to distinguish "nothing uploaded" from "one feed failed"."""
        patcher, _clients = fake_ssh_factory(
            [paramiko.AuthenticationException('Authentication failed.')] * 3
        )
        svc = _service(connect_attempts=3)

        with patcher:
            with pytest.raises(paramiko.AuthenticationException):
                with svc.sftp_session():
                    pytest.fail('body must not run when the connection failed')

    def test_ftp_mode_session_is_a_passthrough(self, local_file):
        svc = _service(use_sftp=False)
        with svc.sftp_session() as yielded:
            assert yielded is svc


class TestFailureReporting:
    def test_real_reason_reaches_the_caller(self, fake_ssh_factory, local_file):
        """The alert e-mail said "Upload returned False", which tells nobody anything."""
        patcher, _clients = fake_ssh_factory(
            [paramiko.AuthenticationException('Authentication failed.')] * 3
        )
        svc = _service(connect_attempts=3)
        app = SimpleNamespace(logger=MagicMock())

        with patcher:
            ok, err = _upload_single_file(svc, '<rss></rss>', 'indeed.xml', app)

        assert ok is False
        assert 'authentication failed' in err.lower()
        assert err != 'Upload returned False'

    def test_stale_error_does_not_leak_into_a_later_success(
        self, fake_ssh_factory, local_file
    ):
        patcher, clients = fake_ssh_factory([None])
        svc = _service()
        app = SimpleNamespace(logger=MagicMock())

        with patcher:
            with svc.sftp_session():
                sftp = clients[0].open_sftp.return_value
                sftp.put.side_effect = [IOError('permission denied'), None]
                _upload_single_file(svc, '<rss></rss>', 'v2.xml', app)
                ok, err = _upload_single_file(svc, '<rss></rss>', 'indeed.xml', app)

        assert ok is True
        assert err is None
        assert svc.last_error is None
