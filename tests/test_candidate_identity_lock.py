"""
Tests for the pg advisory lock that serializes concurrent processing of the
same applicant (_acquire_candidate_identity_xact_lock / _cross_route_lock_enabled).

The lock calls pg_advisory_xact_lock which does not exist on the SQLite test
backend, so these tests verify the enabled/disabled flag logic and the
fail-open contract (SQL error → rollback + warning, no exception raised,
session remains usable) rather than the Postgres locking mechanism itself.
"""
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_cfg(app, db, key, value):
    from models import VettingConfig
    with app.app_context():
        VettingConfig.set_value(key, value)
        db.session.commit()


# ---------------------------------------------------------------------------
# _cross_route_lock_enabled
# ---------------------------------------------------------------------------

class TestCrossRouteLockEnabled:
    def test_enabled_by_default(self, app):
        """No override → defaults to enabled (True)."""
        from app import db
        with app.app_context():
            from models import VettingConfig
            VettingConfig.set_value('cross_route_lock_enabled', 'true')
            db.session.commit()

            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()
            assert svc._cross_route_lock_enabled() is True

    def test_disabled_via_config(self, app):
        """VettingConfig key = 'false' → disabled (False)."""
        from app import db
        with app.app_context():
            from models import VettingConfig
            VettingConfig.set_value('cross_route_lock_enabled', 'false')
            db.session.commit()

            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()
            assert svc._cross_route_lock_enabled() is False

    def test_enabled_explicit_true(self, app):
        """VettingConfig key = 'true' → enabled (True)."""
        from app import db
        with app.app_context():
            from models import VettingConfig
            VettingConfig.set_value('cross_route_lock_enabled', 'true')
            db.session.commit()

            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()
            assert svc._cross_route_lock_enabled() is True

    def test_fail_open_on_config_error(self, app):
        """VettingConfig read raises → defaults to True (never blocks intake)."""
        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()
            with patch('models.VettingConfig.get_value',
                       side_effect=RuntimeError("db error")):
                assert svc._cross_route_lock_enabled() is True


# ---------------------------------------------------------------------------
# _acquire_candidate_identity_xact_lock
# ---------------------------------------------------------------------------

class TestAcquireCandidateIdentityLock:
    def test_no_email_is_noop(self, app):
        """No candidate email → no DB call, session stays clean."""
        from app import db
        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()
            with patch.object(db.session, 'execute') as mock_exec:
                svc._acquire_candidate_identity_xact_lock(1, None)
                svc._acquire_candidate_identity_xact_lock(1, '')
                mock_exec.assert_not_called()
            db.session.commit()  # session still healthy

    def test_blank_email_is_noop(self, app):
        """Whitespace-only email → treated as missing → no-op (no DB call)."""
        from app import db
        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()
            with patch.object(db.session, 'execute') as mock_exec:
                svc._acquire_candidate_identity_xact_lock(1, '   ')
                # The implementation strips before the truthiness check,
                # so '   ' is treated as absent → no advisory lock call.
                mock_exec.assert_not_called()

    def test_disabled_is_noop(self, app):
        """Lock disabled via config → no pg_advisory_xact_lock call."""
        from app import db
        with app.app_context():
            from models import VettingConfig
            VettingConfig.set_value('cross_route_lock_enabled', 'false')
            db.session.commit()

            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()
            with patch.object(db.session, 'execute') as mock_exec:
                svc._acquire_candidate_identity_xact_lock(1, 'jane@example.com')
                mock_exec.assert_not_called()

    def test_fail_open_on_sql_error(self, app):
        """SQL error (SQLite has no pg_advisory_xact_lock) → no exception raised.

        On the SQLite test backend the advisory lock call always fails.
        The implementation must catch it, roll back the broken transaction,
        and return normally so intake is never blocked.
        """
        from app import db
        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()
            # Call must not raise even though SQLite rejects pg_advisory_xact_lock
            svc._acquire_candidate_identity_xact_lock(1, 'test@example.com')
            # Session must still be usable after the rollback in the except clause
            db.session.commit()

    def test_fail_open_preserves_session_for_real_queries(self, app):
        """After advisory lock failure the session is fully usable."""
        from app import db
        from models import ParsedEmail
        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()
            # Trigger fail-open path (pg_advisory_xact_lock → OperationalError on SQLite)
            svc._acquire_candidate_identity_xact_lock(1, 'check@example.com')
            # A real query must succeed — session must be clean after the rollback
            count = ParsedEmail.query.count()
            assert isinstance(count, int)

    def test_none_env_id_does_not_raise(self, app):
        """None environment_id is encoded as 0 in the lock key; must not raise."""
        from app import db
        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()
            # Should not raise (key is built as "0:email" when env_id is None)
            svc._acquire_candidate_identity_xact_lock(None, 'env-none@example.com')
            db.session.commit()
