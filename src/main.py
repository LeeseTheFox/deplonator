"""
Main FastAPI application entry point for Deplonator.
"""

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import List
import uvicorn

from .database import init_db, get_db
from .services.project_service import ProjectService
from .services.file_service import FileService, get_file_service
from .services.deployment_service import DeploymentService, get_deployment_service
from .schemas import (
    ProjectCreate, ProjectUpdate, ProjectConfig, ProjectResponse, 
    ProjectListResponse, FileTreeResponse, FileInfo, DeploymentResult,
    ContainerStatus, LogsResponse, ProjectRename, ProjectRenameResponse,
    DockerExecCommand
)
from .models import ProjectStatus

# Create FastAPI application instance
app = FastAPI(
    title="Deplonator",
    description="Web-based deployment panel for managing multiple Telegram bots",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup Jinja2 templates
templates = Jinja2Templates(directory="templates")

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    """Initialize database on application startup."""
    init_db()

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint to verify the service is running."""
    return {"status": "healthy", "service": "deplonator"}

# Root endpoint - serve the web UI (now serving projects directly)
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the projects management page as the main web interface."""
    return templates.TemplateResponse(request, "projects.html")

# Projects page
@app.get("/projects", response_class=HTMLResponse)
async def projects_page(request: Request):
    """Serve the projects management page."""
    return templates.TemplateResponse(request, "projects.html")

# New project page (must come before the generic project_id route)
@app.get("/projects/new", response_class=HTMLResponse)
async def new_project_page(request: Request):
    """Serve the new project creation page."""
    return templates.TemplateResponse(request, "projects.html")

# Individual project page
@app.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail_page(request: Request, project_id: str):
    """Serve the project detail page with file management."""
    return templates.TemplateResponse(request, "project_detail.html", {"project_id": project_id})


# Project API Endpoints

@app.post("/api/projects", response_model=ProjectResponse, status_code=201)
async def create_project(
    project_data: ProjectCreate,
    db: Session = Depends(get_db)
):
    """Create a new project."""
    try:
        service = ProjectService(db)
        project = service.create_project(project_data)
        return ProjectResponse.model_validate(project)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/projects", response_model=ProjectListResponse)
async def list_projects(
    sort_by: str = "status",
    db: Session = Depends(get_db)
):
    """List all projects with optional sorting."""
    service = ProjectService(db)
    projects = service.list_projects(sort_by=sort_by)
    return ProjectListResponse(
        projects=[ProjectResponse.model_validate(p) for p in projects],
        total=len(projects)
    )


@app.get("/api/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db: Session = Depends(get_db)):
    """Get project details."""
    service = ProjectService(db)
    project = service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse.model_validate(project)


@app.put("/api/projects/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    updates: ProjectUpdate,
    db: Session = Depends(get_db)
):
    """Update project metadata (rename)."""
    try:
        service = ProjectService(db)
        project = service.update_project(project_id, updates)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return ProjectResponse.model_validate(project)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/projects/{project_id}/config", response_model=ProjectResponse)
async def configure_project(
    project_id: str,
    config: ProjectConfig,
    db: Session = Depends(get_db)
):
    """Configure project with requirements and startup file paths."""
    try:
        service = ProjectService(db)
        project = service.configure_project(project_id, config)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return ProjectResponse.model_validate(project)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/projects/{project_id}", status_code=204)
async def delete_project(project_id: str, db: Session = Depends(get_db)):
    """Delete project with complete cleanup."""
    try:
        service = ProjectService(db)
        deleted = service.delete_project(project_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Project not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/projects/{project_id}/rename", response_model=ProjectRenameResponse)
async def rename_project(
    project_id: str,
    rename_data: ProjectRename,
    db: Session = Depends(get_db)
):
    """
    Rename a project, updating its ID, directory, and Docker container name.
    
    This operation can only be performed when the container is stopped.
    The project ID (used in URLs) will change to reflect the new name.
    """
    try:
        service = ProjectService(db)
        project, old_id = service.rename_project(project_id, rename_data.name)
        return ProjectRenameResponse(
            old_id=old_id,
            new_id=project.id,
            name=project.name,
            message=f"Project renamed successfully. New URL: /projects/{project.id}"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# File API Endpoints

@app.post("/api/projects/{project_id}/files", response_model=List[FileInfo], status_code=201)
async def upload_files(
    project_id: str,
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service)
):
    """Upload files to a project."""
    # Debug logging
    print(f"Upload request received for project {project_id}")
    print(f"Number of files: {len(files)}")
    
    # Check file count limit
    if len(files) > 1000:
        raise HTTPException(status_code=400, detail="Too many files. Maximum number of files is 1000.")
    
    # Verify project exists
    project_service = ProjectService(db)
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    # Status check removed to allow hot-swapping files

    try:
        uploaded_files = file_service.upload_files(project_id, files)
        
        # Update project timestamp to indicate changes
        project_service.touch_project(project_id)
        
        print(f"Successfully uploaded {len(uploaded_files)} files")
        return uploaded_files
    except ValueError as e:
        print(f"Upload failed with ValueError: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Upload failed with Exception: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@app.get("/api/projects/{project_id}/files", response_model=FileTreeResponse)
async def list_files(
    project_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service)
):
    """List all files in a project."""
    # Verify project exists
    project_service = ProjectService(db)
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    try:
        files = file_service.list_files(project_id)
        return FileTreeResponse(files=files)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list files: {str(e)}")


@app.delete("/api/projects/{project_id}/files", status_code=204)
async def delete_file(
    project_id: str,
    path: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service)
):
    """Delete a file or folder from a project."""
    # Verify project exists
    project_service = ProjectService(db)
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    # Status check removed to allow hot-swapping files

    try:
        deleted = file_service.delete_file(project_id, path)
        
        # Update project timestamp to indicate changes
        if deleted:
            project_service.touch_project(project_id)
            
        if not deleted:
            raise HTTPException(status_code=404, detail="File not found")
    except HTTPException:
        raise  # Re-raise HTTPExceptions without wrapping
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")


@app.get("/api/projects/{project_id}/download", response_class=StreamingResponse)
async def download_project(
    project_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service)
):
    """Download the entire project as a zip file."""
    # Verify project exists
    project_service = ProjectService(db)
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    try:
        # Create zip file
        zip_buffer = file_service.create_project_zip(project_id)
        
        # Set filename for download
        filename = f"{project.name.replace(' ', '_')}.zip"
        
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create project zip: {str(e)}")


# Deployment API Endpoints

@app.post("/api/projects/{project_id}/deploy", response_model=DeploymentResult)
async def deploy_project(
    project_id: str,
    db: Session = Depends(get_db)
):
    """Deploy a bot project by building Docker image and starting container."""
    try:
        deployment_service = get_deployment_service(db)
        result = deployment_service.deploy(project_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Deployment failed: {str(e)}")


@app.post("/api/projects/{project_id}/start", status_code=204)
async def start_project(
    project_id: str,
    db: Session = Depends(get_db)
):
    """Start a stopped bot container."""
    try:
        deployment_service = get_deployment_service(db)
        
        # Check if project is in maintenance mode
        project_service = ProjectService(db)
        project = project_service.get_project(project_id)
        if project and project.status == ProjectStatus.MAINTENANCE:
            deployment_service.exit_maintenance(project_id)
        else:
            deployment_service.start(project_id)
    except RuntimeError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start project: {str(e)}")


@app.post("/api/projects/{project_id}/stop", status_code=204)
async def stop_project(
    project_id: str,
    db: Session = Depends(get_db)
):
    """Stop a running bot container."""
    try:
        deployment_service = get_deployment_service(db)
        deployment_service.stop(project_id)
    except RuntimeError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stop project: {str(e)}")


@app.post("/api/projects/{project_id}/restart", status_code=204)
async def restart_project(
    project_id: str,
    db: Session = Depends(get_db)
):
    """Restart a bot container (stop then start)."""
    try:
        deployment_service = get_deployment_service(db)
        deployment_service.restart(project_id)
    except RuntimeError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to restart project: {str(e)}")


@app.post("/api/projects/{project_id}/redeploy", response_model=DeploymentResult)
async def redeploy_project(
    project_id: str,
    db: Session = Depends(get_db)
):
    """Redeploy a bot project by rebuilding the Docker image and replacing the container."""
    try:
        deployment_service = get_deployment_service(db)
        result = deployment_service.redeploy(project_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Redeploy failed: {str(e)}")


@app.post("/api/projects/{project_id}/maintenance/start", status_code=204)
async def start_maintenance_mode(
    project_id: str,
    db: Session = Depends(get_db)
):
    """
    Start the project in maintenance mode.
    
    This replaces the running container with one that just sleeps,
    allowing you to enter it with 'docker exec' for manual configuration.
    """
    try:
        deployment_service = get_deployment_service(db)
        deployment_service.start_maintenance(project_id)
    except RuntimeError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start maintenance mode: {str(e)}")


@app.post("/api/projects/{project_id}/maintenance/exit", status_code=204)
async def exit_maintenance_mode(
    project_id: str,
    db: Session = Depends(get_db)
):
    """
    Exit maintenance mode and start the bot normally.
    This is equivalent to 'Start' but explicitly for maintenance mode.
    """
    try:
        deployment_service = get_deployment_service(db)
        deployment_service.exit_maintenance(project_id)
    except RuntimeError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to exit maintenance mode: {str(e)}")


@app.get("/api/projects/{project_id}/status", response_model=ContainerStatus)
async def get_project_status(
    project_id: str,
    db: Session = Depends(get_db)
):
    """Get the current status of a bot project and its container."""
    try:
        deployment_service = get_deployment_service(db)
        status = deployment_service.get_status(project_id)
        
        # Convert deployment service ContainerStatus to schema ContainerStatus
        from .models import ProjectStatus
        
        # Map status string to ProjectStatus enum
        if status.status == "not_found":
            project_status = ProjectStatus.FAILED
            error_message = "Container not found"
        elif status.status == "running":
            project_status = ProjectStatus.RUNNING
            error_message = None
        elif status.status in ["stopped", "exited"]:
            project_status = ProjectStatus.STOPPED
            error_message = None
        else:
            # Get project from database to use its current status
            project_service = ProjectService(db)
            project = project_service.get_project(project_id)
            if project:
                project_status = project.status
            else:
                project_status = ProjectStatus.FAILED
            error_message = f"Container status: {status.status}"
        
        return ContainerStatus(
            status=project_status,
            container_id=status.container_id if status.container_id else None,
            is_running=status.is_running,
            error_message=error_message,
            started_at=status.started_at,
            image_created_at=status.image_created_at
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get status: {str(e)}")


@app.post("/api/projects/{project_id}/acknowledge-errors", response_model=ProjectResponse)
async def acknowledge_project_errors(
    project_id: str,
    db: Session = Depends(get_db)
):
    """Acknowledge errors for a project."""
    try:
        service = ProjectService(db)
        project = service.acknowledge_errors(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return ProjectResponse.model_validate(project)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/projects/{project_id}/silence-errors", response_model=ProjectResponse)
async def toggle_project_error_silencing(
    project_id: str,
    silence: bool,
    db: Session = Depends(get_db)
):
    """Toggle error silencing for a project."""
    try:
        service = ProjectService(db)
        project = service.toggle_error_silencing(project_id, silence)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return ProjectResponse.model_validate(project)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/projects/{project_id}/logs", response_model=LogsResponse)
async def get_project_logs(
    project_id: str,
    tail: int = 100,
    db: Session = Depends(get_db)
):
    """Get container logs for a bot project."""
    try:
        deployment_service = get_deployment_service(db)
        logs = deployment_service.get_logs_simple(project_id, tail=tail, follow=False)
        
        # Get container ID for response
        project_service = ProjectService(db)
        project = project_service.get_project(project_id)
        container_id = project.container_id if project else None
        
        return LogsResponse(
            logs=logs,
            container_id=container_id
        )
    except RuntimeError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get logs: {str(e)}")


@app.get("/api/projects/{project_id}/docker-exec-command", response_model=DockerExecCommand)
async def get_docker_exec_command(
    project_id: str,
    db: Session = Depends(get_db)
):
    """
    Get a docker exec command to enter the project's container.
    
    This command can be used to enter the container from the server's terminal,
    even when the bot is not running, for manual configuration or debugging.
    """
    try:
        project_service = ProjectService(db)
        project = project_service.get_project(project_id)
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        # The project ID is used as the container name
        container_name = project.id
        
        # Generate the docker exec command
        # Use bash if available, otherwise fall back to sh
        command = f"docker exec -it {container_name} /bin/bash"
        
        return DockerExecCommand(
            command=command,
            container_name=container_name,
            container_id=project.container_id
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate command: {str(e)}")

# Development server entry point
if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=5643,
        reload=True,
        log_level="info"
    )