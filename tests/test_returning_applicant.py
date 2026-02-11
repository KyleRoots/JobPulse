"""
Test returning applicant re-vetting behavior.

Ensures that when a candidate re-applies (same or different job), a fresh
CandidateVettingLog is created and a new Bullhorn note would be written,
rather than silently reusing the old completed log.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock


class TestReturningApplicantRevetting:
    """Test that returning applicants get fresh vetting logs and notes."""

    @patch('candidate_vetting_service.BullhornService')
    def test_process_candidate_creates_fresh_log_for_returning_applicant(self, mock_bullhorn_cls, app):
        """
        When a candidate applies again (same job), process_candidate must
        create a NEW CandidateVettingLog — not reuse the old completed one.
        This ensures note_created starts as False so a new AI Vetting note
        is written to Bullhorn.
        """
        from app import db
        from models import CandidateVettingLog
        from candidate_vetting_service import CandidateVettingService

        with app.app_context():
            candidate_id = 999001
            job_id = 33633

            # --- Arrange: simulate a completed vetting from a previous application ---
            old_log = CandidateVettingLog(
                bullhorn_candidate_id=candidate_id,
                candidate_name='Juan Lopez',
                candidate_email='juan@example.com',
                status='completed',
                applied_job_id=job_id,
                note_created=True,
                created_at=datetime.utcnow() - timedelta(hours=24)
            )
            db.session.add(old_log)
            db.session.commit()
            old_log_id = old_log.id

            # Verify the old log exists
            assert CandidateVettingLog.query.filter_by(
                bullhorn_candidate_id=candidate_id
            ).count() == 1

            # --- Act: process the same candidate again (simulating a re-application) ---
            mock_bullhorn = Mock()
            mock_bullhorn.authenticate.return_value = True
            mock_bullhorn.base_url = 'https://rest.bullhorn.com/rest-services/'
            mock_bullhorn.rest_token = 'fake-token'
            mock_bullhorn.session = Mock()
            mock_bullhorn_cls.return_value = mock_bullhorn

            service = CandidateVettingService(bullhorn_service=mock_bullhorn)

            # Mock get_active_jobs_from_tearsheets to return no jobs
            # (we only need to verify the log creation, not the full pipeline)
            service.get_active_jobs_from_tearsheets = Mock(return_value=[])

            candidate_data = {
                'id': candidate_id,
                'firstName': 'Juan',
                'lastName': 'Lopez',
                'email': 'juan@example.com',
                'description': 'Software engineer with 5 years experience in Python and Flask...' * 10,
                '_parsed_email_id': 2962,
                '_applied_job_id': job_id,
                '_is_duplicate': True,
            }

            result = service.process_candidate(candidate_data)

            # --- Assert: a NEW log was created (not the old one reused) ---
            all_logs = CandidateVettingLog.query.filter_by(
                bullhorn_candidate_id=candidate_id
            ).order_by(CandidateVettingLog.created_at.asc()).all()

            # Must have 2 logs now: the old completed one + the new one
            assert len(all_logs) == 2, (
                f"Expected 2 CandidateVettingLog entries for candidate {candidate_id}, "
                f"got {len(all_logs)}. The old log should not be reused."
            )

            new_log = all_logs[1]
            assert new_log.id != old_log_id, "New log must have a different ID than the old one"
            assert new_log.note_created is not True, (
                "New log's note_created must start as False/None so a new Bullhorn note is written"
            )
            assert new_log.applied_job_id == job_id

            # Clean up
            for log in all_logs:
                db.session.delete(log)
            db.session.commit()

    @patch('candidate_vetting_service.BullhornService')
    def test_detect_unvetted_scopes_dedup_to_candidate_and_job(self, mock_bullhorn_cls, app):
        """
        The 1-hour dedup in detect_unvetted_applications is keyed on
        (candidate_id, job_id). A recent completed log for Job A should NOT
        block vetting for the same candidate applying to Job B.
        """
        from app import db
        from models import CandidateVettingLog, ParsedEmail
        from candidate_vetting_service import CandidateVettingService

        with app.app_context():
            candidate_id = 999002
            job_a = 11111
            job_b = 22222

            # --- Arrange: recent completed log for Job A (within 1 hour) ---
            recent_log = CandidateVettingLog(
                bullhorn_candidate_id=candidate_id,
                candidate_name='Test Candidate',
                candidate_email='test@example.com',
                status='completed',
                applied_job_id=job_a,
                note_created=True,
                created_at=datetime.utcnow() - timedelta(minutes=30)
            )
            db.session.add(recent_log)

            # Two ParsedEmail records: one vetted (Job A), one unvetted (Job B)
            pe_vetted = ParsedEmail(
                sender_email='noreply@indeed.com',
                recipient_email='jobs@lyntrix.com',
                subject='Application for Job A',
                status='completed',
                bullhorn_candidate_id=candidate_id,
                bullhorn_job_id=job_a,
                vetted_at=datetime.utcnow() - timedelta(minutes=30),
                received_at=datetime.utcnow() - timedelta(minutes=45),
                processed_at=datetime.utcnow() - timedelta(minutes=40),
            )
            pe_unvetted = ParsedEmail(
                sender_email='noreply@indeed.com',
                recipient_email='jobs@lyntrix.com',
                subject='Application for Job B',
                status='completed',
                bullhorn_candidate_id=candidate_id,
                bullhorn_job_id=job_b,
                vetted_at=None,  # Not yet vetted
                received_at=datetime.utcnow() - timedelta(minutes=10),
                processed_at=datetime.utcnow() - timedelta(minutes=5),
            )
            db.session.add_all([pe_vetted, pe_unvetted])
            db.session.commit()

            # --- Act ---
            mock_bullhorn = Mock()
            mock_bullhorn.authenticate.return_value = True
            mock_bullhorn.base_url = 'https://rest.bullhorn.com/rest-services/'
            mock_bullhorn.rest_token = 'fake-token'
            mock_bullhorn.session = Mock()

            # Mock the Bullhorn candidate fetch to return candidate data
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                'data': {
                    'id': candidate_id,
                    'firstName': 'Test',
                    'lastName': 'Candidate',
                    'email': 'test@example.com',
                }
            }
            mock_bullhorn.session.get.return_value = mock_response
            mock_bullhorn_cls.return_value = mock_bullhorn

            service = CandidateVettingService(bullhorn_service=mock_bullhorn)
            candidates = service.detect_unvetted_applications(limit=25)

            # --- Assert: Job B application should be in the queue ---
            candidate_ids_in_queue = [c.get('id') for c in candidates]
            assert candidate_id in candidate_ids_in_queue, (
                f"Candidate {candidate_id} should be queued for Job B vetting, "
                f"even though Job A was recently vetted. Got queue: {candidate_ids_in_queue}"
            )

            # Clean up
            db.session.delete(recent_log)
            db.session.delete(pe_vetted)
            db.session.delete(pe_unvetted)
            db.session.commit()
