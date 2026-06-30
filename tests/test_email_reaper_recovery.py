"""
Tests for the stuck-'processing' reaper (email_parsing_timeout_cleanup).

Flag OFF (default): stuck rows are auto-failed — current behavior unchanged.
Flag ON:  rows are superseded and re-driven via a windowed mailbox backfill.
  - Poison cap:   messages that keep crashing are failed after max_retries.
  - Marker busy:  cycle deferred when a manual outage-recovery is in flight.
  - No message_id: cannot auto-recover; failed visibly for manual review.

Notes on test isolation:
  email_parsing_timeout_cleanup() runs inside its own app.app_context() and
  commits its own changes, so data it writes persists in the SQLite test DB.
  Each test queries by the specific row IDs it created to avoid coupling.
  ParsedEmail rows created here are cleaned up in _db_cleanup via teardown.
"""
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stuck(db, ParsedEmail, *, message_id='<msg-default@test.com>',
           minutes_ago=20, subject='Test Application'):
    """Insert a 'processing' ParsedEmail stuck for `minutes_ago` minutes."""
    pe = ParsedEmail(
        message_id=message_id,
        sender_email='board@dice.com',
        recipient_email='apply@myticas.com',
        subject=subject,
        status='processing',
    )
    db.session.add(pe)
    db.session.commit()
    past = datetime.utcnow() - timedelta(minutes=minutes_ago)
    pe.created_at = past
    pe.received_at = past
    db.session.commit()
    return pe


def _breadcrumb(db, ParsedEmail, recovery_message_id, *, minutes_ago=30):
    """Insert a 'recovery_superseded' breadcrumb for poison-guard counting."""
    crumb = ParsedEmail(
        message_id=None,
        recovery_message_id=recovery_message_id,
        sender_email='board@dice.com',
        recipient_email='apply@myticas.com',
        subject='Test Application (crumb)',
        status='recovery_superseded',
    )
    db.session.add(crumb)
    db.session.commit()
    past = datetime.utcnow() - timedelta(minutes=minutes_ago)
    crumb.created_at = past
    db.session.commit()
    return crumb


def _set(app, db, key, value):
    with app.app_context():
        from models import VettingConfig
        VettingConfig.set_value(key, value)
        db.session.commit()


# ---------------------------------------------------------------------------
# Flag OFF — current fail-only behavior unchanged
# ---------------------------------------------------------------------------

