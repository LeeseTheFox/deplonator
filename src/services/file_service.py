"""
File service for managing project files and directories.

Handles file uploads, directory management, and file tree operations
for bot projects with proper path validation and security.
"""

import os
import shutil
import zipfile
import io
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from fastapi import UploadFile

from ..schemas import FileInfo


class FileService:
    """Service class for managing project files and directories."""
    
    def __init__(self, base_data_dir: str = "data"):
        """
        Initialize the file service.
        
        Args:
            base_data_dir: Base directory for all project data
        """
        self.base_data_dir = base_data_dir
    
    def upload_files(self, project_id: str, files: List[UploadFile]) -> List[FileInfo]:
        """
        Upload files to a project's directory with path preservation.
        
        Args:
            project_id: Unique project identifier
            files: List of uploaded files with their paths
            
        Returns:
            List of FileInfo objects for uploaded files
            
        Raises:
            ValueError: If upload fails or paths are invalid
        """
        project_files_dir = self._get_project_files_directory(project_id)
        
        # Ensure project files directory exists
        os.makedirs(project_files_dir, exist_ok=True)
        
        uploaded_files = []
        
        try:
            for file in files:
                # Get filename, handling empty or None filenames
                filename = file.filename or "unnamed_file"
                
                # Validate and sanitize the file path
                try:
                    safe_path = self._sanitize_path(filename)
                except ValueError as e:
                    # Skip files with invalid paths and continue with others
                    print(f"Skipping file with invalid path '{filename}': {e}")
                    continue
                
                # Create full file path
                file_path = os.path.join(project_files_dir, safe_path)
                
                # Create parent directories if they don't exist
                parent_dir = os.path.dirname(file_path)
                if parent_dir != project_files_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                
                # Write file content
                try:
                    with open(file_path, "wb") as f:
                        content = file.file.read()
                        f.write(content)
                    
                    # Create FileInfo object
                    file_info = self._create_file_info(file_path, safe_path)
                    uploaded_files.append(file_info)
                    
                except Exception as e:
                    print(f"Failed to write file '{safe_path}': {e}")
                    # Clean up the failed file if it was partially created
                    if os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except:
                            pass
                    continue
                
                # Reset file pointer for potential reuse
                try:
                    file.file.seek(0)
                except:
                    pass  # Ignore seek errors
            
            if not uploaded_files:
                raise ValueError("No files were successfully uploaded")
            
            return uploaded_files
            
        except Exception as e:
            # Clean up any partially uploaded files
            for file_info in uploaded_files:
                file_path = os.path.join(project_files_dir, file_info.path)
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except:
                        pass  # Ignore cleanup errors
            
            # Re-raise if it's already a ValueError, otherwise wrap it
            if isinstance(e, ValueError):
                raise
            else:
                raise ValueError(f"Failed to upload files: {str(e)}")
    
    def list_files(self, project_id: str) -> List[FileInfo]:
        """
        List all files in a project as a tree structure.
        
        Args:
            project_id: Unique project identifier
            
        Returns:
            List of FileInfo objects representing the file tree
        """
        project_files_dir = self._get_project_files_directory(project_id)
        
        if not os.path.exists(project_files_dir):
            return []
        
        return self._build_file_tree(project_files_dir, "")
    
    def delete_file(self, project_id: str, relative_path: str) -> bool:
        """
        Delete a file or folder from the project directory.
        
        Args:
            project_id: Unique project identifier
            relative_path: Relative path to the file/folder within the project
            
        Returns:
            True if file/folder was deleted, False if not found
            
        Raises:
            ValueError: If deletion fails or path is invalid
        """
        try:
            # Validate and sanitize the path
            safe_path = self._sanitize_path(relative_path)
            
            project_files_dir = self._get_project_files_directory(project_id)
            file_path = os.path.join(project_files_dir, safe_path)
            
            # Ensure the path is within the project directory (security check)
            if not self._is_safe_path(file_path, project_files_dir):
                raise ValueError(f"Invalid path: {relative_path}")
            
            if not os.path.exists(file_path):
                return False
            
            # Delete file or directory
            if os.path.isdir(file_path):
                shutil.rmtree(file_path)
            else:
                os.remove(file_path)
            
            return True
            
        except Exception as e:
            if "Invalid path" in str(e):
                raise  # Re-raise validation errors
            raise ValueError(f"Failed to delete file: {str(e)}")
    
    def get_file_path(self, project_id: str, relative_path: str) -> Path:
        """
        Get the absolute path to a file within a project.
        
        Args:
            project_id: Unique project identifier
            relative_path: Relative path to the file within the project
            
        Returns:
            Absolute Path object to the file
            
        Raises:
            ValueError: If path is invalid
        """
        # Validate and sanitize the path
        safe_path = self._sanitize_path(relative_path)
        
        project_files_dir = self._get_project_files_directory(project_id)
        file_path = os.path.join(project_files_dir, safe_path)
        
        # Ensure the path is within the project directory (security check)
        if not self._is_safe_path(file_path, project_files_dir):
            raise ValueError(f"Invalid path: {relative_path}")
        
        return Path(file_path)
    
    def _get_project_files_directory(self, project_id: str) -> str:
        """
        Get the files directory path for a project.
        
        Args:
            project_id: Unique project identifier
            
        Returns:
            Absolute path to project files directory
        """
        return os.path.abspath(os.path.join(self.base_data_dir, "projects", project_id, "files"))
    
    def _sanitize_path(self, path: str) -> str:
        """
        Sanitize a file path to prevent directory traversal attacks while preserving spaces.
        
        This method now fully supports spaces in filenames and folder names, which is
        important for user-friendly file management and Docker compatibility.
        
        Args:
            path: Raw file path
            
        Returns:
            Sanitized path safe for use
            
        Raises:
            ValueError: If path contains invalid characters or patterns
        """
        if not path:
            raise ValueError("Path cannot be empty")
        
        # Store original path for error messages
        original_path = path
        
        # Remove any leading/trailing whitespace (but preserve internal spaces)
        path = path.strip()
        
        # Check for absolute paths (before any processing)
        if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
            raise ValueError(f"Absolute paths are not allowed: {original_path}")
        
        # Convert to forward slashes for consistency
        path = path.replace("\\", "/")
        
        # Check for directory traversal attempts
        if ".." in path:
            raise ValueError(f"Directory traversal attempts are not allowed: {original_path}")
        
        # Check for Windows drive letters (but allow colons in filenames after first character)
        if len(path) > 1 and path[1] == ":" and path[0].isalpha():
            raise ValueError(f"Drive letters are not allowed: {original_path}")
        
        # Remove any leading slashes that might have been created
        path = path.lstrip("/")
        
        # Check for empty path after sanitization
        if not path:
            raise ValueError("Path cannot be empty after sanitization")
        
        # Additional validation: ensure no path component is empty (double slashes)
        # but preserve spaces within path components
        path_parts = path.split("/")
        if any(part == "" for part in path_parts):
            # Clean up empty parts (but keep the overall structure)
            path_parts = [part for part in path_parts if part]
            path = "/".join(path_parts)
        
        # Final check for empty path
        if not path:
            raise ValueError("Path cannot be empty after sanitization")
        
        # Validate that each path component doesn't contain problematic characters
        # Allow spaces, but reject other potentially dangerous characters
        dangerous_chars = ['<', '>', ':', '"', '|', '?', '*']
        for part in path_parts:
            for char in dangerous_chars:
                if char in part:
                    raise ValueError(f"Path contains invalid character '{char}': {original_path}")
        
        return path
    
    def _is_safe_path(self, file_path: str, base_dir: str) -> bool:
        """
        Check if a file path is within the allowed base directory.
        
        Args:
            file_path: Absolute file path to check
            base_dir: Base directory that should contain the file
            
        Returns:
            True if path is safe, False otherwise
        """
        try:
            # Resolve both paths to handle any symbolic links or relative components
            resolved_file_path = os.path.realpath(file_path)
            resolved_base_dir = os.path.realpath(base_dir)
            
            # Check if the file path starts with the base directory
            return resolved_file_path.startswith(resolved_base_dir + os.sep) or resolved_file_path == resolved_base_dir
        except:
            return False
    
    def _build_file_tree(self, base_dir: str, relative_path: str) -> List[FileInfo]:
        """
        Recursively build a file tree structure.
        
        Args:
            base_dir: Base directory to scan
            relative_path: Current relative path from project root
            
        Returns:
            List of FileInfo objects representing the directory contents
        """
        items = []
        current_dir = os.path.join(base_dir, relative_path) if relative_path else base_dir
        
        if not os.path.exists(current_dir):
            return items
        
        try:
            # Get all items in the current directory
            for item_name in sorted(os.listdir(current_dir)):
                item_path = os.path.join(current_dir, item_name)
                item_relative_path = os.path.join(relative_path, item_name) if relative_path else item_name
                
                # Create FileInfo object
                file_info = self._create_file_info(item_path, item_relative_path)
                
                # If it's a directory, recursively get its contents
                if file_info.is_directory:
                    file_info.children = self._build_file_tree(base_dir, item_relative_path)
                
                items.append(file_info)
        
        except PermissionError:
            # Skip directories we can't read
            pass
        
        return items
    
    def _create_file_info(self, file_path: str, relative_path: str) -> FileInfo:
        """
        Create a FileInfo object from a file path.
        
        Args:
            file_path: Absolute path to the file
            relative_path: Relative path from project root
            
        Returns:
            FileInfo object with file metadata
        """
        stat = os.stat(file_path)
        is_directory = os.path.isdir(file_path)
        
        # Use timezone-aware datetime with local timezone
        modified_time = datetime.fromtimestamp(stat.st_mtime).astimezone()
        
        return FileInfo(
            name=os.path.basename(file_path),
            path=relative_path.replace("\\", "/"),  # Normalize path separators
            is_directory=is_directory,
            size=0 if is_directory else stat.st_size,
            modified_at=modified_time,
            children=[] if is_directory else None
        )

    def create_project_zip(self, project_id: str) -> io.BytesIO:
        """
        Create a zip archive of the project's files directory.
        
        Args:
            project_id: Unique project identifier
            
        Returns:
            BytesIO object containing the zip data
            
        Raises:
            ValueError: If project directory doesn't exist or is empty
        """
        project_files_dir = self._get_project_files_directory(project_id)
        
        if not os.path.exists(project_files_dir):
            raise ValueError("Project files directory not found")
            
        # Create in-memory zip file
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            # Walk through all files in the project directory
            has_files = False
            for root, dirs, files in os.walk(project_files_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    # Calculate relative path for the archive
                    rel_path = os.path.relpath(file_path, project_files_dir)
                    zip_file.write(file_path, rel_path)
                    has_files = True
            
            # If directory is conceptually empty (no files), add an empty marker or just valid zip
            # But normally we expect at least requirements.txt etc.
            if not has_files:
                # Add an empty README if totally empty to make a valid zip
                zip_file.writestr("README.txt", "This project is empty.")
                
        zip_buffer.seek(0)
        return zip_buffer


def get_file_service() -> FileService:
    """
    Factory function to create FileService instance.
    
    Returns:
        FileService instance
    """
    return FileService()