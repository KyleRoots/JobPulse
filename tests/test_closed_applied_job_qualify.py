"""Closed/ineligible applied jobs must never drive Qualified + recruiter email.

Regression: Aug 4 2026 — Indeed inbound remap bumped dateLastModified on
Luke Duwel (4657295); Scout re-screened against closed job 34990
(Lost - Competition), marked is_qualified=True at 85%, and emailed
assigned recruiter Christine Carter.
"""
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from utils.job_status import is_job_eligible, job_can_qualify


class TestJobCanQualify:
    def test_open_accepting_can_qualify(self):
        assert job_can_qualify({
            'id': 1, 'isOpen': True, 'status': 'Accepting Candidates',
        }) is True

    def test_lost_competition_cannot_qualify(self):
        job = {'id': 34990, 'isOpen': False, 'status': 'Lost - Competition'}
        assert is_job_eligible(job) is False
        assert job_can_qualify(job) is False

    def test_half_closed_cannot_qualify(self):
        """isOpen=False + Accepting Candidates: inject for notes, never qualify."""
        job = {'id': 35421, 'isOpen': False, 'status': 'Accepting Candidates'}
        assert job_can_qualify(job) is False

    def test_filled_cannot_qualify(self):
        assert job_can_qualify({
            'id': 2, 'isOpen': True, 'status': 'Filled',
        }) is False

    def test_empty_job_cannot_qualify(self):
        assert job_can_qualify({}) is False
        assert job_can_qualify(None) is False


class TestClosedAppliedJobQualifySuppress:
    """Score-above-threshold on an ineligible job must leave is_qualified=False."""

    def test_score_threshold_alone_does_not_qualify_closed_job(self):
        job = {
            'id': 34990,
            'title': 'Procurement Specialist',
            'isOpen': False,
            'status': 'Lost - Competition',
            '_injected_applied_job': True,
        }
        score = 85.0
        threshold = 80.0
        qualifies = (
            score >= threshold
            and not False
            and job_can_qualify(job)
        )
        assert qualifies is False

    def test_open_job_still_qualifies_at_threshold(self):
        job = {
            'id': 36000,
            'title': 'Open Role',
            'isOpen': True,
            'status': 'Accepting Candidates',
        }
        assert (
            85.0 >= 80.0
            and not False
            and job_can_qualify(job)
        ) is True


@pytest.mark.usefixtures('app')
class TestSkipRescreenClosedAppliedPrior:
    """process_candidate short-circuits when closed applied job was already screened."""

    def test_skips_when_prior_completed_for_closed_applied(self, app):
        from app import db
        from candidate_vetting_service import CandidateVettingService
        from models import CandidateVettingLog

        with app.app_context():
            prior = CandidateVettingLog(
                bullhorn_candidate_id=4657295,
                candidate_name='Luke Duwel',
                applied_job_id=34990,
                applied_job_title='Procurement Specialist',
                status='completed',
                is_qualified=False,
                highest_match_score=73.0,
                created_at=datetime(2026, 5, 1, 3, 12, 51),
            )
            db.session.add(prior)
            db.session.commit()

            service = CandidateVettingService()
            closed_job = {
                'id': 34990,
                'title': 'Procurement Specialist',
                'isOpen': False,
                'status': 'Lost - Competition',
                '_injected_applied_job': True,
                'assignedUsers': {'data': []},
            }
            candidate = {
                'id': 4657295,
                'firstName': 'Luke',
                'lastName': 'Duwel',
                'email': 'lukeduwel@gmail.com',
                'description': (
                    'Luke A. Duwel procurement professional with seven years of '
                    'defense procurement experience managing complex acquisitions '
                    'across the full procurement lifecycle and cost price analysis. '
                    'Wyoming MI based. Microsoft Excel Word PowerPoint proficient.'
                ),
                '_applied_job_id': 34990,
                '_applied_job_title': 'Procurement Specialist',
            }

            with patch.object(service, 'get_candidate_job_submission', return_value=None):
                # Tearsheet jobs present — remap re-detect must still skip, not
                # rediscover against open roles when applied job is closed + prior.
                tearsheet_jobs = [
                    {
                        'id': 36001,
                        'title': 'Open Procurement Role',
                        'isOpen': True,
                        'status': 'Accepting Candidates',
                        'assignedUsers': {'data': []},
                    }
                ]
                with patch.object(service, 'get_active_jobs_from_tearsheets', return_value=tearsheet_jobs):
                    with patch.object(service, '_get_bullhorn_service', return_value=Mock(rest_token='t')):
                        with patch.object(service, '_fetch_applied_job', return_value=closed_job):
                            with patch.object(service, '_run_fraud_assessment'):
                                with patch.object(
                                    service, 'embedding_service', create=True
                                ) as mock_emb:
                                    mock_emb.filter_relevant_jobs.side_effect = AssertionError(
                                        'embedding must not run when closed applied prior-skip fires'
                                    )
                                    result = service.process_candidate(candidate)

            assert result is not None
            assert result.id != prior.id
            assert result.status == 'completed'
            assert result.is_qualified is False
            assert result.highest_match_score == 0.0
            assert 'previously screened' in (result.error_message or '')
            assert str(prior.id) in (result.error_message or '')
