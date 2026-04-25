"""
Tests for the `pending_eligible` stat in detect_unvetted_applications.

Regression guard against the misleading `pending_vetting=14588` log line
which counted pre-cutoff historical records that the cutoff filter would
never actually process. The stat must respect the configured
vetting_cutoff_date so operators see the truly-actionable backlog.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest


@pytest.fixture(autouse=True)
def _cleanup(app):
    yield
    from app import db
    from models import ParsedEmail, VettingConfig
    with app.app_context():
        ParsedEmail.query.filter(ParsedEmail.bullhorn_candidate_id.in_([
            991001, 991002, 991003,
        ])).delete(synchronize_session=False)
        VettingConfig.query.filter_by(setting_key='vetting_cutoff_date').delete()
        db.session.commit()


def _seed(app, *, pre_cutoff_count: int, post_cutoff_count: int, cutoff: datetime):
    from app import db
    from models import ParsedEmail
    with app.app_context():
        next_id = 991001
        for _ in range(pre_cutoff_count):
            db.session.add(ParsedEmail(
                sender_email='noreply@indeed.com',
                recipient_email='jobs@lyntrix.com',
                subject='Pre-cutoff app',
                status='completed',
                bullhorn_candidate_id=next_id,
                bullhorn_job_id=11111,
                vetted_at=None,
                received_at=cutoff - timedelta(hours=2),
                processed_at=cutoff - timedelta(hours=1),
            ))
            next_id += 1
        for _ in range(post_cutoff_count):
            db.session.add(ParsedEmail(
                sender_email='noreply@indeed.com',
                recipient_email='jobs@lyntrix.com',
                subject='Post-cutoff app',
                status='completed',
                bullhorn_candidate_id=next_id,
                bullhorn_job_id=11111,
                vetted_at=None,
                received_at=cutoff + timedelta(hours=1),
                processed_at=cutoff + timedelta(hours=2),
            ))
            next_id += 1
        db.session.commit()


@patch('candidate_vetting_service.BullhornService')
def test_pending_eligible_excludes_pre_cutoff(mock_bullhorn_cls, app, caplog):
    """The new stat must report only post-cutoff actionable records."""
    from models import VettingConfig
    from candidate_vetting_service import CandidateVettingService

    cutoff = datetime.utcnow().replace(microsecond=0)
    with app.app_context():
        VettingConfig.set_value(
            'vetting_cutoff_date',
            cutoff.strftime('%Y-%m-%d %H:%M:%S'),
            description='Test cutoff',
        )
    _seed(app, pre_cutoff_count=2, post_cutoff_count=1, cutoff=cutoff)

    mock_bullhorn = Mock()
    mock_bullhorn.authenticate.return_value = True
    mock_bullhorn.base_url = 'https://rest.bullhorn.com/rest-services/'
    mock_bullhorn.rest_token = 'fake-token'
    mock_bullhorn.session = Mock()
    mock_bullhorn_cls.return_value = mock_bullhorn

    service = CandidateVettingService(bullhorn_service=mock_bullhorn)

    import logging as _logging
    with caplog.at_level(_logging.INFO):
        with app.app_context():
            service.detect_unvetted_applications(limit=25)

    stats_lines = [r.message for r in caplog.records if 'ParsedEmail stats' in r.message]
    assert stats_lines, "Expected a ParsedEmail stats log line"
    line = stats_lines[-1]

    # Post-cutoff record(s) seeded = 1; pre-cutoff = 2 (should be excluded)
    assert 'pending_eligible=1' in line, f"Expected pending_eligible=1, got: {line}"
    assert 'pre_cutoff_excluded=2' in line, f"Expected pre_cutoff_excluded=2, got: {line}"
    # Sanity guard: regression marker for the old misleading metric
    assert 'pending_vetting=' not in line, (
        "Old misleading 'pending_vetting' metric should be replaced; "
        f"got: {line}"
    )


@patch('candidate_vetting_service.BullhornService')
def test_pending_eligible_equals_unvetted_when_no_cutoff(mock_bullhorn_cls, app, caplog):
    """With no cutoff configured, pending_eligible should match total unvetted."""
    from candidate_vetting_service import CandidateVettingService

    # No cutoff set — the autouse cleanup ensures a clean slate
    base = datetime.utcnow().replace(microsecond=0) - timedelta(days=10)
    _seed(app, pre_cutoff_count=2, post_cutoff_count=1, cutoff=base)

    mock_bullhorn = Mock()
    mock_bullhorn.authenticate.return_value = True
    mock_bullhorn.base_url = 'https://rest.bullhorn.com/rest-services/'
    mock_bullhorn.rest_token = 'fake-token'
    mock_bullhorn.session = Mock()
    mock_bullhorn_cls.return_value = mock_bullhorn

    service = CandidateVettingService(bullhorn_service=mock_bullhorn)

    import logging as _logging
    with caplog.at_level(_logging.INFO):
        with app.app_context():
            service.detect_unvetted_applications(limit=25)

    stats_lines = [r.message for r in caplog.records if 'ParsedEmail stats' in r.message]
    assert stats_lines, "Expected a ParsedEmail stats log line"
    line = stats_lines[-1]

    # All 3 seeded rows (no cutoff filter) -> pending_eligible == total_unvetted
    assert 'pending_eligible=3' in line, f"Expected pending_eligible=3, got: {line}"
    assert 'pre_cutoff_excluded=0' in line, f"Expected pre_cutoff_excluded=0, got: {line}"


def test_resolve_vetting_cutoff_handles_invalid_format(app, caplog):
    """Malformed cutoff value should disable cutoff (return None) and log error."""
    from models import VettingConfig
    from screening.detection import _resolve_vetting_cutoff

    with app.app_context():
        VettingConfig.set_value(
            'vetting_cutoff_date',
            'not-a-real-datetime',
            description='Bad value',
        )
        import logging as _logging
        with caplog.at_level(_logging.ERROR):
            result = _resolve_vetting_cutoff()

    assert result is None
    assert any('Invalid vetting_cutoff_date format' in r.message for r in caplog.records)


def test_resolve_vetting_cutoff_returns_none_when_unset(app):
    """Missing config returns None (cutoff disabled, no error)."""
    from screening.detection import _resolve_vetting_cutoff
    with app.app_context():
        assert _resolve_vetting_cutoff() is None


@patch('candidate_vetting_service.BullhornService')
def test_pending_eligible_includes_received_at_equal_to_cutoff(mock_bullhorn_cls, app, caplog):
    """Boundary: a record whose received_at == cutoff must be counted as eligible (>=)."""
    from app import db
    from models import ParsedEmail, VettingConfig
    from candidate_vetting_service import CandidateVettingService

    cutoff = datetime.utcnow().replace(microsecond=0)
    with app.app_context():
        VettingConfig.set_value(
            'vetting_cutoff_date',
            cutoff.strftime('%Y-%m-%d %H:%M:%S'),
            description='Boundary test',
        )
        db.session.add(ParsedEmail(
            sender_email='noreply@indeed.com',
            recipient_email='jobs@lyntrix.com',
            subject='Boundary app',
            status='completed',
            bullhorn_candidate_id=991001,
            bullhorn_job_id=11111,
            vetted_at=None,
            received_at=cutoff,  # exactly equal
            processed_at=cutoff,
        ))
        db.session.commit()

    mock_bullhorn = Mock()
    mock_bullhorn.authenticate.return_value = True
    mock_bullhorn.base_url = 'https://rest.bullhorn.com/rest-services/'
    mock_bullhorn.rest_token = 'fake-token'
    mock_bullhorn.session = Mock()
    mock_bullhorn_cls.return_value = mock_bullhorn

    service = CandidateVettingService(bullhorn_service=mock_bullhorn)

    import logging as _logging
    with caplog.at_level(_logging.INFO):
        with app.app_context():
            service.detect_unvetted_applications(limit=25)

    line = [r.message for r in caplog.records if 'ParsedEmail stats' in r.message][-1]
    assert 'pending_eligible=1' in line, f"Boundary record (received_at==cutoff) should be eligible: {line}"
    assert 'pre_cutoff_excluded=0' in line, f"Boundary record should not be excluded: {line}"


@patch('candidate_vetting_service.BullhornService')
def test_pending_eligible_excludes_null_received_at_when_cutoff_active(mock_bullhorn_cls, app, caplog):
    """A record with NULL received_at must NOT be counted as pending_eligible when a cutoff is active."""
    from app import db
    from models import ParsedEmail, VettingConfig
    from candidate_vetting_service import CandidateVettingService

    cutoff = datetime.utcnow().replace(microsecond=0)
    with app.app_context():
        VettingConfig.set_value(
            'vetting_cutoff_date',
            cutoff.strftime('%Y-%m-%d %H:%M:%S'),
            description='NULL received_at test',
        )
        pe = ParsedEmail(
            sender_email='noreply@indeed.com',
            recipient_email='jobs@lyntrix.com',
            subject='Null received_at app',
            status='completed',
            bullhorn_candidate_id=991002,
            bullhorn_job_id=11111,
            vetted_at=None,
            processed_at=None,
        )
        db.session.add(pe)
        db.session.commit()
        # ParsedEmail.received_at has default=datetime.utcnow on the model,
        # so we must NULL it explicitly via raw UPDATE to bypass the default
        # and simulate legacy rows that predate received_at tracking.
        db.session.execute(
            db.text("UPDATE parsed_email SET received_at = NULL WHERE id = :id"),
            {"id": pe.id},
        )
        db.session.commit()

    mock_bullhorn = Mock()
    mock_bullhorn.authenticate.return_value = True
    mock_bullhorn.base_url = 'https://rest.bullhorn.com/rest-services/'
    mock_bullhorn.rest_token = 'fake-token'
    mock_bullhorn.session = Mock()
    mock_bullhorn_cls.return_value = mock_bullhorn

    service = CandidateVettingService(bullhorn_service=mock_bullhorn)

    import logging as _logging
    with caplog.at_level(_logging.INFO):
        with app.app_context():
            service.detect_unvetted_applications(limit=25)

    line = [r.message for r in caplog.records if 'ParsedEmail stats' in r.message][-1]
    assert 'pending_eligible=0' in line, f"NULL received_at should NOT be eligible when cutoff active: {line}"
    assert 'pre_cutoff_excluded=1' in line, f"NULL received_at row should fall into excluded bucket: {line}"


def test_resolve_vetting_cutoff_accepts_iso_format(app):
    """ISO 'YYYY-MM-DDTHH:MM:SS' format should parse correctly."""
    from models import VettingConfig
    from screening.detection import _resolve_vetting_cutoff

    with app.app_context():
        VettingConfig.set_value(
            'vetting_cutoff_date',
            '2026-04-01T06:46:26',
            description='ISO format test',
        )
        result = _resolve_vetting_cutoff()
        assert result == datetime(2026, 4, 1, 6, 46, 26)
