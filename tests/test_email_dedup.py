"""
Tests for new job notification deduplication.

These tests verify that:
1. First-time detection of a new job sends an email
2. Second detection of the same job within 24 hours does NOT send
3. Deduplication persists across service restarts (uses DB, not memory)
"""
import pytest
import os
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


class TestNewJobNotificationDedup:
    """Tests for send_new_job_notification deduplication"""
    
    def test_first_notification_sends_email(self, app):
        """First time a job is detected as new, the email should be sent"""
        from app import db
        from email_service import EmailService
        from models import EmailDeliveryLog
        
        with app.app_context():
            # Create email service with mocked SendGrid
            with patch.dict(os.environ, {'SENDGRID_API_KEY': 'fake-key'}):
                with patch('email_service.SendGridAPIClient') as mock_sg_class:
                    # Create mock response
                    mock_response = MagicMock()
                    mock_response.status_code = 202
                    mock_response.headers = {'X-Message-Id': 'test-msg-id'}
                    mock_sg_class.return_value.send.return_value = mock_response
                    
                    email_service = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)
                    
                    # Send notification for a new job
                    result = email_service.send_new_job_notification(
                        to_email='test@example.com',
                        job_id='12345',
                        job_title='Software Engineer',
                        monitor_name='Test Monitor'
                    )
                    
                    # Should have sent the email
                    assert result == True
                    mock_sg_class.return_value.send.assert_called_once()
                    
                    # Verify it was logged
                    log = EmailDeliveryLog.query.filter_by(
                        notification_type='new_job_notification',
                        job_id='12345'
                    ).first()
                    assert log is not None
                    assert log.delivery_status == 'sent'
    
    def test_duplicate_notification_blocked_within_24_hours(self, app):
        """Second notification for same job within 24 hours should NOT send"""
        from app import db
        from email_service import EmailService
        from models import EmailDeliveryLog
        
        with app.app_context():
            # Create a log entry simulating an already-sent notification (2 hours ago)
            existing_log = EmailDeliveryLog(
                notification_type='new_job_notification',
                job_id='67890',
                job_title='Test Position',
                recipient_email='test@example.com',
                delivery_status='sent'
            )
            # Set sent_at to 2 hours ago by modifying after creation
            db.session.add(existing_log)
            db.session.commit()
            existing_log.sent_at = datetime.utcnow() - timedelta(hours=2)
            db.session.commit()
            
            # Create email service with mocked SendGrid
            with patch.dict(os.environ, {'SENDGRID_API_KEY': 'fake-key'}):
                with patch('email_service.SendGridAPIClient') as mock_sg_class:
                    mock_response = MagicMock()
                    mock_response.status_code = 202
                    mock_sg_class.return_value.send.return_value = mock_response
                    
                    email_service = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)
                    
                    # Try to send notification for the same job
                    result = email_service.send_new_job_notification(
                        to_email='test@example.com',
                        job_id='67890',
                        job_title='Test Position',
                        monitor_name='Test Monitor'
                    )
                    
                    # Should return True (intentionally skipped, not an error)
                    assert result == True
                    
                    # But SendGrid should NOT have been called
                    mock_sg_class.return_value.send.assert_not_called()
    
    def test_notification_allowed_after_24_hours(self, app):
        """Notification should be allowed if previous one was > 24 hours ago"""
        from app import db
        from email_service import EmailService
        from models import EmailDeliveryLog
        
        with app.app_context():
            # Create a log entry from 25 hours ago (outside 24h window)
            old_log = EmailDeliveryLog(
                notification_type='new_job_notification',
                job_id='11111',
                job_title='Old Job',
                recipient_email='test@example.com',
                delivery_status='sent'
            )
            db.session.add(old_log)
            db.session.commit()
            old_log.sent_at = datetime.utcnow() - timedelta(hours=25)
            db.session.commit()
            
            # Create email service with mocked SendGrid
            with patch.dict(os.environ, {'SENDGRID_API_KEY': 'fake-key'}):
                with patch('email_service.SendGridAPIClient') as mock_sg_class:
                    mock_response = MagicMock()
                    mock_response.status_code = 202
                    mock_response.headers = {'X-Message-Id': 'test-msg-id'}
                    mock_sg_class.return_value.send.return_value = mock_response
                    
                    email_service = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)
                    
                    # Send notification for the same job
                    result = email_service.send_new_job_notification(
                        to_email='test@example.com',
                        job_id='11111',
                        job_title='Old Job',
                        monitor_name='Test Monitor'
                    )
                    
                    # Should have sent the email (outside 24h window)
                    assert result == True
                    mock_sg_class.return_value.send.assert_called_once()
    
    def test_dedup_survives_service_restart(self, app):
        """Deduplication should work even if service is re-initialized"""
        from app import db
        from email_service import EmailService
        from models import EmailDeliveryLog
        
        with app.app_context():
            # Simulate first "run" - send initial notification
            with patch.dict(os.environ, {'SENDGRID_API_KEY': 'fake-key'}):
                with patch('email_service.SendGridAPIClient') as mock_sg_class:
                    mock_response = MagicMock()
                    mock_response.status_code = 202
                    mock_response.headers = {'X-Message-Id': 'test-msg-id'}
                    mock_sg_class.return_value.send.return_value = mock_response
                    
                    email_service_v1 = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)
                    
                    # First notification
                    result1 = email_service_v1.send_new_job_notification(
                        to_email='test@example.com',
                        job_id='99999',
                        job_title='Restart Test Job',
                        monitor_name='Test Monitor'
                    )
                    assert result1 == True
                    mock_sg_class.return_value.send.assert_called_once()
            
            # Simulate "restart" - create brand new service instance
            with patch.dict(os.environ, {'SENDGRID_API_KEY': 'fake-key'}):
                with patch('email_service.SendGridAPIClient') as mock_sg_class:
                    mock_response = MagicMock()
                    mock_response.status_code = 202
                    mock_sg_class.return_value.send.return_value = mock_response
                    
                    email_service_v2 = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)
                    
                    # Try to send same notification after "restart"
                    result2 = email_service_v2.send_new_job_notification(
                        to_email='test@example.com',
                        job_id='99999',
                        job_title='Restart Test Job',
                        monitor_name='Test Monitor'
                    )
                    
                    # Should be blocked (DB check survives restart)
                    assert result2 == True  # Returns True (intentionally skipped)
                    mock_sg_class.return_value.send.assert_not_called()
    
    def test_different_jobs_not_blocked(self, app):
        """Notification for different job IDs should not be blocked by each other"""
        from app import db
        from email_service import EmailService
        from models import EmailDeliveryLog
        
        with app.app_context():
            # Create a log entry for job A
            existing_log = EmailDeliveryLog(
                notification_type='new_job_notification',
                job_id='AAA111',
                job_title='Job A',
                recipient_email='test@example.com',
                delivery_status='sent'
            )
            db.session.add(existing_log)
            db.session.commit()
            existing_log.sent_at = datetime.utcnow() - timedelta(hours=1)
            db.session.commit()
            
            # Create email service with mocked SendGrid
            with patch.dict(os.environ, {'SENDGRID_API_KEY': 'fake-key'}):
                with patch('email_service.SendGridAPIClient') as mock_sg_class:
                    mock_response = MagicMock()
                    mock_response.status_code = 202
                    mock_response.headers = {'X-Message-Id': 'test-msg-id'}
                    mock_sg_class.return_value.send.return_value = mock_response
                    
                    email_service = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)
                    
                    # Send notification for a DIFFERENT job (B)
                    result = email_service.send_new_job_notification(
                        to_email='test@example.com',
                        job_id='BBB222',
                        job_title='Job B',
                        monitor_name='Test Monitor'
                    )
                    
                    # Should be sent (different job ID)
                    assert result == True
                    mock_sg_class.return_value.send.assert_called_once()
