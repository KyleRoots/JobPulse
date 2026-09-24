"""Tenant-aware tearsheet list for incremental auto-removal."""


def test_monitored_tearsheets_myticas_default(monkeypatch):
    monkeypatch.delenv('SCOUT_TENANT', raising=False)
    from incremental_monitoring_service import IncrementalMonitoringService

    svc = IncrementalMonitoringService.__new__(IncrementalMonitoringService)
    pairs = svc._monitored_tearsheets()
    ids = {tid for tid, _ in pairs}
    assert 1231 in ids
    assert 1641 in ids
    assert 3 not in ids


def test_monitored_tearsheets_qualified(monkeypatch):
    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    from incremental_monitoring_service import IncrementalMonitoringService

    svc = IncrementalMonitoringService.__new__(IncrementalMonitoringService)
    pairs = svc._monitored_tearsheets()
    assert pairs == [
        (2, 'Sponsored - Indeed'),
        (3, 'Sponsored - ZipRecruiter'),
        (4, 'Sponsored - LinkedIn'),
    ]
