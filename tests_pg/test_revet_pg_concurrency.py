"""Competing-connection PostgreSQL tests for the revet run-claim protocol.

STATUS: UNRUN ARTIFACT. Written offline; never executed. They need the
disposable PostgreSQL 16 loopback cluster from the setup proposal and fail
(not skip) without it. Each test drives two or more real connections, each on
its own thread, app context and session, and orders them with events and
``pg_stat_activity`` lock-wait checks rather than sleeps. Lock timeouts are
patched down where a test needs a timeout to fire.

Races covered (one class each):
  1. stale back-fill versus reset / rebind
  2. live blocking job versus lease recovery
  3. candidate-lock and renewal failure
  4. legacy unique-constraint retry conflict
  5. multiple completed logs in the back-fill fallback
  6. bound partial-match run versus the audit age cap
  7. simultaneous claims, lock ordering / deadlock, second-connection fencing
  8. distinct applications scoring the same related job
  9. revet_vetting_log_id boot migration
"""

import threading
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import text

from tests_pg.conftest import Worker, wait_until_lock_wait
from tests.test_revet_parsed_email_fanout import (
    AUDITED_JOB, CANDIDATE, OTHER_JOB, RELATED_JOB, SUBMISSION_JOB_1,
    SUBMISSION_JOB_2, TEARSHEET_JOB_IDS, Harness, _TickLease, _audit, _logs,
    _match_scores, _now, _seed_audit, _seed_email, _seed_vlog, _unvetted_ids,
)

pytestmark = pytest.mark.pg_unrun

NS = 0x52564554


def _run_claim():
    import vetting_audit_service.run_claim as run_claim
    return run_claim


def _pgcode(error):
    return getattr(getattr(error, 'orig', None), 'pgcode', None)


def _holder_connection(app):
    from app import db
    return db.engine.connect()


def _gate_in_thread(target_thread_name, before_real, real):
    """Wrap ``real`` so that, only on the named thread, ``before_real`` runs
    first. Patches are process-wide; other threads call ``real`` directly."""
    def wrapper(*args, **kwargs):
        if threading.current_thread().name == target_thread_name:
            before_real()
        return real(*args, **kwargs)
    return wrapper


class TestBackfillVersusResetRebind:
    def test_backfill_holding_lock_finishes_before_reset(self, app):
        """Back-fill reads under L(c); a reset arriving after that read waits,
        so the score written is the bound run's and the reset follows."""
        from vetting_audit_service import backfill_revet_new_score, clear_candidate_vetting_state
        run_claim = _run_claim()
        audit_id = _seed_audit()
        run_a = _seed_vlog(created_at=_now(), scores={AUDITED_JOB: 66.0}, covers=(audit_id,))

        read_done, go = threading.Event(), threading.Event()

        def pause():
            read_done.set()
            assert go.wait(10)

        real = run_claim.write_bound_score
        with patch.object(run_claim, 'write_bound_score', _gate_in_thread('backfill', pause, real)):
            backfill = Worker(app, backfill_revet_new_score, CANDIDATE, _name='backfill')
            assert read_done.wait(10)
            reset = Worker(app, clear_candidate_vetting_state, CANDIDATE, AUDITED_JOB)
            assert wait_until_lock_wait(app, reset.pid) == 'advisory'
            go.set()
            backfill.join()
            reset.join()

        assert backfill.error is None and backfill.result == 1
        assert reset.error is None
        assert _audit(audit_id).revet_new_score == 66.0
        assert run_a not in _logs()

    def test_reset_and_rebind_first_makes_old_backfill_write_nothing(self, app):
        from vetting_audit_service import backfill_revet_new_score
        from vetting_audit_service.helpers import reset_candidate_vetting_state_locked
        from app import db
        run_claim = _run_claim()
        audit_id = _seed_audit()
        run_a = _seed_vlog(created_at=_now(), scores={AUDITED_JOB: 66.0}, covers=(audit_id,))
        locked, go = threading.Event(), threading.Event()

        def reset_holding_lock():
            run_claim.acquire_candidate_lock(CANDIDATE)
            reset_candidate_vetting_state_locked(CANDIDATE, AUDITED_JOB)
            locked.set()
            assert go.wait(10)
            db.session.commit()

        reset = Worker(app, reset_holding_lock)
        assert locked.wait(10)
        backfill = Worker(app, backfill_revet_new_score, CANDIDATE, SimpleNamespace(id=run_a))
        assert wait_until_lock_wait(app, backfill.pid) == 'advisory'
        go.set()
        reset.join()
        backfill.join()
        assert backfill.error is None and backfill.result == 0

        claim = {'id': CANDIDATE, '_pending_revet_audit_id': audit_id}
        from models import CandidateVettingLog
        new_run = run_claim.claim_run(claim, CandidateVettingLog(
            bullhorn_candidate_id=CANDIDATE, candidate_name='Synthetic', status='processing'))
        assert claim['_run_claim_outcome'] == 'claimed'
        row = _audit(audit_id)
        assert row.revet_new_score is None
        assert row.revet_vetting_log_id == new_run.id

    def test_detector_repair_and_backfill_write_once(self, app, harness):
        """Detector pass 1 and the cycle's back-fill both try to copy the same
        score; the conditional write lets exactly one land."""
        from vetting_audit_service import backfill_revet_new_score
        audit_id = _seed_audit()
        _seed_vlog(created_at=_now(), scores={AUDITED_JOB: 66.0}, covers=(audit_id,))
        barrier = threading.Barrier(2)

        def detector():
            barrier.wait(10)
            return harness.svc.detect_pending_revet_candidates()

        def backfill():
            barrier.wait(10)
            return backfill_revet_new_score(CANDIDATE)

        a, b = Worker(app, detector), Worker(app, backfill)
        a.join()
        b.join()
        assert a.error is None and b.error is None
        assert _audit(audit_id).revet_new_score == 66.0
        assert b.result in (0, 1)


