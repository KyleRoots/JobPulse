"""Approved marker backfill scope and scheduler pause restoration order."""
import threading
import time
from datetime import datetime, timedelta

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING

TARGET_ENV = {
    'RAILWAY_PROJECT_ID': '1932fe81-eb34-4c38-b0bf-0a8a64089a5f',
    'RAILWAY_ENVIRONMENT_ID': '8710a448-09b0-4a45-98f4-de75c91b1203',
    'RAILWAY_SERVICE_ID': '26983a3e-5660-444e-86c2-d4d316d19843',
}
APPROVED = {
    54953: datetime(2026, 10, 26, 17, 18, 15, 842860),
    54955: datetime(2026, 10, 26, 23, 53, 22, 343897),
    54956: datetime(2026, 10, 26, 23, 54, 47, 428073),
}
EARLIER_STAMP = datetime(2026, 10, 1, 9, 0, 0)


def _set_target(monkeypatch, **overrides):
    for key, value in {**TARGET_ENV, **overrides}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


@pytest.fixture
def jvr_rows(app):
    from app import db
    from models import JobVettingRequirements

    created = []

    def make(row_id, created_at, ai='Must have 5 years', stamp=None):
        row = JobVettingRequirements(
            id=row_id,
            bullhorn_job_id=900000 + row_id,
            ai_interpreted_requirements=ai,
            created_at=created_at,
            spec_create_notified_at=stamp,
        )
        db.session.add(row)
        db.session.commit()
        created.append(row_id)
        return row

    with app.app_context():
        yield make
        db.session.rollback()
        JobVettingRequirements.query.filter(
            JobVettingRequirements.id.in_(created)
        ).delete(synchronize_session=False)
        db.session.commit()


def _stamps(ids):
    from app import db
    from models import JobVettingRequirements

    db.session.expire_all()
    return {
        r.id: r.spec_create_notified_at
        for r in JobVettingRequirements.query.filter(JobVettingRequirements.id.in_(ids))
    }


class TestApprovedSpecNotifyBackfill:
    def test_stamps_exactly_the_three_approved_rows(self, app, jvr_rows, monkeypatch):
        from app import db
        from seeding.migrations import backfill_approved_spec_create_notified_at

        _set_target(monkeypatch)
        for row_id, created_at in APPROVED.items():
            jvr_rows(row_id, created_at)
        jvr_rows(54957, datetime(2026, 10, 26, 23, 59, 0, 1))

        assert backfill_approved_spec_create_notified_at(db) == 3
        stamps = _stamps(list(APPROVED) + [54957])
        for row_id, created_at in APPROVED.items():
            assert stamps[row_id] == created_at
        assert stamps[54957] is None

        assert backfill_approved_spec_create_notified_at(db) == 0

    def test_ineligible_marked_and_mismatched_rows_untouched(self, app, jvr_rows, monkeypatch):
        from app import db
        from seeding.migrations import backfill_approved_spec_create_notified_at

        _set_target(monkeypatch)
        jvr_rows(54953, APPROVED[54953], ai=None)
        jvr_rows(54955, APPROVED[54955], stamp=EARLIER_STAMP)
        jvr_rows(54956, APPROVED[54956] + timedelta(microseconds=1))

        assert backfill_approved_spec_create_notified_at(db) == 0
        stamps = _stamps(list(APPROVED))
        assert stamps[54953] is None
        assert stamps[54955] == EARLIER_STAMP
        assert stamps[54956] is None

    @pytest.mark.parametrize('overrides', [
        {'RAILWAY_PROJECT_ID': None, 'RAILWAY_ENVIRONMENT_ID': None, 'RAILWAY_SERVICE_ID': None},
        {'RAILWAY_ENVIRONMENT_ID': '00000000-0000-0000-0000-000000000000'},
        {'RAILWAY_SERVICE_ID': None},
        {'RAILWAY_PROJECT_ID': ''},
    ])
    def test_other_environment_untouched(self, app, jvr_rows, monkeypatch, overrides):
        from app import db
        from seeding.migrations import backfill_approved_spec_create_notified_at

        _set_target(monkeypatch, **overrides)
        for row_id, created_at in APPROVED.items():
            jvr_rows(row_id, created_at)

        assert backfill_approved_spec_create_notified_at(db) == 0
        assert all(v is None for v in _stamps(list(APPROVED)).values())


