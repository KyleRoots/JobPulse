"""Tenant-aware inbound Candidate.status for board/email ingest."""


def test_inbound_status_myticas_default(monkeypatch):
    monkeypatch.delenv('SCOUT_TENANT', raising=False)
    from feeds.feed_config import get_inbound_candidate_status

    assert get_inbound_candidate_status() == 'Online Applicant'


def test_inbound_status_qualified(monkeypatch):
    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    from feeds.feed_config import get_inbound_candidate_status

    assert get_inbound_candidate_status() == 'New Lead'


def test_map_to_bullhorn_fields_qualified_new_lead(monkeypatch):
    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    from email_inbound_service import EmailInboundService

    mapped = EmailInboundService().map_to_bullhorn_fields(
        {'first_name': 'Avery', 'last_name': 'Renaud', 'email': 'a@example.com'},
        {},
        'ZipRecruiter Job Board',
    )
    assert mapped['status'] == 'New Lead'
    assert mapped['source'] == 'ZipRecruiter Job Board'


def test_map_to_bullhorn_fields_myticas_online_applicant(monkeypatch):
    monkeypatch.delenv('SCOUT_TENANT', raising=False)
    from email_inbound_service import EmailInboundService

    mapped = EmailInboundService().map_to_bullhorn_fields(
        {'first_name': 'Sam', 'last_name': 'Lee', 'email': 's@example.com'},
        {},
        'ZipRecruiter Job Board',
    )
    assert mapped['status'] == 'Online Applicant'
    assert mapped['source'] == 'ZipRecruiter Job Board'
