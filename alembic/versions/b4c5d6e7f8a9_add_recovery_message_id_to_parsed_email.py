"""add recovery_message_id to parsed_email

Revision ID: b4c5d6e7f8a9
Revises: a1b2c3d4e5f6
Create Date: 2026-06-19

Auto-recovery poison-loop stable identity. The stuck-'processing' reaper
(`email_parsing_timeout_cleanup`) can re-drive a stuck inbound row by
superseding it and re-fetching the email from the mailbox. Each crashing copy
of the same email is re-fetched under the SAME Message-ID, but the superseded
breadcrumb has its message_id cleared (so the re-fetch is not deduped away).

To bound a poison email that keeps crashing the pipeline, the reaper counts
prior superseded breadcrumbs for the same logical email. The per-row id/subject
is NOT a stable identity (every re-fetch creates a new successor row), so this
adds a dedicated column that preserves the original Message-ID on the
breadcrumb. The poison cap then counts by recovery_message_id, which survives
the successor-row churn and guarantees the retry loop terminates.

Nullable with no backfill — only the auto-recovery path (default OFF) ever
writes it, so existing behavior is byte-for-byte unchanged. Boot-time
idempotent ALTERs in seeding/migrations.py cover dev DBs; this keeps production
schema in parity.
"""
from alembic import op
import sqlalchemy as sa


revision = "b4c5d6e7f8a9"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parsed_email",
        sa.Column("recovery_message_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("parsed_email", "recovery_message_id")
