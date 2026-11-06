"""An audit re-vet causes one re-screen, and calls-per-run survives deletion.

Candidate 4412341 (Nov 5, 2026) was fully screened three times in eight
minutes. The first was the new application. The audit then reset every
completed ParsedEmail for that candidate, the pending-revet detector
screened one of them, and the parsed-email queue screened the other a
minute later. The human-owner skip never applied to that queue. It only
runs on Bullhorn search, which is why it showed up after the third screen.

These tests pin the repair: reset only the audited email, leave a second
email vetted, do not enqueue the pending-revet path when that email is
already queued, and count screening runs from telemetry entity ids so a
deleted vetting log cannot inflate calls per run.
"""
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from services.ai_cost_monitor import calls_per_run_ratio


CANDIDATE = 8804412
OTHER_CANDIDATE = 8804413


def _email(app, *, candidate_id, job_id, vetted, subject):
    from app import db
    from models import ParsedEmail

    row = ParsedEmail(
        sender_email='noreply@example.com',
        recipient_email='jobs@example.com',
        subject=subject,
        status='completed',
        bullhorn_candidate_id=candidate_id,
        bullhorn_job_id=job_id,
        vetted_at=datetime.utcnow() - timedelta(hours=1) if vetted else None,
        received_at=datetime.utcnow() - timedelta(hours=2),
        processed_at=datetime(2000, 1, 1),
    )
    db.session.add(row)
    db.session.flush()
    return row


def _log(app, *, candidate_id, job_id, parsed_email_id, status='completed'):
    from app import db
    from models import CandidateJobMatch, CandidateVettingLog, EscalationLog

    log = CandidateVettingLog(
        bullhorn_candidate_id=candidate_id,
        candidate_name='Audit Candidate',
        status=status,
        applied_job_id=job_id,
        parsed_email_id=parsed_email_id,
        highest_match_score=18.0,
        created_at=datetime.utcnow() - timedelta(hours=3),
    )
    db.session.add(log)
    db.session.flush()
    db.session.add(CandidateJobMatch(
        vetting_log_id=log.id,
        bullhorn_job_id=job_id,
        job_title='Solution Architect',
        match_score=18.0,
        is_applied_job=True,
    ))
    db.session.add(EscalationLog(
        vetting_log_id=log.id,
        bullhorn_candidate_id=candidate_id,
        bullhorn_job_id=job_id,
        job_title='Solution Architect',
        mini_score=18.0,
        gpt4o_score=22.0,
        score_delta=4.0,
        material_change=False,
        threshold_used=75.0,
        crossed_threshold=False,
    ))
    db.session.flush()
    return log


def _audit(app, *, candidate_id, job_id, log_id):
    from app import db
    from models import VettingAuditLog

    row = VettingAuditLog(
        candidate_vetting_log_id=log_id,
        bullhorn_candidate_id=candidate_id,
        candidate_name='Audit Candidate',
        job_id=job_id,
        job_title='Solution Architect',
        original_score=18.0,
        finding_type='recency_misfire',
        confidence='high',
        action_taken='revet_triggered',
        revet_new_score=None,
        audit_finding='AI confirmed recency misfire.',
        created_at=datetime.utcnow() - timedelta(minutes=20),
    )
    db.session.add(row)
    db.session.flush()
    return row


def _cleanup(app, candidate_ids):
    from app import db
    from models import (
        CandidateJobMatch, CandidateVettingLog, EscalationLog,
        ParsedEmail, VettingAuditLog,
    )

    with app.app_context():
        logs = CandidateVettingLog.query.filter(
            CandidateVettingLog.bullhorn_candidate_id.in_(candidate_ids)
        ).all()
        log_ids = [row.id for row in logs]
        if log_ids:
            CandidateJobMatch.query.filter(
                CandidateJobMatch.vetting_log_id.in_(log_ids)
            ).delete(synchronize_session=False)
            EscalationLog.query.filter(
                EscalationLog.vetting_log_id.in_(log_ids)
            ).delete(synchronize_session=False)
            VettingAuditLog.query.filter(
                VettingAuditLog.candidate_vetting_log_id.in_(log_ids)
            ).delete(synchronize_session=False)
            CandidateVettingLog.query.filter(
                CandidateVettingLog.id.in_(log_ids)
            ).delete(synchronize_session=False)
        ParsedEmail.query.filter(
            ParsedEmail.bullhorn_candidate_id.in_(candidate_ids)
        ).delete(synchronize_session=False)
        db.session.commit()


