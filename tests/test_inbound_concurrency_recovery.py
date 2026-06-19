"""
Tests for two inbound-pipeline hardening fixes (the inbound path is the ONLY
live route to Bullhorn while AI screening is paused).

FIX 1 — Cross-route DUPLICATE-CANDIDATE race
    The same application can arrive as TWO inbox messages with different
    Message-IDs (Exchange/Graph mailbox-pull copy + SendGrid webhook copy). When
    the two copies are processed CONCURRENTLY, both can pass
    find_duplicate_candidate before either has created the Bullhorn candidate, so
    BOTH create one → two candidate records (observed in prod). The existing
    _find_cross_route_sibling guard keys on the minted candidate_id, so it cannot
    link two copies that each created their own. A transaction-scoped Postgres
    advisory lock keyed on (environment, normalized email) serializes the two so
    the second finds the first's candidate and collapses.

FIX 2 — Stuck-'processing' reaper RECOVERS instead of only failing
    email_parsing_timeout_cleanup previously only marked stuck rows 'failed',
    which silently drops the applicant. With auto-recovery enabled it re-drives
    them via one bounded mailbox backfill (coordinated by the same cross-worker
    marker as the manual outage-recovery), with poison-message protection.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


# ───────────────────────────── FIX 1 ─────────────────────────────

class TestCandidateIdentityLock:
    def test_noop_when_disabled(self, app):
        """Master switch off → no advisory lock SQL is issued."""
        with app.app_context():
            from email_inbound_service import EmailInboundService
            from models import VettingConfig
            svc = EmailInboundService()

            VettingConfig.set_value('cross_route_lock_enabled', 'false')
            mock_db = MagicMock()
            try:
                with patch('app.db', mock_db):
                    svc._acquire_candidate_identity_xact_lock(
                        7, 'person@example.com')
                mock_db.session.execute.assert_not_called()
            finally:
                VettingConfig.set_value('cross_route_lock_enabled', 'true')

    def test_noop_when_no_email(self, app):
        """No candidate email → nothing to serialize on, no lock SQL."""
        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()

            mock_db = MagicMock()
            with patch('app.db', mock_db):
                svc._acquire_candidate_identity_xact_lock(7, None)
                svc._acquire_candidate_identity_xact_lock(7, '')
            mock_db.session.execute.assert_not_called()

    def test_executes_with_normalized_env_email_key(self, app):
        """Enabled + email present → one pg_advisory_xact_lock keyed on
        env:lower(trim(email))."""
        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()

            mock_db = MagicMock()
            with patch('app.db', mock_db):
                svc._acquire_candidate_identity_xact_lock(
                    5, '  Test@Example.COM ')

            assert mock_db.session.execute.call_count == 1
            sql_arg, params = mock_db.session.execute.call_args[0]
            assert 'pg_advisory_xact_lock' in str(sql_arg)
            assert params == {'k': '5:test@example.com'}

    def test_key_uses_zero_when_env_none(self, app):
        """A None environment (single-tenant default) maps to '0'."""
        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()

            mock_db = MagicMock()
            with patch('app.db', mock_db):
                svc._acquire_candidate_identity_xact_lock(
                    None, 'a@b.com')
            _, params = mock_db.session.execute.call_args[0]
            assert params == {'k': '0:a@b.com'}

    def test_fails_open_on_sql_error(self, app):
        """A locking error must NEVER raise — intake continues (fail-open) — AND
        must roll back the session, or the aborted transaction would make the
        following duplicate lookup / candidate write raise and BLOCK intake."""
        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()

            mock_db = MagicMock()
            mock_db.session.execute.side_effect = RuntimeError('no such fn')
            with patch('app.db', mock_db):
                # Must not raise.
                svc._acquire_candidate_identity_xact_lock(1, 'x@y.com')
            # The aborted transaction is cleared so downstream DB work can run.
            mock_db.session.rollback.assert_called_once()

    def test_process_email_acquires_lock_before_duplicate_lookup(self, app):
        """Wiring: process_email calls the identity lock with (environment_id,
        candidate_email) BEFORE find_duplicate_candidate runs."""
        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()

            svc.detect_source = MagicMock(return_value='LinkedIn')
            svc.detect_feed = MagicMock(return_value='')
            svc.extract_bullhorn_job_id = MagicMock(return_value=42)
            svc.extract_candidate_from_email = MagicMock(return_value={
                'first_name': 'Jane', 'last_name': 'Doe',
                'email': 'jane.doe@example.com', 'phone': '555-0100',
            })
            svc._extract_attachments = MagicMock(return_value=[])
            svc._select_best_resume = MagicMock(return_value=None)
            svc._acquire_candidate_identity_xact_lock = MagicMock()
            # Stop right after the lock so we don't traverse candidate creation.
            svc.find_duplicate_candidate = MagicMock(
                side_effect=RuntimeError('stop after lock'))

            payload = {
                'from': 'info@myticas.com', 'to': 'apply@myticas.com',
                'subject': 'Jane Doe has applied on LinkedIn',
                'text': 'application', 'html': 'application',
                'headers': 'Message-ID: <wiring@geopod-1>\n',
            }
            with patch('app.get_bullhorn_service', return_value=MagicMock()):
                svc.process_email(payload)

            svc._acquire_candidate_identity_xact_lock.assert_called_once()
            env_arg, email_arg = (
                svc._acquire_candidate_identity_xact_lock.call_args[0])
            assert email_arg == 'jane.doe@example.com'
            # Lock came first; the duplicate lookup raised afterward.
            svc.find_duplicate_candidate.assert_called_once()


# ───────────────────────────── FIX 2 ─────────────────────────────

def _mk_parsed(db, ParsedEmail, *, subject, status='processing',
               created_min_ago=20, message_id=None, candidate_name='Stuck Person',
               candidate_email=None, retry=0, recovery_message_id=None):
    pe = ParsedEmail(
        message_id=message_id,
        sender_email='info@myticas.com',
        recipient_email='apply@myticas.com',
        subject=subject,
        status=status,
        candidate_name=candidate_name,
        candidate_email=candidate_email,
        vetting_retry_count=retry,
        recovery_message_id=recovery_message_id,
    )
    db.session.add(pe)
    db.session.commit()
    backdate = datetime.utcnow() - timedelta(minutes=created_min_ago)
    pe.created_at = backdate
    pe.received_at = backdate
    db.session.commit()
    return pe


def _clear_parsed(db, ParsedEmail):
    ParsedEmail.query.delete()
    db.session.commit()


def _backfill_stub(*, create_mid=None, summary, succ_status='completed',
                   succ_bh_id=999001):
    """Build a run_mailbox_backfill side_effect that optionally creates a
    successor ParsedEmail (as a real backfill re-fetch would) and returns the
    given summary dict. `succ_status`/`succ_bh_id` control whether that successor
    looks like it reached Bullhorn (success) or failed mid-pipeline."""
    def _bf(app_ref, since_iso, limit=None):
        if create_mid:
            from app import db as _db
            from models import ParsedEmail as _PE
            succ = _PE(
                message_id=create_mid,
                sender_email='info@myticas.com',
                recipient_email='apply@myticas.com',
                subject='reprocessed',
                status=succ_status,
                bullhorn_candidate_id=succ_bh_id,
            )
            _db.session.add(succ)
            _db.session.commit()
        return dict(summary)
    return _bf


class TestStuckReaper:
    def test_default_off_fails_stuck_rows(self, app):
        """Flag OFF (default) preserves the legacy behavior: stuck rows are
        auto-FAILED, message_id untouched, no backfill."""
        from app import db
        from models import ParsedEmail, VettingConfig
        from tasks.cleanup import email_parsing_timeout_cleanup

        with app.app_context():
            _clear_parsed(db, ParsedEmail)
            VettingConfig.set_value(
                'email_processing_auto_recovery_enabled', 'false')
            pe = _mk_parsed(db, ParsedEmail, subject='OFF case',
                            message_id='<off@x>')
            pid = pe.id

            email_parsing_timeout_cleanup()

            db.session.expire_all()
            row = ParsedEmail.query.get(pid)
            assert row.status == 'failed'
            assert 'Auto-failed: Processing timeout' in (row.processing_notes or '')
            assert row.message_id == '<off@x>'

    def test_recovers_when_enabled(self, app):
        """Flag ON happy path: a stuck row is reset, the backfill runs once
        (creating a successor that reaches Bullhorn), and the original row stays
        superseded as the audit breadcrumb."""
        from app import db
        from models import ParsedEmail, VettingConfig
        from tasks.cleanup import email_parsing_timeout_cleanup

        with app.app_context():
            _clear_parsed(db, ParsedEmail)
            VettingConfig.set_value(
                'email_processing_auto_recovery_enabled', 'true')
            try:
                pe = _mk_parsed(db, ParsedEmail, subject='RECOVER ME',
                                message_id='<rec@x>', retry=0)
                pid = pe.id

                stub = _backfill_stub(
                    create_mid='<rec@x>',
                    summary={'fetched': 1, 'processed': 1, 'failed': 0,
                             'duplicates': 0})
                with patch('routes.email._acquire_recovery_marker',
                           return_value=True) as m_acq, \
                     patch('routes.email._release_recovery_marker') as m_rel, \
                     patch('tasks.run_mailbox_backfill',
                           side_effect=stub) as m_bf:
                    email_parsing_timeout_cleanup()

                m_acq.assert_called_once()
                m_rel.assert_called_once()
                m_bf.assert_called_once()
                # since_iso is the first positional arg after `app`.
                since_arg = m_bf.call_args[0][1]
                assert since_arg.endswith('Z')
                assert m_bf.call_args[1].get('limit') == 100

                db.session.expire_all()
                row = ParsedEmail.query.get(pid)
                assert row.status == 'recovery_superseded'
                assert row.message_id is None
                assert row.vetting_retry_count == 1
            finally:
                VettingConfig.set_value(
                    'email_processing_auto_recovery_enabled', 'false')

    def test_backfill_failure_requeues_row(self, app):
        """Durability: if the backfill errors / fetches nothing and no successor
        was created, the reset row is restored to 'processing' (message_id back)
        so the NEXT cycle retries — a real applicant is never stranded."""
        from app import db
        from models import ParsedEmail, VettingConfig
        from tasks.cleanup import email_parsing_timeout_cleanup

        with app.app_context():
            _clear_parsed(db, ParsedEmail)
            VettingConfig.set_value(
                'email_processing_auto_recovery_enabled', 'true')
            try:
                pe = _mk_parsed(db, ParsedEmail, subject='GRAPH DOWN',
                                message_id='<down@x>')
                pid = pe.id

                stub = _backfill_stub(  # no successor created
                    create_mid=None,
                    summary={'fetched': 0, 'processed': 0, 'failed': 0,
                             'error': 'graph unavailable'})
                with patch('routes.email._acquire_recovery_marker',
                           return_value=True), \
                     patch('routes.email._release_recovery_marker'), \
                     patch('tasks.run_mailbox_backfill', side_effect=stub):
                    email_parsing_timeout_cleanup()

                db.session.expire_all()
                row = ParsedEmail.query.get(pid)
                assert row.status == 'processing'   # requeued for next cycle
                assert row.message_id == '<down@x>'  # key restored
            finally:
                VettingConfig.set_value(
                    'email_processing_auto_recovery_enabled', 'false')

    def test_backfill_limit_miss_requeues_row(self, app):
        """Durability: a backfill that hits its limit before reaching the row
        (no successor) requeues it rather than stranding it."""
        from app import db
        from models import ParsedEmail, VettingConfig
        from tasks.cleanup import email_parsing_timeout_cleanup

        with app.app_context():
            _clear_parsed(db, ParsedEmail)
            VettingConfig.set_value(
                'email_processing_auto_recovery_enabled', 'true')
            VettingConfig.set_value(
                'email_processing_recovery_backfill_limit', '5')
            try:
                pe = _mk_parsed(db, ParsedEmail, subject='BEYOND LIMIT',
                                message_id='<far@x>')
                pid = pe.id

                stub = _backfill_stub(  # fetched == limit, no successor for us
                    create_mid=None,
                    summary={'fetched': 5, 'processed': 5, 'failed': 0,
                             'duplicates': 0})
                with patch('routes.email._acquire_recovery_marker',
                           return_value=True), \
                     patch('routes.email._release_recovery_marker'), \
                     patch('tasks.run_mailbox_backfill', side_effect=stub) as m_bf:
                    email_parsing_timeout_cleanup()

                assert m_bf.call_args[1].get('limit') == 5
                db.session.expire_all()
                row = ParsedEmail.query.get(pid)
                assert row.status == 'processing'
                assert row.message_id == '<far@x>'
            finally:
                VettingConfig.set_value(
                    'email_processing_recovery_backfill_limit', '100')
                VettingConfig.set_value(
                    'email_processing_auto_recovery_enabled', 'false')

    def test_clean_success_no_successor_keeps_superseded(self, app):
        """A clean, complete backfill (no error, under limit) that produced no
        same-message_id successor means the email was reprocessed under another
        transport's id (already handled) — leave the breadcrumb superseded, do
        NOT re-drive (no false loop)."""
        from app import db
        from models import ParsedEmail, VettingConfig
        from tasks.cleanup import email_parsing_timeout_cleanup

        with app.app_context():
            _clear_parsed(db, ParsedEmail)
            VettingConfig.set_value(
                'email_processing_auto_recovery_enabled', 'true')
            try:
                pe = _mk_parsed(db, ParsedEmail, subject='OTHER TRANSPORT',
                                message_id='<sendgrid@x>')
                pid = pe.id

                stub = _backfill_stub(  # clean success, no <sendgrid@x> successor
                    create_mid=None,
                    summary={'fetched': 3, 'processed': 3, 'failed': 0,
                             'duplicates': 1})
                with patch('routes.email._acquire_recovery_marker',
                           return_value=True), \
                     patch('routes.email._release_recovery_marker'), \
                     patch('tasks.run_mailbox_backfill', side_effect=stub):
                    email_parsing_timeout_cleanup()

                db.session.expire_all()
                row = ParsedEmail.query.get(pid)
                assert row.status == 'recovery_superseded'
                assert row.message_id is None
            finally:
                VettingConfig.set_value(
                    'email_processing_auto_recovery_enabled', 'false')

    def test_failed_successor_is_rearmed(self, app):
        """If the backfill re-fetch CREATED a successor row (it owns the
        message_id) but that successor never reached Bullhorn, the original row
        cannot be restored — so the successor is re-armed to 'processing' for the
        reaper to re-drive next cycle (never left dedupe-blocked + stranded)."""
        from app import db
        from models import ParsedEmail, VettingConfig
        from tasks.cleanup import email_parsing_timeout_cleanup

        with app.app_context():
            _clear_parsed(db, ParsedEmail)
            VettingConfig.set_value(
                'email_processing_auto_recovery_enabled', 'true')
            try:
                pe = _mk_parsed(db, ParsedEmail, subject='FAILED SUCC',
                                message_id='<fs@x>')
                pid = pe.id

                stub = _backfill_stub(  # successor created but did NOT reach BH
                    create_mid='<fs@x>',
                    succ_status='failed', succ_bh_id=None,
                    summary={'fetched': 1, 'processed': 0, 'failed': 1,
                             'duplicates': 0})
                with patch('routes.email._acquire_recovery_marker',
                           return_value=True), \
                     patch('routes.email._release_recovery_marker'), \
                     patch('tasks.run_mailbox_backfill', side_effect=stub):
                    email_parsing_timeout_cleanup()

                db.session.expire_all()
                original = ParsedEmail.query.get(pid)
                # Original stays superseded (its key now belongs to the successor).
                assert original.status == 'recovery_superseded'
                # The failed successor is re-armed so the reaper retries it.
                successor = ParsedEmail.query.filter(
                    ParsedEmail.message_id == '<fs@x>',
                    ParsedEmail.id != pid,
                ).first()
                assert successor is not None
                assert successor.status == 'processing'
            finally:
                VettingConfig.set_value(
                    'email_processing_auto_recovery_enabled', 'false')

    def test_null_message_id_fails_visibly(self, app):
        """A stuck row with NO message_id has no dedupe key, so it cannot be
        safely auto-recovered: it is FAILED visibly for manual review (never
        silently superseded), and no backfill is attempted for it."""
        from app import db
        from models import ParsedEmail, VettingConfig
        from tasks.cleanup import email_parsing_timeout_cleanup

        with app.app_context():
            _clear_parsed(db, ParsedEmail)
            VettingConfig.set_value(
                'email_processing_auto_recovery_enabled', 'true')
            try:
                pe = _mk_parsed(db, ParsedEmail, subject='NO KEY',
                                message_id=None)
                pid = pe.id

                with patch('routes.email._acquire_recovery_marker',
                           return_value=True) as m_acq, \
                     patch('routes.email._release_recovery_marker'), \
                     patch('tasks.run_mailbox_backfill') as m_bf:
                    email_parsing_timeout_cleanup()

                # Nothing re-drivable → marker never claimed, no backfill.
                m_acq.assert_not_called()
                m_bf.assert_not_called()
                db.session.expire_all()
                row = ParsedEmail.query.get(pid)
                assert row.status == 'failed'
                assert 'no message_id' in (row.processing_notes or '')
            finally:
                VettingConfig.set_value(
                    'email_processing_auto_recovery_enabled', 'false')

    def test_poison_cap_fails_without_redrive(self, app):
        """A message already re-driven >= max_retries (counted via prior
        'recovery_superseded' breadcrumbs whose recovery_message_id matches the
        stuck row's message_id — the STABLE identity) is FAILED, not re-driven,
        with message_id intact so the backfill dedupes it."""
        from app import db
        from models import ParsedEmail, VettingConfig
        from tasks.cleanup import email_parsing_timeout_cleanup

        with app.app_context():
            _clear_parsed(db, ParsedEmail)
            VettingConfig.set_value(
                'email_processing_auto_recovery_enabled', 'true')
            try:
                mid = '<poison@x>'
                # 2 prior attempts == default max_retries (2) → exhausted. Each
                # breadcrumb preserves the original Message-ID (message_id itself
                # was cleared on supersede, exactly as the reaper does).
                _mk_parsed(db, ParsedEmail, subject='whatever',
                           status='recovery_superseded', created_min_ago=10,
                           recovery_message_id=mid)
                _mk_parsed(db, ParsedEmail, subject='whatever',
                           status='recovery_superseded', created_min_ago=5,
                           recovery_message_id=mid)
                pe = _mk_parsed(db, ParsedEmail, subject='whatever',
                                message_id=mid)
                pid = pe.id

                with patch('routes.email._acquire_recovery_marker',
                           return_value=True) as m_acq, \
                     patch('routes.email._release_recovery_marker'), \
                     patch('tasks.run_mailbox_backfill') as m_bf:
                    email_parsing_timeout_cleanup()

                # No eligible rows → marker never claimed, no backfill.
                m_acq.assert_not_called()
                m_bf.assert_not_called()

                db.session.expire_all()
                row = ParsedEmail.query.get(pid)
                assert row.status == 'failed'
                assert 'gave up' in (row.processing_notes or '')
                assert row.message_id == mid  # intact → dedupe-blocks
            finally:
                VettingConfig.set_value(
                    'email_processing_auto_recovery_enabled', 'false')

    def test_poison_cap_terminates_blank_subject_no_email(self, app):
        """The exact loop the stable identity exists to stop: a poison email with
        NO candidate_email and NO subject. Keying on subject/email would never
        accumulate across successor-row churn → infinite loop. Keying on
        recovery_message_id caps it."""
        from app import db
        from models import ParsedEmail, VettingConfig
        from tasks.cleanup import email_parsing_timeout_cleanup

        with app.app_context():
            _clear_parsed(db, ParsedEmail)
            VettingConfig.set_value(
                'email_processing_auto_recovery_enabled', 'true')
            try:
                mid = '<blank@x>'
                # Prior breadcrumbs: blank subject, no candidate email — only the
                # preserved Message-ID links them.
                _mk_parsed(db, ParsedEmail, subject=None, candidate_email=None,
                           status='recovery_superseded', created_min_ago=10,
                           recovery_message_id=mid)
                _mk_parsed(db, ParsedEmail, subject=None, candidate_email=None,
                           status='recovery_superseded', created_min_ago=5,
                           recovery_message_id=mid)
                pe = _mk_parsed(db, ParsedEmail, subject=None,
                                candidate_email=None, message_id=mid)
                pid = pe.id

                with patch('routes.email._acquire_recovery_marker',
                           return_value=True) as m_acq, \
                     patch('routes.email._release_recovery_marker'), \
                     patch('tasks.run_mailbox_backfill') as m_bf:
                    email_parsing_timeout_cleanup()

                m_acq.assert_not_called()
                m_bf.assert_not_called()
                db.session.expire_all()
                row = ParsedEmail.query.get(pid)
                assert row.status == 'failed'
                assert row.message_id == mid
            finally:
                VettingConfig.set_value(
                    'email_processing_auto_recovery_enabled', 'false')

    def test_marker_busy_defers_cycle(self, app):
        """If a manual outage-recovery already holds the marker, the reaper
        defers: the stuck row is left 'processing' (untouched), no backfill."""
        from app import db
        from models import ParsedEmail, VettingConfig
        from tasks.cleanup import email_parsing_timeout_cleanup

        with app.app_context():
            _clear_parsed(db, ParsedEmail)
            VettingConfig.set_value(
                'email_processing_auto_recovery_enabled', 'true')
            try:
                pe = _mk_parsed(db, ParsedEmail, subject='BUSY case',
                                message_id='<busy@x>')
                pid = pe.id

                with patch('routes.email._acquire_recovery_marker',
                           return_value=False), \
                     patch('tasks.run_mailbox_backfill') as m_bf:
                    email_parsing_timeout_cleanup()

                m_bf.assert_not_called()
                db.session.expire_all()
                row = ParsedEmail.query.get(pid)
                assert row.status == 'processing'  # untouched, retried next cycle
                assert row.message_id == '<busy@x>'
            finally:
                VettingConfig.set_value(
                    'email_processing_auto_recovery_enabled', 'false')

    def test_recent_rows_not_reaped(self, app):
        """A row younger than the timeout is never touched."""
        from app import db
        from models import ParsedEmail, VettingConfig
        from tasks.cleanup import email_parsing_timeout_cleanup

        with app.app_context():
            _clear_parsed(db, ParsedEmail)
            VettingConfig.set_value(
                'email_processing_auto_recovery_enabled', 'false')
            pe = _mk_parsed(db, ParsedEmail, subject='fresh',
                            message_id='<fresh@x>', created_min_ago=1)
            pid = pe.id

            email_parsing_timeout_cleanup()

            db.session.expire_all()
            row = ParsedEmail.query.get(pid)
            assert row.status == 'processing'
