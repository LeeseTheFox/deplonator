"""
Deployment service for managing Docker container lifecycle.

Handles Docker integration including image building, container management,
and deployment workflows for Telegram bot projects.
"""

import os
import tempfile
import shutil
import re
from typing import Optional, Dict, Any, Generator, List
from pathlib import Path
from datetime import datetime, timezone
import docker
from docker.errors import DockerException, BuildError, APIError
from sqlalchemy.orm import Session

from ..models import Project, ProjectStatus
from .project_service import ProjectService


class DeploymentResult:
    """Result of a deployment operation."""
    
    def __init__(self, success: bool, container_id: str = "", error_message: str = "", logs: str = ""):
        self.success = success
        self.container_id = container_id
        self.error_message = error_message
        self.logs = logs


class ContainerStatus:
    """Container status information."""
    
    def __init__(self, status: str, is_running: bool = False, container_id: str = "", started_at: str = None, image_created_at: str = None):
        self.status = status
        self.is_running = is_running
        self.container_id = container_id
        self.started_at = started_at
        self.image_created_at = image_created_at


class LogStream:
    """Container for log streaming data."""
    
    def __init__(self, logs: str = "", stream: Optional[Generator] = None):
        self.logs = logs
        self.stream = stream


class LogFilter:
    """Filter parameters for log retrieval."""
    
    def __init__(self, 
                 tail: int = 100, 
                 since: Optional[datetime] = None, 
                 until: Optional[datetime] = None,
                 search: Optional[str] = None,
                 follow: bool = False):
        self.tail = tail
        self.since = since
        self.until = until
        self.search = search
        self.follow = follow


