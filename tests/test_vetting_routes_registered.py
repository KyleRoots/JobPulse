"""Regression guard: vetting blueprint registration and legacy import surface.

These tests exist purely to lock in the routes/vetting_handlers/ package split
(routes/vetting.py: 2,274 → 56 lines). They catch:
  - A future refactor silently dropping one of the 30 vetting.* endpoints
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
    'vetting.full_clean_slate',
    'vetting.process_backlog',
    'vetting.refresh_job_requirements',
    'vetting.rescreen_count',
    'vetting.rescreen_recent',
    'vetting.rescreen_remote_misfires',
    'vetting.retry_failed_notes',
    'vetting.revet_candidate',
    'vetting.run_health_check_now',
    'vetting.run_vetting_now',
    'vetting.save_job_requirements',
    'vetting.save_job_threshold',
    'vetting.save_vetting_settings',
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
    """All 30 vetting.* endpoints must be present in the URL map."""
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
    assert len(registered) == 30, (
        f"Expected exactly 30 vetting.* endpoints, found {len(registered)}"
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
