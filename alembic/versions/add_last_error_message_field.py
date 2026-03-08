"""Add last_error_message field to projects table

Revision ID: add_last_error_message
Revises: add_error_tracking
Create Date: 2026-01-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_last_error_message'
down_revision: Union[str, None] = 'add_error_tracking'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add last_error_message column to projects table."""
    op.add_column('projects', sa.Column('last_error_message', sa.String(), nullable=True))


def downgrade() -> None:
    """Remove last_error_message column from projects table."""
    op.drop_column('projects', 'last_error_message')
