"""Fix timezone handling for timestamps

Revision ID: fix_timezone_timestamps
Revises: add_last_error_message
Create Date: 2026-01-09 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone

# revision identifiers, used by Alembic.
revision = 'fix_timezone_timestamps'
down_revision = 'add_last_error_message'
branch_labels = None
depends_on = None


def upgrade():
    """
    Update existing timestamp columns to use proper timezone handling.
    Since SQLite doesn't have native timezone support, we'll update the 
    application logic to handle UTC timestamps properly.
    """
    # For SQLite, we don't need to change the schema, but we can update
    # existing records to ensure they're interpreted correctly
    # The Python code changes will handle new records properly
    pass


def downgrade():
    """
    Downgrade is not needed since we're not changing the database schema,
    only how the application handles timestamps.
    """
    pass