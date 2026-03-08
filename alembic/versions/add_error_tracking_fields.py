"""add_error_tracking_fields

Revision ID: add_error_tracking
Revises: 54917aaf4883
Create Date: 2026-01-09 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_error_tracking'
down_revision = '54917aaf4883'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns to projects table
    op.add_column('projects', sa.Column('errors_silenced', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('projects', sa.Column('last_error_acknowledged_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    # Remove the columns
    op.drop_column('projects', 'last_error_acknowledged_at')
    op.drop_column('projects', 'errors_silenced')