class TestLiveJobVersusRecovery:
    def test_heartbeat_row_update_wins_over_waiting_recovery(self, app):
        """Recovery's DELETE waits on the heartbeat's row lock, then re-checks
        its predicate against the new row version (READ COMMITTED) and
        deletes nothing."""
        from app import db
        from models import CandidateVettingLog
        run_claim = _run_claim()
        lease = _seed_vlog(created_at=_now() - timedelta(minutes=30), status='processing',
                           updated_at=_now() - timedelta(minutes=20))
        updated, go = threading.Event(), threading.Event()

        def heartbeat_holding_row():
            CandidateVettingLog.query.filter_by(id=lease, status='processing').update(
                {'updated_at': _now()}, synchronize_session=False)
            updated.set()
            assert go.wait(10)
            db.session.commit()

        with patch.object(run_claim, 'RECOVERY_LOCK_TIMEOUT_MS', 10000):
            beat = Worker(app, heartbeat_holding_row)
            assert updated.wait(10)
            recovery = Worker(app, run_claim.recover_expired_lease, lease,
                              _now() - timedelta(minutes=10))
            assert wait_until_lock_wait(app, recovery.pid) in ('transactionid', 'tuple')
            go.set()
            beat.join()
            recovery.join()

        assert recovery.error is None and recovery.result is None
        assert _logs() == {lease: 'processing'}

    def test_full_worker_blocking_call_is_not_recovered(self, app, harness):
        from models import CandidateVettingLog
        lease_clock = _TickLease()
        email = _seed_email(received_at=_now(), vetted=False)
        audit_id = _seed_audit(age_hours=1.0)
        during = {}

        def blocking_call():
            from app import db
            lease = next(iter(_logs()))
            CandidateVettingLog.query.filter_by(id=lease).update(
                {'updated_at': _now() - timedelta(minutes=20)}, synchronize_session=False)
            db.session.commit()
            lease_clock.tick()
            recovery = Worker(app, harness.svc._reset_stuck_processing).join()
            during['error'] = recovery.error
            during['logs'] = _logs()
            during['lease'] = lease

        harness.hook = blocking_call
        with lease_clock.patch():
            harness.run_cycle()

        assert during['error'] is None
        assert during['logs'] == {during['lease']: 'processing'}
        assert _logs() == {during['lease']: 'completed'}
        assert len(harness.model_calls) == len(TEARSHEET_JOB_IDS)
        assert _audit(audit_id).revet_new_score == 66.0
        assert _unvetted_ids() == []
        assert email


