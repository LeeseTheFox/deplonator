"""
Project service for managing bot projects.

Handles CRUD operations for projects including creation, retrieval,
updates, and deletion with proper cleanup.
"""

import os
import re
import shutil
import unicodedata
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from ..models import Project, ProjectStatus, utc_now
from ..schemas import ProjectCreate, ProjectUpdate, ProjectConfig
from ..database import get_db


class ProjectService:
    """Service class for managing bot projects."""
    
    def __init__(self, db: Session):
        """Initialize the project service with a database session."""
        self.db = db
    
    def touch_project(self, project_id: str) -> None:
        """
        Update the project's updated_at timestamp.
        
        Args:
            project_id: Unique project identifier
        """
        project = self.get_project(project_id)
        if project:
            # Update modification timestamp (updated_at will be updated automatically)
            project.last_modification_at = utc_now()
            self.db.commit()
    
    def _check_duplicate_name(self, name: str, exclude_project_id: str = None) -> None:
        """
        Check if a project name already exists.
        
        Args:
            name: Project name to check
            exclude_project_id: Project ID to exclude from check (for updates)
            
        Raises:
            ValueError: If a project with the same name exists
        """
        query = self.db.query(Project).filter(Project.name == name)
        if exclude_project_id:
            query = query.filter(Project.id != exclude_project_id)
        
        existing = query.first()
        if existing:
            raise ValueError(f"A project with the name '{name}' already exists")
    
    def create_project(self, project_data: ProjectCreate) -> Project:
        """
        Create a new project with Docker-safe ID derived from project name.
        
        Args:
            project_data: Project creation data containing name
            
        Returns:
            Created project instance
            
        Raises:
            ValueError: If project creation fails or name already exists
        """
        try:
            # Check for duplicate project name
            self._check_duplicate_name(project_data.name)
            
            # Generate Docker-safe ID from project name
            project_id = Project.generate_id_from_name(project_data.name)
            
            # Create new project instance with generated ID
            project = Project(
                id=project_id,
                name=project_data.name,
                status=ProjectStatus.CREATED,
                last_modification_at=utc_now(),
                last_config_change_at=utc_now()
            )
            
            # Add to database
            self.db.add(project)
            self.db.commit()
            self.db.refresh(project)
            
            # Create project storage directory
            project_dir = self._get_project_directory(project.id)
            os.makedirs(project_dir, exist_ok=True)
            
            # Create files subdirectory
            files_dir = os.path.join(project_dir, "files")
            os.makedirs(files_dir, exist_ok=True)
            
            return project
            
        except IntegrityError as e:
            self.db.rollback()
            raise ValueError(f"Failed to create project: {str(e)}")
        except Exception as e:
            self.db.rollback()
            # Clean up any created directories
            if 'project' in locals() and project.id:
                project_dir = self._get_project_directory(project.id)
                if os.path.exists(project_dir):
                    shutil.rmtree(project_dir, ignore_errors=True)
            raise ValueError(f"Failed to create project: {str(e)}")
    
    def get_project(self, project_id: str) -> Optional[Project]:
        """
        Get a single project by ID.
        
        Args:
            project_id: Unique project identifier
            
        Returns:
            Project instance if found, None otherwise
        """
        return self.db.query(Project).filter(Project.id == project_id).first()
    
    def list_projects(self, sort_by: str = "status") -> List[Project]:
        """
        List all projects with optional sorting.
        
        Args:
            sort_by: Sorting option - "name", "date_newest", "date_oldest", "status"
        
        Returns:
            List of all project instances sorted according to sort_by parameter
        """
        query = self.db.query(Project)
        
        if sort_by == "name":
            query = query.order_by(Project.name.asc())
        elif sort_by == "date_oldest":
            query = query.order_by(Project.created_at.asc())
        elif sort_by == "status":
            # Custom order: running first, error second, stopped next, then the rest
            # Using CASE statement for custom ordering
            from sqlalchemy import case
            status_order = case(
                (Project.status == ProjectStatus.RUNNING, 1),
                (Project.status == ProjectStatus.ERROR, 2),
                (Project.status == ProjectStatus.STOPPED, 3),
                (Project.status == ProjectStatus.CONFIGURED, 4),
                (Project.status == ProjectStatus.DEPLOYING, 5),
                (Project.status == ProjectStatus.CREATED, 6),
                (Project.status == ProjectStatus.FAILED, 7),
                else_=8
            )
            query = query.order_by(status_order, Project.name.asc())
        else:  # default: "date_newest"
            query = query.order_by(Project.created_at.desc())
        
        return query.all()
    
    def update_project(self, project_id: str, updates: ProjectUpdate) -> Optional[Project]:
        """
        Update project metadata (rename).
        
        Args:
            project_id: Unique project identifier
            updates: Project update data
            
        Returns:
            Updated project instance if found, None otherwise
            
        Raises:
            ValueError: If update fails or new name already exists
        """
        try:
            project = self.get_project(project_id)
            if not project:
                return None
            
            # Apply updates - only update fields that are provided
            if updates.name is not None and updates.name != project.name:
                # Check for duplicate project name
                self._check_duplicate_name(updates.name, exclude_project_id=project_id)
                
                project.name = updates.name
                
            project.last_modification_at = utc_now()
            
            self.db.commit()
            self.db.refresh(project)
            
            return project
            
        except IntegrityError as e:
            self.db.rollback()
            raise ValueError(f"Failed to update project: {str(e)}")
        except ValueError:
            # Re-raise validation errors as-is
            self.db.rollback()
            raise
        except Exception as e:
            self.db.rollback()
            raise ValueError(f"Failed to update project: {str(e)}")
    
    def delete_project(self, project_id: str) -> bool:
        """
        Delete project with complete cleanup.
        
        This method:
        1. Stops any running container and removes it
        2. Removes the Docker image
        3. Removes all associated files and directories
        4. Deletes the project record from database
        
        Args:
            project_id: Unique project identifier
            
        Returns:
            True if project was deleted, False if not found
            
        Raises:
            ValueError: If deletion fails
        """
        try:
            project = self.get_project(project_id)
            if not project:
                return False
            
            # Clean up Docker resources (stop container, remove container and image)
            try:
                from .deployment_service import DeploymentService
                deployment_service = DeploymentService(self.db)
                deployment_service.cleanup_project(project_id)
            except Exception as e:
                # Log but don't fail deletion if Docker cleanup fails
                print(f"Warning: Docker cleanup failed for project {project_id}: {str(e)}")
            
            # Remove project storage directory
            project_dir = self._get_project_directory(project_id)
            if os.path.exists(project_dir):
                shutil.rmtree(project_dir)
            
            # Delete from database
            self.db.delete(project)
            self.db.commit()
            
            return True
            
        except Exception as e:
            self.db.rollback()
            raise ValueError(f"Failed to delete project: {str(e)}")
    
    def configure_project(self, project_id: str, config: ProjectConfig) -> Optional[Project]:
        """
        Configure project with requirements and startup file paths.
        
        This method now fully supports file paths with spaces and special characters.
        
        Args:
            project_id: Unique project identifier
            config: Project configuration data
            
        Returns:
            Updated project instance if found, None otherwise
            
        Raises:
            ValueError: If configuration is invalid or update fails
        """
        try:
            project = self.get_project(project_id)
            if not project:
                return None
            
            # Determine if any configuration requiring a redeploy is changing
            # (auto_start does not require a redeploy)
            requires_redeploy_change = False
            if config.requirements_path != project.requirements_path:
                requires_redeploy_change = True
            if config.startup_file != project.startup_file:
                requires_redeploy_change = True
            if config.system_dependencies != project.system_dependencies:
                requires_redeploy_change = True
            if config.python_version != project.python_version:
                requires_redeploy_change = True

            # Prevent configuration changes while bot is running
            # Exception: auto_start can be changed while running since it updates container policy immediately
            if project.status == ProjectStatus.RUNNING or project.status == ProjectStatus.ERROR:
                if requires_redeploy_change:
                    raise ValueError("Cannot change configuration while bot is running. Please stop the bot first.")
            
            # Validate that configured files exist if specified
            if config.requirements_path:
                req_file_path = os.path.join(
                    self._get_project_directory(project_id), 
                    "files", 
                    config.requirements_path
                )
                if not os.path.exists(req_file_path):
                    raise ValueError(f"Requirements file not found: {config.requirements_path}")
                
                # Validate it's actually a requirements file
                if not config.requirements_path.lower().endswith('.txt'):
                    raise ValueError(f"Requirements file must be a .txt file: {config.requirements_path}")
            
            if config.startup_file:
                startup_file_path = os.path.join(
                    self._get_project_directory(project_id), 
                    "files", 
                    config.startup_file
                )
                if not os.path.exists(startup_file_path):
                    raise ValueError(f"Startup file not found: {config.startup_file}")
                
                # Validate it's a Python or shell script file
                if not (config.startup_file.lower().endswith('.py') or config.startup_file.lower().endswith('.sh')):
                    raise ValueError(f"Startup file must be a Python (.py) or shell script (.sh) file: {config.startup_file}")
                
                # Additional validation: check if file is readable and appears to be a valid Python file
                try:
                    with open(startup_file_path, 'r', encoding='utf-8') as f:
                        # Just try to read the first few bytes to ensure it's readable
                        f.read(100)
                except UnicodeDecodeError:
                    raise ValueError(f"Startup file appears to be binary or corrupted: {config.startup_file}")
                except Exception as e:
                    raise ValueError(f"Cannot read startup file {config.startup_file}: {str(e)}")
            
            # Check if auto_start is changing and we have a deployed container
            auto_start_changed = (
                hasattr(config, 'auto_start') and 
                config.auto_start is not None and 
                project.auto_start != config.auto_start and
                project.container_id is not None
            )
            
            # Update configuration
            project.requirements_path = config.requirements_path
            project.startup_file = config.startup_file
            project.auto_start = config.auto_start
            project.system_dependencies = config.system_dependencies
            project.python_version = config.python_version
            
            if requires_redeploy_change:
                project.last_modification_at = utc_now()
                project.last_config_change_at = utc_now()
            
            # Update status to configured ONLY if project hasn't been deployed yet
            # Don't change status if bot is already running, stopped, or has been deployed
            if config.requirements_path and config.startup_file:
                if project.status == ProjectStatus.CREATED:
                    project.status = ProjectStatus.CONFIGURED
            
            self.db.commit()
            self.db.refresh(project)
            
            # Update container restart policy if auto_start changed and container exists
            if auto_start_changed:
                try:
                    # Import here to avoid circular imports
                    from .deployment_service import DeploymentService
                    deployment_service = DeploymentService(self.db)
                    deployment_service.update_restart_policy(project_id)
                except Exception as e:
                    # Log the error but don't fail the configuration update
                    # The restart policy can be updated later during next deployment
                    print(f"Warning: Failed to update restart policy: {str(e)}")
            
            return project
            
        except ValueError:
            # Re-raise validation errors as-is
            raise
        except Exception as e:
            self.db.rollback()
            raise ValueError(f"Failed to configure project: {str(e)}")
    
    def acknowledge_errors(self, project_id: str) -> Optional[Project]:
        """
        Acknowledge errors for a project, updating the last acknowledged timestamp.
        
        Args:
            project_id: Unique project identifier
            
        Returns:
            Updated project instance if found, None otherwise
        """
        try:
            project = self.get_project(project_id)
            if not project:
                return None
            
            from datetime import datetime, timezone
            # Store acknowledgment time in UTC with timezone info for proper comparison
            project.last_error_acknowledged_at = datetime.now(timezone.utc)
            
            # If project is in error state, return it to running
            if project.status == ProjectStatus.ERROR:
                project.status = ProjectStatus.RUNNING
            
            self.db.commit()
            self.db.refresh(project)
            
            return project
            
        except Exception as e:
            self.db.rollback()
            raise ValueError(f"Failed to acknowledge errors: {str(e)}")
    
    def toggle_error_silencing(self, project_id: str, silence: bool) -> Optional[Project]:
        """
        Toggle error silencing for a project.
        
        Args:
            project_id: Unique project identifier
            silence: Whether to silence errors (True) or enable them (False)
            
        Returns:
            Updated project instance if found, None otherwise
        """
        try:
            project = self.get_project(project_id)
            if not project:
                return None
            
            project.errors_silenced = silence
            
            # If we're unsilencing and project was in error state, check for errors again
            if not silence and project.status == ProjectStatus.RUNNING:
                # The next status check will detect errors if they exist
                pass
            elif silence and project.status == ProjectStatus.ERROR:
                # Silencing errors, return to running status
                project.status = ProjectStatus.RUNNING
            
            self.db.commit()
            self.db.refresh(project)
            
            return project
            
        except Exception as e:
            self.db.rollback()
            raise ValueError(f"Failed to toggle error silencing: {str(e)}")
    
    def rename_project(self, project_id: str, new_name: str) -> tuple[Project, str]:
        """
        Rename a project, updating its ID, directory, and Docker container name.
        
        This operation:
        1. Validates the container is stopped
        2. Generates a new Docker-safe ID from the new name
        3. Renames the project directory
        4. Renames the Docker container and image
        5. Updates the database record with new ID and name
        
        Args:
            project_id: Current unique project identifier
            new_name: New project name
            
        Returns:
            Tuple of (updated project instance, old project ID)
            
        Raises:
            ValueError: If rename fails or container is running
        """
        try:
            project = self.get_project(project_id)
            if not project:
                raise ValueError("Project not found")
            
            # Check if container is running - renaming only allowed when stopped
            if project.status == ProjectStatus.RUNNING or project.status == ProjectStatus.ERROR:
                raise ValueError("Cannot rename project while container is running. Please stop the bot first.")
            
            if project.status == ProjectStatus.DEPLOYING:
                raise ValueError("Cannot rename project while deployment is in progress.")
            
            # Check for duplicate project name
            self._check_duplicate_name(new_name, exclude_project_id=project_id)
            
            # Generate new Docker-safe ID from new name
            new_project_id = Project.generate_id_from_name(new_name)
            
            # Check if new ID already exists (unlikely but possible)
            existing = self.db.query(Project).filter(Project.id == new_project_id).first()
            if existing:
                raise ValueError(f"A project with a similar name already exists. Please choose a different name.")
            
            old_project_id = project.id
            old_project_dir = self._get_project_directory(old_project_id)
            new_project_dir = self._get_project_directory(new_project_id)
            
            # Rename Docker resources if container exists
            if project.container_id:
                try:
                    from .deployment_service import DeploymentService
                    deployment_service = DeploymentService(self.db)
                    deployment_service.rename_container(old_project_id, new_project_id)
                except Exception as e:
                    raise ValueError(f"Failed to rename Docker container: {str(e)}")
            
            # Rename project directory
            if os.path.exists(old_project_dir):
                try:
                    shutil.move(old_project_dir, new_project_dir)
                except Exception as e:
                    raise ValueError(f"Failed to rename project directory: {str(e)}")
            
            # Update database record - need to create new record and delete old one
            # because we're changing the primary key
            new_project = Project(
                id=new_project_id,
                name=new_name,
                created_at=project.created_at,
                updated_at=project.updated_at,
                status=project.status,
                requirements_path=project.requirements_path,
                startup_file=project.startup_file,
                auto_start=project.auto_start,
                system_dependencies=project.system_dependencies,
                python_version=project.python_version,
                container_id=project.container_id,
                errors_silenced=project.errors_silenced,
                last_error_acknowledged_at=project.last_error_acknowledged_at,
                last_error_message=project.last_error_message,
                last_modification_at=utc_now(),
                last_config_change_at=utc_now() # Rename changes ID/container name, effectively a config change
            )
            
            # Delete old record and add new one
            self.db.delete(project)
            self.db.add(new_project)
            self.db.commit()
            self.db.refresh(new_project)
            
            return new_project, old_project_id
            
        except ValueError:
            self.db.rollback()
            raise
        except Exception as e:
            self.db.rollback()
            raise ValueError(f"Failed to rename project: {str(e)}")

    def _get_project_directory(self, project_id: str) -> str:
        """
        Get the storage directory path for a project.
        
        Args:
            project_id: Unique project identifier
            
        Returns:
            Absolute path to project directory
        """
        return os.path.abspath(os.path.join("data", "projects", project_id))


def get_project_service(db: Session = None) -> ProjectService:
    """
    Factory function to create ProjectService instance.
    
    Args:
        db: Database session (if None, will get from dependency)
        
    Returns:
        ProjectService instance
    """
    if db is None:
        db = next(get_db())
    return ProjectService(db)