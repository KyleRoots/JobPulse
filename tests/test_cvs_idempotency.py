"""
Idempotency / deduplication tests for CandidateVettingService.

Covers:
  - _should_skip_candidate: 24h same-job window, 7-day soft cap, cross-job rescreening
  - No duplicate vetting logs for same candidate/job within guard window
  - Different-job applications always rescreen
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


def _make_cvs():
    from candidate_vetting_service import CandidateVettingService
    cvs = CandidateVettingService.__new__(CandidateVettingService)
    cvs.bullhorn_service = MagicMock()
    cvs.openai_client = MagicMock()
    cvs.model = 'gpt-4o'
    cvs.embedding_model = 'text-embedding-3-small'
    return cvs


class TestShouldSkipCandidate:
    """Tests for _should_skip_candidate dedup rules."""

    def test_no_history_allows_vetting(self, app):
        """A candidate with no vetting history should NOT be skipped."""
        cvs = _make_cvs()
        with app.app_context():
            assert cvs._should_skip_candidate(999, applied_job_id=10) is False

    def test_same_job_within_24h_skipped(self, app):
        """Same candidate + same job within 24h → skip."""
        from app import db
        from models import CandidateVettingLog
        cvs = _make_cvs()

        with app.app_context():
            log = CandidateVettingLog(
                bullhorn_candidate_id=1001,
                candidate_name='Test',
                status='completed',
                applied_job_id=50,
                created_at=datetime.utcnow() - timedelta(hours=2)
            )
            db.session.add(log)
            db.session.commit()

            assert cvs._should_skip_candidate(1001, applied_job_id=50) is True

            db.session.delete(log)
            db.session.commit()

    def test_same_job_after_24h_allowed(self, app):
        """Same candidate + same job but > 24h ago → allow rescreening."""
        from app import db
        from models import CandidateVettingLog
        cvs = _make_cvs()

        with app.app_context():
            log = CandidateVettingLog(
                bullhorn_candidate_id=1002,
                candidate_name='Test2',
                status='completed',
                applied_job_id=50,
                created_at=datetime.utcnow() - timedelta(hours=25)
            )
            db.session.add(log)
            db.session.commit()

            assert cvs._should_skip_candidate(1002, applied_job_id=50) is False

            db.session.delete(log)
            db.session.commit()

    def test_different_job_always_rescreened(self, app):
        """Same candidate but different job → always allow, even if vetted recently."""
        from app import db
        from models import CandidateVettingLog
        cvs = _make_cvs()

        with app.app_context():
            log = CandidateVettingLog(
                bullhorn_candidate_id=1003,
                candidate_name='CrossJob',
                status='completed',
                applied_job_id=50,
                created_at=datetime.utcnow() - timedelta(hours=1)
            )
            db.session.add(log)
            db.session.commit()

            # Different job → should NOT skip
            assert cvs._should_skip_candidate(1003, applied_job_id=99) is False

            db.session.delete(log)
            db.session.commit()

    def test_same_job_3_times_in_7_days_capped(self, app):
        """Same candidate + same job 3+ times in 7 days → skip (soft cap)."""
        from app import db
        from models import CandidateVettingLog
        cvs = _make_cvs()

        with app.app_context():
            for i in range(3):
                log = CandidateVettingLog(
                    bullhorn_candidate_id=1004,
                    candidate_name='Repeat',
                    status='completed',
                    applied_job_id=60,
                    created_at=datetime.utcnow() - timedelta(days=i+1, hours=12)
                )
                db.session.add(log)
            db.session.commit()

            assert cvs._should_skip_candidate(1004, applied_job_id=60) is True

            CandidateVettingLog.query.filter_by(bullhorn_candidate_id=1004).delete()
            db.session.commit()

    def test_no_job_context_24h_global_dedup(self, app):
        """No applied_job_id → fall back to 24h global dedup."""
        from app import db
        from models import CandidateVettingLog
        cvs = _make_cvs()

        with app.app_context():
            log = CandidateVettingLog(
                bullhorn_candidate_id=1005,
                candidate_name='NoJob',
                status='completed',
                created_at=datetime.utcnow() - timedelta(hours=2)
            )
            db.session.add(log)
            db.session.commit()

            assert cvs._should_skip_candidate(1005, applied_job_id=None) is True

            db.session.delete(log)
            db.session.commit()

    def test_processing_status_also_skips(self, app):
        """A log in 'processing' status within 24h also causes a skip."""
        from app import db
        from models import CandidateVettingLog
        cvs = _make_cvs()

        with app.app_context():
            log = CandidateVettingLog(
                bullhorn_candidate_id=1006,
                candidate_name='InProgress',
                status='processing',
                applied_job_id=70,
                created_at=datetime.utcnow() - timedelta(minutes=5)
            )
            db.session.add(log)
            db.session.commit()

            assert cvs._should_skip_candidate(1006, applied_job_id=70) is True

            db.session.delete(log)
            db.session.commit()