class TestLockAndRenewalFailure:
    def test_every_entry_point_fails_closed_while_lock_is_held_elsewhere(self, app, harness):
        from models import CandidateVettingLog
        from vetting_audit_service import backfill_revet_new_score
        run_claim = _run_claim()
        audit_id = _seed_audit()
        done = _seed_vlog(created_at=_now(), scores={AUDITED_JOB: 66.0}, covers=(audit_id,))
        stale = _seed_vlog(created_at=_now() - timedelta(hours=1), status='processing',
                           updated_at=_now() - timedelta(minutes=30))
        holder = _holder_connection(app)
        holder.execute(text('SELECT pg_advisory_lock(:ns, :c)'), {'ns': NS, 'c': CANDIDATE})
        try:
            with patch.object(run_claim, 'LOCK_TIMEOUT_MS', 200), \
                    patch.object(run_claim, 'RECOVERY_LOCK_TIMEOUT_MS', 200):
                claim = {'id': CANDIDATE, '_pending_revet_audit_id': audit_id}
                with pytest.raises(run_claim.CandidateLockUnavailable):
                    run_claim.claim_run(claim, CandidateVettingLog(
                        bullhorn_candidate_id=CANDIDATE, candidate_name='S', status='processing'))
                assert backfill_revet_new_score(CANDIDATE) == 0
                with pytest.raises(run_claim.CandidateLockUnavailable):
                    run_claim.recover_expired_lease(stale, _now() - timedelta(minutes=10))
                assert run_claim.commit_if_lease_held(CANDIDATE, stale) is False
                assert harness.svc.detect_pending_revet_candidates() == []
        finally:
            holder.execute(text('SELECT pg_advisory_unlock(:ns, :c)'), {'ns': NS, 'c': CANDIDATE})
            holder.close()

        assert _audit(audit_id).revet_new_score is None
        assert set(_logs()) == {done, stale}
        assert backfill_revet_new_score(CANDIDATE) == 1

    def test_renewal_blocked_on_row_is_unknown_and_sticky(self, app):
        run_claim = _run_claim()
        lease_id = _seed_vlog(created_at=_now(), status='processing')
        holder = _holder_connection(app)
        trans = holder.begin()
        holder.execute(text('SELECT id FROM candidate_vetting_log WHERE id = :i FOR UPDATE'), {'i': lease_id})
        try:
            with patch.object(run_claim, 'RENEW_LOCK_TIMEOUT_MS', 200):
                lease = run_claim.RunLease(app, CANDIDATE, lease_id)
                assert lease.beat() == 'unknown'
        finally:
            trans.rollback()
            holder.close()
        assert run_claim.renew_lease(CANDIDATE, lease_id) == 'held'
        assert lease.beat() == 'unknown'
        assert lease.allow_paid_call() is False

    def test_renewal_failure_during_active_analysis(self, app, harness):
        run_claim = _run_claim()
        lease_clock = _TickLease()
        email = _seed_email(received_at=_now(), vetted=False)
        audit_id = _seed_audit(age_hours=1.0)
        states = []
        harness.call_observer = lambda job_id: states.append(lease_clock.instances[0].state)

        def row_locked_during_heartbeat():
            lease_id = next(iter(_logs()))
            holder = _holder_connection(app)
            trans = holder.begin()
            holder.execute(text('SELECT id FROM candidate_vetting_log WHERE id = :i FOR UPDATE'),
                           {'i': lease_id})
            try:
                lease_clock.tick()
            finally:
                trans.rollback()
                holder.close()

        harness.hook = row_locked_during_heartbeat
        with lease_clock.patch(), patch.object(run_claim, 'RENEW_LOCK_TIMEOUT_MS', 200):
            harness.run_cycle()

        assert set(states) == {'held'}
        assert harness.model_calls == [AUDITED_JOB]
        assert harness.attempts == [(f'email:{email}', 'lease_unknown')]
        assert _logs() == {}
        assert _unvetted_ids() == [email]
        row = _audit(audit_id)
        assert row.revet_new_score is None and row.revet_vetting_log_id is None


