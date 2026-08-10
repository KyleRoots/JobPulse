"""Boot unique-swap migrations must not DDL when already complete.

Regression for Aug 10 2026 Railway healthcheck hangs: unconditional
``DROP CONSTRAINT IF EXISTS`` takes AccessExclusiveLock every deploy and
stalls behind idle-in-transaction sessions, so gunicorn never binds.
"""
from unittest.mock import MagicMock, call


def test_unique_swap_skips_drop_when_already_migrated(app):
    from seeding.migrations import run_schema_migrations

    executed = []

    def fake_execute(statement, params=None):
        sql = str(getattr(statement, 'text', statement))
        executed.append((sql, params))
        result = MagicMock()
        # Column / index existence checks used throughout migrations.
        if 'pg_indexes' in sql and 'has_composite' in sql:
            row = {
                'has_composite': True,
                'has_legacy': False,
                'has_plain': True,
            }
            result.mappings.return_value.first.return_value = row
            result.fetchone.return_value = (1,)
            return result
        if 'pg_indexes' in sql or 'information_schema' in sql or 'pg_constraint' in sql:
            # Treat other catalog lookups as "already present" where a row means skip.
            result.fetchone.return_value = (1,)
            result.mappings.return_value.first.return_value = {
                'has_composite': True,
                'has_legacy': False,
                'has_plain': True,
            }
            return result
        result.fetchone.return_value = None
        result.rowcount = 0
        result.mappings.return_value.first.return_value = None
        return result

    with app.app_context():
        from extensions import db
        original = db.session.execute
        db.session.execute = fake_execute
        try:
            run_schema_migrations(db)
        finally:
            db.session.execute = original

    ddl = [sql for sql, _ in executed if 'DROP CONSTRAINT' in sql.upper()]
    assert ddl == [], f'expected no DROP CONSTRAINT when swap complete, got: {ddl}'