class TestReaperFlagOff:
    def test_fail_only_when_auto_recovery_off(self, app):
        """Default behavior (flag off): stuck rows are auto-failed, not re-driven."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'false')
            db.session.commit()
            stuck = _stuck(db, ParsedEmail,
                           message_id='<fail-only-1@test.com>', minutes_ago=20)
            stuck_id = stuck.id

        from tasks.cleanup import email_parsing_timeout_cleanup
        email_parsing_timeout_cleanup()

        with app.app_context():
            row = ParsedEmail.query.get(stuck_id)
            assert row.status == 'failed'
            assert row.recovery_message_id is None   # never superseded
            assert row.processed_at is not None

    def test_recent_row_not_reaped(self, app):
        """Row stuck < timeout window is left alone regardless of flag."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'false')
            VettingConfig.set_value('email_processing_recovery_timeout_minutes', '10')
            db.session.commit()
            # Only 5 minutes old — inside the 10-minute window
            stuck = _stuck(db, ParsedEmail,
                           message_id='<recent-row@test.com>', minutes_ago=5)
            stuck_id = stuck.id

        from tasks.cleanup import email_parsing_timeout_cleanup
        email_parsing_timeout_cleanup()

        with app.app_context():
            row = ParsedEmail.query.get(stuck_id)
            assert row.status == 'processing'   # untouched

    def test_no_stuck_rows_returns_early(self, app):
        """No stuck rows → function returns cleanly with no DB writes."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'false')
            db.session.commit()
            initial_count = ParsedEmail.query.filter_by(
                status='failed').count()

        from tasks.cleanup import email_parsing_timeout_cleanup
        email_parsing_timeout_cleanup()

        with app.app_context():
            final_count = ParsedEmail.query.filter_by(
                status='failed').count()
            # No new failures added (no stuck rows existed)
            assert final_count == initial_count


# ---------------------------------------------------------------------------
# Flag ON — auto-recovery path
# ---------------------------------------------------------------------------

_DUMMY_OK = {'fetched': 1, 'processed': 1, 'failed': 0}


class TestReaperFlagOn:
    def test_recover_when_on(self, app):
        """Flag on + message_id present → row superseded, backfill triggered."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'true')
            db.session.commit()
            stuck = _stuck(db, ParsedEmail,
                           message_id='<recover-1@test.com>', minutes_ago=20)
            stuck_id = stuck.id
            orig_mid = stuck.message_id

        with patch('routes.email._acquire_recovery_marker', return_value=True), \
             patch('routes.email._release_recovery_marker'), \
             patch('tasks.run_mailbox_backfill', return_value=_DUMMY_OK):
            from tasks.cleanup import email_parsing_timeout_cleanup
            email_parsing_timeout_cleanup()

        with app.app_context():
            row = ParsedEmail.query.get(stuck_id)
            assert row.status == 'recovery_superseded'
            assert row.message_id is None          # cleared so backfill re-fetches it
            assert row.recovery_message_id == orig_mid   # stable poison-guard key

    def test_no_message_id_fails_visibly(self, app):
        """Stuck row with no message_id → failed with admin-visible note."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'true')
            db.session.commit()
            stuck = _stuck(db, ParsedEmail, message_id=None, minutes_ago=20)
            stuck_id = stuck.id

        with patch('routes.email._acquire_recovery_marker', return_value=True), \
             patch('routes.email._release_recovery_marker'), \
             patch('tasks.run_mailbox_backfill', return_value={'fetched': 0}):
            from tasks.cleanup import email_parsing_timeout_cleanup
            email_parsing_timeout_cleanup()

        with app.app_context():
            row = ParsedEmail.query.get(stuck_id)
            assert row.status == 'failed'
            assert 'message_id' in (row.processing_notes or '').lower()

    def test_backfill_called_once(self, app):
        """One backfill call per reaper cycle regardless of how many stuck rows."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'true')
            db.session.commit()
            _stuck(db, ParsedEmail, message_id='<batch-a@test.com>', minutes_ago=20)
            _stuck(db, ParsedEmail, message_id='<batch-b@test.com>', minutes_ago=25)

        with patch('routes.email._acquire_recovery_marker', return_value=True), \
             patch('routes.email._release_recovery_marker'), \
             patch('tasks.run_mailbox_backfill', return_value=_DUMMY_OK) as mock_bf:
            from tasks.cleanup import email_parsing_timeout_cleanup
            email_parsing_timeout_cleanup()

        assert mock_bf.call_count == 1

    def test_retry_count_incremented(self, app):
        """vetting_retry_count increments on each re-drive."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'true')
            db.session.commit()
            stuck = _stuck(db, ParsedEmail,
                           message_id='<retry-cnt@test.com>', minutes_ago=20)
            stuck_id = stuck.id

        with patch('routes.email._acquire_recovery_marker', return_value=True), \
             patch('routes.email._release_recovery_marker'), \
             patch('tasks.run_mailbox_backfill', return_value=_DUMMY_OK):
            from tasks.cleanup import email_parsing_timeout_cleanup
            email_parsing_timeout_cleanup()

        with app.app_context():
            row = ParsedEmail.query.get(stuck_id)
            assert (row.vetting_retry_count or 0) >= 1


# ---------------------------------------------------------------------------
# Poison cap
# ---------------------------------------------------------------------------

class TestReaperPoisonCap:
    def test_poison_exhausted_fails_row(self, app):
        """Row with >= max_retries breadcrumbs → failed (not re-driven again)."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'true')
            VettingConfig.set_value('email_processing_recovery_max_retries', '2')
            db.session.commit()
            stuck = _stuck(db, ParsedEmail,
                           message_id='<poison-1@test.com>', minutes_ago=20)
            stuck_id = stuck.id
            # Two breadcrumbs == max_retries → exhausted.
            # Use minutes_ago well inside the poison window (which is
            # max(60, timeout*4)=60min) to avoid a cutoff boundary race.
            _breadcrumb(db, ParsedEmail, '<poison-1@test.com>', minutes_ago=30)
            _breadcrumb(db, ParsedEmail, '<poison-1@test.com>', minutes_ago=20)

        with patch('routes.email._acquire_recovery_marker', return_value=True), \
             patch('routes.email._release_recovery_marker'), \
             patch('tasks.run_mailbox_backfill', return_value={'fetched': 0}) as mock_bf:
            from tasks.cleanup import email_parsing_timeout_cleanup
            email_parsing_timeout_cleanup()

        with app.app_context():
            row = ParsedEmail.query.get(stuck_id)
            assert row.status == 'failed'
            notes = (row.processing_notes or '').lower()
            assert any(kw in notes for kw in ('poison', 'gave up', 'manual', 'attempt'))

        # Poison-exhausted row has no recoverable siblings → backfill not called
        mock_bf.assert_not_called()

    def test_poison_exhausted_message_id_preserved(self, app):
        """Exhausted row keeps its message_id so future backfills dedupe it."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'true')
            VettingConfig.set_value('email_processing_recovery_max_retries', '2')
            db.session.commit()
            stuck = _stuck(db, ParsedEmail,
                           message_id='<poison-keep-mid@test.com>', minutes_ago=20)
            stuck_id = stuck.id
            _breadcrumb(db, ParsedEmail, '<poison-keep-mid@test.com>', minutes_ago=60)
            _breadcrumb(db, ParsedEmail, '<poison-keep-mid@test.com>', minutes_ago=40)

        with patch('routes.email._acquire_recovery_marker', return_value=True), \
             patch('routes.email._release_recovery_marker'), \
             patch('tasks.run_mailbox_backfill', return_value={'fetched': 0}):
            from tasks.cleanup import email_parsing_timeout_cleanup
            email_parsing_timeout_cleanup()

        with app.app_context():
            row = ParsedEmail.query.get(stuck_id)
            # message_id must be preserved on exhausted rows (crash-loop stop)
            assert row.message_id == '<poison-keep-mid@test.com>'

    def test_one_breadcrumb_still_recovers(self, app):
        """Row with < max_retries breadcrumbs → superseded (not failed)."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'true')
            VettingConfig.set_value('email_processing_recovery_max_retries', '2')
            db.session.commit()
            stuck = _stuck(db, ParsedEmail,
                           message_id='<partial-poison@test.com>', minutes_ago=20)
            stuck_id = stuck.id
            # Only 1 breadcrumb — below max_retries=2 → should still re-drive
            _breadcrumb(db, ParsedEmail, '<partial-poison@test.com>', minutes_ago=60)

        with patch('routes.email._acquire_recovery_marker', return_value=True), \
             patch('routes.email._release_recovery_marker'), \
             patch('tasks.run_mailbox_backfill', return_value=_DUMMY_OK):
            from tasks.cleanup import email_parsing_timeout_cleanup
            email_parsing_timeout_cleanup()

        with app.app_context():
            row = ParsedEmail.query.get(stuck_id)
            assert row.status == 'recovery_superseded'


