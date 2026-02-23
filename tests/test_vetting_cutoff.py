"""
Test vetting cutoff date filter behavior.

Ensures that when vetting_cutoff_date is set in VettingConfig,
detect_unvetted_applications() only returns candidates whose
ParsedEmail.received_at is on or after the cutoff timestamp.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch


class TestVettingCutoffDateFilter:
    """Test that the cutoff date filter correctly skips old candidates."""

    @patch('candidate_vetting_service.BullhornService')
    def test_old_candidates_skipped_when_cutoff_set(self, mock_bullhorn_cls, app):
        """
        Candidates with received_at BEFORE the cutoff should be excluded
        from detect_unvetted_applications() results.
        """
        from app import db
        from models import ParsedEmail, VettingConfig
        from candidate_vetting_service import CandidateVettingService

        with app.app_context():
            # Set cutoff to "now" so old candidates are excluded
            cutoff = datetime.utcnow()
            VettingConfig.set_value(
                'vetting_cutoff_date',
                cutoff.strftime('%Y-%m-%d %H:%M:%S'),
                description='Test cutoff'
            )

            # Create a ParsedEmail with received_at BEFORE cutoff (old backlog)
            old_email = ParsedEmail(
                sender_email='noreply@indeed.com',
                recipient_email='jobs@lyntrix.com',
                subject='Old Application',
                status='completed',
                bullhorn_candidate_id=888001,
                bullhorn_job_id=11111,
                vetted_at=None,
                received_at=cutoff - timedelta(hours=2),
                processed_at=cutoff - timedelta(hours=1),
            )
            db.session.add(old_email)
            db.session.commit()

            # Act
            mock_bullhorn = Mock()
            mock_bullhorn.authenticate.return_value = True
            mock_bullhorn.base_url = 'https://rest.bullhorn.com/rest-services/'
            mock_bullhorn.rest_token = 'fake-token'
            mock_bullhorn.session = Mock()
            mock_bullhorn_cls.return_value = mock_bullhorn

            service = CandidateVettingService(bullhorn_service=mock_bullhorn)
            candidates = service.detect_unvetted_applications(limit=25)

            # Assert: old candidate should NOT be in the queue
            candidate_ids = [c.get('id') for c in candidates]
            assert 888001 not in candidate_ids, (
                f"Candidate 888001 (received before cutoff) should be excluded, "
                f"but was found in queue: {candidate_ids}"
            )

            # Clean up
            db.session.delete(old_email)
            config = VettingConfig.query.filter_by(setting_key='vetting_cutoff_date').first()
            if config:
                db.session.delete(config)
            db.session.commit()

    @patch('candidate_vetting_service.BullhornService')
    def test_new_candidates_processed_when_cutoff_set(self, mock_bullhorn_cls, app):
        """
        Candidates with received_at AFTER the cutoff should still be
        returned by detect_unvetted_applications().
        """
        from app import db
        from models import ParsedEmail, VettingConfig
        from candidate_vetting_service import CandidateVettingService

        with app.app_context():
            # Set cutoff to 2 hours ago
            cutoff = datetime.utcnow() - timedelta(hours=2)
            VettingConfig.set_value(
                'vetting_cutoff_date',
                cutoff.strftime('%Y-%m-%d %H:%M:%S'),
                description='Test cutoff'
            )

            # Create a ParsedEmail with received_at AFTER cutoff (new applicant)
            new_email = ParsedEmail(
                sender_email='noreply@indeed.com',
                recipient_email='jobs@lyntrix.com',
                subject='New Application',
                status='completed',
                bullhorn_candidate_id=888002,
                bullhorn_job_id=22222,
                vetted_at=None,
                received_at=datetime.utcnow() - timedelta(minutes=10),
                processed_at=datetime.utcnow() - timedelta(minutes=5),
            )
            db.session.add(new_email)
            db.session.commit()

            # Act
            mock_bullhorn = Mock()
            mock_bullhorn.authenticate.return_value = True
            mock_bullhorn.base_url = 'https://rest.bullhorn.com/rest-services/'
            mock_bullhorn.rest_token = 'fake-token'
            mock_bullhorn.session = Mock()

            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                'data': {
                    'id': 888002,
                    'firstName': 'New',
                    'lastName': 'Applicant',
                    'email': 'new@example.com',
                }
            }
            mock_bullhorn.session.get.return_value = mock_response
            mock_bullhorn_cls.return_value = mock_bullhorn

            service = CandidateVettingService(bullhorn_service=mock_bullhorn)
            candidates = service.detect_unvetted_applications(limit=25)

            # Assert: new candidate SHOULD be in the queue
            candidate_ids = [c.get('id') for c in candidates]
            assert 888002 in candidate_ids, (
                f"Candidate 888002 (received after cutoff) should be in the queue, "
                f"but was not found. Queue: {candidate_ids}"
            )

            # Clean up
            db.session.delete(new_email)
            config = VettingConfig.query.filter_by(setting_key='vetting_cutoff_date').first()
            if config:
                db.session.delete(config)
            db.session.commit()

    @patch('candidate_vetting_service.BullhornService')
    def test_no_cutoff_processes_all_candidates(self, mock_bullhorn_cls, app):
        """
        When vetting_cutoff_date is NOT set, all unvetted candidates
        should be returned regardless of received_at.
        """
        from app import db
        from models import ParsedEmail, VettingConfig
        from candidate_vetting_service import CandidateVettingService

        with app.app_context():
            # Ensure no cutoff is set
            config = VettingConfig.query.filter_by(setting_key='vetting_cutoff_date').first()
            if config:
                db.session.delete(config)
                db.session.commit()

            # Create an old ParsedEmail
            old_email = ParsedEmail(
                sender_email='noreply@indeed.com',
                recipient_email='jobs@lyntrix.com',
                subject='Old Application No Cutoff',
                status='completed',
                bullhorn_candidate_id=888003,
                bullhorn_job_id=33333,
                vetted_at=None,
                received_at=datetime.utcnow() - timedelta(days=30),
                processed_at=datetime.utcnow() - timedelta(days=29),
            )
            db.session.add(old_email)
            db.session.commit()

            # Act
            mock_bullhorn = Mock()
            mock_bullhorn.authenticate.return_value = True
            mock_bullhorn.base_url = 'https://rest.bullhorn.com/rest-services/'
            mock_bullhorn.rest_token = 'fake-token'
            mock_bullhorn.session = Mock()

            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                'data': {
                    'id': 888003,
                    'firstName': 'Old',
                    'lastName': 'Candidate',
                    'email': 'old@example.com',
                }
            }
            mock_bullhorn.session.get.return_value = mock_response
            mock_bullhorn_cls.return_value = mock_bullhorn

            service = CandidateVettingService(bullhorn_service=mock_bullhorn)
            candidates = service.detect_unvetted_applications(limit=25)

            # Assert: old candidate SHOULD be in the queue (no cutoff)
            candidate_ids = [c.get('id') for c in candidates]
            assert 888003 in candidate_ids, (
                f"Candidate 888003 should be in the queue when no cutoff is set. "
                f"Queue: {candidate_ids}"
            )

            # Clean up
            db.session.delete(old_email)
            db.session.commit()
