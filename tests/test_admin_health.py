"""
Tests for the super-admin operational health dashboard (M2).

Covers:
- Authorization (anonymous redirect, non-admin 403, admin 200).
- JSON data endpoint shape + auth gating.
- Service-layer guarantees (every tile method returns a HealthTile and never
  raises, even when upstream backing services are broken).
- overall_status rollup logic.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from services.admin_health_service import (
    AdminHealthService,
    HealthTile,
    _format_age,
    _status_from_age,
)


# ---------------------------------------------------------------- helpers
#
# Auth-gating tests patch Flask-Login's `current_user` proxy directly via
# `unittest.mock`, rather than driving real session cookies. Real-cookie
# fixtures (form-POST login, session_transaction, FlaskLoginClient) all
# proved fragile in this project's pytest suite: state from earlier
# fixtures (the conftest's `authenticated_client`, scheduler init, the
# scoped DB session) leaked into later tests and silently produced
# anonymous `current_user`, which surfaces as 302 → /login instead of
# the expected 200/403. Patching `current_user` at the import sites is
# isolated, deterministic, and exercises the exact gating logic in
# `routes/admin_health.py` without depending on any session machinery.

from contextlib import contextmanager
from unittest.mock import patch, MagicMock


def _make_user_mock(*, is_authenticated: bool, is_admin: bool):
    user = MagicMock()
    user.is_authenticated = is_authenticated
    user.is_admin = is_admin
    user.is_anonymous = not is_authenticated
    user.is_active = True
    user.id = 999
    user.get_id = lambda: '999'
    return user


@contextmanager
def _as_user(*, is_authenticated: bool, is_admin: bool):
    """Patch current_user across all sites the admin_health route reads it from.

    Patches:
      - `routes.admin_health.current_user`  (used by `_require_admin`)
      - `flask_login.utils._get_user`        (used by `@login_required`)
    """
    user = _make_user_mock(is_authenticated=is_authenticated, is_admin=is_admin)
    with patch('routes.admin_health.current_user', user), \
         patch('flask_login.utils._get_user', return_value=user):
        yield user


@pytest.fixture
def admin_ctx():
    """Yield a context where `current_user` is an authenticated admin."""
    with _as_user(is_authenticated=True, is_admin=True) as u:
        yield u


@pytest.fixture
def non_admin_ctx():
    """Yield a context where `current_user` is authenticated but not admin."""
    with _as_user(is_authenticated=True, is_admin=False) as u:
        yield u


# ---------------------------------------------------------------- auth tests

class TestAdminHealthAuth:
    """Authorization gates for /admin/health."""

    def test_anon_redirected_to_login(self, client):
        resp = client.get('/admin/health/', follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert '/login' in resp.headers.get('Location', '')

    def test_anon_data_redirected_to_login(self, client):
        resp = client.get('/admin/health/data', follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert '/login' in resp.headers.get('Location', '')

    def test_non_admin_forbidden(self, client, non_admin_ctx):
        resp = client.get('/admin/health/', follow_redirects=False)
        assert resp.status_code == 403

    def test_non_admin_data_forbidden(self, client, non_admin_ctx):
        resp = client.get('/admin/health/data', follow_redirects=False)
        assert resp.status_code == 403

    def test_admin_can_view_dashboard(self, client, admin_ctx):
        resp = client.get('/admin/health/')
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # Sanity check: page is the health dashboard, not a fallback.
        assert 'System Health' in body
        # All canonical tiles are rendered.
        for label in [
            'Database',
            'Scheduler',
            'Bullhorn Auth',
            'OpenAI',
            'In-Flight Vetting',
            'Vetting Failures (24h)',
            'SFTP Uploads',
            'OneDrive Token',
        ]:
            assert label in body, f'missing tile: {label}'

    def test_admin_data_endpoint_shape(self, client, admin_ctx):
        resp = client.get('/admin/health/data')
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload is not None
        assert 'tiles' in payload
        assert 'overall_status' in payload
        assert 'last_checked' in payload
        assert isinstance(payload['tiles'], list)
        assert len(payload['tiles']) == 8
        assert payload['overall_status'] in ('green', 'amber', 'red', 'unknown')
        for tile in payload['tiles']:
            for required in ('key', 'label', 'icon', 'status', 'value'):
                assert required in tile, f'tile {tile} missing {required}'
            assert tile['status'] in ('green', 'amber', 'red', 'unknown')


# ---------------------------------------------------------------- helper unit tests

class TestHelpers:
    def test_format_age_handles_none(self):
        assert _format_age(None, datetime.utcnow()) == 'never'

    def test_format_age_seconds(self):
        now = datetime.utcnow()
        assert _format_age(now - timedelta(seconds=10), now) == '10s ago'

    def test_format_age_minutes(self):
        now = datetime.utcnow()
        assert _format_age(now - timedelta(minutes=5), now) == '5m ago'

    def test_format_age_hours(self):
        now = datetime.utcnow()
        assert _format_age(now - timedelta(hours=3), now) == '3h ago'

    def test_format_age_days(self):
        now = datetime.utcnow()
        assert _format_age(now - timedelta(days=2), now) == '2d ago'

    def test_status_from_age_none_is_red(self):
        assert _status_from_age(None, datetime.utcnow()) == 'red'

    def test_status_from_age_recent_is_green(self):
        now = datetime.utcnow()
        assert _status_from_age(now - timedelta(minutes=30), now) == 'green'

    def test_status_from_age_stale_is_amber(self):
        now = datetime.utcnow()
        assert _status_from_age(now - timedelta(hours=2), now) == 'amber'

    def test_status_from_age_very_stale_is_red(self):
        now = datetime.utcnow()
        assert _status_from_age(now - timedelta(hours=12), now) == 'red'


# ---------------------------------------------------------------- service unit tests

class TestAdminHealthService:
    """Service-layer guarantees: every tile method returns a HealthTile and
    never raises, even when its backing service is broken."""

    def test_collect_all_returns_eight_tiles(self, app):
        with app.app_context():
            tiles = AdminHealthService().collect_all()
            assert len(tiles) == 8
            assert all(isinstance(t, HealthTile) for t in tiles)
            keys = {t.key for t in tiles}
            assert keys == {
                'database', 'scheduler', 'bullhorn_auth', 'openai',
                'inflight_vetting', 'failed_vetting_24h',
                'sftp_uploads', 'onedrive_token',
            }

    def test_database_tile_green_when_query_succeeds(self, app):
        with app.app_context():
            tile = AdminHealthService().tile_database()
            assert tile.status == 'green'
            assert tile.value == 'Connected'

    def test_database_tile_red_when_query_raises(self, app):
        with app.app_context():
            with patch('extensions.db.session.execute', side_effect=RuntimeError('boom')):
                tile = AdminHealthService().tile_database()
            assert tile.status == 'red'
            assert tile.value == 'Disconnected'
            assert 'boom' in tile.subtext
            assert tile.remediation  # non-empty remediation hint

    def test_scheduler_tile_running(self, app):
        with app.app_context():
            mock_sched = MagicMock()
            mock_sched.running = True
            mock_sched.get_jobs.return_value = [object(), object(), object()]
            with patch.dict('sys.modules', {'app': MagicMock(scheduler=mock_sched)}):
                tile = AdminHealthService().tile_scheduler()
            assert tile.status == 'green'
            assert tile.value == 'Running'
            assert '3' in tile.subtext

    def test_scheduler_tile_amber_when_no_jobs(self, app):
        with app.app_context():
            mock_sched = MagicMock()
            mock_sched.running = True
            mock_sched.get_jobs.return_value = []
            with patch.dict('sys.modules', {'app': MagicMock(scheduler=mock_sched)}):
                tile = AdminHealthService().tile_scheduler()
            assert tile.status == 'amber'

    def test_scheduler_tile_red_when_stopped(self, app):
        with app.app_context():
            mock_sched = MagicMock()
            mock_sched.running = False
            mock_sched.get_jobs.return_value = []
            with patch.dict('sys.modules', {'app': MagicMock(scheduler=mock_sched)}):
                tile = AdminHealthService().tile_scheduler()
            assert tile.status == 'red'
            assert tile.value == 'Stopped'

    def test_bullhorn_tile_no_data_when_table_empty(self, app):
        from extensions import db
        from models import VettingHealthCheck
        with app.app_context():
            VettingHealthCheck.query.delete()
            db.session.commit()
            tile = AdminHealthService().tile_bullhorn_auth()
            assert tile.status == 'unknown'
            assert tile.value == 'No data'

    def test_bullhorn_tile_green_with_recent_success(self, app):
        from extensions import db
        from models import VettingHealthCheck
        with app.app_context():
            VettingHealthCheck.query.delete()
            db.session.add(VettingHealthCheck(
                check_time=datetime.utcnow(),
                bullhorn_status=True,
                openai_status=True,
                database_status=True,
                scheduler_status=True,
                is_healthy=True,
            ))
            db.session.commit()
            tile = AdminHealthService().tile_bullhorn_auth()
            assert tile.status == 'green'
            assert tile.value == 'Authenticated'

    def test_bullhorn_tile_red_when_check_failed(self, app):
        from extensions import db
        from models import VettingHealthCheck
        with app.app_context():
            VettingHealthCheck.query.delete()
            db.session.add(VettingHealthCheck(
                check_time=datetime.utcnow(),
                bullhorn_status=False,
                bullhorn_error='auth refused',
                openai_status=True,
                database_status=True,
                scheduler_status=True,
                is_healthy=False,
            ))
            db.session.commit()
            tile = AdminHealthService().tile_bullhorn_auth()
            assert tile.status == 'red'
            assert 'auth refused' in tile.subtext

    def test_openai_tile_red_when_key_missing(self, app, monkeypatch):
        with app.app_context():
            monkeypatch.delenv('OPENAI_API_KEY', raising=False)
            tile = AdminHealthService().tile_openai()
            assert tile.status == 'red'
            assert 'API key' in tile.value

    def test_inflight_tile_green_when_empty(self, app):
        from extensions import db
        from models import CandidateVettingLog
        with app.app_context():
            CandidateVettingLog.query.delete()
            db.session.commit()
            tile = AdminHealthService().tile_inflight_vetting()
            assert tile.status == 'green'
            assert tile.value == '0'

    def test_inflight_tile_red_when_oldest_is_stuck(self, app):
        from extensions import db
        from models import CandidateVettingLog
        with app.app_context():
            CandidateVettingLog.query.delete()
            old = CandidateVettingLog(
                bullhorn_candidate_id=99001,
                candidate_name='Stuck Candidate',
                status='processing',
                created_at=datetime.utcnow() - timedelta(hours=2),
            )
            db.session.add(old)
            db.session.commit()
            tile = AdminHealthService().tile_inflight_vetting()
            assert tile.status == 'red'
            assert 'over 30 minutes' in tile.remediation

    def test_failed_24h_tile_green_when_zero(self, app):
        from extensions import db
        from models import CandidateVettingLog
        with app.app_context():
            CandidateVettingLog.query.delete()
            db.session.commit()
            tile = AdminHealthService().tile_failed_vetting_24h()
            assert tile.status == 'green'
            assert tile.value == '0'

    def test_failed_24h_tile_amber_at_threshold(self, app):
        from extensions import db
        from models import CandidateVettingLog
        with app.app_context():
            CandidateVettingLog.query.delete()
            for i in range(6):  # > VETTING_FAILED_AMBER (5)
                db.session.add(CandidateVettingLog(
                    bullhorn_candidate_id=98000 + i,
                    status='failed',
                    created_at=datetime.utcnow() - timedelta(hours=1),
                ))
            db.session.commit()
            tile = AdminHealthService().tile_failed_vetting_24h()
            assert tile.status == 'amber'
            assert tile.value == '6'

    def test_sftp_tile_amber_when_disabled(self, app):
        from extensions import db
        from models import GlobalSettings
        with app.app_context():
            GlobalSettings.query.filter(
                GlobalSettings.setting_key.in_([
                    'automated_uploads_enabled',
                    'last_sftp_upload_time',
                ])
            ).delete(synchronize_session=False)
            db.session.add(GlobalSettings(setting_key='automated_uploads_enabled', setting_value='false'))
            db.session.commit()
            tile = AdminHealthService().tile_sftp_uploads()
            assert tile.status == 'amber'
            assert tile.value == 'Disabled'

    def test_sftp_tile_red_when_enabled_but_no_uploads(self, app):
        from extensions import db
        from models import GlobalSettings
        with app.app_context():
            GlobalSettings.query.filter(
                GlobalSettings.setting_key.in_([
                    'automated_uploads_enabled',
                    'last_sftp_upload_time',
                ])
            ).delete(synchronize_session=False)
            db.session.add(GlobalSettings(setting_key='automated_uploads_enabled', setting_value='true'))
            db.session.commit()
            tile = AdminHealthService().tile_sftp_uploads()
            assert tile.status == 'red'

    def test_sftp_tile_green_with_recent_upload(self, app):
        from extensions import db
        from models import GlobalSettings
        with app.app_context():
            GlobalSettings.query.filter(
                GlobalSettings.setting_key.in_([
                    'automated_uploads_enabled',
                    'last_sftp_upload_time',
                ])
            ).delete(synchronize_session=False)
            db.session.add(GlobalSettings(setting_key='automated_uploads_enabled', setting_value='true'))
            recent = (datetime.utcnow() - timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S UTC')
            db.session.add(GlobalSettings(setting_key='last_sftp_upload_time', setting_value=recent))
            db.session.commit()
            tile = AdminHealthService().tile_sftp_uploads()
            assert tile.status == 'green'

    def test_onedrive_tile_red_when_disconnected(self, app):
        with app.app_context():
            mock_svc = MagicMock()
            mock_svc.get_access_token.side_effect = ConnectionError('OneDrive: no token')
            with patch('onedrive_service.OneDriveService', return_value=mock_svc):
                tile = AdminHealthService().tile_onedrive_token()
            assert tile.status == 'red'
            assert tile.value == 'Disconnected'

    def test_onedrive_tile_green_when_connected_with_long_expiry(self, app):
        with app.app_context():
            mock_svc = MagicMock()
            mock_svc.get_access_token.return_value = 'tok'
            mock_svc._token_expires_at = (datetime.utcnow() + timedelta(hours=2)).timestamp()
            with patch('onedrive_service.OneDriveService', return_value=mock_svc):
                tile = AdminHealthService().tile_onedrive_token()
            assert tile.status == 'green'

    def test_onedrive_tile_amber_when_expiring_soon(self, app):
        with app.app_context():
            mock_svc = MagicMock()
            mock_svc.get_access_token.return_value = 'tok'
            mock_svc._token_expires_at = (datetime.utcnow() + timedelta(minutes=15)).timestamp()
            with patch('onedrive_service.OneDriveService', return_value=mock_svc):
                tile = AdminHealthService().tile_onedrive_token()
            assert tile.status == 'amber'

    def test_overall_status_rollup_red_wins(self, app):
        with app.app_context():
            svc = AdminHealthService()
            tiles = [
                HealthTile(key='a', label='A', icon='', status='green'),
                HealthTile(key='b', label='B', icon='', status='red'),
                HealthTile(key='c', label='C', icon='', status='amber'),
            ]
            assert svc.overall_status(tiles) == 'red'

    def test_overall_status_rollup_amber_when_no_red(self, app):
        with app.app_context():
            svc = AdminHealthService()
            tiles = [
                HealthTile(key='a', label='A', icon='', status='green'),
                HealthTile(key='b', label='B', icon='', status='amber'),
            ]
            assert svc.overall_status(tiles) == 'amber'

    def test_overall_status_rollup_unknown_treated_as_amber(self, app):
        with app.app_context():
            svc = AdminHealthService()
            tiles = [
                HealthTile(key='a', label='A', icon='', status='green'),
                HealthTile(key='b', label='B', icon='', status='unknown'),
            ]
            assert svc.overall_status(tiles) == 'amber'

    def test_overall_status_rollup_all_green(self, app):
        with app.app_context():
            svc = AdminHealthService()
            tiles = [HealthTile(key='a', label='A', icon='', status='green')]
            assert svc.overall_status(tiles) == 'green'
