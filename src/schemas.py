"""
Pydantic schemas for API request/response models.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime
from enum import Enum

from .models import ProjectStatus


class ProjectCreate(BaseModel):
    """Schema for creating a new project."""
    name: str = Field(..., min_length=1, max_length=50, description="Project name (1-50 characters)")
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate and clean project name."""
        if not isinstance(v, str):
            raise ValueError('Project name must be a string')
        
        # Strip leading and trailing whitespace
        cleaned_name = v.strip()
        
        # Check if name is empty or only whitespace
        if not cleaned_name:
            raise ValueError('Project name cannot be empty or contain only spaces')
        
        # Check length after trimming
        if len(cleaned_name) > 50:
            raise ValueError('Project name cannot exceed 50 characters')
        
        return cleaned_name


class ProjectUpdate(BaseModel):
    """Schema for updating project metadata."""
    name: Optional[str] = Field(None, min_length=1, max_length=50, description="Project name (1-50 characters)")
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        """Validate and clean project name."""
        if v is None:
            return v
        
        if not isinstance(v, str):
            raise ValueError('Project name must be a string')
        
        # Strip leading and trailing whitespace
        cleaned_name = v.strip()
        
        # Check if name is empty or only whitespace
        if not cleaned_name:
            raise ValueError('Project name cannot be empty or contain only spaces')
        
        # Check length after trimming
        if len(cleaned_name) > 50:
            raise ValueError('Project name cannot exceed 50 characters')
        
        return cleaned_name


class ProjectRename(BaseModel):
    """Schema for renaming a project (changes project ID, container name, and directory)."""
    name: str = Field(..., min_length=1, max_length=50, description="New project name (1-50 characters)")
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate and clean project name."""
        if not isinstance(v, str):
            raise ValueError('Project name must be a string')
        
        # Strip leading and trailing whitespace
        cleaned_name = v.strip()
        
        # Check if name is empty or only whitespace
        if not cleaned_name:
            raise ValueError('Project name cannot be empty or contain only spaces')
        
        # Check length after trimming
        if len(cleaned_name) > 50:
            raise ValueError('Project name cannot exceed 50 characters')
        
        return cleaned_name


class ProjectRenameResponse(BaseModel):
    """Schema for project rename response with old and new IDs."""
    old_id: str
    new_id: str
    name: str
    message: str


class ProjectConfig(BaseModel):
    """Schema for project configuration."""
    requirements_path: Optional[str] = Field(None, description="Relative path to requirements.txt")
    startup_file: Optional[str] = Field(None, description="Relative path to startup file (Python or shell script)")
    auto_start: bool = Field(False, description="Enable auto-start on boot")
    system_dependencies: Optional[str] = Field(None, description="Comma-separated list of system packages (e.g., 'ffmpeg,imagemagick')")
    python_version: Optional[str] = Field("3.11", description="Python version (e.g., '3.11')")
    
    @field_validator('python_version')
    @classmethod
    def validate_python_version(cls, v: Optional[str]) -> Optional[str]:
        """Validate Python version format."""
        if v is None:
            return v
        
        import re
        if not re.match(r'^3\.\d+$', v):
            raise ValueError("Python version must be in format '3.x'")
            
        return v
    
    @field_validator('system_dependencies')
    @classmethod
    def validate_system_dependencies(cls, v: Optional[str]) -> Optional[str]:
        """Validate system dependencies format."""
        if v is None or v.strip() == "":
            return None
        
        # Clean up the string
        cleaned = v.strip()
        
        # Split by comma and validate each package name
        packages = [pkg.strip() for pkg in cleaned.split(',') if pkg.strip()]
        
        # Basic validation - package names should be alphanumeric with hyphens/underscores
        import re
        for pkg in packages:
            if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9\-_]*$', pkg):
                raise ValueError(f'Invalid package name: {pkg}. Package names should contain only letters, numbers, hyphens, and underscores.')
        
        return ','.join(packages) if packages else None


class ProjectResponse(BaseModel):
    """Schema for project API responses."""
    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    status: ProjectStatus
    requirements_path: Optional[str] = None
    startup_file: Optional[str] = None
    auto_start: bool = False
    system_dependencies: Optional[str] = None
    python_version: str = "3.11"
    container_id: Optional[str] = None
    errors_silenced: bool = False
    last_error_acknowledged_at: Optional[datetime] = None
    last_error_message: Optional[str] = None
    last_modification_at: Optional[datetime] = None
    last_config_change_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ProjectListResponse(BaseModel):
    """Schema for project list API response."""
    projects: list[ProjectResponse]
    total: int


class FileInfo(BaseModel):
    """Schema for file information."""
    name: str
    path: str
    is_directory: bool
    size: int
    modified_at: datetime
    children: Optional[list['FileInfo']] = None


class FileTreeResponse(BaseModel):
    """Schema for file tree API response."""
    files: list[FileInfo]


class DeploymentResult(BaseModel):
    """Schema for deployment operation results."""
    success: bool
    container_id: Optional[str] = None
    error_message: Optional[str] = None
    logs: Optional[str] = None


class ContainerStatus(BaseModel):
    """Schema for container status information."""
    status: ProjectStatus
    container_id: Optional[str] = None
    is_running: bool = False
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    image_created_at: Optional[str] = None


class LogsResponse(BaseModel):
    """Schema for container logs response."""
    logs: str
    container_id: Optional[str] = None


class DockerExecCommand(BaseModel):
    """Schema for docker exec command response."""
    command: str
    container_name: str
    container_id: Optional[str] = None


# Update forward references
FileInfo.model_rebuild()