@pytest.fixture
def clean_candidates(app):
    _cleanup(app, [CANDIDATE, OTHER_CANDIDATE])
    yield
    _cleanup(app, [CANDIDATE, OTHER_CANDIDATE])


class TestAuditRevetSingleScreen:
    def test_trigger_resets_only_the_audited_email_and_keeps_the_run(
        self, app, clean_candidates
    ):
        from models import CandidateJobMatch, CandidateVettingLog, EscalationLog, ParsedEmail
        from vetting_audit_service.revet_mixin import RevetMixin

        with app.app_context():
            audited = _email(
                app, candidate_id=CANDIDATE, job_id=35902,
                vetted=True, subject='audited application',
            )
            other = _email(
                app, candidate_id=CANDIDATE, job_id=36101,
                vetted=True, subject='other application',
            )
            audited_log = _log(
                app, candidate_id=CANDIDATE, job_id=35902,
                parsed_email_id=audited.id,
            )
            other_log = _log(
                app, candidate_id=CANDIDATE, job_id=36101,
                parsed_email_id=other.id,
            )
            from app import db
            db.session.commit()
            audited_id = audited.id
            other_id = other.id
            audited_log_id = audited_log.id
            other_log_id = other_log.id

            class _Auditor(RevetMixin):
                pass

            result = _Auditor()._trigger_revet(CANDIDATE, audited_log_id)

            assert result is None
            audited_email = ParsedEmail.query.get(audited_id)
            other_email = ParsedEmail.query.get(other_id)
            assert audited_email.vetted_at is None
            assert other_email.vetted_at is not None
            kept = CandidateVettingLog.query.filter(
                CandidateVettingLog.id.in_([audited_log_id, other_log_id])
            ).all()
            assert len(kept) == 2
            assert {row.status for row in kept} == {'superseded'}
            assert CandidateJobMatch.query.filter_by(
                vetting_log_id=audited_log_id
            ).count() == 1
            assert EscalationLog.query.filter_by(
                vetting_log_id=audited_log_id
            ).count() == 1

    def test_pending_detector_does_not_enqueue_when_the_email_queue_owns_it(
        self, app, clean_candidates
    ):
        from app import db
        from models import CandidateVettingLog, ParsedEmail
        from screening.detection import CandidateDetectionMixin

        class _Detector(CandidateDetectionMixin):
            pass

        with app.app_context():
            audited = _email(
                app, candidate_id=CANDIDATE, job_id=35902,
                vetted=False, subject='queued by the audit',
            )
            other = _email(
                app, candidate_id=CANDIDATE, job_id=36101,
                vetted=True, subject='must stay vetted',
            )
            log = _log(
                app, candidate_id=CANDIDATE, job_id=35902,
                parsed_email_id=audited.id,
            )
            _audit(app, candidate_id=CANDIDATE, job_id=35902, log_id=log.id)
            db.session.commit()
            other_vetted_at = other.vetted_at
            log_id = log.id

            enqueued = _Detector().detect_pending_revet_candidates()

            assert all(row.get('id') != CANDIDATE for row in enqueued)
            assert ParsedEmail.query.get(other.id).vetted_at == other_vetted_at
            assert CandidateVettingLog.query.get(log_id).status == 'completed'

    def test_pending_detector_enqueues_once_when_there_is_no_email(
        self, app, clean_candidates
    ):
        from app import db
        from models import CandidateJobMatch, CandidateVettingLog, ParsedEmail
        from screening.detection import CandidateDetectionMixin

        class _Detector(CandidateDetectionMixin):
            pass

        with app.app_context():
            unrelated = _email(
                app, candidate_id=CANDIDATE, job_id=35902,
                vetted=True, subject='already screened application',
            )
            log = _log(
                app, candidate_id=CANDIDATE, job_id=36191,
                parsed_email_id=None,
            )
            audit = _audit(
                app, candidate_id=CANDIDATE, job_id=36191, log_id=log.id,
            )
            db.session.commit()
            unrelated_vetted_at = unrelated.vetted_at
            log_id = log.id
            audit_id = audit.id

            enqueued = [
                row for row in _Detector().detect_pending_revet_candidates()
                if row.get('id') == CANDIDATE
            ]

            assert len(enqueued) == 1
            assert enqueued[0]['_pending_revet_audit_id'] == audit_id
            assert enqueued[0]['_applied_job_id'] == 36191
            assert '_parsed_email_id' not in enqueued[0]
            assert ParsedEmail.query.get(unrelated.id).vetted_at == unrelated_vetted_at
            assert CandidateVettingLog.query.get(log_id).status == 'superseded'
            assert CandidateJobMatch.query.filter_by(vetting_log_id=log_id).count() == 1

    def test_pre_cutoff_email_does_not_block_the_pending_path(
        self, app, clean_candidates
    ):
        from app import db
        from models import ParsedEmail, VettingConfig
        from screening.detection import CandidateDetectionMixin

        class _Detector(CandidateDetectionMixin):
            pass

        with app.app_context():
            email = _email(
                app, candidate_id=OTHER_CANDIDATE, job_id=40001,
                vetted=False, subject='older than the cutoff',
            )
            email.received_at = datetime(2020, 1, 1)
            log = _log(
                app, candidate_id=OTHER_CANDIDATE, job_id=40001,
                parsed_email_id=email.id,
            )
            _audit(
                app, candidate_id=OTHER_CANDIDATE, job_id=40001, log_id=log.id,
            )
            VettingConfig.set_value(
                'vetting_cutoff_date',
                '2099-01-01 00:00:00',
                'test cutoff',
            )
            db.session.commit()
            try:
                enqueued = [
                    row for row in _Detector().detect_pending_revet_candidates()
                    if row.get('id') == OTHER_CANDIDATE
                ]
                assert len(enqueued) == 1
                assert ParsedEmail.query.get(email.id).vetted_at is None
            finally:
                row = VettingConfig.query.filter_by(
                    setting_key='vetting_cutoff_date'
                ).first()
                if row is not None:
                    db.session.delete(row)
                    db.session.commit()

    def test_bullhorn_detectors_leave_a_queued_email_alone(
        self, app, clean_candidates
    ):
        from app import db
        from screening.dedup import CandidateDeduplicationMixin

        class _Dedup(CandidateDeduplicationMixin):
            pass

        with app.app_context():
            _email(
                app, candidate_id=CANDIDATE, job_id=35902,
                vetted=False, subject='waiting in the email queue',
            )
            db.session.commit()
            assert _Dedup()._should_skip_candidate(CANDIDATE, applied_job_id=35902) is True

            ParsedEmail = __import__('models', fromlist=['ParsedEmail']).ParsedEmail
            row = ParsedEmail.query.filter_by(bullhorn_candidate_id=CANDIDATE).one()
            row.vetted_at = datetime.utcnow()
            db.session.commit()
            assert _Dedup()._should_skip_candidate(CANDIDATE, applied_job_id=35902) is False

    def test_reset_email_is_the_only_one_the_queue_returns(
        self, app, clean_candidates
    ):
        from app import db
        from candidate_vetting_service import CandidateVettingService
        from vetting_audit_service.revet_mixin import RevetMixin

        with app.app_context():
            audited = _email(
                app, candidate_id=CANDIDATE, job_id=35902,
                vetted=True, subject='audited application',
            )
            other = _email(
                app, candidate_id=CANDIDATE, job_id=36101,
                vetted=True, subject='other application',
            )
            audited_log = _log(
                app, candidate_id=CANDIDATE, job_id=35902,
                parsed_email_id=audited.id,
            )
            _log(
                app, candidate_id=CANDIDATE, job_id=36101,
                parsed_email_id=other.id,
            )
            db.session.commit()

            class _Auditor(RevetMixin):
                pass

            _Auditor()._trigger_revet(CANDIDATE, audited_log.id)

            bullhorn = Mock()
            bullhorn.authenticate.return_value = True
            bullhorn.base_url = 'https://rest.example/'
            bullhorn.rest_token = 'token'
            bullhorn.session = Mock()
            response = Mock()
            response.status_code = 200
            response.json.return_value = {
                'data': {
                    'id': CANDIDATE,
                    'firstName': 'Audit',
                    'lastName': 'Candidate',
                }
            }
            bullhorn.session.get.return_value = response
            service = CandidateVettingService(bullhorn_service=bullhorn)
            queued = [
                row for row in service.detect_unvetted_applications(limit=10000)
                if row.get('id') == CANDIDATE
            ]

            assert len(queued) == 1
            assert queued[0]['_parsed_email_id'] == audited.id
            assert queued[0]['_applied_job_id'] == 35902


