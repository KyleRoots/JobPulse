"""auth_revision column on a database that already has users."""

from unittest.mock import MagicMock


def test_boot_migration_issues_quoted_alter_when_column_is_absent(app):
    from seeding.migrations import run_schema_migrations

    executed = []

    def fake_execute(statement, params=None):
        sql = str(getattr(statement, 'text', statement))
        executed.append(sql)
        result = MagicMock()
        if 'auth_revision' in sql and 'information_schema' in sql:
            result.fetchone.return_value = None
            return result
        if (
            'information_schema' in sql
            or 'pg_indexes' in sql
            or 'pg_constraint' in sql
            or 'pg_class' in sql
        ):
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

    alters = [
        sql for sql in executed
        if 'ADD COLUMN auth_revision' in sql
    ]
    assert alters == [
        'ALTER TABLE "user" ADD COLUMN auth_revision INTEGER NOT NULL DEFAULT 0'
    ]
    assert any('SET LOCAL lock_timeout' in sql for sql in executed)


def _upgrade_auth_revision():
    import importlib.util
    path = 'alembic/versions/b6c7d8e9f0a1_add_user_auth_revision.py'
    spec = importlib.util.spec_from_file_location('auth_revision_revision', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.upgrade


def test_existing_sqlite_user_row_keeps_revision_zero_when_column_is_added(tmp_path):
    """Add the column to a table that already has a row, then run it again."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(f'sqlite:///{tmp_path / "existing.db"}')
    with engine.begin() as conn:
        conn.execute(text(
            'CREATE TABLE "user" (id INTEGER PRIMARY KEY, username VARCHAR(80) NOT NULL)'
        ))
        conn.execute(text(
            "INSERT INTO \"user\" (id, username) VALUES (1, 'kept-user')"
        ))

    upgrade = _upgrade_auth_revision()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            upgrade()

    with engine.connect() as conn:
        columns = {col['name'] for col in inspect(engine).get_columns('user')}
        assert 'auth_revision' in columns
        row = conn.execute(text(
            'SELECT username, auth_revision FROM "user" WHERE id = 1'
        )).one()
    assert row.username == 'kept-user'
    assert row.auth_revision == 0

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            upgrade()

    with engine.connect() as conn:
        again = conn.execute(text(
            'SELECT auth_revision FROM "user" WHERE id = 1'
        )).one()
    assert again.auth_revision == 0
