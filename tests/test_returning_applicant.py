"""
Test returning applicant re-vetting behavior.

Ensures that when a candidate re-applies (same or different job), a fresh
CandidateVettingLog is created and a new Bullhorn note would be written,
rather than silently reusing the old completed log.

Also tests the ParsedEmail.id-linked dedup to prevent duplicate loop vetting
while allowing valid re-applications.
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
                parsed_email_id=5001,  # Linked to a specific ParsedEmail
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
                '_parsed_email_id': 5002,  # NEW ParsedEmail (re-application)
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
            assert new_log.parsed_email_id == 5002, "New log must be linked to the new ParsedEmail ID"

            # Clean up
            for log in all_logs:
                db.session.delete(log)
            db.session.commit()

    @patch('candidate_vetting_service.BullhornService')
    def test_detect_unvetted_dedup_by_parsed_email_id(self, mock_bullhorn_cls, app):
        """
        The 3-run dedup scenario:
        Run 1: Candidate vets successfully (ParsedEmail ID 100) → PROCESS
        Run 2: Same ParsedEmail ID 100 still unvetted → SKIP (duplicate loop)
        Run 3: New ParsedEmail ID 200 (re-application) → PROCESS
        """
        from app import db
        from models import CandidateVettingLog, ParsedEmail
        from candidate_vetting_service import CandidateVettingService

        with app.app_context():
            candidate_id = 999003
            job_id = 33333

            # --- Arrange: Create ParsedEmail 100 (first application) ---
            pe_100 = ParsedEmail(
                sender_email='noreply@indeed.com',
                recipient_email='jobs@lyntrix.com',
                subject='Application Run 1',
                status='completed',
                bullhorn_candidate_id=candidate_id,
                bullhorn_job_id=job_id,
                vetted_at=None,  # Not yet vetted
                received_at=datetime.utcnow() - timedelta(minutes=30),
                processed_at=datetime.utcnow() - timedelta(minutes=25),
            )
            db.session.add(pe_100)
            db.session.commit()
            pe_100_id = pe_100.id

            # --- Run 1: Should detect and process ---
            mock_bullhorn = Mock()
            mock_bullhorn.authenticate.return_value = True
            mock_bullhorn.base_url = 'https://rest.bullhorn.com/rest-services/'
            mock_bullhorn.rest_token = 'fake-token'
            mock_bullhorn.session = Mock()

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
            candidates_run1 = service.detect_unvetted_applications(limit=25)

            assert any(c.get('id') == candidate_id for c in candidates_run1), (
                f"Run 1: Candidate {candidate_id} should be detected for first-time vetting"
            )

            # Simulate vetting completion: create a completed log linked to pe_100
            vetting_log = CandidateVettingLog(
                bullhorn_candidate_id=candidate_id,
                candidate_name='Test Candidate',
                candidate_email='test@example.com',
                status='completed',
                applied_job_id=job_id,
                parsed_email_id=pe_100_id,
                note_created=True,
                created_at=datetime.utcnow()
            )
            db.session.add(vetting_log)
            # DON'T set vetted_at on the ParsedEmail (simulating the bug scenario)
            db.session.commit()

            # --- Run 2: Same ParsedEmail, should SKIP ---
            candidates_run2 = service.detect_unvetted_applications(limit=25)
            run2_ids = [c.get('id') for c in candidates_run2]

            assert candidate_id not in run2_ids, (
                f"Run 2: Candidate {candidate_id} should be SKIPPED — "
                f"vetting log already exists for ParsedEmail {pe_100_id}"
            )

            # --- Run 3: New ParsedEmail (re-application) → should PROCESS ---
            pe_200 = ParsedEmail(
                sender_email='noreply@indeed.com',
                recipient_email='jobs@lyntrix.com',
                subject='Application Run 3 - Re-application',
                status='completed',
                bullhorn_candidate_id=candidate_id,
                bullhorn_job_id=job_id,
                vetted_at=None,  # New, unvetted
                received_at=datetime.utcnow() - timedelta(minutes=5),
                processed_at=datetime.utcnow() - timedelta(minutes=2),
            )
            db.session.add(pe_200)
            db.session.commit()

            candidates_run3 = service.detect_unvetted_applications(limit=25)

            assert any(c.get('id') == candidate_id for c in candidates_run3), (
                f"Run 3: Candidate {candidate_id} should be detected for re-application "
                f"(new ParsedEmail ID {pe_200.id} is different from {pe_100_id})"
            )

            # Clean up
            CandidateVettingLog.query.filter_by(bullhorn_candidate_id=candidate_id).delete()
            ParsedEmail.query.filter_by(bullhorn_candidate_id=candidate_id).delete()
            db.session.commit()

    @patch('candidate_vetting_service.BullhornService')
    def test_detect_unvetted_allows_different_job_same_candidate(self, mock_bullhorn_cls, app):
        """
        A candidate applying to Job B should be vetted even if they were
        already vetted for Job A — as long as it's a different ParsedEmail.
        """
        from app import db
        from models import CandidateVettingLog, ParsedEmail
        from candidate_vetting_service import CandidateVettingService

        with app.app_context():
            candidate_id = 999004
            job_a = 11111
            job_b = 22222

            # --- Arrange: completed log for Job A linked to ParsedEmail 300 ---
            pe_job_a = ParsedEmail(
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
            db.session.add(pe_job_a)
            db.session.commit()

            recent_log = CandidateVettingLog(
                bullhorn_candidate_id=candidate_id,
                candidate_name='Test Candidate',
                candidate_email='test@example.com',
                status='completed',
                applied_job_id=job_a,
                parsed_email_id=pe_job_a.id,
                note_created=True,
                created_at=datetime.utcnow() - timedelta(minutes=30)
            )
            db.session.add(recent_log)

            # Unvetted ParsedEmail for Job B (different ParsedEmail ID)
            pe_job_b = ParsedEmail(
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
            db.session.add(pe_job_b)
            db.session.commit()

            # --- Act ---
            mock_bullhorn = Mock()
            mock_bullhorn.authenticate.return_value = True
            mock_bullhorn.base_url = 'https://rest.bullhorn.com/rest-services/'
            mock_bullhorn.rest_token = 'fake-token'
            mock_bullhorn.session = Mock()

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
                f"even though Job A was recently vetted (different ParsedEmail ID). "
                f"Got queue: {candidate_ids_in_queue}"
            )

            # Clean up
            CandidateVettingLog.query.filter_by(bullhorn_candidate_id=candidate_id).delete()
            ParsedEmail.query.filter_by(bullhorn_candidate_id=candidate_id).delete()
            db.session.commit()