@pytest.fixture
def sched(app, monkeypatch):
    import app as app_module
    from app import db
    from models import GlobalSettings

    test_scheduler = BackgroundScheduler(
        timezone='UTC',
        job_defaults={'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 30},
    )
    monkeypatch.setattr(app_module, 'scheduler', test_scheduler)
    monkeypatch.setattr(app_module, '_register_scheduler_listeners', lambda: None)
    monkeypatch.setattr(app_module, '_scheduler_pause_restore_failed', False)

    def set_paused(value):
        with app.app_context():
            GlobalSettings.query.filter_by(setting_key='scheduler_paused_jobs').delete()
            if value is not None:
                db.session.add(GlobalSettings(setting_key='scheduler_paused_jobs', setting_value=value))
            db.session.commit()

    yield test_scheduler, set_paused
    if test_scheduler.running:
        test_scheduler.shutdown(wait=False)
    set_paused(None)


def _add_due_jobs(test_scheduler):
    ran = {'paused_job': threading.Event(), 'normal_job': threading.Event()}
    now = datetime.utcnow()
    for job_id, event in ran.items():
        test_scheduler.add_job(func=event.set, trigger='interval', minutes=5,
                               id=job_id, next_run_time=now)
    return ran


class TestSchedulerPauseRestoreOrder:
    def test_due_job_cannot_run_before_restore(self, sched, monkeypatch):
        import app as app_module
        test_scheduler, set_paused = sched
        set_paused('["paused_job"]')
        ran = _add_due_jobs(test_scheduler)

        real_read = app_module._read_paused_job_ids
        ran_during_restore = {}

        def slow_read():
            time.sleep(0.5)
            ran_during_restore.update({k: e.is_set() for k, e in ran.items()})
            return real_read()

        monkeypatch.setattr(app_module, '_read_paused_job_ids', slow_read)

        assert app_module._start_scheduler_with_restored_pauses() is True
        assert ran_during_restore == {'paused_job': False, 'normal_job': False}
        assert ran['normal_job'].wait(2)
        time.sleep(0.5)
        assert not ran['paused_job'].is_set()
        assert test_scheduler.get_job('paused_job').next_run_time is None
        assert test_scheduler.state == STATE_RUNNING

    def test_control_start_then_pause_lets_due_job_run(self, sched):
        test_scheduler, _ = sched
        ran = _add_due_jobs(test_scheduler)
        test_scheduler.start()
        time.sleep(0.5)
        test_scheduler.pause_job('paused_job')
        assert ran['paused_job'].is_set()

    @pytest.mark.parametrize('stored', ['not json', '{"paused_job": true}', '[1]', ''])
    def test_undecodable_state_fails_closed(self, sched, stored):
        import app as app_module
        test_scheduler, set_paused = sched
        set_paused(stored)
        ran = _add_due_jobs(test_scheduler)

        assert app_module._start_scheduler_with_restored_pauses() is False
        time.sleep(0.5)
        assert not any(e.is_set() for e in ran.values())
        assert test_scheduler.state == STATE_PAUSED
        assert app_module.lazy_start_scheduler() is False
        assert app_module.scheduled_job_paused('normal_job') is True

    def test_unreadable_state_fails_closed(self, sched, monkeypatch):
        import app as app_module
        from models import GlobalSettings
        test_scheduler, _ = sched
        ran = _add_due_jobs(test_scheduler)

        def boom(*_a, **_k):
            raise RuntimeError('database unavailable')

        monkeypatch.setattr(GlobalSettings, 'get_value', classmethod(boom))
        assert app_module._start_scheduler_with_restored_pauses() is False
        time.sleep(0.5)
        assert not any(e.is_set() for e in ran.values())
        assert test_scheduler.state == STATE_PAUSED

    def test_pause_error_fails_closed(self, sched, monkeypatch):
        import app as app_module
        test_scheduler, set_paused = sched
        set_paused('["paused_job"]')
        ran = _add_due_jobs(test_scheduler)

        def broken_pause(job_id):
            raise RuntimeError('jobstore error')

        monkeypatch.setattr(test_scheduler, 'pause_job', broken_pause)
        assert app_module._start_scheduler_with_restored_pauses() is False
        time.sleep(0.5)
        assert not any(e.is_set() for e in ran.values())

    def test_normal_unpaused_startup(self, sched):
        import app as app_module
        test_scheduler, set_paused = sched
        set_paused(None)
        ran = _add_due_jobs(test_scheduler)

        assert app_module.lazy_start_scheduler() is True
        assert ran['paused_job'].wait(2) and ran['normal_job'].wait(2)
        assert test_scheduler.state == STATE_RUNNING
        assert app_module.lazy_start_scheduler() is True

    def test_pause_for_job_not_on_this_worker_is_not_an_error(self, sched):
        import app as app_module
        test_scheduler, set_paused = sched
        set_paused('["process_bullhorn_monitors"]')
        ran = _add_due_jobs(test_scheduler)

        assert app_module._start_scheduler_with_restored_pauses() is True
        assert ran['normal_job'].wait(2)
        assert app_module.scheduled_job_paused('process_bullhorn_monitors') is True
        assert app_module.scheduled_job_paused('normal_job') is False


class TestManualMonitorKick:
    def _forbid_cycle(self, monkeypatch):
        import incremental_monitoring_service

        class Forbidden:
            def __init__(self, *a, **k):
                raise AssertionError('monitoring cycle must not start')

        monkeypatch.setattr(incremental_monitoring_service, 'IncrementalMonitoringService', Forbidden)

    def test_skipped_when_persisted_paused(self, sched, monkeypatch):
        import app as app_module
        _, set_paused = sched
        set_paused('["process_bullhorn_monitors"]')
        monkeypatch.delenv('XML_FEED_FRZ', raising=False)
        self._forbid_cycle(monkeypatch)
        with app_module.app.app_context():
            app_module.process_bullhorn_monitors()

    def test_skipped_when_restore_failed(self, sched, monkeypatch):
        import app as app_module
        _, set_paused = sched
        set_paused(None)
        monkeypatch.setattr(app_module, '_scheduler_pause_restore_failed', True)
        monkeypatch.delenv('XML_FEED_FRZ', raising=False)
        self._forbid_cycle(monkeypatch)
        with app_module.app.app_context():
            app_module.process_bullhorn_monitors()