class TestLegacyUniqueConflict:
    @pytest.fixture
    def legacy_unique(self, app):
        from app import db
        db.session.execute(text(
            'CREATE UNIQUE INDEX legacy_cvl_candidate_uq ON candidate_vetting_log (bullhorn_candidate_id)'))
        db.session.commit()
        yield
        db.session.rollback()
        db.session.execute(text('DROP INDEX IF EXISTS legacy_cvl_candidate_uq'))
        db.session.commit()

    def test_processing_path_defers_and_never_reopens_old_row(self, app, harness, legacy_unique):
        old = _seed_vlog(created_at=_now() - timedelta(days=2), status='incomplete',
                         scores={AUDITED_JOB: 0.0})
        email = _seed_email(received_at=_now(), vetted=False)
        result = harness.process({'id': CANDIDATE, '_parsed_email_id': email, '_applied_job_id': AUDITED_JOB})
        assert result is None
        assert harness.attempts == [(f'email:{email}', 'deferred')]
        assert harness.model_calls == []
        assert _logs() == {old: 'incomplete'}
        assert _match_scores(old) == {AUDITED_JOB: 0.0}
        assert _unvetted_ids() == [email]

    def test_competing_claims_serialise_then_second_hits_constraint(self, app, legacy_unique):
        from models import CandidateVettingLog
        run_claim = _run_claim()
        e1 = _seed_email(received_at=_now(), vetted=False)
        e2 = _seed_email(received_at=_now(), job_id=OTHER_JOB, vetted=False)
        flushed, go = threading.Event(), threading.Event()
        real_bind = run_claim._bind_open_audits

        def pause():
            flushed.set()
            assert go.wait(10)

        def claim(email):
            cand = {'id': CANDIDATE, '_parsed_email_id': email}
            return run_claim.claim_run(cand, CandidateVettingLog(
                bullhorn_candidate_id=CANDIDATE, candidate_name='S', status='processing',
                parsed_email_id=email)), cand

        with patch.object(run_claim, '_bind_open_audits', _gate_in_thread('first', pause, real_bind)):
            first = Worker(app, claim, e1, _name='first')
            assert flushed.wait(10)
            second = Worker(app, claim, e2, _name='second')
            assert wait_until_lock_wait(app, second.pid) == 'advisory'
            go.set()
            first.join()
            second.join()

        assert first.error is None and first.result[1]['_run_claim_outcome'] == 'claimed'
        assert second.error is not None and _pgcode(second.error) == '23505'
        assert list(_logs().values()) == ['processing']


class TestMultipleCompletedLogs:
    def test_fallback_uses_newest_completed_run(self, app):
        from vetting_audit_service import backfill_revet_new_score
        audit_id = _seed_audit()
        _seed_vlog(created_at=_now() - timedelta(hours=3), scores={AUDITED_JOB: 20.0})
        _seed_vlog(created_at=_now() - timedelta(hours=2), scores={AUDITED_JOB: 30.0})
        _seed_vlog(created_at=_now(), scores={AUDITED_JOB: 71.0}, covers=(audit_id,))
        assert backfill_revet_new_score(CANDIDATE) == 1
        assert _audit(audit_id).revet_new_score == 71.0


class TestBoundPartialMatchCap:
    def test_dead_partial_run_kept_and_audit_capped(self, app, harness):
        from app import db
        from models import CandidateVettingLog
        email = _seed_email(received_at=_now() - timedelta(hours=14))
        capped = _seed_audit(age_hours=13.0)
        dead = _seed_vlog(created_at=_now() - timedelta(hours=13), status='processing',
                          updated_at=_now() - timedelta(hours=12, minutes=50),
                          parsed_email_id=email, scores={AUDITED_JOB: 35.0}, covers=(capped,))

        # A worker inserting another match holds FOR KEY SHARE on the run
        # row; marking it failed (a non-key update) must not wait on that.
        holder = _holder_connection(app)
        trans = holder.begin()
        holder.execute(text('SELECT id FROM candidate_vetting_log WHERE id = :i FOR KEY SHARE'), {'i': dead})
        try:
            recovery = Worker(app, harness.svc._reset_stuck_processing).join()
        finally:
            trans.rollback()
            holder.close()
        assert recovery.error is None
        assert _logs() == {dead: 'failed'}
        assert _match_scores(dead) == {AUDITED_JOB: 35.0}
        assert db.session.get(CandidateVettingLog, dead).parsed_email_id is None
        assert _unvetted_ids() == [email]

        Worker(app, harness.svc.detect_pending_revet_candidates).join()
        row = _audit(capped)
        assert row.action_taken == 'revet_skipped_job_mismatch'
        assert row.revet_new_score is None

    def test_live_run_past_cap_untouched(self, app, harness):
        capped = _seed_audit(age_hours=13.0)
        live = _seed_vlog(created_at=_now() - timedelta(hours=13), status='processing',
                          updated_at=_now() - timedelta(minutes=1),
                          scores={AUDITED_JOB: 35.0}, covers=(capped,))
        Worker(app, harness.svc._reset_stuck_processing).join()
        Worker(app, harness.svc.detect_pending_revet_candidates).join()
        assert _logs() == {live: 'processing'}
        assert _audit(capped).revet_vetting_log_id == live


