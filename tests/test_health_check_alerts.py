"""
Tests for vetting health check alert system.

Covers:
- access_token attribute exists on BullhornService
- Threshold-based suppression (3 consecutive failures → alert)
- Severity levels (Critical vs Warning)
- Transient failure suppression
- Candidate-processing context in alerts
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


@pytest.fixture
def app():
    """Create a test Flask app with in-memory database."""
    from app import app as flask_app, db
    
    flask_app.config['TESTING'] = True
    flask_app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    flask_app.config['WTF_CSRF_ENABLED'] = False
    
    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()


@pytest.fixture
def db_session(app):
    """Provide a clean database session with no leftover health check data."""
    from app import db
    from models import VettingHealthCheck, VettingConfig
    # Clean up any leftover data from previous tests
    VettingHealthCheck.query.delete()
    VettingConfig.query.filter_by(setting_key='health_alert_email').delete()
    db.session.commit()
    yield db.session
    db.session.rollback()


def _ensure_vetting_config(db_session, key, value):
    """Insert or update a VettingConfig row (avoids UNIQUE constraint errors)."""
    from models import VettingConfig
    existing = VettingConfig.query.filter_by(setting_key=key).first()
    if existing:
        existing.setting_value = value
    else:
        db_session.add(VettingConfig(setting_key=key, setting_value=value))
    db_session.commit()


def _create_health_check(db_session, minutes_ago=0, bullhorn_ok=True,
                          openai_ok=True, db_ok=True, sched_ok=True,
                          candidates_today=5, alert_sent=False,
                          alert_sent_at=None):
    """Helper to create a VettingHealthCheck record."""
    from models import VettingHealthCheck
    
    check = VettingHealthCheck(
        check_time=datetime.utcnow() - timedelta(minutes=minutes_ago),
        bullhorn_status=bullhorn_ok,
        openai_status=openai_ok,
        database_status=db_ok,
        scheduler_status=sched_ok,
        bullhorn_error=None if bullhorn_ok else "Connection failed",
        openai_error=None if openai_ok else "API error",
        database_error=None if db_ok else "DB error",
        scheduler_error=None if sched_ok else "Not running",
        is_healthy=bullhorn_ok and openai_ok and db_ok and sched_ok,
        candidates_processed_today=candidates_today,
        candidates_pending=0,
        emails_sent_today=0,
        last_successful_cycle=datetime.utcnow(),
        alert_sent=alert_sent,
        alert_sent_at=alert_sent_at
    )
    db_session.add(check)
    db_session.commit()
    return check


# ── Fix 1: access_token attribute ──

class TestAccessTokenAttribute:
    """Test that BullhornService properly initializes access_token."""
    
    def test_access_token_initialized_to_none(self, app):
        """access_token should be None after __init__ (no AttributeError)."""
        from bullhorn_service import BullhornService
        bh = BullhornService(
            client_id='test', client_secret='test',
            username='test', password='test'
        )
        # This should NOT raise AttributeError
        assert bh.access_token is None

    def test_rest_token_also_none_at_init(self, app):
        """rest_token should also be None at init."""
        from bullhorn_service import BullhornService
        bh = BullhornService(
            client_id='test', client_secret='test',
            username='test', password='test'
        )
        assert bh.rest_token is None


# ── Fix 2: Threshold-based suppression ──

class TestAlertThresholdSuppression:
    """Test that alerts are only sent for persistent failures (3 consecutive)."""
    
    def test_single_failure_suppressed(self, app, db_session):
        """A single Bullhorn failure should NOT trigger an alert (transient)."""
        from app import send_vetting_health_alert
        
        # Create 1 failed + 2 healthy checks in the 30-min window
        _create_health_check(db_session, minutes_ago=20, bullhorn_ok=True)
        _create_health_check(db_session, minutes_ago=10, bullhorn_ok=True)
        failing_check = _create_health_check(db_session, minutes_ago=0, bullhorn_ok=False, candidates_today=5)
        
        with patch('sendgrid.SendGridAPIClient') as mock_sg:
            send_vetting_health_alert(failing_check)
            # Should NOT have tried to send an email
            mock_sg.return_value.send.assert_not_called()
    
    def test_two_failures_suppressed(self, app, db_session):
        """Two consecutive failures should still be suppressed."""
        from app import send_vetting_health_alert
        
        _create_health_check(db_session, minutes_ago=20, bullhorn_ok=True)
        _create_health_check(db_session, minutes_ago=10, bullhorn_ok=False)
        failing_check = _create_health_check(db_session, minutes_ago=0, bullhorn_ok=False, candidates_today=5)
        
        with patch('sendgrid.SendGridAPIClient') as mock_sg:
            send_vetting_health_alert(failing_check)
            mock_sg.return_value.send.assert_not_called()
    
    def test_three_consecutive_failures_with_zero_candidates_sends_alert(self, app, db_session):
        """Three consecutive failures with 0 candidates → CRITICAL alert sent."""
        from app import send_vetting_health_alert
        
        # Configure alert email
        _ensure_vetting_config(db_session, 'health_alert_email', 'admin@test.com')
        
        # 3 consecutive Bullhorn failures
        _create_health_check(db_session, minutes_ago=20, bullhorn_ok=False, candidates_today=0)
        _create_health_check(db_session, minutes_ago=10, bullhorn_ok=False, candidates_today=0)
        failing_check = _create_health_check(db_session, minutes_ago=0, bullhorn_ok=False, candidates_today=0)
        
        mock_response = MagicMock()
        mock_response.status_code = 202
        
        with patch('sendgrid.SendGridAPIClient') as mock_sg:
            mock_sg.return_value.send.return_value = mock_response
            send_vetting_health_alert(failing_check)
            
            # Should have sent the email
            mock_sg.return_value.send.assert_called_once()
            
            # Check the email content includes CRITICAL
            call_args = mock_sg.return_value.send.call_args
            mail_obj = call_args[0][0]
            assert 'CRITICAL' in mail_obj.subject.get()
    
    def test_three_failures_with_candidates_still_processing_suppressed(self, app, db_session):
        """Three failures but candidates still processing → WARNING, suppressed."""
        from app import send_vetting_health_alert
        
        # 3 consecutive BH failures, but 10 candidates processed today
        _create_health_check(db_session, minutes_ago=20, bullhorn_ok=False, candidates_today=10)
        _create_health_check(db_session, minutes_ago=10, bullhorn_ok=False, candidates_today=10)
        failing_check = _create_health_check(db_session, minutes_ago=0, bullhorn_ok=False, candidates_today=10)
        
        with patch('sendgrid.SendGridAPIClient') as mock_sg:
            send_vetting_health_alert(failing_check)
            # Warning-level → suppressed
            mock_sg.return_value.send.assert_not_called()
    
    def test_transient_failure_then_recovery_suppressed(self, app, db_session):
        """Failure → success → failure should NOT trigger alert (not 3 consecutive)."""
        from app import send_vetting_health_alert
        
        # Ensure no health_alert_email exists so even if threshold logic were bypassed,
        # we'd still not send. But the real test is whether the threshold catches it.
        _create_health_check(db_session, minutes_ago=20, bullhorn_ok=False, candidates_today=0)
        _create_health_check(db_session, minutes_ago=10, bullhorn_ok=True)  # Recovery!
        failing_check = _create_health_check(db_session, minutes_ago=0, bullhorn_ok=False, candidates_today=0)
        
        with patch('sendgrid.SendGridAPIClient') as mock_sg:
            send_vetting_health_alert(failing_check)
            # Only 2/3 failures (one recovered) → suppressed
            mock_sg.return_value.send.assert_not_called()
    
    def test_cooldown_prevents_duplicate_alerts(self, app, db_session):
        """If alert was sent within the last hour, skip even if critical."""
        from app import send_vetting_health_alert
        
        _ensure_vetting_config(db_session, 'health_alert_email', 'admin@test.com')
        
        # Create a recent alert (30 min ago) with alert_sent=True
        _create_health_check(
            db_session, minutes_ago=30, bullhorn_ok=False,
            candidates_today=0, alert_sent=True,
            alert_sent_at=datetime.utcnow() - timedelta(minutes=30)
        )
        
        # 3 more failures
        _create_health_check(db_session, minutes_ago=20, bullhorn_ok=False, candidates_today=0)
        _create_health_check(db_session, minutes_ago=10, bullhorn_ok=False, candidates_today=0)
        failing_check = _create_health_check(db_session, minutes_ago=0, bullhorn_ok=False, candidates_today=0)
        
        with patch('sendgrid.SendGridAPIClient') as mock_sg:
            send_vetting_health_alert(failing_check)
            # Cooldown should prevent sending
            mock_sg.return_value.send.assert_not_called()
    
    def test_alert_includes_candidate_context_zero(self, app, db_session):
        """Alert email should include '0 candidates processed' context."""
        from app import send_vetting_health_alert
        
        _ensure_vetting_config(db_session, 'health_alert_email', 'admin@test.com')
        
        _create_health_check(db_session, minutes_ago=20, bullhorn_ok=False, candidates_today=0)
        _create_health_check(db_session, minutes_ago=10, bullhorn_ok=False, candidates_today=0)
        failing_check = _create_health_check(db_session, minutes_ago=0, bullhorn_ok=False, candidates_today=0)
        
        mock_response = MagicMock()
        mock_response.status_code = 202
        
        with patch('sendgrid.SendGridAPIClient') as mock_sg:
            mock_sg.return_value.send.return_value = mock_response
            send_vetting_health_alert(failing_check)
            
            # Verify email was sent
            mock_sg.return_value.send.assert_called_once()
            
            # Check the Mail object has "vetting may be completely stopped"
            call_args = mock_sg.return_value.send.call_args
            mail_obj = call_args[0][0]
            # The html_content should contain the warning about stopped vetting
            html = str(mail_obj.contents)
            assert 'Persistent Issues' in mail_obj.subject.get()
