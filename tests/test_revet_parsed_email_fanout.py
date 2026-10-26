"""Audit re-vet must re-queue one analysis, not one per old ParsedEmail.

``clear_candidate_vetting_state`` used to reset ``vetted_at`` on every
completed ParsedEmail for the candidate. The ParsedEmail detector then picked
them up one per cycle (the cycle dedupes by candidate and marks only the
processed email), so a candidate with N past applications got N full
multi-job analyses. Oct 2026: one candidate reset 7 emails and was screened
8 times in 25 minutes.

The pending-revet detector also called the reset every cycle while an audit
row waited for its score, re-queuing emails behind the analysis it had just
enqueued and deleting the vetting log of a run still in flight.

Everything here runs against the disposable SQLite test DB with Bullhorn and
the analysis itself stubbed out.
"""

from datetime import datetime, timedelta

import pytest

CANDIDATE = 4700001
AUDITED_JOB = 35903
OTHER_JOB = 35904
TEARSHEET_JOBS = (AUDITED_JOB, OTHER_JOB, 35905)


class _FakeBullhorn:
    def authenticate(self):
        return True


@pytest.fixture
def detector():
    from screening.detection import CandidateDetectionMixin

    class Detector(CandidateDetectionMixin):
        def _get_bullhorn_service(self):
            return _FakeBullhorn()

        def _fetch_candidate_details(self, bullhorn, candidate_id):
            return {'id': candidate_id, 'firstName': 'Synthetic', 'lastName': 'Candidate'}

    return Detector()


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
        CandidateJobMatch, CandidateVettingLog, ParsedEmail,
        VettingAuditLog, VettingConfig,
    )

    def wipe():
        with app.app_context():
            CandidateJobMatch.query.delete()
            CandidateVettingLog.query.delete()
            VettingAuditLog.query.delete()
            ParsedEmail.query.delete()
            VettingConfig.query.filter_by(setting_key='vetting_cutoff_date').delete()
            db.session.commit()

    wipe()
    yield
    wipe()


def _now():
    return datetime.utcnow()


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


def _seed_vlog(*, created_at, status='completed', parsed_email_id=None,
               scores=None, candidate_id=CANDIDATE):
    from app import db
    from models import CandidateVettingLog, CandidateJobMatch

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
    )
    db.session.add(vlog)
    db.session.flush()
    for job_id, score in scores.items():
        db.session.add(CandidateJobMatch(
            vetting_log_id=vlog.id,
            bullhorn_job_id=job_id,
            job_title='Synthetic Job',
            match_score=score,
            is_qualified=False,
            is_applied_job=(job_id == AUDITED_JOB),
            match_summary='synthetic',
            gaps_identified='',
        ))
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


def _set_cutoff(value: datetime):
    from models import VettingConfig
    VettingConfig.set_value('vetting_cutoff_date', value.strftime('%Y-%m-%d %H:%M:%S'))


def _unvetted_ids(candidate_id=CANDIDATE):
    from models import ParsedEmail
    return sorted(
        pe.id for pe in ParsedEmail.query.filter_by(bullhorn_candidate_id=candidate_id)
        if pe.vetted_at is None
    )


class _Analyzer:
    """Stands in for process_candidate: one completed run scoring every job."""

    def __init__(self, scores=None, fail=False):
        self.calls = []
        self.scores = scores or {job: 66.0 for job in TEARSHEET_JOBS}
        self.fail = fail

    def __call__(self, cand):
        from models import CandidateVettingLog
        self.calls.append(cand.get('_parsed_email_id') or f"revet:{cand.get('_pending_revet_audit_id')}")
        vlog_id = _seed_vlog(
            created_at=_now(),
            status='failed' if self.fail else 'completed',
            parsed_email_id=cand.get('_parsed_email_id'),
            scores={} if self.fail else self.scores,
            candidate_id=cand['id'],
        )
        return CandidateVettingLog.query.get(vlog_id)


