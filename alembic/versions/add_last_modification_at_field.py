"""Add last_modification_at field to projects table

Revision ID: add_last_modification_at
Revises: add_system_dependencies
Create Date: 2026-01-17 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timezone

# revision identifiers, used by Alembic.
revision = 'add_last_modification_at'
down_revision = 'add_system_dependencies'
branch_labels = None
depends_on = None

def upgrade():
    """Add last_modification_at column to projects table."""
    # Add column, nullable=True initially to allow backfill
    op.add_column('projects', sa.Column('last_modification_at', sa.DateTime(), nullable=True))
    
    # Backfill existing records: update last_modification_at = updated_at
    # Using raw SQL for compatibility
    bind = op.get_bind()
    session = Session(bind=bind)
    session.execute(text("UPDATE projects SET last_modification_at = updated_at"))
    session.commit()

def downgrade():
    """Remove last_modification_at column from projects table."""
    op.drop_column('projects', 'last_modification_at')