# ---------------------------------------------------------------------------
# Marker busy — cross-worker coordination
# ---------------------------------------------------------------------------

class TestReaperBackfillException:
    def test_backfill_exception_restores_rows(self, app):
        """If run_mailbox_backfill() RAISES, the superseded rows are restored
        to 'processing' so they are retried next cycle — no stranded applicants."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'true')
            db.session.commit()
            stuck = _stuck(db, ParsedEmail,
                           message_id='<exc-restore@test.com>', minutes_ago=20)
            stuck_id = stuck.id
            orig_mid = stuck.message_id

        with patch('routes.email._acquire_recovery_marker', return_value=True), \
             patch('routes.email._release_recovery_marker'), \
             patch('tasks.run_mailbox_backfill',
                   side_effect=RuntimeError("Graph API timeout")):
            from tasks.cleanup import email_parsing_timeout_cleanup
            email_parsing_timeout_cleanup()

        with app.app_context():
            row = ParsedEmail.query.get(stuck_id)
            # Backfill raised → reconciliation ran → row restored to 'processing'
            assert row.status == 'processing'
            # message_id restored so next backfill can re-fetch the email
            assert row.message_id == orig_mid

    def test_backfill_exception_does_not_leave_null_message_id(self, app):
        """After a raised backfill the row must NOT be left with message_id=NULL
        (that would make future deduplication impossible)."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'true')
            db.session.commit()
            stuck = _stuck(db, ParsedEmail,
                           message_id='<exc-null-guard@test.com>', minutes_ago=20)
            stuck_id = stuck.id

        with patch('routes.email._acquire_recovery_marker', return_value=True), \
             patch('routes.email._release_recovery_marker'), \
             patch('tasks.run_mailbox_backfill',
                   side_effect=ConnectionError("SFTP unreachable")):
            from tasks.cleanup import email_parsing_timeout_cleanup
            email_parsing_timeout_cleanup()

        with app.app_context():
            row = ParsedEmail.query.get(stuck_id)
            assert row.message_id is not None