def _run_cycle(detector, analyze):
    """Same detection → dedupe → analyse → back-fill → mark order as
    ``run_vetting_cycle`` (Bullhorn-search fallbacks stubbed out)."""
    from vetting_audit_service import backfill_revet_new_score

    candidates = detector.detect_unvetted_applications(limit=25)
    for cand in detector.detect_pending_revet_candidates():
        if cand['id'] not in {c['id'] for c in candidates}:
            candidates.append(cand)
    seen, unique = set(), []
    for cand in candidates:
        if cand['id'] not in seen:
            seen.add(cand['id'])
            unique.append(cand)
    for cand in unique:
        vlog = analyze(cand)
        ok = vlog is not None and vlog.status == 'completed'
        if ok:
            backfill_revet_new_score(cand['id'], vetting_log=vlog)
        if cand.get('_parsed_email_id'):
            detector._mark_application_vetted(cand['_parsed_email_id'], success=ok)
    return unique


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


class TestSingleEmailReset:
    def test_seven_vetted_emails_reset_to_one_analysis_and_no_repeat(self, app, detector, auditor):
        from models import VettingAuditLog
        with app.app_context():
            ids = _seed_seven_vetted_emails()
            _seed_vlog(created_at=_now() - timedelta(hours=2), scores={AUDITED_JOB: 20.0})
            audit_id = _seed_audit(age_hours=0.0)

            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)
            # Newest email for the audited job (index 6 is AUDITED_JOB).
            assert _unvetted_ids() == [ids[6]]

            analyze = _Analyzer()
            for _ in range(8):
                _run_cycle(detector, analyze)

            assert analyze.calls == [ids[6]]
            assert _unvetted_ids() == []
            assert VettingAuditLog.query.get(audit_id).revet_new_score == 66.0

    def test_legacy_reset_fanned_out_one_analysis_per_email(self, app, detector):
        """Negative regression: the old reset produced 7 analyses."""
        with app.app_context():
            _seed_seven_vetted_emails()
            _legacy_clear(CANDIDATE)
            analyze = _Analyzer()
            for _ in range(8):
                _run_cycle(detector, analyze)
            assert len(analyze.calls) == 7

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
            null_id = _seed_email(received_at=None)
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
            stats = clear_candidate_vetting_state(CANDIDATE)
            assert stats['parsed_emails_reset'] == 0


class TestCutoffBoundary:
    def test_before_exact_after_and_null(self, app, detector):
        from vetting_audit_service import select_revet_parsed_email, clear_candidate_vetting_state
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
            picked = [c['_parsed_email_id'] for c in detector.detect_unvetted_applications()]
            assert picked == [after]

    def test_exact_boundary_only_is_eligible(self, app):
        from vetting_audit_service import select_revet_parsed_email
        with app.app_context():
            cutoff = (_now() - timedelta(days=5)).replace(microsecond=0)
            _set_cutoff(cutoff)
            exact = _seed_email(received_at=cutoff)
            assert select_revet_parsed_email(CANDIDATE)[0].id == exact

    def test_tied_timestamps_after_cutoff_pick_highest_id(self, app):
        from vetting_audit_service import select_revet_parsed_email
        with app.app_context():
            cutoff = (_now() - timedelta(days=5)).replace(microsecond=0)
            _set_cutoff(cutoff)
            t = cutoff + timedelta(days=1)
            _seed_email(received_at=t)
            second = _seed_email(received_at=t)
            assert select_revet_parsed_email(CANDIDATE)[0].id == second

    def test_all_emails_before_cutoff_falls_back_to_pending_revet(self, app, detector, auditor):
        from models import VettingAuditLog
        with app.app_context():
            cutoff = (_now() - timedelta(days=5)).replace(microsecond=0)
            _set_cutoff(cutoff)
            _seed_email(received_at=cutoff - timedelta(days=1))
            _seed_email(received_at=None)
            audit_id = _seed_audit()
            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)
            assert _unvetted_ids() == []

            analyze = _Analyzer()
            for _ in range(3):
                _run_cycle(detector, analyze)
            assert analyze.calls == [f'revet:{audit_id}']
            assert VettingAuditLog.query.get(audit_id).revet_new_score == 66.0


class TestNonEmailRecovery:
    def test_candidate_without_emails_is_enqueued_once_and_backfilled(self, app, detector, auditor):
        from models import VettingAuditLog
        with app.app_context():
            _seed_vlog(created_at=_now() - timedelta(hours=3), scores={AUDITED_JOB: 20.0})
            audit_id = _seed_audit()
            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)

            analyze = _Analyzer()
            for _ in range(4):
                _run_cycle(detector, analyze)
            assert analyze.calls == [f'revet:{audit_id}']
            assert VettingAuditLog.query.get(audit_id).revet_new_score == 66.0


