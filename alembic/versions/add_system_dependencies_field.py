"""Add system_dependencies field to projects table

Revision ID: add_system_dependencies
Revises: fix_timezone_timestamps
Create Date: 2026-01-09 20:32:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_system_dependencies'
down_revision = 'fix_timezone_timestamps'
branch_labels = None
depends_on = None


def upgrade():
    """Add system_dependencies column to projects table."""
    op.add_column('projects', sa.Column('system_dependencies', sa.String(), nullable=True))


def downgrade():
    """Remove system_dependencies column from projects table."""
    op.drop_column('projects', 'system_dependencies')