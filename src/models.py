"""
SQLAlchemy models for Telegram Bot Deployer.
"""

from sqlalchemy import Column, String, DateTime, Boolean, Enum as SQLEnum, types
from sqlalchemy.sql import func
from datetime import datetime, timezone
from enum import Enum
import uuid
import re
import unicodedata

from .database import Base


def utc_now():
    """Return current UTC timestamp with timezone info."""
    return datetime.now(timezone.utc)


class UtcDateTime(types.TypeDecorator):
    """
    A DateTime type that ensures UTC timezone handling for SQLite.
    
    SQLite doesn't natively support timezone-aware datetimes, so this
    TypeDecorator ensures that:
    - Values are stored as UTC
    - Values are returned with UTC timezone info
    """
    impl = types.DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Convert datetime to UTC before storing."""
        if value is not None:
            if value.tzinfo is None:
                # Assume naive datetime is already UTC
                value = value.replace(tzinfo=timezone.utc)
            else:
                # Convert to UTC
                value = value.astimezone(timezone.utc)
            # Return naive datetime for storage (SQLite doesn't support tz)
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        """Add UTC timezone info when reading from database."""
        if value is not None:
            # SQLite returns naive datetime, assume it's UTC
            return value.replace(tzinfo=timezone.utc)
        return value


class ProjectStatus(Enum):
    """Enumeration for project deployment status."""
    CREATED = "created"        # Project created, not configured
    CONFIGURED = "configured"  # Configuration set, not deployed
    DEPLOYING = "deploying"    # Deployment in progress
    RUNNING = "running"        # Container running
    ERROR = "error"            # Container running but errors detected in logs
    STOPPED = "stopped"        # Container stopped
    FAILED = "failed"          # Deployment or runtime failure
    MAINTENANCE = "maintenance" # Container running in maintenance mode (sleep infinity)


class Project(Base):
    """
    SQLAlchemy model for bot projects.
    
    Represents a single Telegram bot project with its configuration
    and deployment status.
    
    The project ID is a Docker-safe identifier derived from the project name,
    used as both the database primary key and Docker container name.
    Format: sanitized-name-xxxx (e.g., "my-bot-a1b2")
    """
    __tablename__ = "projects"

    # Primary key - Docker-safe ID derived from project name, also used as container name
    id = Column(String, primary_key=True)
    
    # Project metadata
    name = Column(String, nullable=False, index=True)
    created_at = Column(UtcDateTime, nullable=False, default=utc_now)
    updated_at = Column(UtcDateTime, nullable=False, default=utc_now, onupdate=utc_now)
    
    # Project status
    status = Column(
        SQLEnum(ProjectStatus), 
        nullable=False, 
        default=ProjectStatus.CREATED,
        index=True
    )
    
    # Configuration fields
    requirements_path = Column(String, nullable=True)  # Relative path to requirements.txt
    startup_file = Column(String, nullable=True)       # Relative path to startup file (Python or shell script)
    auto_start = Column(Boolean, nullable=False, default=False)  # Enable auto-start on boot
    system_dependencies = Column(String, nullable=True)  # Comma-separated list of system packages (e.g., "ffmpeg,imagemagick")
    
    # Container information (container_id is the Docker internal ID, project.id is the container name)
    container_id = Column(String, nullable=True)  # Docker container ID (if deployed)
    
    # Error tracking fields
    errors_silenced = Column(Boolean, nullable=False, default=False)  # Whether error status is silenced
    last_error_acknowledged_at = Column(UtcDateTime, nullable=True)  # When errors were last acknowledged
    last_error_message = Column(String, nullable=True)  # Last deployment/runtime error message
    last_modification_at = Column(UtcDateTime, nullable=True)  # Last content/config modification time
    last_config_change_at = Column(UtcDateTime, nullable=True)  # Last configuration/structure modification time (requires redeploy)

    @staticmethod
    def generate_id_from_name(name: str) -> str:
        """
        Generate a Docker-safe ID from a project name.
        
        Docker container names must be:
        - 1-63 characters long
        - Only lowercase letters, digits, hyphens, underscores, and periods
        - Cannot start with a hyphen or period
        
        Args:
            name: The project name to sanitize
            
        Returns:
            Docker-safe string: sanitized-name-xxxx (with 4-char unique suffix)
        """
        # Generate a short unique suffix (4 chars)
        short_uuid = str(uuid.uuid4()).replace('-', '')[:4]
        
        # Sanitize the project name
        sanitized = Project._sanitize_name(name)
        
        # Combine: sanitized name + suffix
        # Leave room for the suffix (-xxxx = 5 chars)
        max_name_length = 63 - 5  # 58 chars max for the name part
        if len(sanitized) > max_name_length:
            sanitized = sanitized[:max_name_length].rstrip('-')
        
        project_id = f"{sanitized}-{short_uuid}"
        
        return project_id
    
    @staticmethod
    def _sanitize_name(name: str) -> str:
        """
        Sanitize a project name for use in Docker container names.
        
        Args:
            name: Original project name
            
        Returns:
            Docker-safe sanitized name
        """
        if not name or not name.strip():
            return "project"
        
        # Normalize Unicode and remove accents
        normalized = unicodedata.normalize('NFKD', name)
        ascii_name = ''.join(c for c in normalized if not unicodedata.combining(c))
        
        # Convert to lowercase
        sanitized = ascii_name.lower()
        
        # Replace spaces and invalid characters with hyphens
        sanitized = re.sub(r'[^a-z0-9]+', '-', sanitized)
        
        # Remove leading/trailing hyphens
        sanitized = sanitized.strip('-')
        
        # Collapse multiple hyphens
        sanitized = re.sub(r'-+', '-', sanitized)
        
        # Ensure it starts with alphanumeric
        if sanitized and not sanitized[0].isalnum():
            sanitized = 'p' + sanitized
        
        # Fallback if empty
        if not sanitized:
            sanitized = "project"
        
        return sanitized

    def __repr__(self):
        return f"<Project(id='{self.id}', name='{self.name}', status='{self.status.value}')>"

    def to_dict(self):
        """Convert model instance to dictionary for API responses."""
        def serialize_datetime(dt):
            """Serialize datetime to ISO format with UTC timezone if naive."""
            if dt is None:
                return None
            # If datetime is naive, assume it's UTC and add timezone info
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        
        return {
            "id": self.id,
            "name": self.name,
            "created_at": serialize_datetime(self.created_at),
            "updated_at": serialize_datetime(self.updated_at),
            "status": self.status.value,
            "requirements_path": self.requirements_path,
            "startup_file": self.startup_file,
            "auto_start": self.auto_start,
            "system_dependencies": self.system_dependencies,
            "container_id": self.container_id,
            "errors_silenced": self.errors_silenced,
            "last_error_acknowledged_at": serialize_datetime(self.last_error_acknowledged_at),
            "last_error_message": self.last_error_message,
            "last_modification_at": serialize_datetime(self.last_modification_at),
            "last_config_change_at": serialize_datetime(self.last_config_change_at),
        }