"""Qualified auto-merge stays off until it is explicitly turned back on."""
from duplicate_merge_service import DuplicateMergeService, duplicate_merge_enabled


def test_myticas_merge_stays_enabled(monkeypatch):
    monkeypatch.delenv('SCOUT_TENANT', raising=False)
    monkeypatch.delenv('DUPLICATE_MERGE_ENABLED', raising=False)
    assert duplicate_merge_enabled() is True


def test_qualified_merge_is_disabled(monkeypatch):
    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    monkeypatch.delenv('DUPLICATE_MERGE_ENABLED', raising=False)
    assert duplicate_merge_enabled() is False


def test_explicit_flag_overrides_tenant(monkeypatch):
    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    monkeypatch.setenv('DUPLICATE_MERGE_ENABLED', 'true')
    assert duplicate_merge_enabled() is True
    monkeypatch.setenv('DUPLICATE_MERGE_ENABLED', 'false')
    monkeypatch.delenv('SCOUT_TENANT', raising=False)
    assert duplicate_merge_enabled() is False


def test_qualified_scheduled_check_does_not_authenticate(monkeypatch):
    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    monkeypatch.delenv('DUPLICATE_MERGE_ENABLED', raising=False)
    svc = DuplicateMergeService()

    def _auth():
        raise AssertionError('Bullhorn auth should not run while merge is disabled')

    monkeypatch.setattr(svc, '_ensure_auth', _auth)
    stats = svc.run_scheduled_check()
    assert stats['disabled'] is True
    assert stats['merged'] == 0
    assert stats['candidates_checked'] == 0


def test_qualified_bulk_scan_does_not_authenticate(monkeypatch):
    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    monkeypatch.delenv('DUPLICATE_MERGE_ENABLED', raising=False)
    svc = DuplicateMergeService()

    def _auth():
        raise AssertionError('Bullhorn auth should not run while merge is disabled')

    monkeypatch.setattr(svc, '_ensure_auth', _auth)
    stats = svc.run_bulk_scan()
    assert stats['disabled'] is True
    assert stats['merged'] == 0
