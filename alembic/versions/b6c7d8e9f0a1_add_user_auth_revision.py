"""add user.auth_revision

Revision ID: b6c7d8e9f0a1
Revises: f3a4b5c6d7e8
Create Date: 2026-10-14

Parity record. Boot migrations add this column on existing databases.
Default 0 keeps session and remember cookies issued before the column
valid until that user changes or resets their password.
"""
from alembic import op
import sqlalchemy as sa


revision = "b6c7d8e9f0a1"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("user")}
    if "auth_revision" not in cols:
        op.add_column(
            "user",
            sa.Column(
                "auth_revision",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("user")}
    if "auth_revision" in cols:
        op.drop_column("user", "auth_revision")
