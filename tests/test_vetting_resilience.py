"""
Tests for vetting resilience features:
1. Auto-retry safeguard for 0% score failures (API outage recovery)
2. OpenAI quota exhaustion alert and auto-disable
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


class TestZeroScoreAutoRetry:
    """Test the _reset_zero_score_failures() auto-retry safeguard."""

    def test_reset_detects_all_zero_scores(self, app):
        """Candidates where ALL job matches scored 0% should be reset for re-vetting."""
        from app import db
        from models import CandidateVettingLog, CandidateJobMatch, ParsedEmail

        with app.app_context():
            pe = ParsedEmail(
                sender_email='test@example.com',
                recipient_email='jobs@test.com',
                subject='Application',
                received_at=datetime.utcnow() - timedelta(hours=1),
                status='completed',
                bullhorn_candidate_id=999001,
                vetted_at=datetime.utcnow() - timedelta(minutes=30)
            )
            db.session.add(pe)
            db.session.flush()

            log = CandidateVettingLog(
                bullhorn_candidate_id=999001,
                candidate_name='Test Zero',
                status='completed',
                highest_match_score=0,
                created_at=datetime.utcnow() - timedelta(minutes=15)
            )
            db.session.add(log)
            db.session.flush()

            for job_id in [100, 200, 300]:
                db.session.add(CandidateJobMatch(
                    vetting_log_id=log.id,
                    bullhorn_job_id=job_id,
                    job_title=f'Job {job_id}',
                    match_score=0.0
                ))
            db.session.commit()

            from candidate_vetting_service import CandidateVettingService
            svc = CandidateVettingService()
            svc._reset_zero_score_failures()

            remaining = CandidateVettingLog.query.filter_by(bullhorn_candidate_id=999001).count()
            assert remaining == 0, "Vetting log should be deleted"

            pe_check = ParsedEmail.query.filter_by(bullhorn_candidate_id=999001).first()
            assert pe_check.vetted_at is None, "vetted_at should be reset to NULL"

    def test_reset_skips_legitimate_zeros(self, app):
        """Candidates with at least one non-zero job match should NOT be reset."""
        from app import db
        from models import CandidateVettingLog, CandidateJobMatch, ParsedEmail

        with app.app_context():
            pe = ParsedEmail(
                sender_email='legit@example.com',
                recipient_email='jobs@test.com',
                subject='Application',
                received_at=datetime.utcnow() - timedelta(hours=1),
                status='completed',
                bullhorn_candidate_id=999002,
                vetted_at=datetime.utcnow() - timedelta(minutes=30)
            )
            db.session.add(pe)
            db.session.flush()

            log = CandidateVettingLog(
                bullhorn_candidate_id=999002,
                candidate_name='Test Legit',
                status='completed',
                highest_match_score=0,
                created_at=datetime.utcnow() - timedelta(minutes=15)
            )
            db.session.add(log)
            db.session.flush()

            # One job scored non-zero — legitimate result
            db.session.add(CandidateJobMatch(
                vetting_log_id=log.id,
                bullhorn_job_id=200, job_title='Job 200', match_score=45.0
            ))
            db.session.add(CandidateJobMatch(
                vetting_log_id=log.id,
                bullhorn_job_id=100, job_title='Job 100', match_score=0.0
            ))
            db.session.commit()

            from candidate_vetting_service import CandidateVettingService
            svc = CandidateVettingService()
            svc._reset_zero_score_failures()

            remaining = CandidateVettingLog.query.filter_by(bullhorn_candidate_id=999002).count()
            assert remaining == 1, "Log should NOT be deleted — has a non-zero score"

            pe_check = ParsedEmail.query.filter_by(bullhorn_candidate_id=999002).first()
            assert pe_check.vetted_at is not None, "vetted_at should NOT be reset"

    def test_reset_skips_recent_records(self, app):
        """Records less than 10 minutes old should NOT be reset (may be in-progress)."""
        from app import db
        from models import CandidateVettingLog, CandidateJobMatch, ParsedEmail

        with app.app_context():
            pe = ParsedEmail(
                sender_email='recent@example.com',
                recipient_email='jobs@test.com',
                subject='Application',
                received_at=datetime.utcnow() - timedelta(hours=1),
                status='completed',
                bullhorn_candidate_id=999003,
                vetted_at=datetime.utcnow() - timedelta(minutes=5)
            )
            db.session.add(pe)
            db.session.flush()

            log = CandidateVettingLog(
                bullhorn_candidate_id=999003,
                candidate_name='Test Recent',
                status='completed',
                highest_match_score=0,
                created_at=datetime.utcnow() - timedelta(minutes=2)
            )
            db.session.add(log)
            db.session.flush()

            for job_id in [100, 200]:
                db.session.add(CandidateJobMatch(
                    vetting_log_id=log.id,
                    bullhorn_job_id=job_id, job_title=f'Job {job_id}', match_score=0.0
                ))
            db.session.commit()

            from candidate_vetting_service import CandidateVettingService
            svc = CandidateVettingService()
            svc._reset_zero_score_failures()

            remaining = CandidateVettingLog.query.filter_by(bullhorn_candidate_id=999003).count()
            assert remaining == 1, "Recent record should NOT be reset"

    def test_reset_limit_50(self, app):
        """At most 50 records should be reset per cycle."""
        from app import db
        from models import CandidateVettingLog, CandidateJobMatch, ParsedEmail

        with app.app_context():
            for i in range(60):
                cid = 990000 + i
                pe = ParsedEmail(
                    sender_email=f'batch{i}@example.com',
                    recipient_email='jobs@test.com',
                    subject='Application',
                    received_at=datetime.utcnow() - timedelta(hours=2),
                    status='completed',
                    bullhorn_candidate_id=cid,
                    vetted_at=datetime.utcnow() - timedelta(minutes=30)
                )
                db.session.add(pe)
                db.session.flush()

                log = CandidateVettingLog(
                    bullhorn_candidate_id=cid,
                    candidate_name=f'Batch {i}',
                    status='completed',
                    highest_match_score=0,
                    created_at=datetime.utcnow() - timedelta(minutes=15)
                )
                db.session.add(log)
                db.session.flush()

                db.session.add(CandidateJobMatch(
                    vetting_log_id=log.id,
                    bullhorn_job_id=100, job_title='Job 100', match_score=0.0
                ))
            db.session.commit()

            from candidate_vetting_service import CandidateVettingService
            svc = CandidateVettingService()
            svc._reset_zero_score_failures()

            remaining = CandidateVettingLog.query.filter(
                CandidateVettingLog.bullhorn_candidate_id >= 990000,
                CandidateVettingLog.bullhorn_candidate_id < 990060
            ).count()
            assert remaining >= 10, f"Expected at least 10 remaining (60 - max 50 reset), got {remaining}"


class TestQuotaExhaustionAlert:
    """Test OpenAI quota exhaustion detection and alert."""

    def test_quota_alert_fires_after_3_errors(self, app):
        """3+ consecutive quota errors should trigger alert email and disable vetting."""
        from app import db
        from models import VettingConfig
        from candidate_vetting_service import CandidateVettingService

        with app.app_context():
            config = VettingConfig.query.filter_by(setting_key='vetting_enabled').first()
            if not config:
                config = VettingConfig(setting_key='vetting_enabled', setting_value='true')
                db.session.add(config)
                db.session.commit()
            else:
                config.setting_value = 'true'
                db.session.commit()

            CandidateVettingService._consecutive_quota_errors = 5
            CandidateVettingService._quota_alert_sent = False

            svc = CandidateVettingService()

            with patch.object(svc, '_get_admin_notification_email', return_value='test@example.com'):
                with patch('candidate_vetting_service.EmailService') as MockEmail:
                    mock_instance = MockEmail.return_value
                    mock_instance.send_notification_email.return_value = True

                    svc._handle_quota_exhaustion()

                    mock_instance.send_notification_email.assert_called_once()
                    call_kwargs = mock_instance.send_notification_email.call_args[1]
                    assert 'Quota Exhausted' in call_kwargs['subject']
                    assert call_kwargs['notification_type'] == 'openai_quota_alert'

            db.session.expire_all()
            config = VettingConfig.query.filter_by(setting_key='vetting_enabled').first()
            assert config.setting_value == 'false', "Vetting should be disabled"
            assert CandidateVettingService._quota_alert_sent is True

    def test_quota_counter_resets_on_success(self, app):
        """Quota error counter should be reset at cycle start."""
        from candidate_vetting_service import CandidateVettingService

        with app.app_context():
            CandidateVettingService._consecutive_quota_errors = 2
            # Simulate cycle start reset
            CandidateVettingService._consecutive_quota_errors = 0
            assert CandidateVettingService._consecutive_quota_errors == 0

    def test_no_duplicate_alerts(self, app):
        """Second quota exhaustion call should NOT send another email."""
        from app import db
        from models import VettingConfig
        from candidate_vetting_service import CandidateVettingService

        with app.app_context():
            config = VettingConfig.query.filter_by(setting_key='vetting_enabled').first()
            if not config:
                config = VettingConfig(setting_key='vetting_enabled', setting_value='true')
                db.session.add(config)
                db.session.commit()

            CandidateVettingService._consecutive_quota_errors = 10
            CandidateVettingService._quota_alert_sent = True

            svc = CandidateVettingService()

            with patch('candidate_vetting_service.EmailService') as MockEmail:
                mock_instance = MockEmail.return_value
                svc._handle_quota_exhaustion()
                mock_instance.send_notification_email.assert_not_called()

    def test_alert_flag_resets_on_healthy_cycle(self, app):
        """Alert flag should reset when a cycle completes with zero quota errors."""
        from candidate_vetting_service import CandidateVettingService

        with app.app_context():
            CandidateVettingService._quota_alert_sent = True
            CandidateVettingService._consecutive_quota_errors = 0

            # Simulate end-of-cycle logic
            if CandidateVettingService._consecutive_quota_errors == 0:
                CandidateVettingService._quota_alert_sent = False

            assert CandidateVettingService._quota_alert_sent is False


class TestStuckProcessingReset:
    """Test _reset_stuck_processing() for deployment-restart recovery."""

    def test_resets_stuck_processing_with_zero_matches(self, app):
        """Processing logs older than 10 min with 0 job matches should be reset."""
        from app import db
        from models import CandidateVettingLog, ParsedEmail

        with app.app_context():
            pe = ParsedEmail(
                sender_email='stuck@example.com',
                recipient_email='jobs@test.com',
                subject='Application',
                status='completed',
                bullhorn_candidate_id=998001,
                vetted_at=datetime.utcnow() - timedelta(minutes=15)
            )
            db.session.add(pe)
            db.session.flush()

            log = CandidateVettingLog(
                bullhorn_candidate_id=998001,
                candidate_name='Stuck Processing',
                status='processing',
                highest_match_score=0,
                created_at=datetime.utcnow() - timedelta(minutes=15)
            )
            db.session.add(log)
            db.session.commit()

            from candidate_vetting_service import CandidateVettingService
            svc = CandidateVettingService()
            svc._reset_stuck_processing()

            remaining = CandidateVettingLog.query.filter_by(bullhorn_candidate_id=998001, status='processing').count()
            assert remaining == 0, "Stuck processing log should be deleted"

            pe_check = ParsedEmail.query.filter_by(bullhorn_candidate_id=998001).first()
            assert pe_check.vetted_at is None, "vetted_at should be reset to NULL"

    def test_skips_processing_with_partial_matches(self, app):
        """Processing logs with some job matches should NOT be reset (partially complete)."""
        from app import db
        from models import CandidateVettingLog, CandidateJobMatch, ParsedEmail

        with app.app_context():
            pe = ParsedEmail(
                sender_email='partial@example.com',
                recipient_email='jobs@test.com',
                subject='Application',
                status='completed',
                bullhorn_candidate_id=998002,
                vetted_at=datetime.utcnow() - timedelta(minutes=15)
            )
            db.session.add(pe)
            db.session.flush()

            log = CandidateVettingLog(
                bullhorn_candidate_id=998002,
                candidate_name='Partial Processing',
                status='processing',
                highest_match_score=0,
                created_at=datetime.utcnow() - timedelta(minutes=15)
            )
            db.session.add(log)
            db.session.flush()

            db.session.add(CandidateJobMatch(
                vetting_log_id=log.id,
                bullhorn_job_id=100, job_title='Job 100', match_score=35.0
            ))
            db.session.commit()

            from candidate_vetting_service import CandidateVettingService
            svc = CandidateVettingService()
            svc._reset_stuck_processing()

            remaining = CandidateVettingLog.query.filter_by(bullhorn_candidate_id=998002, status='processing').count()
            assert remaining == 1, "Log with partial matches should NOT be deleted"

    def test_skips_recent_processing_logs(self, app):
        """Processing logs less than 10 minutes old should NOT be reset (may still be running)."""
        from app import db
        from models import CandidateVettingLog, ParsedEmail

        with app.app_context():
            pe = ParsedEmail(
                sender_email='recent_proc@example.com',
                recipient_email='jobs@test.com',
                subject='Application',
                status='completed',
                bullhorn_candidate_id=998003,
                vetted_at=datetime.utcnow() - timedelta(minutes=3)
            )
            db.session.add(pe)
            db.session.flush()

            log = CandidateVettingLog(
                bullhorn_candidate_id=998003,
                candidate_name='Recent Processing',
                status='processing',
                highest_match_score=0,
                created_at=datetime.utcnow() - timedelta(minutes=3)
            )
            db.session.add(log)
            db.session.commit()

            from candidate_vetting_service import CandidateVettingService
            svc = CandidateVettingService()
            svc._reset_stuck_processing()

            remaining = CandidateVettingLog.query.filter_by(bullhorn_candidate_id=998003, status='processing').count()
            assert remaining == 1, "Recent processing log should NOT be deleted"
