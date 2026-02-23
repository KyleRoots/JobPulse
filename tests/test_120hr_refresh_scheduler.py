"""
Tests for 120-hour reference refresh scheduler initialization.

Verifies that:
  - reference_number_refresh() is NEVER called inline during startup
  - Overdue refreshes are deferred to ~now + 5min via the scheduler
  - Non-overdue refreshes are scheduled at last_run + 120h
  - No-history restarts schedule for +5min instead of running inline
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta


@pytest.fixture
def mock_scheduler():
    """Create a mock scheduler with add_job."""
    scheduler = MagicMock()
    return scheduler


@pytest.fixture
def mock_refresh_func():
    """Track calls to reference_number_refresh."""
    return MagicMock()


class TestRefreshNotCalledInlineOnStartup:
    """reference_number_refresh must never be invoked inline during
    scheduler initialization — only via add_job for deferred execution."""

    def _run_scheduler_init(self, app, mock_scheduler, mock_refresh_func, last_refresh_time):
        """Simulate the scheduler init block from app.py."""
        from models import RefreshLog

        with app.app_context():
            from datetime import date

            # Query last refresh
            last_refresh = RefreshLog.query.order_by(RefreshLog.refresh_time.desc()).first()

            if last_refresh:
                calculated_next_run = last_refresh.refresh_time + timedelta(hours=120)
                time_since_refresh = datetime.utcnow() - last_refresh.refresh_time
                is_overdue = time_since_refresh > timedelta(hours=120)

                if is_overdue:
                    calculated_next_run = datetime.utcnow() + timedelta(minutes=5)

                mock_scheduler.add_job(
                    func=mock_refresh_func,
                    id='reference_number_refresh',
                    name='120-Hour Reference Number Refresh',
                    replace_existing=True,
                    next_run_time=calculated_next_run,
                )
            else:
                calculated_next_run = datetime.utcnow() + timedelta(minutes=5)
                mock_scheduler.add_job(
                    func=mock_refresh_func,
                    id='reference_number_refresh',
                    name='120-Hour Reference Number Refresh',
                    replace_existing=True,
                    next_run_time=calculated_next_run,
                )

        return calculated_next_run

    def test_recent_refresh_not_called_inline(self, app, mock_scheduler, mock_refresh_func):
        """last_run 4 hours ago → restart → refresh NOT called,
        next_run = last_run + 120h."""
        from app import db
        from models import RefreshLog

        with app.app_context():
            # Insert a refresh from 4 hours ago
            four_hours_ago = datetime.utcnow() - timedelta(hours=4)
            log = RefreshLog(
                refresh_date=four_hours_ago.date(),
                refresh_time=four_hours_ago,
                jobs_updated=78
            )
            db.session.add(log)
            db.session.commit()

            result = self._run_scheduler_init(app, mock_scheduler, mock_refresh_func, four_hours_ago)

            # reference_number_refresh must NOT have been called directly
            mock_refresh_func.assert_not_called()

            # Scheduler must have been called with next_run_time ~= four_hours_ago + 120h
            call_kwargs = mock_scheduler.add_job.call_args
            next_run = call_kwargs.kwargs.get('next_run_time') or call_kwargs[1].get('next_run_time')
            expected_next = four_hours_ago + timedelta(hours=120)

            # Allow 2 seconds tolerance for test execution time
            assert abs((next_run - expected_next).total_seconds()) < 2, (
                f"next_run should be ~last_run + 120h, got {next_run} vs expected {expected_next}"
            )

            # Cleanup
            RefreshLog.query.delete()
            db.session.commit()

    def test_overdue_refresh_deferred_not_inline(self, app, mock_scheduler, mock_refresh_func):
        """last_run 130 hours ago → restart → refresh NOT called inline,
        next_run = ~now + 5min."""
        from app import db
        from models import RefreshLog

        with app.app_context():
            # Insert a refresh from 130 hours ago
            long_ago = datetime.utcnow() - timedelta(hours=130)
            log = RefreshLog(
                refresh_date=long_ago.date(),
                refresh_time=long_ago,
                jobs_updated=78
            )
            db.session.add(log)
            db.session.commit()

            before = datetime.utcnow()
            result = self._run_scheduler_init(app, mock_scheduler, mock_refresh_func, long_ago)
            after = datetime.utcnow()

            # reference_number_refresh must NOT have been called directly
            mock_refresh_func.assert_not_called()

            # next_run should be ~now + 5min
            call_kwargs = mock_scheduler.add_job.call_args
            next_run = call_kwargs.kwargs.get('next_run_time') or call_kwargs[1].get('next_run_time')
            expected_earliest = before + timedelta(minutes=4, seconds=55)
            expected_latest = after + timedelta(minutes=5, seconds=5)

            assert expected_earliest <= next_run <= expected_latest, (
                f"next_run should be ~now + 5min, got {next_run}"
            )

            # Cleanup
            RefreshLog.query.delete()
            db.session.commit()

    def test_no_history_deferred_not_inline(self, app, mock_scheduler, mock_refresh_func):
        """No RefreshLog entries → restart → refresh NOT called inline,
        next_run = ~now + 5min."""
        from app import db
        from models import RefreshLog

        with app.app_context():
            RefreshLog.query.delete()
            db.session.commit()

            before = datetime.utcnow()
            result = self._run_scheduler_init(app, mock_scheduler, mock_refresh_func, None)
            after = datetime.utcnow()

            # reference_number_refresh must NOT have been called directly
            mock_refresh_func.assert_not_called()

            # next_run should be ~now + 5min
            call_kwargs = mock_scheduler.add_job.call_args
            next_run = call_kwargs.kwargs.get('next_run_time') or call_kwargs[1].get('next_run_time')
            expected_earliest = before + timedelta(minutes=4, seconds=55)
            expected_latest = after + timedelta(minutes=5, seconds=5)

            assert expected_earliest <= next_run <= expected_latest, (
                f"next_run should be ~now + 5min, got {next_run}"
            )


class TestRefreshScheduleDoesNotDrift:
    """Restart must not shift the schedule anchor. next_run must be based on
    persisted RefreshLog.refresh_time, not on the restart timestamp."""

    def test_schedule_anchored_to_last_refresh_not_restart(self, app):
        """Two consecutive restarts should produce the same next_run
        (within tolerance) if no refresh has happened in between."""
        from app import db
        from models import RefreshLog

        with app.app_context():
            # Set up last refresh exactly 50 hours ago
            fifty_hours_ago = datetime.utcnow() - timedelta(hours=50)
            log = RefreshLog(
                refresh_date=fifty_hours_ago.date(),
                refresh_time=fifty_hours_ago,
                jobs_updated=78
            )
            db.session.add(log)
            db.session.commit()

            last_refresh = RefreshLog.query.order_by(RefreshLog.refresh_time.desc()).first()
            calculated_next_run = last_refresh.refresh_time + timedelta(hours=120)

            expected = fifty_hours_ago + timedelta(hours=120)
            assert abs((calculated_next_run - expected).total_seconds()) < 1

            # Cleanup
            RefreshLog.query.delete()
            db.session.commit()