class TestClaimsOrderingAndFencing:
    N = 6

    def _claim_many(self, app, make_candidate):
        from models import CandidateVettingLog
        run_claim = _run_claim()
        barrier = threading.Barrier(self.N)

        def claim():
            cand = make_candidate()
            barrier.wait(10)
            try:
                run_claim.claim_run(cand, CandidateVettingLog(
                    bullhorn_candidate_id=CANDIDATE, candidate_name='S', status='processing',
                    parsed_email_id=cand.get('_parsed_email_id')))
            except run_claim.CandidateLockUnavailable:
                cand['_run_claim_outcome'] = 'lock_unavailable'
            return cand['_run_claim_outcome']

        workers = [Worker(app, claim) for _ in range(self.N)]
        for w in workers:
            w.join()
        assert all(w.error is None for w in workers)
        return sorted(w.result for w in workers)

    def test_simultaneous_revet_claims_one_wins(self, app):
        audit_id = _seed_audit()
        outcomes = self._claim_many(app, lambda: {'id': CANDIDATE, '_pending_revet_audit_id': audit_id})
        assert outcomes == ['claimed'] + ['deferred'] * (self.N - 1)
        [lease] = _logs()
        assert _audit(audit_id).revet_vetting_log_id == lease

    def test_simultaneous_same_email_claims_one_wins(self, app):
        email = _seed_email(received_at=_now(), vetted=False)
        outcomes = self._claim_many(app, lambda: {'id': CANDIDATE, '_parsed_email_id': email})
        assert outcomes == ['claimed'] + ['deferred'] * (self.N - 1)

    def test_recovery_yields_to_worker_holding_its_row(self, app):
        """Worker holds its lease row (autoflush) and waits for L(c); recovery
        holds L(c) and waits for the row. Recovery's lock_timeout (500 ms)
        fires before deadlock_timeout (1 s), so the worker commits."""
        from app import db
        from models import CandidateVettingLog
        run_claim = _run_claim()
        lease = _seed_vlog(created_at=_now() - timedelta(hours=1), status='processing',
                           updated_at=_now() - timedelta(minutes=30))
        flushed, go = threading.Event(), threading.Event()

        def worker():
            CandidateVettingLog.query.filter_by(id=lease).update(
                {'status': 'completed'}, synchronize_session=False)
            flushed.set()
            assert go.wait(10)
            return run_claim.commit_if_lease_held(CANDIDATE, lease)

        w = Worker(app, worker)
        assert flushed.wait(10)
        r = Worker(app, run_claim.recover_expired_lease, lease, _now() - timedelta(minutes=10))
        assert wait_until_lock_wait(app, r.pid) in ('transactionid', 'tuple')
        go.set()
        w.join()
        r.join()
        assert w.error is None and w.result is True
        assert r.error is not None and _pgcode(r.error) == '55P03'
        assert _logs() == {lease: 'completed'}
        db.session.expire_all()

    def test_fence_reads_committed_state_on_second_connection(self, app):
        from app import db
        from models import CandidateJobMatch, CandidateVettingLog
        run_claim = _run_claim()
        lease = _seed_vlog(created_at=_now(), status='processing')

        CandidateVettingLog.query.filter_by(id=lease).update({'status': 'completed'}, synchronize_session=False)
        db.session.flush()
        assert run_claim.commit_if_lease_held(CANDIDATE, lease) is True
        assert _logs() == {lease: 'completed'}

        lease2 = _seed_vlog(created_at=_now() - timedelta(hours=1), status='processing',
                            updated_at=_now() - timedelta(minutes=30))
        recovered = Worker(app, run_claim.recover_expired_lease, lease2,
                           _now() - timedelta(minutes=10)).join()
        assert recovered.error is None and recovered.result is not None
        # The stale worker's pending results, never flushed (the fence runs
        # without autoflush), are rolled back.
        db.session.add(CandidateJobMatch(vetting_log_id=lease2, bullhorn_job_id=AUDITED_JOB,
                                         job_title='S', match_score=90.0, is_qualified=True))
        assert run_claim.commit_if_lease_held(CANDIDATE, lease2) is False
        assert lease2 not in _logs()
        assert CandidateJobMatch.query.filter_by(vetting_log_id=lease2).count() == 0

    def test_fence_fails_closed_when_pool_is_exhausted(self, app):
        from app import db
        from sqlalchemy.exc import TimeoutError as PoolTimeout
        run_claim = _run_claim()
        lease = _seed_vlog(created_at=_now(), status='processing')

        def exhausted():
            raise PoolTimeout('QueuePool limit reached')

        with patch.object(db.engine, 'connect', exhausted):
            assert run_claim.commit_if_lease_held(CANDIDATE, lease) is False
        assert _logs() == {lease: 'processing'}