class TestLegitimateNewWork:
    def test_fresh_application_at_reset_time_is_the_only_analysis(self, app, detector, auditor):
        with app.app_context():
            ids = _seed_seven_vetted_emails()
            fresh = _seed_email(received_at=_now(), job_id=OTHER_JOB, vetted=False)
            _seed_audit()
            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)
            assert _unvetted_ids() == [fresh]

            analyze = _Analyzer()
            for _ in range(4):
                _run_cycle(detector, analyze)
            assert analyze.calls == [fresh]
            assert set(ids).isdisjoint(analyze.calls)

    def test_fresh_application_during_analysis_is_still_processed(self, app, detector, auditor):
        with app.app_context():
            ids = _seed_seven_vetted_emails()
            _seed_audit()
            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)

            fresh = {}

            class AnalyzerWithArrival(_Analyzer):
                def __call__(self, cand):
                    result = super().__call__(cand)
                    if 'id' not in fresh:
                        fresh['id'] = _seed_email(received_at=_now(), job_id=OTHER_JOB, vetted=False)
                    return result

            analyze = AnalyzerWithArrival()
            for _ in range(4):
                _run_cycle(detector, analyze)
            assert analyze.calls == [ids[6], fresh['id']]

    def test_separate_later_audit_runs_again(self, app, detector, auditor):
        from app import db
        from models import VettingAuditLog
        with app.app_context():
            ids = _seed_seven_vetted_emails()
            first = _seed_audit(age_hours=0.0)
            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)
            analyze = _Analyzer()
            for _ in range(3):
                _run_cycle(detector, analyze)
            assert analyze.calls == [ids[6]]

            later = _seed_audit(job_id=OTHER_JOB, age_hours=0.0)
            VettingAuditLog.query.get(later).created_at = _now()
            db.session.commit()
            auditor._trigger_revet(CANDIDATE, 2, OTHER_JOB)
            # Newest OTHER_JOB email (index 5).
            assert _unvetted_ids() == [ids[5]]
            for _ in range(3):
                _run_cycle(detector, analyze)
            assert analyze.calls == [ids[6], ids[5]]
            assert VettingAuditLog.query.get(first).revet_new_score == 66.0
            assert VettingAuditLog.query.get(later).revet_new_score == 66.0


