"""Add functional GIN trigram index on normalized phone for fast substring search

Task #49 / scope: ParsedEmail.candidate_phone substring lookups in the
Scout Screening recruiter dashboard. Task #47 introduced a server-side
phone search that normalizes the column with
``regexp_replace(candidate_phone, '[^0-9]', '', 'g')`` and ILIKEs against
``%digits%``. Without an index this performs a sequential scan on
``parsed_email`` for every recruiter keystroke (after debounce). This
migration adds a functional GIN trigram index on the normalized phone
expression so the planner can satisfy the ILIKE substring with an index
scan as the table grows.

The migration is idempotent — re-running ``alembic upgrade head`` after
the index already exists is a no-op (uses ``CREATE INDEX IF NOT EXISTS``
and ``CREATE EXTENSION IF NOT EXISTS``).

The downgrade only drops the index. We deliberately do NOT drop the
``pg_trgm`` extension because other indexes/queries in the database may
depend on it.

Revision ID: 0523f094143b
Revises: 7ddb7c626333
Create Date: 2026-04-25 17:08:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0523f094143b'
down_revision: Union[str, Sequence[str], None] = '7ddb7c626333'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_NAME = 'ix_parsed_email_phone_normalized_trgm'


def upgrade() -> None:
    """Create the trigram extension (if missing) and the functional GIN index.

    The CREATE INDEX runs ``CONCURRENTLY`` inside an Alembic
    ``autocommit_block`` so it does not take an exclusive write lock on
    ``parsed_email`` during the build — important for production rollout
    where the table receives writes from inbound email parsing. The
    extension creation stays in the implicit transaction (it is fast and
    must complete before the index build can reference ``gin_trgm_ops``).
    """
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        # SQLite and other test dialects: this index is PostgreSQL-only
        # (functional GIN with gin_trgm_ops). Skip silently — the search
        # query in routes/scout_screening.py already has a SQLite fallback.
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    with op.get_context().autocommit_block():
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}
            ON parsed_email
            USING GIN (
                (regexp_replace(candidate_phone, '[^0-9]', '', 'g')) gin_trgm_ops
            );
            """
        )


def downgrade() -> None:
    """Drop the functional index concurrently. Leave pg_trgm in place — other
    code may use it."""
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME};")