class DeploymentService:
    """Service class for managing Docker deployments."""
    
    def __init__(self, db: Session):
        """Initialize the deployment service with database session."""
        self.db = db
        self.project_service = ProjectService(db)
        self._docker_client = None
    
    @property
    def docker_client(self):
        """Get Docker client instance, creating it if needed."""
        if self._docker_client is None:
            try:
                self._docker_client = docker.from_env()
                # Test connection
                self._docker_client.ping()
            except DockerException as e:
                raise RuntimeError(f"Failed to connect to Docker: {str(e)}")
        return self._docker_client
    
    def _resolve_host_path(self, container_path: str) -> str:
        """
        Resolve a container-internal path to a host-accessible path.
        
        When running in Docker-out-of-Docker (DooD), the panel sees paths like 
        '/app/data/...' but the Docker daemon (on the host) needs paths like 
        '/home/user/bot_panel/data/...'.
        
        This relies on the HOST_DATA_PATH env var being set in docker-compose.yml.
        
        Args:
            container_path: The absolute path inside the panel container
            
        Returns:
            The path usable by the host Docker daemon
        """
        host_data_base = os.getenv("HOST_DATA_PATH")
        
        # If HOST_DATA_PATH is not set, assume we are not running in DooD or paths map 1:1
        if not host_data_base:
            return container_path
            
        # We assume the container's data dir is at /app/data
        # and we need to replace '/app/data' with HOST_DATA_PATH
        # First, normalize paths to remove potential trailing slashes issues
        container_path = os.path.abspath(container_path)
        
        # Hardcoded check for standard DooD setup in this project
        # The internal path for data is usually /app/data
        internal_data_prefix = os.path.abspath("/app/data")
        
        if container_path.startswith(internal_data_prefix):
            rel_path = os.path.relpath(container_path, internal_data_prefix)
            host_path = os.path.join(host_data_base, rel_path)
            return host_path
            
        return container_path
    
    def deploy(self, project_id: str) -> DeploymentResult:
        """
        Deploy a project by building Docker image and starting container.
        
        Args:
            project_id: Unique project identifier (Docker-safe, used as container name)
            
        Returns:
            DeploymentResult with success status and details
        """
        try:
            # Get project and validate configuration
            project = self.project_service.get_project(project_id)
            if not project:
                return DeploymentResult(
                    success=False,
                    error_message="Project not found"
                )
            
            if not project.requirements_path or not project.startup_file:
                return DeploymentResult(
                    success=False,
                    error_message="Project not configured - missing requirements_path or startup_file"
                )
            
            # Update status to deploying
            project.status = ProjectStatus.DEPLOYING
            self.db.commit()
            
            # Stop existing container if running
            if project.container_id:
                try:
                    self._stop_container(project.container_id)
                    self._remove_container(project.container_id)
                except Exception:
                    # Ignore errors when stopping/removing old container
                    pass
            
            # Use project ID as both container name and image name
            # Project ID is already Docker-safe (format: tgbot-xxxxxxxxxxxx)
            container_name = project_id
            image_name = project_id
            
            build_result = self._build_image(project, image_name)
            
            if not build_result.success:
                project.status = ProjectStatus.FAILED
                project.last_error_message = build_result.error_message
                self.db.commit()
                return build_result
            
            # Clear any previous error message on successful build
            project.last_error_message = None
            
            # Create and start container using project ID as name
            restart_policy = "unless-stopped" if project.auto_start else "no"
            
            # Get project files directory for volume mounting
            project_dir = self.project_service._get_project_directory(project_id)
            project_files_dir = os.path.join(project_dir, "files")
            
            os.makedirs(project_files_dir, exist_ok=True)
            
            try:
                # Resolve host path for volume mounting (DooD support)
                host_files_dir = self._resolve_host_path(project_files_dir)
                
                container = self.docker_client.containers.run(
                    image_name,
                    name=container_name,
                    detach=True,
                    restart_policy={"Name": restart_policy},
                    remove=False,
                    volumes={host_files_dir: {'bind': '/app', 'mode': 'rw'}}
                )
                
                # Update project with container info
                project.container_id = container.id
                self.db.commit()
                
                # Monitor container startup to detect startup file failures
                startup_result = self._monitor_container_startup(container, project)
                
                if startup_result.success:
                    project.status = ProjectStatus.RUNNING
                    project.last_error_message = None  # Clear error on success
                    self.db.commit()
                    
                    return DeploymentResult(
                        success=True,
                        container_id=container.id,
                        logs=build_result.logs + "\n" + startup_result.logs
                    )
                else:
                    # Startup failed - update status and return failure
                    project.status = ProjectStatus.FAILED
                    project.last_error_message = startup_result.error_message
                    self.db.commit()
                    
                    return DeploymentResult(
                        success=False,
                        error_message=startup_result.error_message,
                        logs=build_result.logs + "\n" + startup_result.logs
                    )
                
            except APIError as e:
                error_msg = f"Failed to start container: {str(e)}"
                project.status = ProjectStatus.FAILED
                project.last_error_message = error_msg
                self.db.commit()
                return DeploymentResult(
                    success=False,
                    error_message=error_msg,
                    logs=build_result.logs
                )
        
        except Exception as e:
            # Ensure project status is updated on any error
            error_msg = f"Deployment failed: {str(e)}"
            try:
                project = self.project_service.get_project(project_id)
                if project:
                    project.status = ProjectStatus.FAILED
                    project.last_error_message = error_msg
                    self.db.commit()
            except Exception:
                pass
            
            return DeploymentResult(
                success=False,
                error_message=error_msg
            )
    
    def start(self, project_id: str) -> None:
        """
        Start a stopped container.
        
        Args:
            project_id: Unique project identifier
            
        Raises:
            RuntimeError: If start operation fails
        """
        project = self.project_service.get_project(project_id)
        if not project or not project.container_id:
            raise RuntimeError("Project not found or not deployed")
        
        try:
            container = self.docker_client.containers.get(project.container_id)
            
            # Check if this is a maintenance container
            # If so, switch back to normal bot mode instead of just starting the sleep loop
            labels = container.labels or {}
            if labels.get('maintenance_mode') == 'true':
                self.exit_maintenance(project_id)
                # Now that we've switched to a normal container (which is stopped),
                # we should start it as requested.
                return self.start(project_id)

            container.start()
            
            project.status = ProjectStatus.RUNNING
            self.db.commit()
            
        except APIError as e:
            raise RuntimeError(f"Failed to start container: {str(e)}")
    
    def stop(self, project_id: str) -> None:
        """
        Stop a running container.
        
        Args:
            project_id: Unique project identifier
            
        Raises:
            RuntimeError: If stop operation fails
        """
        project = self.project_service.get_project(project_id)
        if not project or not project.container_id:
            raise RuntimeError("Project not found or not deployed")
        
        try:
            container = self.docker_client.containers.get(project.container_id)
            container.stop()
            
            project.status = ProjectStatus.STOPPED
            self.db.commit()
            
        except APIError as e:
            raise RuntimeError(f"Failed to stop container: {str(e)}")
    
    def restart(self, project_id: str) -> None:
        """
        Restart a container (stop then start).
        
        Args:
            project_id: Unique project identifier
            
        Raises:
            RuntimeError: If restart operation fails
        """
        project = self.project_service.get_project(project_id)
        if not project or not project.container_id:
            raise RuntimeError("Project not found or not deployed")
        
        try:
            container = self.docker_client.containers.get(project.container_id)
            container.restart()
            
            project.status = ProjectStatus.RUNNING
            self.db.commit()
            
        except APIError as e:
            raise RuntimeError(f"Failed to restart container: {str(e)}")
    
    def redeploy(self, project_id: str) -> DeploymentResult:
        """
        Redeploy a project by rebuilding the Docker image and replacing the container.
        
        This operation stops the existing container, removes it, rebuilds the image
        with the latest project files, and starts a new container.
        
        Args:
            project_id: Unique project identifier (Docker-safe, used as container name)
            
        Returns:
            DeploymentResult with success status and details
        """
        try:
            # Get project and validate configuration
            project = self.project_service.get_project(project_id)
            if not project:
                return DeploymentResult(
                    success=False,
                    error_message="Project not found"
                )
            
            if not project.requirements_path or not project.startup_file:
                return DeploymentResult(
                    success=False,
                    error_message="Project not configured - missing requirements_path or startup_file"
                )
            
            # Update status to deploying
            project.status = ProjectStatus.DEPLOYING
            self.db.commit()
            
            # Stop and remove existing container if it exists
            if project.container_id:
                try:
                    container = self.docker_client.containers.get(project.container_id)
                    if container.status == "running":
                        container.stop()
                    container.remove()
                except Exception:
                    # Ignore errors when stopping/removing old container
                    pass
            
            # Use project ID as both container name and image name
            # Project ID is already Docker-safe (format: tgbot-xxxxxxxxxxxx)
            container_name = project_id
            image_name = project_id
            
            # Remove existing image to force rebuild
            try:
                self.docker_client.images.remove(image_name, force=True)
            except Exception:
                # Ignore if image doesn't exist
                pass
            
            # Build new Docker image
            build_result = self._build_image(project, image_name)
            
            if not build_result.success:
                project.status = ProjectStatus.FAILED
                project.last_error_message = build_result.error_message
                self.db.commit()
                return build_result
            
            # Clear any previous error message on successful build
            project.last_error_message = None
            
            # Create and start new container using project ID as name
            restart_policy = "unless-stopped" if project.auto_start else "no"
            
            # Get project files directory for volume mounting
            project_dir = self.project_service._get_project_directory(project_id)
            project_files_dir = os.path.join(project_dir, "files")
            
            os.makedirs(project_files_dir, exist_ok=True)
            
            try:
                # Resolve host path for volume mounting (DooD support)
                host_files_dir = self._resolve_host_path(project_files_dir)
                
                container = self.docker_client.containers.run(
                    image_name,
                    name=container_name,
                    detach=True,
                    restart_policy={"Name": restart_policy},
                    remove=False,
                    volumes={host_files_dir: {'bind': '/app', 'mode': 'rw', 'propagation': 'rslave'}}
                )
                
                # Update project with new container info
                project.container_id = container.id
                self.db.commit()
                
                # Monitor container startup to detect startup file failures
                startup_result = self._monitor_container_startup(container, project)
                
                if startup_result.success:
                    project.status = ProjectStatus.RUNNING
                    project.last_error_message = None  # Clear error on success
                    self.db.commit()
                    
                    return DeploymentResult(
                        success=True,
                        container_id=container.id,
                        logs=build_result.logs + "\n" + startup_result.logs
                    )
                else:
                    # Startup failed - update status and return failure
                    project.status = ProjectStatus.FAILED
                    project.last_error_message = startup_result.error_message
                    self.db.commit()
                    
                    return DeploymentResult(
                        success=False,
                        error_message=startup_result.error_message,
                        logs=build_result.logs + "\n" + startup_result.logs
                    )
                
            except APIError as e:
                error_msg = f"Failed to start container: {str(e)}"
                project.status = ProjectStatus.FAILED
                project.last_error_message = error_msg
                self.db.commit()
                return DeploymentResult(
                    success=False,
                    error_message=error_msg,
                    logs=build_result.logs
                )
        
        except Exception as e:
            # Ensure project status is updated on any error
            error_msg = f"Redeploy failed: {str(e)}"
            try:
                project = self.project_service.get_project(project_id)
                if project:
                    project.status = ProjectStatus.FAILED
                    project.last_error_message = error_msg
                    self.db.commit()
            except Exception:
                pass
            
            return DeploymentResult(
                success=False,
                error_message=error_msg
            )
            
    def start_maintenance(self, project_id: str) -> None:
        """
        Start container in maintenance mode (sleep infinity).
        This replaces the existing container with one that just sleeps,
        allowing manual access via docker exec.
        
        Args:
            project_id: Unique project identifier
            
        Raises:
            RuntimeError: If operation fails
        """
        project = self.project_service.get_project(project_id)
        if not project:
            raise RuntimeError("Project not found")

        # Stop and remove existing container to clear the command/entrypoint
        if project.container_id:
            try:
                self._stop_container(project.container_id)
                self._remove_container(project.container_id)
            except Exception:
                pass
        
        try:
            # Recreate container with sleep command
            # Use project ID as image and container name
            
            # Get project files directory for volume mounting
            project_dir = self.project_service._get_project_directory(project_id)
            project_files_dir = os.path.join(project_dir, "files")
            
            # Resolve host path for volume mounting (DooD support)
            host_files_dir = self._resolve_host_path(project_files_dir)
            
            container = self.docker_client.containers.run(
                project_id,
                name=project_id,
                command=["/bin/sh", "-c", "echo 'Maintenance Mode Started' && sleep infinity"],
                detach=True,
                labels={"maintenance_mode": "true"},
                restart_policy={"Name": "no"},  # Do not auto-restart maintenance mode
                remove=False,
                volumes={host_files_dir: {'bind': '/app', 'mode': 'rw', 'propagation': 'rslave'}}
            )
            
            project.container_id = container.id
            project.status = ProjectStatus.MAINTENANCE
            self.db.commit()
            
        except APIError as e:
            raise RuntimeError(f"Failed to start maintenance mode: {str(e)}")
            
    def exit_maintenance(self, project_id: str) -> None:
        """
        Exit maintenance mode and start the bot normally.
        This recreates the container with the default command.
        
        Args:
            project_id: Unique project identifier
            
        Raises:
            RuntimeError: If operation fails
        """
        project = self.project_service.get_project(project_id)
        if not project:
            raise RuntimeError("Project not found")
            
        # Stop and remove maintenance container
        if project.container_id:
            try:
                self._stop_container(project.container_id)
                self._remove_container(project.container_id)
            except Exception:
                pass
        
        # Create container for normal operation but DO NOT start it
        restart_policy = "unless-stopped" if project.auto_start else "no"
        
        try:
            # Create container (don't start) so it's ready to be started normally
            # No command specified - uses Dockerfile CMD
            
            # Get project files directory for volume mounting
            project_dir = self.project_service._get_project_directory(project_id)
            project_files_dir = os.path.join(project_dir, "files")
            
            # Resolve host path for volume mounting (DooD support)
            host_files_dir = self._resolve_host_path(project_files_dir)
            
            container = self.docker_client.containers.create(
                project_id,
                name=project_id,
                restart_policy={"Name": restart_policy},
                volumes={host_files_dir: {'bind': '/app', 'mode': 'rw', 'propagation': 'rslave'}}
            )
            
            project.container_id = container.id
            project.status = ProjectStatus.STOPPED
            # Clear error on success
            project.last_error_message = None
            
            self.db.commit()
            
        except APIError as e:
            project.status = ProjectStatus.FAILED
            project.last_error_message = f"Failed to create container: {str(e)}"
            self.db.commit()
            raise RuntimeError(f"Failed to create container: {str(e)}")

    
    def get_status(self, project_id: str) -> ContainerStatus:
        """
        Get container status information with error detection.
        
        Args:
            project_id: Unique project identifier
            
        Returns:
            ContainerStatus with current status
        """
        project = self.project_service.get_project(project_id)
        if not project:
            return ContainerStatus("not_found")

        if not project.container_id:
            return ContainerStatus(project.status.value)

        try:
            container = self.docker_client.containers.get(project.container_id)
            container.reload()

            is_running = container.status == "running"

            # Get container start time and image creation time if running
            started_at = None
            image_created_at = None
            
            # Helper to get image info safely
            try:
                if container.image:
                    image_id = container.image.id
                    # Sometimes container.image is just an object with id, need to inspect
                    image = self.docker_client.images.get(image_id)
                    if image and image.attrs and 'Created' in image.attrs:
                        image_created_at = image.attrs['Created']
            except Exception:
                pass # Ignore image info errors

            if is_running:
                # Get container inspect data for start time
                inspect_data = container.attrs
                if 'State' in inspect_data and 'StartedAt' in inspect_data['State']:
                    started_at = inspect_data['State']['StartedAt']
            else:
                # If not running, we can still try to get image info from the container's image
                # The container object already has the image info if we got it above
                pass

            # Check for errors in logs if container is running and errors are not silenced
            # Skip error checking if in maintenance mode
            if is_running and not project.errors_silenced and project.status != ProjectStatus.MAINTENANCE:
                has_errors = self._check_for_errors_in_logs(container, project)
                if has_errors and project.status != ProjectStatus.ERROR:
                    project.status = ProjectStatus.ERROR
                    self.db.commit()
                elif not has_errors and project.status == ProjectStatus.ERROR:
                    # No more errors detected, return to running status
                    project.status = ProjectStatus.RUNNING
                    self.db.commit()
            elif is_running and project.status == ProjectStatus.ERROR and project.errors_silenced:
                # Errors are silenced, show as running
                project.status = ProjectStatus.RUNNING
                self.db.commit()
            elif is_running and project.status != ProjectStatus.RUNNING and project.status != ProjectStatus.ERROR and project.status != ProjectStatus.MAINTENANCE:
                project.status = ProjectStatus.RUNNING
                self.db.commit()
            elif not is_running and project.status == ProjectStatus.RUNNING:
                project.status = ProjectStatus.STOPPED
                self.db.commit()
            elif not is_running and project.status == ProjectStatus.MAINTENANCE:
                # If maintenance container stops, go to STOPPED
                project.status = ProjectStatus.STOPPED
                self.db.commit()

            return ContainerStatus(
                status=container.status,
                is_running=is_running,
                container_id=container.id,
                started_at=started_at,
                image_created_at=image_created_at
            )

        except APIError:
            # Container not found, update project status
            project.status = ProjectStatus.FAILED
            self.db.commit()
            return ContainerStatus("not_found")
    
    def _check_for_errors_in_logs(self, container, project, tail_lines: int = 50) -> bool:
        """
        Check container logs for error indicators that occurred after last acknowledgment.
        
        This method intelligently filters out errors that occur during shutdown/restart
        cycles by detecting startup patterns and only reporting errors that occur
        after the most recent successful startup.
        
        Args:
            container: Docker container instance
            project: Project instance (to check last acknowledgment time)
            tail_lines: Number of recent log lines to check
            
        Returns:
            True if new errors are detected, False otherwise
        """
        try:
            # Get recent logs with timestamps
            logs = container.logs(tail=tail_lines, timestamps=True).decode('utf-8', errors='replace')
            
            # Error patterns to look for (made more specific to avoid false positives)
            error_patterns = [
                'error:',           # More specific - requires colon
                'exception:',       # More specific - requires colon  
                'traceback',
                'failed:',          # More specific - requires colon
                'fatal:',           # More specific - requires colon
                'critical:',        # More specific - requires colon
                'panic:',           # More specific - requires colon
                'abort',
                'crash',
                'connection refused',
                'permission denied',
                'no such file or directory',  # More specific
                'cannot connect to',          # More specific
                'authentication failed',
                'invalid token',
                'unauthorized access',        # More specific
                '403 forbidden',             # More specific
                '404 not found',             # More specific (HTTP error)
                '400 bad request',           # More specific
                '500 internal server error', # More specific
                '503 service unavailable'    # More specific
            ]
            
            # Patterns that indicate a bot startup/restart
            # When we see these, we know the bot has (re)started
            startup_patterns = [
                'starting',
                'started',
                'initializing',
                'initialized',
                'connected',
                'ready',
                'listening',
                'running',
                'bot is now online',
                'session initialized',
                'networktask started',
                'pingtask started',
                'handlertasks',
            ]
            
            # Patterns that indicate shutdown context - errors with these are expected
            shutdown_context_patterns = [
                'sigterm',
                'sigint',
                'sigkill',
                'sighup',
                'stop signal received',
                'shutting down',
                'shutdown',
                'exiting',
                'graceful',
                'terminating',
                'stopping',
                'attached to a different loop',  # Common async shutdown error
                'event loop is closed',
                'event loop stopped',
            ]
            
            # If we have a last acknowledgment time, only check logs after that time
            last_ack_time = project.last_error_acknowledged_at
            if last_ack_time:
                # Ensure acknowledgment time has timezone info (for backward compatibility)
                from datetime import timezone
                if last_ack_time.tzinfo is None:
                    last_ack_time = last_ack_time.replace(tzinfo=timezone.utc)
            
            # Split logs into lines
            log_lines = logs.strip().split('\n')
            
            # Find the most recent startup timestamp
            # We only care about errors that happen AFTER the bot has started
            last_startup_timestamp = None
            last_shutdown_timestamp = None
            
            for line in log_lines:
                if not line.strip():
                    continue
                line_lower = line.lower()
                log_timestamp = self._parse_log_timestamp(line)
                
                # Check for shutdown signals
                for pattern in shutdown_context_patterns:
                    if pattern in line_lower:
                        last_shutdown_timestamp = log_timestamp
                        break
                
                # Check for startup indicators
                for pattern in startup_patterns:
                    if pattern in line_lower:
                        # Only update if this startup is after the last shutdown
                        # This means the bot has restarted successfully
                        if log_timestamp:
                            if last_shutdown_timestamp is None or log_timestamp > last_shutdown_timestamp:
                                last_startup_timestamp = log_timestamp
                        break
            
            # Now check for errors, but only after the last startup
            for line in log_lines:
                if not line.strip():
                    continue
                    
                log_timestamp = self._parse_log_timestamp(line)
                
                # If we have an acknowledgment time and this log entry is older, skip it
                if last_ack_time and log_timestamp and log_timestamp <= last_ack_time:
                    continue
                
                # If we have a startup timestamp, only check errors after that
                # This filters out shutdown-related errors from before the restart
                if last_startup_timestamp and log_timestamp and log_timestamp < last_startup_timestamp:
                    continue
                
                line_lower = line.lower()
                
                # Skip DEBUG level logs entirely (they're not real errors)
                if ' - debug - ' in line_lower or 'debug:' in line_lower:
                    continue
                
                # Skip lines that contain shutdown context patterns
                # These are expected during graceful shutdown
                is_shutdown_related = any(pattern in line_lower for pattern in shutdown_context_patterns)
                if is_shutdown_related:
                    continue
                
                # Check for error patterns in this line (case insensitive)
                for pattern in error_patterns:
                    if pattern in line_lower:
                        print(f"[ErrorDetection] Pattern '{pattern}' matched in line: {line[:200]}")
                        return True
            
            return False
            
        except Exception:
            # If we can't check logs, assume no errors
            return False
    
    def get_logs(self, project_id: str, log_filter: Optional[LogFilter] = None) -> LogStream:
        """
        Get container logs with filtering and streaming support.
        
        Args:
            project_id: Unique project identifier
            log_filter: Optional filter parameters for logs
            
        Returns:
            LogStream with logs and optional stream generator
            
        Raises:
            RuntimeError: If logs cannot be retrieved
        """
        if log_filter is None:
            log_filter = LogFilter()
            
        project = self.project_service.get_project(project_id)
        if not project or not project.container_id:
            raise RuntimeError("Project not found or not deployed")
        
        try:
            container = self.docker_client.containers.get(project.container_id)
            
            # Prepare Docker logs parameters
            logs_kwargs = {
                'stdout': True,
                'stderr': True,
                'timestamps': True,
                'tail': log_filter.tail if not log_filter.follow else 'all'
            }
            
            # Add time filtering if specified
            if log_filter.since:
                logs_kwargs['since'] = log_filter.since
            if log_filter.until:
                logs_kwargs['until'] = log_filter.until
            
            if log_filter.follow:
                # Return streaming logs
                logs_kwargs['stream'] = True
                logs_kwargs['follow'] = True
                
                log_stream = container.logs(**logs_kwargs)
                filtered_stream = self._filter_log_stream(log_stream, log_filter.search)
                
                return LogStream(stream=filtered_stream)
            else:
                # Return static logs
                logs = container.logs(**logs_kwargs)
                logs_text = logs.decode('utf-8', errors='replace')
                
                # Apply search filtering if specified
                if log_filter.search:
                    logs_text = self._filter_logs_text(logs_text, log_filter.search)
                
                return LogStream(logs=logs_text)
                
        except APIError as e:
            raise RuntimeError(f"Failed to get logs: {str(e)}")
    
    def get_logs_simple(self, project_id: str, tail: int = 100, follow: bool = False) -> str:
        """
        Get container logs (simple interface for backward compatibility).
        
        Args:
            project_id: Unique project identifier
            tail: Number of lines to return from end of logs
            follow: Whether to follow log stream (not implemented yet)
            
        Returns:
            Container logs as string
            
        Raises:
            RuntimeError: If logs cannot be retrieved
        """
        log_filter = LogFilter(tail=tail, follow=follow)
        log_stream = self.get_logs(project_id, log_filter)
        
        if log_stream.stream:
            # For streaming, return empty string (client should use stream)
            return ""
        else:
            return log_stream.logs
    
    def _filter_log_stream(self, log_stream: Generator, search_term: Optional[str]) -> Generator[str, None, None]:
        """
        Filter log stream entries based on search term.
        
        Args:
            log_stream: Generator of log entries from Docker
            search_term: Optional search term to filter logs
            
        Yields:
            Filtered log entries as strings
        """
        for log_entry in log_stream:
            try:
                log_line = log_entry.decode('utf-8', errors='replace').strip()
                
                if search_term is None or search_term.lower() in log_line.lower():
                    yield log_line
                    
            except Exception:
                # Skip malformed log entries
                continue
    
    def _filter_logs_text(self, logs_text: str, search_term: str) -> str:
        """
        Filter log text based on search term.
        
        Args:
            logs_text: Complete log text
            search_term: Search term to filter logs
            
        Returns:
            Filtered log text containing only matching lines
        """
        if not search_term:
            return logs_text
        
        filtered_lines = []
        search_lower = search_term.lower()
        
        for line in logs_text.split('\n'):
            if search_lower in line.lower():
                filtered_lines.append(line)
        
        return '\n'.join(filtered_lines)
    
    def _parse_log_timestamp(self, log_line: str) -> Optional[datetime]:
        """
        Parse timestamp from Docker log line.
        
        Args:
            log_line: Log line with timestamp prefix
            
        Returns:
            Parsed datetime or None if parsing fails
        """
        try:
            # Docker log format: "2024-01-07T10:30:45.123456789Z message"
            timestamp_match = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)', log_line)
            if timestamp_match:
                timestamp_str = timestamp_match.group(1)
                # Parse ISO format timestamp - handle nanoseconds by truncating to microseconds
                if '.' in timestamp_str:
                    base_time, fractional = timestamp_str.split('.')
                    # Take only first 6 digits (microseconds) and add Z back
                    fractional = fractional[:6] + 'Z'
                    timestamp_str = base_time + '.' + fractional
                
                # Parse ISO format timestamp
                return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except Exception:
            pass
        
        return None
    
    def update_restart_policy(self, project_id: str) -> None:
        """
        Update container restart policy based on project's auto_start setting.
        
        This method updates the restart policy of an existing container to match
        the project's auto_start configuration:
        - auto_start=True: restart policy "unless-stopped"
        - auto_start=False: restart policy "no"
        
        Args:
            project_id: Unique project identifier
            
        Raises:
            RuntimeError: If update operation fails
        """
        project = self.project_service.get_project(project_id)
        if not project:
            raise RuntimeError("Project not found")
        
        if not project.container_id:
            raise RuntimeError("Project not deployed - no container to update")
        
        try:
            container = self.docker_client.containers.get(project.container_id)
            
            # Determine the restart policy based on auto_start setting
            restart_policy = "unless-stopped" if project.auto_start else "no"
            
            # Update the container's restart policy
            # Note: Docker API requires updating the container configuration
            container.update(restart_policy={"Name": restart_policy})
            
        except APIError as e:
            raise RuntimeError(f"Failed to update restart policy: {str(e)}")
        except Exception as e:
            raise RuntimeError(f"Failed to update restart policy: {str(e)}")
    
    def _build_image(self, project: Project, image_name: str) -> DeploymentResult:
        """
        Build Docker image for project.
        
        Args:
            project: Project instance
            image_name: Name for the Docker image
            
        Returns:
            DeploymentResult with build status
        """
        try:
            # Create temporary build context
            with tempfile.TemporaryDirectory() as temp_dir:
                # Copy project files to temp directory
                project_files_dir = self.project_service._get_project_directory(project.id)
                project_files_dir = os.path.join(project_files_dir, "files")
                if not os.path.exists(project_files_dir):
                    return DeploymentResult(
                        success=False,
                        error_message="Project files directory not found"
                    )
                
                # Verify required files exist
                requirements_file = os.path.join(project_files_dir, project.requirements_path)
                startup_file = os.path.join(project_files_dir, project.startup_file)
                
                if not os.path.exists(requirements_file):
                    return DeploymentResult(
                        success=False,
                        error_message=f"Requirements file not found: {project.requirements_path}"
                    )
                
                if not os.path.exists(startup_file):
                    return DeploymentResult(
                        success=False,
                        error_message=f"Startup file not found: {project.startup_file}"
                    )
                
                # Copy all files
                for item in os.listdir(project_files_dir):
                    src = os.path.join(project_files_dir, item)
                    dst = os.path.join(temp_dir, item)
                    if os.path.isdir(src):
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
                
                # Generate Dockerfile
                dockerfile_content = self._generate_dockerfile(
                    project.requirements_path,
                    project.startup_file,
                    project.system_dependencies,
                    project.python_version
                )
                
                dockerfile_path = os.path.join(temp_dir, "Dockerfile")
                with open(dockerfile_path, 'w') as f:
                    f.write(dockerfile_content)
                
                # Build image
                try:
                    build_logs = []
                    image, build_generator = self.docker_client.images.build(
                        path=temp_dir,
                        tag=image_name,
                        rm=True,
                        forcerm=True
                    )
                    
                    # Collect build logs
                    for log_entry in build_generator:
                        if 'stream' in log_entry:
                            build_logs.append(log_entry['stream'].strip())
                    
                    return DeploymentResult(
                        success=True,
                        logs='\n'.join(build_logs)
                    )
                    
                except BuildError as e:
                    error_logs = []
                    for log_entry in e.build_log:
                        if 'stream' in log_entry:
                            error_logs.append(log_entry['stream'].strip())
                    
                    # Check if this is a requirements installation failure
                    full_log = '\n'.join(error_logs)
                    if 'pip install' in full_log and ('ERROR:' in full_log or 'FAILED:' in full_log):
                        error_message = "Requirements installation failed"
                    else:
                        error_message = f"Docker build failed: {str(e)}"
                    
                    return DeploymentResult(
                        success=False,
                        error_message=error_message,
                        logs=full_log
                    )
        
        except Exception as e:
            return DeploymentResult(
                success=False,
                error_message=f"Build preparation failed: {str(e)}"
            )
    
    def _generate_dockerfile(self, requirements_path: str, startup_file: str, system_dependencies: Optional[str] = None, python_version: str = "3.11") -> str:
        """
        Generate Dockerfile content for project with proper path handling.
        
        This method properly handles file paths with spaces and special characters
        by using JSON array format for CMD instruction, which Docker parses correctly.
        It also includes build dependencies needed for packages that compile C extensions,
        and optionally includes additional system dependencies like FFmpeg.
        
        For shell script startup files (.sh), the script is made executable and run with bash.
        For Python files (.py), the script is run using the virtual environment's Python.
        
        Args:
            requirements_path: Relative path to requirements.txt
            startup_file: Relative path to startup file (Python or shell script)
            system_dependencies: Optional comma-separated list of system packages
            python_version: Selected Python version (e.g. '3.11')
            
        Returns:
            Dockerfile content as string
        """
        # Escape any double quotes in paths for JSON safety
        safe_requirements_path = requirements_path.replace('"', '\\"') if requirements_path else ""
        safe_startup_file = startup_file.replace('"', '\\"')
        
        # Determine if this is a shell script or Python file
        is_shell_script = startup_file.lower().endswith('.sh')
        
        # Build the system dependencies installation command
        base_packages = ["gcc", "g++", "make"]
        
        if system_dependencies:
            # Parse system dependencies and add to base packages
            additional_packages = [pkg.strip() for pkg in system_dependencies.split(',') if pkg.strip()]
            all_packages = base_packages + additional_packages
            print(f"[DeploymentService] Building with system dependencies: {additional_packages}")
        else:
            all_packages = base_packages
            print("[DeploymentService] Building with default packages only (no custom system dependencies)")
        
        # Create the apt-get install command
        packages_str = " \\\n    ".join(all_packages)
        
        # Generate appropriate CMD based on startup file type
        if is_shell_script:
            # For shell scripts, make it executable and run with bash
            startup_cmd = f'CMD ["/bin/bash", "{safe_startup_file}"]'
            make_executable = f'RUN chmod +x "{safe_startup_file}"'
        else:
            # For Python files, run with the virtual environment's Python
            startup_cmd = f'CMD ["/opt/venv/bin/python", "{safe_startup_file}"]'
            make_executable = ""
        
        # Build requirements installation section (only if requirements_path is provided)
        if requirements_path:
            requirements_section = f"""# Create virtual environment and install dependencies
# Use /opt/venv to avoid conflict with /app volume mount
RUN python -m venv /opt/venv && \\
    /opt/venv/bin/pip install --upgrade pip && \\
    /opt/venv/bin/pip install -r "{safe_requirements_path}"
"""
        else:
            requirements_section = """# Create virtual environment (no requirements specified)
RUN python -m venv /opt/venv && \\
    /opt/venv/bin/pip install --upgrade pip
"""
        
        # Build the make executable section if needed
        make_executable_section = f"\n{make_executable}\n" if make_executable else ""
        
        return f"""FROM python:{python_version}-slim

WORKDIR /app

# Install system dependencies needed for building Python packages and additional dependencies
RUN apt-get update && apt-get install -y \\
    {packages_str} \\
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY . /app/

{requirements_section}{make_executable_section}
# Set the startup command using JSON array format for proper space handling
{startup_cmd}
"""

    
    def _stop_container(self, container_id: str) -> None:
        """Stop container by ID, ignoring errors."""
        try:
            container = self.docker_client.containers.get(container_id)
            container.stop()
        except Exception:
            pass
    
    def _remove_container(self, container_id: str) -> None:
        """Remove container by ID, ignoring errors."""
        try:
            container = self.docker_client.containers.get(container_id)
            container.remove()
        except Exception:
            pass
    
    def _remove_image(self, image_name: str) -> None:
        """Remove Docker image by name, ignoring errors."""
        try:
            self.docker_client.images.remove(image_name, force=True)
        except Exception:
            pass
    
    def cleanup_project(self, project_id: str) -> None:
        """
        Clean up all Docker resources for a project.
        
        This method stops and removes the container, then removes the Docker image.
        Used when deleting a project to ensure no orphaned Docker resources remain.
        
        Args:
            project_id: Unique project identifier (also used as container/image name)
        """
        project = self.project_service.get_project(project_id)
        
        # Stop and remove container if it exists
        if project and project.container_id:
            self._stop_container(project.container_id)
            self._remove_container(project.container_id)
        
        # Also try to remove by project_id as container name (in case container_id is stale)
        try:
            container = self.docker_client.containers.get(project_id)
            if container.status == "running":
                container.stop()
            container.remove()
        except Exception:
            pass
        
        # Remove the Docker image (project_id is used as image name)
        self._remove_image(project_id)
    
    def rename_container(self, old_project_id: str, new_project_id: str) -> None:
        """
        Rename Docker container and image for a project.
        
        This method:
        1. Renames the existing container to the new project ID
        2. Re-tags the Docker image with the new project ID
        3. Removes the old image tag
        
        Note: Container must be stopped before renaming.
        
        Args:
            old_project_id: Current project ID (container/image name)
            new_project_id: New project ID (new container/image name)
            
        Raises:
            RuntimeError: If rename operation fails
        """
        try:
            # Try to get container by old project ID (container name)
            try:
                container = self.docker_client.containers.get(old_project_id)
                
                # Verify container is stopped
                if container.status == "running":
                    raise RuntimeError("Container must be stopped before renaming")
                
                # Rename container
                container.rename(new_project_id)
                
            except APIError as e:
                if "No such container" not in str(e):
                    raise RuntimeError(f"Failed to rename container: {str(e)}")
                # Container doesn't exist, that's okay
            
            # Re-tag the Docker image
            try:
                old_image = self.docker_client.images.get(old_project_id)
                # Tag with new name
                old_image.tag(new_project_id)
                # Remove old tag
                self.docker_client.images.remove(old_project_id, force=False)
            except APIError as e:
                if "No such image" not in str(e):
                    # Log but don't fail - image might not exist yet
                    print(f"Warning: Could not re-tag image: {str(e)}")
            
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to rename Docker resources: {str(e)}")
    
    def _monitor_container_startup(self, container, project: Project, timeout: int = 10) -> DeploymentResult:
        """
        Monitor container startup to detect startup file execution failures.
        
        Args:
            container: Docker container instance
            project: Project instance
            timeout: Maximum time to wait for startup (seconds)
            
        Returns:
            DeploymentResult indicating startup success or failure
        """
        import time
        
        try:
            # Wait a moment for container to start
            time.sleep(2)
            
            # Check container status
            container.reload()
            
            # If container exited immediately, it's likely a startup failure
            if container.status == "exited":
                # Get container logs to understand the failure
                logs = container.logs(timestamps=True).decode('utf-8', errors='replace')
                
                # Check exit code
                exit_code = container.attrs.get('State', {}).get('ExitCode', 0)
                
                if exit_code != 0:
                    return DeploymentResult(
                        success=False,
                        error_message=f"Startup file failed to execute (exit code: {exit_code})",
                        logs=logs
                    )
            
            # Wait a bit more and check again to ensure stability
            time.sleep(3)
            container.reload()
            
            if container.status == "running":
                # Container is running successfully
                logs = container.logs(timestamps=True).decode('utf-8', errors='replace')
                return DeploymentResult(
                    success=True,
                    logs=logs
                )
            elif container.status == "exited":
                # Container exited after brief run - likely startup failure
                logs = container.logs(timestamps=True).decode('utf-8', errors='replace')
                exit_code = container.attrs.get('State', {}).get('ExitCode', 0)
                
                return DeploymentResult(
                    success=False,
                    error_message=f"Startup file failed to execute (exit code: {exit_code})",
                    logs=logs
                )
            else:
                # Container in unexpected state
                logs = container.logs(timestamps=True).decode('utf-8', errors='replace')
                return DeploymentResult(
                    success=False,
                    error_message=f"Container in unexpected state: {container.status}",
                    logs=logs
                )
                
        except Exception as e:
            return DeploymentResult(
                success=False,
                error_message=f"Failed to monitor container startup: {str(e)}"
            )


def get_deployment_service(db: Session) -> DeploymentService:
    """
    Factory function to create DeploymentService instance.
    
    Args:
        db: Database session
        
    Returns:
        DeploymentService instance
    """
    return DeploymentService(db)