class TestPendingRevetGuards:
    def test_two_audits_for_distinct_jobs_share_one_analysis(self, app, detector, auditor):
        from models import VettingAuditLog
        with app.app_context():
            ids = _seed_seven_vetted_emails()
            a = _seed_audit(job_id=AUDITED_JOB)
            b = _seed_audit(job_id=OTHER_JOB)
            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)
            auditor._trigger_revet(CANDIDATE, 1, OTHER_JOB)
            assert _unvetted_ids() == [ids[6]]

            analyze = _Analyzer()
            for _ in range(4):
                _run_cycle(detector, analyze)
            assert analyze.calls == [ids[6]]
            assert VettingAuditLog.query.get(a).revet_new_score == 66.0
            assert VettingAuditLog.query.get(b).revet_new_score == 66.0

    def test_two_audits_without_emails_enqueue_once_per_cycle(self, app, detector):
        from models import VettingAuditLog
        with app.app_context():
            a = _seed_audit(job_id=AUDITED_JOB, age_hours=2.0)
            b = _seed_audit(job_id=OTHER_JOB, age_hours=1.0)
            analyze = _Analyzer()
            for _ in range(3):
                _run_cycle(detector, analyze)
            assert analyze.calls == [f'revet:{a}']
            assert VettingAuditLog.query.get(a).revet_new_score == 66.0
            assert VettingAuditLog.query.get(b).revet_new_score == 66.0

    def test_detector_does_not_requeue_emails_or_duplicate_queued_email(self, app, detector, auditor):
        with app.app_context():
            ids = _seed_seven_vetted_emails()
            _seed_audit()
            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)
            assert detector.detect_pending_revet_candidates() == []
            assert _unvetted_ids() == [ids[6]]

    def test_missing_job_after_completed_run_terminates(self, app, detector):
        from models import VettingAuditLog
        with app.app_context():
            _seed_seven_vetted_emails()
            audit_id = _seed_audit(age_hours=2.0)
            _seed_vlog(created_at=_now() - timedelta(minutes=30), scores={OTHER_JOB: 40.0})
            assert detector.detect_pending_revet_candidates() == []
            row = VettingAuditLog.query.get(audit_id)
            assert row.action_taken == 'revet_skipped_job_mismatch'
            assert _unvetted_ids() == []

    def test_twelve_hour_cap_terminates_even_with_queued_email(self, app, detector):
        from models import VettingAuditLog
        with app.app_context():
            _seed_email(received_at=_now(), vetted=False)
            audit_id = _seed_audit(age_hours=13.0)
            assert detector.detect_pending_revet_candidates(max_attempt_hours=12.0) == []
            assert VettingAuditLog.query.get(audit_id).action_taken == 'revet_skipped_job_mismatch'

    def test_failed_run_retries_one_analysis_per_cycle(self, app, detector, auditor):
        from models import VettingAuditLog
        with app.app_context():
            ids = _seed_seven_vetted_emails()
            audit_id = _seed_audit()
            auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB)

            failing = _Analyzer(fail=True)
            _run_cycle(detector, failing)
            assert failing.calls == [ids[6]]
            assert _unvetted_ids() == []

            _run_cycle(detector, failing)
            assert failing.calls == [ids[6], f'revet:{audit_id}']
            assert _unvetted_ids() == []

            ok = _Analyzer()
            _run_cycle(detector, ok)
            _run_cycle(detector, ok)
            assert ok.calls == [f'revet:{audit_id}']
            assert VettingAuditLog.query.get(audit_id).revet_new_score == 66.0


class TestInflightAndRepeatedResets:
    def test_reset_is_deferred_while_a_run_is_in_flight(self, app, auditor):
        from models import CandidateVettingLog
        from vetting_audit_service import clear_candidate_vetting_state, RevetResetDeferred
        with app.app_context():
            ids = _seed_seven_vetted_emails()
            inflight = _seed_vlog(created_at=_now() - timedelta(minutes=2), status='processing',
                                  parsed_email_id=ids[6])
            with pytest.raises(RevetResetDeferred):
                clear_candidate_vetting_state(CANDIDATE)
            assert CandidateVettingLog.query.get(inflight) is not None

            _seed_audit()
            assert auditor._trigger_revet(CANDIDATE, 1, AUDITED_JOB) is None
            assert CandidateVettingLog.query.get(inflight) is not None
            assert _unvetted_ids() == []

    def test_detector_waits_for_inflight_run_then_backfills(self, app, detector):
        from app import db
        from models import CandidateVettingLog, CandidateJobMatch, VettingAuditLog
        with app.app_context():
            audit_id = _seed_audit(age_hours=1.0)
            inflight = _seed_vlog(created_at=_now() - timedelta(minutes=1), status='processing')
            assert detector.detect_pending_revet_candidates() == []
            assert CandidateVettingLog.query.get(inflight) is not None

            vlog = CandidateVettingLog.query.get(inflight)
            vlog.status = 'completed'
            db.session.add(CandidateJobMatch(
                vetting_log_id=inflight, bullhorn_job_id=AUDITED_JOB, job_title='Synthetic Job',
                match_score=71.0, is_qualified=False, is_applied_job=True,
                match_summary='synthetic', gaps_identified='',
            ))
            db.session.commit()

            from vetting_audit_service import backfill_revet_new_score
            backfill_revet_new_score(CANDIDATE, vetting_log=vlog)
            assert detector.detect_pending_revet_candidates() == []
            assert VettingAuditLog.query.get(audit_id).revet_new_score == 71.0

    def test_stale_processing_log_does_not_block_recovery(self, app):
        from vetting_audit_service import clear_candidate_vetting_state
        with app.app_context():
            _seed_vlog(created_at=_now() - timedelta(minutes=30), status='processing')
            stats = clear_candidate_vetting_state(CANDIDATE)
            assert stats['vetting_logs_deleted'] == 1

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
