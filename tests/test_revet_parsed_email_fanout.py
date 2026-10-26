"""Audit re-vets: one analysis per request, no deleted live work, and scores
only from runs that started after the request.

``clear_candidate_vetting_state`` used to reset ``vetted_at`` on every
completed ParsedEmail for the candidate, and the ParsedEmail detector then
ran one full multi-job analysis per email. Oct 2026: one candidate reset 7
emails and was screened 8 times in 25 minutes.

The concurrency protocol these tests exercise lives in
``vetting_audit_service.run_claim``. Everything runs against the disposable
SQLite test database through the real ``run_vetting_cycle`` and
``process_candidate``, with Bullhorn, the model call, notes, notifications
and fraud checks stubbed. SQLite has no advisory locks and a single writer,
so the interleaving tests below inject the competing write at a fixed point
(through a second connection or from inside the running analysis). They
check the protocol's guards, fencing and binding; they do not show that
PostgreSQL serialises competing connections.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

CANDIDATE = 4700001
AUDITED_JOB = 35903
OTHER_JOB = 35904
THIRD_JOB = 35905
TEARSHEET_JOB_IDS = (AUDITED_JOB, OTHER_JOB, THIRD_JOB)
# Two distinct application submissions, each for its own job, both scored
# against one related tearsheet job.
SUBMISSION_JOB_1 = 61001
SUBMISSION_JOB_2 = 61002
RELATED_JOB = 61051

RESUME_TEXT = ('Synthetic resume text used only by the revet protocol tests. ' * 8).strip()


def _now():
    return datetime.utcnow()


def _job(job_id):
    return {
        'id': job_id,
        'title': f'Synthetic Job {job_id}',
        'isOpen': True,
        'status': 'Accepting Candidates',
        'assignedUsers': {'data': []},
        'address': {},
    }


class _FakeBullhorn:
    rest_token = 'synthetic'

    def authenticate(self):
        return True


def _single_thread_executor(max_workers=None, **kwargs):
    # One analysis thread, so a hook in the first model call runs before
    # the worker's first per-job lease renewal.
    return ThreadPoolExecutor(max_workers=1)


class Harness:
    """Drives the real vetting cycle with every external call stubbed."""

    def __init__(self, app, job_ids=TEARSHEET_JOB_IDS):
        from candidate_vetting_service import CandidateVettingService

        svc = CandidateVettingService.__new__(CandidateVettingService)
        svc.bullhorn = MagicMock()
        svc.openai_client = MagicMock()
        svc.model = 'stub-model'
        svc.embedding_model = 'stub-embedding'
        svc.embedding_service = MagicMock()
        svc.embedding_service.filter_relevant_jobs.side_effect = (
            lambda text, jobs, info, vetting_log_id: (list(jobs), 0)
        )
        self.svc = svc
        self.app = app
        self.job_ids = job_ids
        self.score = 66.0
        self.scores = {}
        self.fail_runs = False
        self.model_calls = []
        self.attempts = []
        self.notes = []
        self.hook = None
        self.final_hook = None
        self.executor = _single_thread_executor
        self._hook_lock = threading.Lock()

    @property
    def runs(self):
        """Runs that started (claimed a lease), in order."""
        return [label for label, outcome in self.attempts if outcome in ('claimed', 'lease_lost')]

    @staticmethod
    def label(cand):
        if cand.get('_parsed_email_id'):
            return f"email:{cand['_parsed_email_id']}"
        if cand.get('_pending_revet_audit_id'):
            return f"revet:{cand['_pending_revet_audit_id']}"
        return f"other:{cand.get('id')}"

    def _analyze(self, resume_text, job, location, **kwargs):
        job_id = job.get('id')
        self.model_calls.append(job_id)
        with self._hook_lock:
            hook, self.hook = self.hook, None
        if hook is not None:
            with self.app.app_context():
                hook()
        score = self.scores.get(job_id, self.score)
        return {
            'match_score': score, 'technical_score': score,
            'match_summary': 'synthetic', 'skills_match': '',
            'experience_match': '', 'gaps_identified': '',
        }

    def _extract(self, content, filename):
        if self.fail_runs:
            raise RuntimeError('synthetic run failure')
        return RESUME_TEXT

    def _stamp(self, vetting_log, service):
        with self._hook_lock:
            hook, self.final_hook = self.final_hook, None
        if hook is not None:
            hook(vetting_log.id)

    def _note(self, vetting_log):
        self.notes.append(vetting_log.id)
        return True

    def process(self, cand):
        """Real ``process_candidate`` with the stubs, outside a cycle."""
        from candidate_vetting_service import CandidateVettingService

        with self._patched():
            result = CandidateVettingService.process_candidate(
                self.svc, cand, cached_jobs=[_job(j) for j in self.job_ids],
            )
        self.attempts.append((self.label(cand), cand.get('_run_claim_outcome')))
        return result

    def run_cycle(self):
        with self._patched(spy=True):
            return self.svc.run_vetting_cycle()

    def _patched(self, spy=False):
        from candidate_vetting_service import CandidateVettingService

        svc = self.svc
        stack = ExitStack()
        none = lambda *a, **k: []  # noqa: E731
        stubs = {
            'is_enabled': lambda: True,
            '_acquire_vetting_lock': lambda: True,
            '_release_vetting_lock': lambda: None,
            '_set_last_run_timestamp': lambda ts: None,
            '_get_batch_size': lambda: 25,
            'detect_new_applicants': none,
            'detect_pandologic_candidates': none,
            'detect_matador_candidates': none,
            'detect_stsi_portal_candidates': none,
            'detect_indeed_applicants': none,
            'detect_pandologic_note_candidates': none,
            'get_active_jobs_from_tearsheets': lambda: [_job(j) for j in self.job_ids],
            '_get_bullhorn_service': lambda: _FakeBullhorn(),
            '_fetch_candidate_details': lambda bh, cid: {
                'id': cid, 'firstName': 'Synthetic', 'lastName': 'Candidate',
                'email': 'synthetic@example.test',
            },
            '_fetch_applied_job': lambda bh, job_id: None,
            'create_candidate_note': self._note,
            'send_recruiter_notifications': lambda vlog: 0,
            'get_candidate_job_submission': lambda cid: None,
            'get_candidate_resume': lambda cid: (b'synthetic', 'resume.txt'),
            'extract_resume_text': self._extract,
            '_run_fraud_assessment': lambda *a, **k: None,
            '_enrich_fraud_contact_validation': lambda *a, **k: None,
            'analyze_candidate_job_match': self._analyze,
            '_cheap_first_route': lambda *a, **k: (False, None),
            '_get_layer2_model': lambda: 'stub-model',
            '_get_escalation_range': lambda: (0, 0),
            '_get_screening_routing_mode': lambda: 'off',
            '_get_cheap_first_reject_threshold': lambda: 0.0,
            '_get_global_custom_requirements': lambda: '',
            '_get_screening_profile': lambda: 'standard',
            'get_threshold': lambda: 80.0,
        }
        if spy:
            def process_spy(cand, cached_jobs=None):
                result = CandidateVettingService.process_candidate(svc, cand, cached_jobs=cached_jobs)
                self.attempts.append((self.label(cand), cand.get('_run_claim_outcome')))
                return result
            stubs['process_candidate'] = process_spy
        for name, value in stubs.items():
            stack.enter_context(patch.object(svc, name, value, create=True))
        stack.enter_context(patch(
            'candidate_vetting_service.processing.ThreadPoolExecutor', self.executor,
        ))
        stack.enter_context(patch('screening.compliance.stamp_vetting_log_compliance', self._stamp))
        return stack


@pytest.fixture
def harness(app):
    return Harness(app)


@pytest.fixture
def auditor():
    from vetting_audit_service.revet_mixin import RevetMixin

    class Auditor(RevetMixin):
        pass

    return Auditor()


@pytest.fixture(autouse=True)
def _clean(app):
    from app import db
    from models import (
        CandidateJobMatch, CandidateVettingLog, EmbeddingFilterLog, EscalationLog,
        ParsedEmail, ScoutVettingSession, VettingAuditLog, VettingConfig,
    )

    def wipe():
        with app.app_context():
            ScoutVettingSession.query.filter(
                ScoutVettingSession.bullhorn_candidate_id == CANDIDATE
            ).delete()
            EmbeddingFilterLog.query.delete()
            EscalationLog.query.delete()
            CandidateJobMatch.query.delete()
            CandidateVettingLog.query.delete()
            VettingAuditLog.query.delete()
            ParsedEmail.query.delete()
            VettingConfig.query.filter_by(setting_key='vetting_cutoff_date').delete()
            db.session.commit()

    wipe()
    yield
    wipe()


def _seed_email(*, received_at, job_id=AUDITED_JOB, vetted=True, candidate_id=CANDIDATE,
                status='completed'):
    from app import db
    from models import ParsedEmail

    pe = ParsedEmail(
        sender_email='synthetic@example.test',
        recipient_email='inbox@example.test',
        status=status,
        bullhorn_candidate_id=candidate_id,
        bullhorn_job_id=job_id,
        received_at=received_at,
        processed_at=received_at or _now() - timedelta(days=30),
        vetted_at=(_now() - timedelta(hours=3)) if vetted else None,
    )
    db.session.add(pe)
    db.session.commit()
    if received_at is None:
        # The column default would otherwise stamp utcnow on insert.
        ParsedEmail.query.filter_by(id=pe.id).update({'received_at': None})
        db.session.commit()
    return pe.id


def _seed_vlog(*, created_at, status='completed', parsed_email_id=None, scores=None,
               candidate_id=CANDIDATE, updated_at=None, covers=()):
    from app import db
    from models import CandidateJobMatch, CandidateVettingLog, VettingAuditLog

    scores = scores or {}
    vlog = CandidateVettingLog(
        bullhorn_candidate_id=candidate_id,
        candidate_name='Synthetic Candidate',
        status=status,
        parsed_email_id=parsed_email_id,
        applied_job_id=AUDITED_JOB,
        highest_match_score=max(scores.values()) if scores else 0.0,
        is_qualified=False,
        is_sandbox=False,
        created_at=created_at,
        updated_at=updated_at or created_at,
    )
    db.session.add(vlog)
    db.session.flush()
    for job_id, score in scores.items():
        db.session.add(CandidateJobMatch(
            vetting_log_id=vlog.id, bullhorn_job_id=job_id, job_title='Synthetic Job',
            match_score=score, is_qualified=False, is_applied_job=(job_id == AUDITED_JOB),
            match_summary='synthetic', gaps_identified='',
        ))
    for audit_id in covers:
        VettingAuditLog.query.get(audit_id).revet_vetting_log_id = vlog.id
    db.session.commit()
    return vlog.id


_audit_seq = iter(range(900000, 999999))


def _seed_audit(*, job_id=AUDITED_JOB, age_hours=1.0, candidate_id=CANDIDATE):
    from app import db
    from models import VettingAuditLog

    row = VettingAuditLog(
        candidate_vetting_log_id=next(_audit_seq),
        bullhorn_candidate_id=candidate_id,
        candidate_name='Synthetic Candidate',
        job_id=job_id,
        job_title='Synthetic Job',
        original_score=20.0,
        finding_type='employment_gap_misfire',
        confidence='high',
        action_taken='revet_triggered',
        revet_new_score=None,
        audit_finding='synthetic finding',
        created_at=_now() - timedelta(hours=age_hours),
    )
    db.session.add(row)
    db.session.commit()
    return row.id


def _audit(audit_id):
    from app import db
    from models import VettingAuditLog

    db.session.expire_all()
    return VettingAuditLog.query.get(audit_id)


def _set_cutoff(value: datetime):
    from models import VettingConfig
    VettingConfig.set_value('vetting_cutoff_date', value.strftime('%Y-%m-%d %H:%M:%S'))


def _unvetted_ids(candidate_id=CANDIDATE):
    from app import db
    from models import ParsedEmail

    db.session.expire_all()
    return sorted(
        pe.id for pe in ParsedEmail.query.filter_by(bullhorn_candidate_id=candidate_id)
        if pe.vetted_at is None
    )


def _logs(candidate_id=CANDIDATE):
    from app import db
    from models import CandidateVettingLog

    db.session.expire_all()
    return {
        row.id: row.status
        for row in CandidateVettingLog.query.filter_by(bullhorn_candidate_id=candidate_id)
    }


def _match_scores(vetting_log_id):
    from models import CandidateJobMatch
    return {
        m.bullhorn_job_id: m.match_score
        for m in CandidateJobMatch.query.filter_by(vetting_log_id=vetting_log_id)
    }


def _other_connection():
    from app import db
    return db.engine.begin()


def _legacy_clear(candidate_id):
    """Pre-fix reset: every completed ParsedEmail re-queued."""
    from app import db
    from models import ParsedEmail
    for pe in ParsedEmail.query.filter_by(bullhorn_candidate_id=candidate_id, status='completed'):
        pe.vetted_at = None
    db.session.commit()


def _seed_seven_vetted_emails():
    base = _now() - timedelta(days=10)
    return [
        _seed_email(received_at=base + timedelta(days=i), job_id=OTHER_JOB if i % 2 else AUDITED_JOB)
        for i in range(7)
    ]


class TestSequentialBehaviour:
    def test_seven_vetted_emails_reset_to_one_analysis(self, app, harness, auditor):
        with app.app_context():
            ids = _seed_seven_vetted_emails()
            _seed_vlog(created_at=_now() - timedelta(hours=2), scores={AUDITED_JOB: 20.0})
            audit_id = _seed_audit(age_hours=0.0)

            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)
            # Newest email for the audited job (index 6 is AUDITED_JOB).
            assert _unvetted_ids() == [ids[6]]

            for _ in range(8):
                harness.run_cycle()

            assert harness.runs == [f'email:{ids[6]}']
            assert _unvetted_ids() == []
            assert _audit(audit_id).revet_new_score == 66.0

    def test_legacy_reset_fanned_out_one_analysis_per_email(self, app, harness):
        """Negative control: the old reset produced 7 analyses."""
        with app.app_context():
            _seed_seven_vetted_emails()
            _legacy_clear(CANDIDATE)
            for _ in range(8):
                harness.run_cycle()
            assert len(harness.runs) == 7

    def test_candidate_without_emails_is_enqueued_once(self, app, harness, auditor):
        with app.app_context():
            _seed_vlog(created_at=_now() - timedelta(hours=3), scores={AUDITED_JOB: 20.0})
            audit_id = _seed_audit()
            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)
            for _ in range(4):
                harness.run_cycle()
            assert harness.runs == [f'revet:{audit_id}']
            assert _audit(audit_id).revet_new_score == 66.0

    def test_all_emails_before_cutoff_falls_back_to_pending_revet(self, app, harness, auditor):
        with app.app_context():
            cutoff = (_now() - timedelta(days=5)).replace(microsecond=0)
            _set_cutoff(cutoff)
            _seed_email(received_at=cutoff - timedelta(days=1))
            _seed_email(received_at=None)
            audit_id = _seed_audit()
            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)
            assert _unvetted_ids() == []
            for _ in range(3):
                harness.run_cycle()
            assert harness.runs == [f'revet:{audit_id}']
            assert _audit(audit_id).revet_new_score == 66.0

    def test_failed_run_is_retried_one_analysis_per_cycle(self, app, harness, auditor):
        with app.app_context():
            ids = _seed_seven_vetted_emails()
            audit_id = _seed_audit()
            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)

            harness.fail_runs = True
            harness.run_cycle()
            assert harness.runs == [f'email:{ids[6]}']
            harness.run_cycle()
            assert harness.runs == [f'email:{ids[6]}', f'revet:{audit_id}']
            assert _unvetted_ids() == []

            harness.fail_runs = False
            harness.run_cycle()
            harness.run_cycle()
            assert harness.runs == [f'email:{ids[6]}', f'revet:{audit_id}', f'revet:{audit_id}']
            assert _audit(audit_id).revet_new_score == 66.0

    def test_two_audits_for_distinct_jobs_share_one_analysis(self, app, harness, auditor):
        with app.app_context():
            ids = _seed_seven_vetted_emails()
            a = _seed_audit(job_id=AUDITED_JOB)
            b = _seed_audit(job_id=OTHER_JOB)
            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)
            auditor._trigger_revet(CANDIDATE, 1, OTHER_JOB)
            assert _unvetted_ids() == [ids[6]]
            harness.scores = {AUDITED_JOB: 71.0, OTHER_JOB: 52.0}
            for _ in range(4):
                harness.run_cycle()
            assert harness.runs == [f'email:{ids[6]}']
            assert _audit(a).revet_new_score == 71.0
            assert _audit(b).revet_new_score == 52.0

    def test_twelve_hour_cap_terminates_unbound_row(self, app, harness):
        with app.app_context():
            audit_id = _seed_audit(age_hours=13.0)
            assert harness.svc.detect_pending_revet_candidates(max_attempt_hours=12.0) == []
            assert _audit(audit_id).action_taken == 'revet_skipped_job_mismatch'

    def test_active_scout_session_blocks_reset_without_deleting(self, app, harness, auditor):
        from app import db
        from models import ScoutVettingSession
        with app.app_context():
            vlog_id = _seed_vlog(created_at=_now() - timedelta(hours=3), scores={AUDITED_JOB: 20.0})
            db.session.add(ScoutVettingSession(
                vetting_log_id=vlog_id, bullhorn_candidate_id=CANDIDATE,
                candidate_email='synthetic@example.test', bullhorn_job_id=AUDITED_JOB,
                status='in_progress',
            ))
            db.session.commit()
            email = _seed_email(received_at=_now() - timedelta(days=1))
            audit_id = _seed_audit()

            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)
            assert harness.svc.detect_pending_revet_candidates() == []
            assert vlog_id in _logs()
            assert email not in _unvetted_ids()
            assert _audit(audit_id).action_taken == 'revet_triggered'


class TestAuditGenerations:
    """Finding 4: a run covers only audits that existed when it started."""

    def test_audit_arriving_during_a_run_needs_a_later_run(self, app, harness, auditor):
        with app.app_context():
            email = _seed_email(received_at=_now() - timedelta(hours=1), vetted=False)
            first = _seed_audit(age_hours=2.0)
            later = {}

            def audit_arrives_mid_run():
                from vetting_audit_service import live_lease_ids
                live = live_lease_ids(CANDIDATE)
                later['id'] = _seed_audit(job_id=AUDITED_JOB, age_hours=0.0)
                auditor._trigger_revet(CANDIDATE, 2, AUDITED_JOB)
                later['live_after_reset'] = live_lease_ids(CANDIDATE)
                later['live_before_reset'] = live

            harness.hook = audit_arrives_mid_run
            harness.run_cycle()

            assert later['live_before_reset'] == later['live_after_reset'] != []
            assert _audit(first).revet_new_score == 66.0
            row = _audit(later['id'])
            assert row.revet_new_score is None
            assert row.revet_vetting_log_id is None
            assert harness.runs == [f'email:{email}']

            harness.score = 77.0
            for _ in range(3):
                harness.run_cycle()
            assert harness.runs == [f'email:{email}', f"revet:{later['id']}"]
            assert _audit(later['id']).revet_new_score == 77.0
            assert _audit(first).revet_new_score == 66.0

    def test_completed_older_run_does_not_satisfy_newer_audit(self, app, harness):
        from vetting_audit_service import backfill_revet_new_score
        from models import CandidateVettingLog
        with app.app_context():
            old_run = _seed_vlog(created_at=_now() - timedelta(hours=2), scores={AUDITED_JOB: 30.0})
            audit_id = _seed_audit(age_hours=1.0)
            assert backfill_revet_new_score(CANDIDATE, vetting_log=CandidateVettingLog.query.get(old_run)) == 0
            assert backfill_revet_new_score(CANDIDATE) == 0
            assert _audit(audit_id).revet_new_score is None

    def test_backfill_needs_a_completed_bound_run(self, app):
        from vetting_audit_service import backfill_revet_new_score
        from models import CandidateVettingLog
        with app.app_context():
            audit_id = _seed_audit()
            live = _seed_vlog(created_at=_now(), status='processing', scores={AUDITED_JOB: 50.0},
                              covers=[audit_id])
            assert backfill_revet_new_score(CANDIDATE, vetting_log=CandidateVettingLog.query.get(live)) == 0
            assert _audit(audit_id).revet_new_score is None


class TestPerAuditResolution:
    """Finding 5: each audit row is settled on its own."""

    def test_partial_backfill_missing_job_and_newer_audit(self, app, harness):
        with app.app_context():
            a = _seed_audit(job_id=AUDITED_JOB, age_hours=3.0)
            b = _seed_audit(job_id=OTHER_JOB, age_hours=3.0)
            # Completed run bound to a and b; its cycle never back-filled.
            _seed_vlog(created_at=_now() - timedelta(hours=2), scores={AUDITED_JOB: 70.0},
                       covers=[a, b])
            c = _seed_audit(job_id=THIRD_JOB, age_hours=1.0)

            harness.score = 44.0
            for _ in range(3):
                harness.run_cycle()

            assert _audit(a).revet_new_score == 70.0
            assert _audit(b).action_taken == 'revet_skipped_job_mismatch'
            assert _audit(b).revet_new_score is None
            assert _audit(c).revet_new_score == 44.0
            assert harness.runs == [f'revet:{c}']

    def test_null_job_row_bound_to_completed_run_terminates(self, app, harness):
        with app.app_context():
            audit_id = _seed_audit(job_id=None, age_hours=1.0)
            _seed_vlog(created_at=_now() - timedelta(minutes=30), scores={AUDITED_JOB: 70.0},
                       covers=[audit_id])
            assert harness.svc.detect_pending_revet_candidates() == []
            assert _audit(audit_id).action_taken == 'revet_skipped_job_mismatch'

    def test_row_bound_to_failed_run_is_reopened(self, app, harness):
        with app.app_context():
            audit_id = _seed_audit(age_hours=1.0)
            _seed_vlog(created_at=_now() - timedelta(minutes=30), status='failed', covers=[audit_id])
            enqueued = harness.svc.detect_pending_revet_candidates()
            assert [c['_pending_revet_audit_id'] for c in enqueued] == [audit_id]
            assert _audit(audit_id).revet_vetting_log_id is None
            assert _logs() == {}

    def test_newer_audit_waits_for_live_run_then_runs_once(self, app, harness):
        from app import db
        from models import CandidateVettingLog
        with app.app_context():
            a = _seed_audit(job_id=AUDITED_JOB, age_hours=2.0)
            live = _seed_vlog(created_at=_now() - timedelta(minutes=1), status='processing',
                              covers=[a])
            c = _seed_audit(job_id=THIRD_JOB, age_hours=0.5)

            assert harness.svc.detect_pending_revet_candidates() == []
            assert live in _logs()

            CandidateVettingLog.query.get(live).status = 'failed'
            db.session.commit()
            for _ in range(3):
                harness.run_cycle()
            assert harness.runs == [f'revet:{a}']
            assert _audit(a).revet_new_score == 66.0
            assert _audit(c).revet_new_score == 66.0


class TestResetInterleavings:
    """Finding 1: rows written between a reset's guard read and its delete.

    On PostgreSQL the candidate lock keeps those writes out entirely; here the
    write comes from a second SQLite connection to show what the reset does if
    one slips through anyway.
    """

    def _inject_before_delete(self, action):
        import vetting_audit_service.helpers as helpers
        original = helpers.select_revet_parsed_email

        def select_then_write(*args, **kwargs):
            action()
            return original(*args, **kwargs)

        return patch.object(helpers, 'select_revet_parsed_email', select_then_write)

    def test_row_inserted_after_guard_read_is_not_deleted(self, app):
        from models import CandidateVettingLog
        from vetting_audit_service import clear_candidate_vetting_state
        with app.app_context():
            old = _seed_vlog(created_at=_now() - timedelta(hours=2), scores={AUDITED_JOB: 20.0})
            new = {}

            def worker_claims():
                with _other_connection() as conn:
                    new['id'] = conn.execute(CandidateVettingLog.__table__.insert().values(
                        bullhorn_candidate_id=CANDIDATE, status='processing',
                        created_at=_now(), updated_at=_now(),
                    )).inserted_primary_key[0]

            with self._inject_before_delete(worker_claims):
                stats = clear_candidate_vetting_state(CANDIDATE, AUDITED_JOB)

            assert stats['vetting_logs_deleted'] == 1
            assert _logs() == {new['id']: 'processing'}
            assert old not in _logs()

    def test_row_turning_processing_before_delete_defers_whole_reset(self, app):
        from models import CandidateVettingLog
        from vetting_audit_service import RevetResetDeferred, clear_candidate_vetting_state
        with app.app_context():
            email = _seed_email(received_at=_now() - timedelta(days=1))
            old = _seed_vlog(created_at=_now() - timedelta(hours=2), scores={AUDITED_JOB: 20.0})

            def reclaimed():
                with _other_connection() as conn:
                    conn.execute(CandidateVettingLog.__table__.update().where(
                        CandidateVettingLog.__table__.c.id == old,
                    ).values(status='processing'))

            with self._inject_before_delete(reclaimed):
                with pytest.raises(RevetResetDeferred):
                    clear_candidate_vetting_state(CANDIDATE, AUDITED_JOB)

            assert _logs() == {old: 'processing'}
            assert _match_scores(old) == {AUDITED_JOB: 20.0}
            assert email not in _unvetted_ids()


class TestClaimInterleavings:
    def test_two_detectors_enqueue_same_audit_one_run(self, app, harness):
        with app.app_context():
            audit_id = _seed_audit()
            first = harness.svc.detect_pending_revet_candidates()
            second = harness.svc.detect_pending_revet_candidates()
            assert len(first) == len(second) == 1

            nested = {}

            def second_worker_starts():
                nested['result'] = harness.process(dict(second[0]))
                nested['calls_after'] = len(harness.model_calls)

            harness.hook = second_worker_starts
            harness.process(dict(first[0]))

            assert nested['result'] is None
            assert harness.attempts[0] == (f'revet:{audit_id}', 'deferred')
            assert harness.runs == [f'revet:{audit_id}']
            assert len(harness.model_calls) == len(TEARSHEET_JOB_IDS)
            assert _audit(audit_id).revet_vetting_log_id is not None

            late = harness.process(dict(second[0]))
            assert late is None
            assert harness.runs == [f'revet:{audit_id}']

    def test_same_email_claimed_twice_runs_once_and_stays_queued_for_owner(self, app, harness):
        with app.app_context():
            email = _seed_email(received_at=_now(), vetted=False)
            dup = {'id': CANDIDATE, '_parsed_email_id': email, '_applied_job_id': AUDITED_JOB}
            seen = {}

            def overlapping_cycle_claims():
                seen['result'] = harness.process(dict(dup))
                seen['unvetted'] = _unvetted_ids()

            harness.hook = overlapping_cycle_claims
            harness.run_cycle()

            assert seen['result'] is None
            assert seen['unvetted'] == [email]
            assert harness.runs == [f'email:{email}']
            assert _unvetted_ids() == []

    def test_unknown_lock_state_starts_nothing(self, app, harness, auditor):
        from vetting_audit_service import CandidateLockUnavailable

        def unavailable(candidate_id):
            raise CandidateLockUnavailable('synthetic lock failure')

        with app.app_context():
            old = _seed_vlog(created_at=_now() - timedelta(hours=2), scores={AUDITED_JOB: 20.0})
            ids = _seed_seven_vetted_emails()
            fresh = _seed_email(received_at=_now(), job_id=OTHER_JOB, vetted=False)
            audit_id = _seed_audit()

            with patch('vetting_audit_service.run_claim.acquire_candidate_lock', unavailable), \
                    patch('vetting_audit_service.acquire_candidate_lock', unavailable):
                auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)
                assert harness.svc.detect_pending_revet_candidates() == []
                harness.run_cycle()

            assert old in _logs()
            assert _unvetted_ids() == [fresh]
            assert harness.runs == []
            assert harness.model_calls == []

            harness.run_cycle()
            assert harness.runs == [f'email:{fresh}']
            assert set(ids).isdisjoint(int(r.split(':')[1]) for r in harness.runs)
            assert _audit(audit_id).revet_new_score == 66.0


class TestGuardFailures:
    """Finding 3: unknown queue state is never treated as an empty queue."""

    def test_email_lookup_failure_skips_then_recovers(self, app, harness):
        import vetting_audit_service
        with app.app_context():
            old = _seed_vlog(created_at=_now() - timedelta(hours=2), scores={AUDITED_JOB: 20.0})
            queued = _seed_email(received_at=_now(), vetted=False)
            audit_id = _seed_audit()

            def broken(*args, **kwargs):
                raise ValueError('synthetic lookup failure')

            with patch.object(vetting_audit_service, 'select_revet_parsed_email', broken):
                assert harness.svc.detect_pending_revet_candidates() == []
            assert old in _logs()
            assert _unvetted_ids() == [queued]

            assert harness.svc.detect_pending_revet_candidates() == []
            for _ in range(2):
                harness.run_cycle()
            assert harness.runs == [f'email:{queued}']
            assert _audit(audit_id).revet_new_score == 66.0

    def test_lease_lookup_failure_skips(self, app, harness):
        import vetting_audit_service
        with app.app_context():
            old = _seed_vlog(created_at=_now() - timedelta(hours=2), scores={AUDITED_JOB: 20.0})
            _seed_audit()

            def broken(candidate_id):
                raise RuntimeError('synthetic database failure')

            with patch.object(vetting_audit_service, 'live_lease_ids', broken):
                assert harness.svc.detect_pending_revet_candidates() == []
            assert old in _logs()


class TestLeases:
    """Finding 2: only a lease that stopped heartbeating can be recovered,
    and a recovered worker cannot commit afterwards."""

    def test_reset_defers_on_old_and_null_timestamp_processing_rows(self, app, auditor):
        from app import db
        from models import CandidateVettingLog
        from vetting_audit_service import RevetResetDeferred, clear_candidate_vetting_state
        with app.app_context():
            ids = _seed_seven_vetted_emails()
            old_live = _seed_vlog(created_at=_now() - timedelta(minutes=30), status='processing')
            with pytest.raises(RevetResetDeferred):
                clear_candidate_vetting_state(CANDIDATE)

            CandidateVettingLog.query.filter_by(id=old_live).update(
                {'created_at': None, 'updated_at': None}, synchronize_session=False,
            )
            db.session.commit()
            _seed_audit()
            assert auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB) is None
            assert old_live in _logs()
            assert set(ids).isdisjoint(_unvetted_ids())

    def test_null_timestamp_lease_is_not_auto_recovered(self, app, harness):
        from app import db
        from models import CandidateVettingLog
        with app.app_context():
            lease = _seed_vlog(created_at=_now(), status='processing')
            CandidateVettingLog.query.filter_by(id=lease).update(
                {'created_at': None, 'updated_at': None}, synchronize_session=False,
            )
            db.session.commit()
            harness.svc._reset_stuck_processing()
            assert lease in _logs()

    def test_live_worker_past_ten_minutes_is_not_deleted_or_duplicated(self, app, harness, auditor):
        from app import db
        from models import CandidateVettingLog
        with app.app_context():
            email = _seed_email(received_at=_now(), vetted=False)
            first = _seed_audit(age_hours=2.0)
            during = {}

            def long_running():
                lease = next(iter(_logs()))
                # Started 30 minutes ago; last heartbeat a minute ago.
                CandidateVettingLog.query.filter_by(id=lease).update({
                    'created_at': _now() - timedelta(minutes=30),
                    'updated_at': _now() - timedelta(minutes=1),
                }, synchronize_session=False)
                db.session.commit()
                during['second_audit'] = _seed_audit(age_hours=0.0)
                auditor._trigger_revet(CANDIDATE, 2, AUDITED_JOB)
                during['detected'] = harness.svc.detect_pending_revet_candidates()
                harness.svc._reset_stuck_processing()
                during['logs'] = _logs()
                during['lease'] = lease

            harness.hook = long_running
            harness.run_cycle()

            assert during['detected'] == []
            assert during['logs'] == {during['lease']: 'processing'}
            assert _logs() == {during['lease']: 'completed'}
            assert harness.runs == [f'email:{email}']
            assert _audit(first).revet_new_score == 66.0
            assert _audit(during['second_audit']).revet_new_score is None

    def test_crashed_lease_recovery_fences_late_stale_worker(self, app, harness):
        from app import db
        from models import CandidateVettingLog
        with app.app_context():
            email = _seed_email(received_at=_now(), vetted=False)
            audit_id = _seed_audit(age_hours=1.0)
            during = {}

            def heartbeat_stops_and_recovery_runs():
                lease = next(iter(_logs()))
                CandidateVettingLog.query.filter_by(id=lease).update(
                    {'updated_at': _now() - timedelta(minutes=20)}, synchronize_session=False,
                )
                db.session.commit()
                harness.svc._reset_stuck_processing()
                during['lease'] = lease
                during['logs'] = _logs()
                during['unvetted'] = _unvetted_ids()
                during['binding'] = _audit(audit_id).revet_vetting_log_id

            harness.score = 55.0
            harness.hook = heartbeat_stops_and_recovery_runs
            harness.run_cycle()

            assert during['logs'] == {}
            assert during['unvetted'] == [email]
            assert during['binding'] is None
            assert harness.attempts == [(f'email:{email}', 'lease_lost')]
            assert _logs() == {}
            assert _unvetted_ids() == [email]
            assert harness.notes == []
            assert _audit(audit_id).revet_new_score is None

            harness.score = 77.0
            harness.run_cycle()
            assert [o for _, o in harness.attempts] == ['lease_lost', 'claimed']
            # SQLite may hand the deleted rowid to the replacement (PostgreSQL
            # sequences never do), so the fencing check is on outcomes.
            replacement = next(iter(_logs()))
            assert _match_scores(replacement) == {j: 77.0 for j in TEARSHEET_JOB_IDS}
            assert _audit(audit_id).revet_new_score == 77.0
            assert harness.notes == [replacement]

    def test_analysis_threads_survive_heartbeat_commits(self, app, harness):
        """Outside the cycle (which disables expire_on_commit) each heartbeat
        commit expires the run's ORM state; pool threads have no app context,
        so they must not touch it."""
        from candidate_vetting_service import CandidateVettingService
        renewals = threading.Semaphore(0)
        real_renew = CandidateVettingService._renew_run_lease

        def counting_renew(candidate, vetting_log):
            held = real_renew(candidate, vetting_log)
            renewals.release()
            return held

        class GatedExecutor(ThreadPoolExecutor):
            # Job n starts only after the main thread's (n+1)-th heartbeat:
            # one before the pool, then one per finished job.
            def __init__(self, max_workers=None, **kwargs):
                super().__init__(max_workers=1)

            def submit(self, fn, *args, **kwargs):
                def gated():
                    assert renewals.acquire(timeout=5)
                    return fn(*args, **kwargs)
                return super().submit(gated)

        harness.executor = GatedExecutor
        with app.app_context():
            email = _seed_email(received_at=_now(), vetted=False)
            with patch.object(CandidateVettingService, '_renew_run_lease', staticmethod(counting_renew)):
                harness.process({'id': CANDIDATE, '_parsed_email_id': email, '_applied_job_id': AUDITED_JOB})

            assert harness.attempts == [(f'email:{email}', 'claimed')]
            [(lease, status)] = _logs().items()
            assert status == 'completed'
            assert _match_scores(lease) == {j: 66.0 for j in TEARSHEET_JOB_IDS}

    @pytest.mark.parametrize('fail', [False, True])
    def test_own_flushed_terminal_status_still_commits(self, app, harness, fail):
        """Any query between setting the terminal status and the fenced commit
        can autoflush it; the fence must not read that as a lost lease."""
        from app import db
        import vetting_audit_service.run_claim as run_claim
        real_lock = run_claim.acquire_candidate_lock

        def flush_then_lock(candidate_id):
            db.session.flush()
            real_lock(candidate_id)

        with app.app_context():
            email = _seed_email(received_at=_now(), vetted=False)
            harness.fail_runs = fail
            with patch.object(run_claim, 'acquire_candidate_lock', flush_then_lock):
                harness.process({'id': CANDIDATE, '_parsed_email_id': email, '_applied_job_id': AUDITED_JOB})

            assert harness.attempts == [(f'email:{email}', 'claimed')]
            [(lease, status)] = _logs().items()
            assert status == ('failed' if fail else 'completed')
            if not fail:
                assert _match_scores(lease) == {j: 66.0 for j in TEARSHEET_JOB_IDS}

    def test_lease_failed_before_final_commit_discards_results(self, app, harness):
        from models import CandidateVettingLog
        with app.app_context():
            email = _seed_email(received_at=_now(), vetted=False)
            audit_id = _seed_audit(age_hours=1.0)

            def admin_marks_failed(vetting_log_id):
                with _other_connection() as conn:
                    conn.execute(CandidateVettingLog.__table__.update().where(
                        CandidateVettingLog.__table__.c.id == vetting_log_id,
                    ).values(status='failed'))

            harness.final_hook = admin_marks_failed
            harness.run_cycle()

            [(lease, status)] = _logs().items()
            assert status == 'failed'
            assert _match_scores(lease) == {}
            assert harness.attempts == [(f'email:{email}', 'lease_lost')]
            assert _unvetted_ids() == [email]
            assert _audit(audit_id).revet_new_score is None


class TestDistinctSubmissions:
    """Two applications by one candidate are two screenings, even when both
    score the same related job and the scores differ."""

    def _harness(self, app):
        return Harness(app, job_ids=(SUBMISSION_JOB_1, SUBMISSION_JOB_2, RELATED_JOB))

    def test_both_submissions_are_screened_with_their_own_scores(self, app):
        harness = self._harness(app)
        with app.app_context():
            s1 = _seed_email(received_at=_now() - timedelta(hours=2), job_id=SUBMISSION_JOB_1, vetted=False)
            s2 = _seed_email(received_at=_now() - timedelta(hours=1), job_id=SUBMISSION_JOB_2, vetted=False)

            harness.scores = {RELATED_JOB: 70.0}
            harness.run_cycle()
            harness.scores = {RELATED_JOB: 40.0}
            harness.run_cycle()
            harness.run_cycle()

            assert harness.runs == [f'email:{s1}', f'email:{s2}']
            assert _unvetted_ids() == []
            from models import CandidateVettingLog
            related = {
                vl.parsed_email_id: _match_scores(vl.id)[RELATED_JOB]
                for vl in CandidateVettingLog.query.filter_by(bullhorn_candidate_id=CANDIDATE)
            }
            assert related == {s1: 70.0, s2: 40.0}

    def test_second_submission_during_first_run_is_claimed(self, app):
        harness = self._harness(app)
        with app.app_context():
            s1 = _seed_email(received_at=_now() - timedelta(hours=2), job_id=SUBMISSION_JOB_1, vetted=False)
            during = {}

            def second_application_arrives_and_starts():
                s2 = _seed_email(received_at=_now(), job_id=SUBMISSION_JOB_2, vetted=False)
                during['s2'] = s2
                harness.scores = {RELATED_JOB: 40.0}
                during['result'] = harness.process({
                    'id': CANDIDATE, '_parsed_email_id': s2, '_applied_job_id': SUBMISSION_JOB_2,
                })

            harness.scores = {RELATED_JOB: 70.0}
            harness.hook = second_application_arrives_and_starts
            harness.run_cycle()

            assert during['result'] is not None
            assert sorted(harness.runs) == sorted([f'email:{s1}', f"email:{during['s2']}"])
            assert sorted(_logs().values()) == ['completed', 'completed']

    def test_pending_audit_is_covered_by_a_new_application_run(self, app):
        harness = self._harness(app)
        with app.app_context():
            audit_id = _seed_audit(job_id=RELATED_JOB, age_hours=1.0)
            s1 = _seed_email(received_at=_now(), job_id=SUBMISSION_JOB_1, vetted=False)
            harness.scores = {RELATED_JOB: 58.0}
            for _ in range(3):
                harness.run_cycle()
            assert harness.runs == [f'email:{s1}']
            assert _audit(audit_id).revet_new_score == 58.0


class TestSelector:
    def test_prefers_audited_job_then_newest_with_id_tiebreak(self, app):
        from vetting_audit_service import select_revet_parsed_email
        with app.app_context():
            t = _now() - timedelta(days=1)
            older_audited = _seed_email(received_at=t - timedelta(days=2), job_id=AUDITED_JOB)
            _seed_email(received_at=t, job_id=OTHER_JOB)
            tie_b = _seed_email(received_at=t, job_id=OTHER_JOB)

            assert select_revet_parsed_email(CANDIDATE, AUDITED_JOB)[0].id == older_audited
            assert select_revet_parsed_email(CANDIDATE, 99999)[0].id == tie_b
            assert select_revet_parsed_email(CANDIDATE, None)[0].id == tie_b

    def test_null_received_at_sorts_oldest_without_cutoff(self, app):
        from vetting_audit_service import select_revet_parsed_email
        with app.app_context():
            _seed_email(received_at=None)
            dated = _seed_email(received_at=_now() - timedelta(days=400))
            assert select_revet_parsed_email(CANDIDATE)[0].id == dated

    def test_only_null_received_at_is_selectable_without_cutoff(self, app):
        from vetting_audit_service import select_revet_parsed_email
        with app.app_context():
            null_id = _seed_email(received_at=None)
            assert select_revet_parsed_email(CANDIDATE)[0].id == null_id

    def test_non_completed_emails_are_never_reset(self, app):
        from vetting_audit_service import clear_candidate_vetting_state
        with app.app_context():
            _seed_email(received_at=_now(), status='failed')
            assert clear_candidate_vetting_state(CANDIDATE)['parsed_emails_reset'] == 0

    def test_repeated_resets_are_idempotent(self, app):
        from vetting_audit_service import clear_candidate_vetting_state
        with app.app_context():
            ids = _seed_seven_vetted_emails()
            first = clear_candidate_vetting_state(CANDIDATE, audit_job_id=AUDITED_JOB)
            second = clear_candidate_vetting_state(CANDIDATE, audit_job_id=AUDITED_JOB)
            assert first['parsed_email_id'] == ids[6]
            assert second['parsed_emails_reset'] == 0
            assert second['pending_parsed_email_id'] == ids[6]
            assert _unvetted_ids() == [ids[6]]

    def test_before_exact_after_and_null_cutoff(self, app, harness):
        from vetting_audit_service import clear_candidate_vetting_state, select_revet_parsed_email
        with app.app_context():
            cutoff = (_now() - timedelta(days=5)).replace(microsecond=0)
            _set_cutoff(cutoff)
            _seed_email(received_at=cutoff - timedelta(seconds=1))
            exact = _seed_email(received_at=cutoff)
            _seed_email(received_at=None)

            assert select_revet_parsed_email(CANDIDATE)[0].id == exact
            after = _seed_email(received_at=cutoff + timedelta(hours=1))
            assert select_revet_parsed_email(CANDIDATE)[0].id == after

            clear_candidate_vetting_state(CANDIDATE)
            with harness._patched():
                picked = [c['_parsed_email_id'] for c in harness.svc.detect_unvetted_applications()]
            assert picked == [after]

    def test_tied_timestamps_after_cutoff_pick_highest_id(self, app):
        from vetting_audit_service import select_revet_parsed_email
        with app.app_context():
            cutoff = (_now() - timedelta(days=5)).replace(microsecond=0)
            _set_cutoff(cutoff)
            t = cutoff + timedelta(days=1)
            _seed_email(received_at=t)
            second = _seed_email(received_at=t)
            assert select_revet_parsed_email(CANDIDATE)[0].id == second
