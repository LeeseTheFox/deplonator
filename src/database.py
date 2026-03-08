"""
Database configuration and session management for Telegram Bot Deployer.
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import os

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/database.sqlite")

# Create engine with SQLite-specific configuration
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # SQLite specific
    poolclass=StaticPool,  # Use static pool for SQLite
    echo=False,  # Set to True for SQL query logging
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create declarative base for models
Base = declarative_base()


def get_db():
    """
    Dependency function to get database session.
    Used with FastAPI dependency injection.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Initialize database by creating all tables.
    This should be called on application startup.
    """
    # Ensure data directory exists
    os.makedirs("data", exist_ok=True)
    
    # Create all tables
    Base.metadata.create_all(bind=engine)
    
    # Run self-healing migrations
    migrate_db()


def migrate_db():
    """
    Perform self-healing database migrations.
    Safely adds new columns to existing tables using raw SQL.
    """
    from sqlalchemy import text
    
    with engine.connect() as conn:
        # Check if last_config_change_at exists in projects table
        try:
            # Try to select the column
            conn.execute(text("SELECT last_config_change_at FROM projects LIMIT 1"))
        except Exception:
            # Column doesn't exist, add it
            print("Migrating database: Adding last_config_change_at column to projects table...")
            try:
                # SQLite ALTER TABLE ADD COLUMN
                conn.execute(text("ALTER TABLE projects ADD COLUMN last_config_change_at DATETIME"))
                conn.commit()
                print("Migration successful.")
            except Exception as e:
                print(f"Migration failed: {str(e)}")