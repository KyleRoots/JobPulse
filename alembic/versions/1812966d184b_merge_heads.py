"""merge heads

Revision ID: 1812966d184b
Revises: a2c4e6f8b1d3, g1b2c3d4e5f6
Create Date: 2026-03-26 21:07:40.682529

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1812966d184b'
down_revision: Union[str, Sequence[str], None] = ('a2c4e6f8b1d3', 'g1b2c3d4e5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