class TestReaperSuccessorRearming:
    def test_failed_successor_is_rearmed(self, app):
        """A successor row that holds the original message_id but never reached
        Bullhorn is re-armed to 'processing' so the reaper re-drives it next cycle.

        Sequence simulated here mirrors prod:
          1. Stuck row is superseded (message_id cleared, committed).
          2. Backfill re-fetches the email and creates a successor row that
             holds the original message_id — but the successor fails mid-pipeline
             (status='failed', bullhorn_candidate_id=None).
          3. Reconciliation sees a successor that did NOT reach Bullhorn →
             re-arms it to 'processing' for the reaper to retry next cycle.

        The UNIQUE constraint on message_id prevents inserting both rows
        simultaneously, so the successor is created inside the backfill mock's
        side_effect — exactly when it would be created in prod.
        """
        from app import db
        from models import ParsedEmail, VettingConfig

        MID = '<rearm-test@test.com>'
        successor_ids = []

        def _backfill_creates_failed_successor(flask_app, since_iso, limit):
            """Simulate a backfill that re-fetches the email but the successor
            fails before reaching Bullhorn."""
            with flask_app.app_context():
                from extensions import db as _db
                succ = ParsedEmail(
                    message_id=MID,
                    sender_email='board@dice.com',
                    recipient_email='apply@myticas.com',
                    subject='Test Application (successor)',
                    status='failed',
                    bullhorn_candidate_id=None,
                )
                _db.session.add(succ)
                _db.session.commit()
                successor_ids.append(succ.id)
            return {'fetched': 1, 'processed': 0, 'failed': 1}

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'true')
            db.session.commit()
            stuck = _stuck(db, ParsedEmail, message_id=MID, minutes_ago=20)
            stuck_id = stuck.id

        with patch('routes.email._acquire_recovery_marker', return_value=True), \
             patch('routes.email._release_recovery_marker'), \
             patch('tasks.run_mailbox_backfill',
                   side_effect=_backfill_creates_failed_successor):
            from tasks.cleanup import email_parsing_timeout_cleanup
            email_parsing_timeout_cleanup()

        assert successor_ids, "Mock backfill was not called — test setup error"
        with app.app_context():
            successor_row = ParsedEmail.query.get(successor_ids[0])
            # Successor held the message_id but never reached Bullhorn →
            # reconciliation re-armed it to 'processing' for the next cycle
            assert successor_row.status == 'processing'


class TestReaperMarkerBusy:
    def test_marker_busy_skips_cycle(self, app):
        """Marker held by another worker → reaper defers; stuck rows untouched."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'true')
            db.session.commit()
            stuck = _stuck(db, ParsedEmail,
                           message_id='<busy-skip@test.com>', minutes_ago=20)
            stuck_id = stuck.id

        with patch('routes.email._acquire_recovery_marker', return_value=False), \
             patch('tasks.run_mailbox_backfill') as mock_bf:
            from tasks.cleanup import email_parsing_timeout_cleanup
            email_parsing_timeout_cleanup()

        with app.app_context():
            row = ParsedEmail.query.get(stuck_id)
            assert row.status == 'processing'   # deferred; row untouched
        mock_bf.assert_not_called()

    def test_marker_busy_does_not_supersede(self, app):
        """Marker busy path must never write recovery_message_id."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            VettingConfig.set_value('email_processing_auto_recovery_enabled', 'true')
            db.session.commit()
            stuck = _stuck(db, ParsedEmail,
                           message_id='<busy-no-mid@test.com>', minutes_ago=20)
            stuck_id = stuck.id

        with patch('routes.email._acquire_recovery_marker', return_value=False):
            from tasks.cleanup import email_parsing_timeout_cleanup
            email_parsing_timeout_cleanup()

        with app.app_context():
            row = ParsedEmail.query.get(stuck_id)
            assert row.recovery_message_id is None
