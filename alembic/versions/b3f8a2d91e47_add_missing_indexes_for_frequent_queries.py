"""Add missing indexes for frequent queries

Adds indexes to three columns that are regularly filtered/queried but
lacked database indexes:

- ScheduleConfig.is_active          — filtered on every scheduler tick
- EmailDeliveryLog.notification_type — filtered in delivery reports
- EnvironmentStatus.environment_name — filtered in status lookups

Revision ID: b3f8a2d91e47
Revises: afc1549786f8
Create Date: 2026-02-18 04:48:00.000000

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'b3f8a2d91e47'
down_revision: Union[str, Sequence[str], None] = 'afc1549786f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Index definitions: (index_name, table_name, column_name)
_INDEXES = [
    ('ix_schedule_config_is_active', 'schedule_config', 'is_active'),
    ('ix_email_delivery_log_notification_type', 'email_delivery_log', 'notification_type'),
    ('ix_environment_status_environment_name', 'environment_status', 'environment_name'),
]


def upgrade() -> None:
    """Create indexes if they don't already exist (idempotent)."""
    conn = op.get_bind()
    inspector = inspect(conn)

    for idx_name, table_name, column_name in _INDEXES:
        existing = [idx['name'] for idx in inspector.get_indexes(table_name)]
        if idx_name not in existing:
            op.create_index(idx_name, table_name, [column_name])


def downgrade() -> None:
    """Drop the three indexes."""
    for idx_name, table_name, _ in reversed(_INDEXES):
        op.drop_index(idx_name, table_name=table_name)