class TestCallsPerRunSurvivesDeletion:
    def test_ratio_uses_the_larger_run_count(self):
        assert calls_per_run_ratio(202, 10, 2) == pytest.approx(20.2)
        assert calls_per_run_ratio(202, 10, 0) == pytest.approx(20.2)
        assert calls_per_run_ratio(202, 0, 10) == pytest.approx(20.2)
        assert calls_per_run_ratio(0, 0, 0) is None

    def test_deleted_vetting_log_does_not_shrink_the_denominator(self, app):
        from app import db
        from models import CandidateVettingLog
        from models.openai_telemetry import OpenAICallLog
        from services.ai_cost_monitor import _fetch_calls_per_run

        stamp = datetime(2099, 6, 1, 12, 0, 0)
        with app.app_context():
            kept = CandidateVettingLog(
                bullhorn_candidate_id=CANDIDATE,
                candidate_name='Kept Run',
                status='completed',
                created_at=stamp,
            )
            deleted = CandidateVettingLog(
                bullhorn_candidate_id=OTHER_CANDIDATE,
                candidate_name='Deleted Run',
                status='completed',
                created_at=stamp,
            )
            db.session.add_all([kept, deleted])
            db.session.flush()
            for entity_id, calls in ((kept.id, 10), (deleted.id, 10)):
                for _ in range(calls):
                    db.session.add(OpenAICallLog(
                        created_at=stamp,
                        call_site_id='screening.scoring',
                        model='gpt-test',
                        entity_type='CandidateVettingLog',
                        entity_id=str(entity_id),
                        estimated_cost_usd=0.01,
                    ))
            db.session.commit()
            deleted_id = deleted.id
            try:
                db.session.delete(deleted)
                db.session.commit()
                ratio = _fetch_calls_per_run(stamp - timedelta(minutes=1))
                # 20 scoring calls, 2 tagged runs, 1 surviving log.
                # Dividing by the surviving log alone would report 20.
                assert ratio == pytest.approx(10.0)
                assert CandidateVettingLog.query.get(deleted_id) is None
            finally:
                OpenAICallLog.query.filter(
                    OpenAICallLog.created_at == stamp
                ).delete(synchronize_session=False)
                CandidateVettingLog.query.filter(
                    CandidateVettingLog.created_at == stamp
                ).delete(synchronize_session=False)
                db.session.commit()


