"""Smoke tests for the phone-search trigram index migration (Task #49).

Validates that the migration module is wired correctly into the Alembic
revision graph and that its upgrade/downgrade SQL is well-formed and
idempotent. We do not stand up a real PostgreSQL instance here — the
project's pytest fixtures use SQLite, and the migration explicitly
no-ops on non-PostgreSQL dialects (the search query has a SQLite
fallback in routes/scout_screening.py).

These tests guard against regressions like:
  - Wrong down_revision pointer that breaks the migration chain
  - Missing IF NOT EXISTS clauses that make the migration non-idempotent
  - A bad index name that would orphan an index across upgrade/downgrade
"""
import importlib.util
import os


def _load_migration():
    """Load the migration module by file path.

    ``alembic/versions/`` is not a Python package (no ``__init__.py``), so
    ``importlib.import_module`` cannot reach it. Load the file directly
    with a spec instead.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mig_path = os.path.join(
        project_root,
        'alembic',
        'versions',
        '0523f094143b_add_phone_trigram_index.py',
    )
    spec = importlib.util.spec_from_file_location(
        'phone_trigram_migration', mig_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_identifiers_are_correct():
    mig = _load_migration()
    assert mig.revision == '0523f094143b'
    assert mig.down_revision == '7ddb7c626333'
    assert mig.branch_labels is None
    assert mig.depends_on is None


def test_index_name_is_consistent():
    mig = _load_migration()
    assert mig.INDEX_NAME == 'ix_parsed_email_phone_normalized_trgm'


def test_alembic_chain_includes_this_revision_and_is_reachable_from_head():
    """The new migration must be present in the script directory and reachable
    from at least one current head. We deliberately do NOT assert that this
    revision IS the current head — future migrations should be able to extend
    the chain on top without breaking this test."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config('alembic.ini')
    script = ScriptDirectory.from_config(cfg)

    # Revision must exist in the script directory.
    revision_obj = script.get_revision('0523f094143b')
    assert revision_obj is not None, (
        'Migration 0523f094143b was not found in the alembic script directory.'
    )
    assert revision_obj.down_revision == '7ddb7c626333', (
        f'Expected down_revision 7ddb7c626333, got {revision_obj.down_revision}.'
    )

    # Revision must be reachable from some current head — i.e. it lives on the
    # active branch, not orphaned. iterate_revisions(heads -> base) walks the
    # whole reachable graph, which is exactly the invariant we want.
    heads = list(script.get_heads())
    reachable = {
        rev.revision
        for rev in script.iterate_revisions(heads, 'base')
    }
    assert '0523f094143b' in reachable, (
        f'Migration 0523f094143b is not reachable from heads {heads}. '
        'It may be orphaned on a dead branch.'
    )


class _FakeBind:
    """Bind stub with a configurable dialect name."""

    def __init__(self, dialect_name):
        self.dialect = type('FakeDialect', (), {'name': dialect_name})()


class _FakeContext:
    """Stub Alembic MigrationContext exposing only ``autocommit_block``.

    The real autocommit_block commits the implicit transaction, runs the
    body outside any transaction, then begins a new one — that machinery
    is irrelevant to our SQL-emission tests, so we just yield a no-op
    context manager.
    """

    from contextlib import contextmanager

    @contextmanager
    def autocommit_block(self):
        yield


def _patch_op(monkeypatch, mig, dialect_name):
    """Wire fake bind/context/execute onto the migration's ``op`` module.

    Returns the list that ``op.execute`` will append SQL strings to.
    """
    executed = []
    bind = _FakeBind(dialect_name)
    monkeypatch.setattr(mig.op, 'get_bind', lambda: bind)
    monkeypatch.setattr(mig.op, 'get_context', lambda: _FakeContext())
    monkeypatch.setattr(mig.op, 'execute', lambda sql: executed.append(str(sql)))
    return executed


def test_upgrade_skips_on_sqlite(monkeypatch):
    """On SQLite (test dialect) the migration must be a silent no-op."""
    mig = _load_migration()
    executed = _patch_op(monkeypatch, mig, 'sqlite')

    mig.upgrade()
    mig.downgrade()

    assert executed == [], (
        f'Migration should be a no-op on SQLite, but executed: {executed}'
    )


def test_upgrade_emits_idempotent_concurrent_sql_on_postgres(monkeypatch):
    """On PostgreSQL the upgrade must use IF NOT EXISTS and CONCURRENTLY.

    The ``CONCURRENTLY`` clause is what keeps the index build from taking
    an exclusive write lock on ``parsed_email`` during a production
    rollout — losing it would silently regress operational safety.
    """
    mig = _load_migration()
    executed = _patch_op(monkeypatch, mig, 'postgresql')

    mig.upgrade()

    assert len(executed) == 2, f'Expected 2 statements, got {len(executed)}: {executed}'
    extension_sql, index_sql = executed
    assert 'CREATE EXTENSION IF NOT EXISTS pg_trgm' in extension_sql
    assert f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {mig.INDEX_NAME}' in index_sql
    assert 'gin_trgm_ops' in index_sql
    assert "regexp_replace(candidate_phone, '[^0-9]', '', 'g')" in index_sql


def test_downgrade_uses_concurrent_if_exists_on_postgres(monkeypatch):
    """Downgrade must be idempotent (IF EXISTS) and non-blocking (CONCURRENTLY)."""
    mig = _load_migration()
    executed = _patch_op(monkeypatch, mig, 'postgresql')

    mig.downgrade()

    assert executed == [
        f'DROP INDEX CONCURRENTLY IF EXISTS {mig.INDEX_NAME};'
    ]