class TestDistinctApplications:
    def test_two_applications_same_related_job_both_claimed_and_scored(self, app):
        harness = Harness(app, job_ids=(SUBMISSION_JOB_1, SUBMISSION_JOB_2, RELATED_JOB))
        s1 = _seed_email(received_at=_now() - timedelta(hours=2), job_id=SUBMISSION_JOB_1, vetted=False)
        s2 = _seed_email(received_at=_now() - timedelta(hours=1), job_id=SUBMISSION_JOB_2, vetted=False)
        from models import CandidateVettingLog
        run_claim = _run_claim()
        barrier = threading.Barrier(2)

        def claim(email):
            cand = {'id': CANDIDATE, '_parsed_email_id': email}
            barrier.wait(10)
            run_claim.claim_run(cand, CandidateVettingLog(
                bullhorn_candidate_id=CANDIDATE, candidate_name='S', status='processing',
                parsed_email_id=email))
            return cand['_run_claim_outcome']

        a, b = Worker(app, claim, s1), Worker(app, claim, s2)
        a.join()
        b.join()
        assert (a.result, b.result) == ('claimed', 'claimed')
        from app import db
        CandidateVettingLog.query.delete()
        db.session.commit()

        harness.scores = {RELATED_JOB: 70.0}
        harness.run_cycle()
        harness.scores = {RELATED_JOB: 40.0}
        harness.run_cycle()
        related = {
            vl.parsed_email_id: _match_scores(vl.id)[RELATED_JOB]
            for vl in CandidateVettingLog.query.filter_by(bullhorn_candidate_id=CANDIDATE)
        }
        assert related == {s1: 70.0, s2: 40.0}


class TestBootMigration:
    def _has_column(self):
        from app import db
        return db.session.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'vetting_audit_log' AND column_name = 'revet_vetting_log_id'"
        )).scalar()

    def test_column_added_idempotently_and_existing_audits_adopted(self, app, harness):
        from app import db
        from seeding.migrations import run_schema_migrations
        audit_id = _seed_audit()
        db.session.execute(text('ALTER TABLE vetting_audit_log DROP COLUMN revet_vetting_log_id'))
        db.session.commit()
        assert self._has_column() == 0

        email = _seed_email(received_at=_now(), vetted=False)
        harness.process({'id': CANDIDATE, '_parsed_email_id': email, '_applied_job_id': AUDITED_JOB})
        # Missing column: the claim fails, nothing is spent, the email stays queued.
        assert harness.attempts == [(f'email:{email}', 'deferred')]
        assert harness.model_calls == []
        assert _unvetted_ids() == [email]

        run_schema_migrations(db)
        assert self._has_column() == 1
        run_schema_migrations(db)
        assert self._has_column() == 1

        harness.run_cycle()
        assert harness.runs == [f'email:{email}']
        # The pre-existing unbound audit is adopted by the first new run.
        assert _audit(audit_id).revet_new_score == 66.0
