"""merge heads after auditor unique constraint

Revision ID: 7ddb7c626333
Revises: 01c2b98595f4, i3d4e5f6g7h8
Create Date: 2026-04-25 16:17:42.268957

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7ddb7c626333'
down_revision: Union[str, Sequence[str], None] = ('01c2b98595f4', 'i3d4e5f6g7h8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