def test_scoring_call_is_stamped_with_the_vetting_log(monkeypatch):
    import json
    import screening.prompt_builder as pb

    captured = {}

    class _Usage:
        prompt_tokens = 100
        prompt_tokens_details = None

    class _Choice:
        message = type('M', (), {'content': json.dumps({
            'match_score': 40,
            'technical_score': 40,
            'match_summary': 'Partial overlap.',
            'skills_match': 'Python',
            'experience_match': '2 years',
            'gaps_identified': 'Missing Databricks.',
            'key_requirements': 'Databricks',
            'years_analysis': {},
            'recency_analysis': {
                'most_recent_role_relevant': False,
                'relevance_justification': 'N/A',
                'months_since_relevant_work': 0,
                'penalty_applied': 0,
            },
        })})()
        finish_reason = 'stop'

    class _Response:
        usage = _Usage()
        choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs):
            return _Response()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    class _Service:
        openai_client = _Client()
        model = 'gpt-4.1-mini'

        def _recheck_years_calculation(self, *args, **kwargs):
            return None

    def _capture(*args, **kwargs):
        captured['args'] = args
        captured['kwargs'] = kwargs

    monkeypatch.setattr('services.openai_helper.resolve_model', lambda site, default: default)
    monkeypatch.setattr('services.openai_helper.log_call', _capture)

    pb.PromptBuilderMixin.analyze_candidate_job_match(
        _Service(),
        resume_text='Retail cashier with no engineering background.',
        job={
            'id': 1,
            'title': 'Data Engineer',
            'description': 'Need Databricks and Spark',
            'address': {'city': 'Toronto', 'state': 'ON', 'countryName': 'Canada'},
        },
        prefetched_requirements='',
        prefetched_global_requirements='',
        screening_profile='standard',
        vetting_log_id=68211,
    )

    assert captured['kwargs']['entity_type'] == 'CandidateVettingLog'
    assert captured['kwargs']['entity_id'] == 68211
