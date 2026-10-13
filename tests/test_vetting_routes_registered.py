"""Regression guard: vetting blueprint registration and legacy import surface.

These tests exist purely to lock in the routes/vetting_handlers/ package split
(routes/vetting.py: 2,274 → 56 lines). They catch:
  - A future refactor silently dropping one of the 34 vetting.* endpoints
  - A circular-import or partial-import breaking the package at boot
  - The legacy import surface being removed ('from routes.vetting import ...')

They do NOT test handler logic — only that routes are registered and importable.
"""

EXPECTED_VETTING_ENDPOINTS = {
    'vetting.activity_monitor',
    'vetting.block_retry',
    'vetting.create_test_vetting_note',
    'vetting.dismiss_pending',
    'vetting.embedding_audit',
    'vetting.export_escalations_csv',
    'vetting.export_filtered_csv',
    'vetting.extract_all_job_requirements',
    'vetting.force_release_lock',
    'vetting.fraud_calibration_label',
    'vetting.fraud_calibration_sample',
    'vetting.full_clean_slate',
    'vetting.process_backlog',
    'vetting.refresh_job_requirements',
    'vetting.rescreen_count',
    'vetting.rescreen_recent',
    'vetting.rescreen_remote_misfires',
    'vetting.retry_failed_notes',
    'vetting.recover_missing_resumes',
    'vetting.revet_candidate',
    'vetting.run_health_check_now',
    'vetting.run_vetting_now',
    'vetting.save_job_requirements',
    'vetting.save_job_threshold',
    'vetting.save_vetting_settings',
    'vetting.screening_compliance_metrics',
    'vetting.send_embedding_digest',
    'vetting.send_test_vetting_email',
    'vetting.show_sample_notes',
    'vetting.start_fresh',
    'vetting.sync_job_requirements',
    'vetting.unblock_retry',
    'vetting.vetting_diagnostic',
    'vetting.vetting_settings',
}


def test_all_vetting_endpoints_registered(app):
    """All 34 vetting.* endpoints must be present in the URL map."""
    registered = {
        rule.endpoint
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith('vetting.')
    }
    missing = EXPECTED_VETTING_ENDPOINTS - registered
    extra = registered - EXPECTED_VETTING_ENDPOINTS

    assert not missing, (
        f"These vetting endpoints are missing from the URL map — "
        f"a route handler was likely dropped during a refactor: {sorted(missing)}"
    )
    assert not extra, (
        f"Unexpected new vetting endpoints found — update EXPECTED_VETTING_ENDPOINTS "
        f"if these are intentional additions: {sorted(extra)}"
    )
    assert len(registered) == 34, (
        f"Expected exactly 34 vetting.* endpoints, found {len(registered)}"
    )


def test_vetting_blueprint_legacy_import():
    """Legacy callers must still be able to import vetting_bp and get_db from routes.vetting."""
    from routes.vetting import vetting_bp, get_db

    assert vetting_bp is not None, "vetting_bp must be importable from routes.vetting"
    assert vetting_bp.name == 'vetting', (
        f"Blueprint name must be 'vetting', got '{vetting_bp.name}'"
    )

    db = get_db()
    assert db is not None, "get_db() must return a non-None SQLAlchemy instance"


def test_vetting_handler_submodules_importable():
    """Every handler submodule must be importable without error."""
    from routes.vetting_handlers import (
        settings,
        dispatch,
        diagnostics,
        email,
        job_requirements,
        embedding_audit,
    )
    for mod in (settings, dispatch, diagnostics, email, job_requirements, embedding_audit):
        assert mod is not None


_PROTECTED_GETS = (
    '/screening/fraud-calibration',
    '/screening/compliance-metrics',
)
_PROTECTED_POSTS = (
    '/screening/fraud-calibration/1',
    '/screening/recover-missing-resumes',
)


def _user_without_vetting(app, username):
    from app import db
    from models import User
    from werkzeug.security import generate_password_hash

    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(
                username=username,
                email=f'{username}@test.com',
                is_admin=False,
                company='Myticas Consulting',
            )
            user.password_hash = generate_password_hash('testpass', method='pbkdf2:sha256')
            db.session.add(user)
        user.is_admin = False
        user.set_modules([])
        db.session.commit()
        return user


def test_new_vetting_routes_reject_anonymous_users(client):
    """The added routes stay behind login. No recovery or calibration write runs."""
    from unittest.mock import patch

    with patch('tasks.run_resume_recovery') as recovery:
        for path in _PROTECTED_GETS:
            response = client.get(path)
            assert response.status_code == 302, path
            assert '/login' in response.headers.get('Location', ''), path
        for path in _PROTECTED_POSTS:
            response = client.post(path, data={'label': 'tp', 'hours': '24'})
            assert response.status_code == 302, path
            assert '/login' in response.headers.get('Location', ''), path
        recovery.assert_not_called()


def test_new_vetting_routes_reject_users_without_the_module(app):
    """A signed-in user without scout_vetting is turned away before any write."""
    from unittest.mock import patch

    _user_without_vetting(app, 'vetting_route_outsider')
    client = app.test_client()
    login = client.post('/login', data={
        'username': 'vetting_route_outsider',
        'password': 'testpass',
    })
    assert login.status_code in (200, 302)

    json_headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    with patch('tasks.run_resume_recovery') as recovery:
        for path in _PROTECTED_GETS:
            response = client.get(path, headers=json_headers)
            assert response.status_code == 403, path
            assert response.get_json().get('error') == 'Module access required'
        for path in _PROTECTED_POSTS:
            response = client.post(path, json={'label': 'tp', 'hours': 24})
            assert response.status_code == 403, path
            assert response.get_json().get('error') == 'Module access required'
        recovery.assert_not_called()
