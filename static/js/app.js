/**
 * Deplonator - Main JavaScript Application
 */

// Global app object
window.BotDeployer = {
    // Configuration
    config: {
        apiBase: '/api',
        refreshInterval: 5000, // 5 seconds
        maxRetries: 3
    },
    
    // State management
    state: {
        currentPage: 'dashboard',
        projects: [],
        loading: false
    },
    
    // Initialize the application
    init() {
        console.log('Initializing Bot Deployer...');
        this.setupNavigation();
        this.setupEventListeners();
        this.detectCurrentPage();
        this.initializeModals(); // Initialize modal functions
        this.loadInitialData();
    },
    
    // Setup navigation highlighting
    setupNavigation() {
        const navLinks = document.querySelectorAll('.nav-link');
        const currentPath = window.location.pathname;
        
        navLinks.forEach(link => {
            const page = link.getAttribute('data-page');
            if (currentPath === '/' && page === 'dashboard') {
                link.classList.add('active');
            } else if (currentPath.includes(page) && page !== 'dashboard') {
                link.classList.add('active');
            }
        });
    },
    
    // Setup global event listeners
    setupEventListeners() {
        // Handle form submissions
        document.addEventListener('submit', this.handleFormSubmit.bind(this));
        
        // Handle button clicks
        document.addEventListener('click', this.handleButtonClick.bind(this));
        
        // Handle file uploads
        document.addEventListener('change', this.handleFileChange.bind(this));
        
        // Handle keyboard shortcuts
        document.addEventListener('keydown', this.handleKeydown.bind(this));
        
        // Handle page visibility changes
        document.addEventListener('visibilitychange', this.handleVisibilityChange.bind(this));
    },
    
    // Detect current page from URL
    detectCurrentPage() {
        const path = window.location.pathname;
        if (path === '/') {
            this.state.currentPage = 'dashboard';
        } else if (path === '/projects') {
            // Only the projects list page, not project detail pages
            this.state.currentPage = 'projects';
        } else if (path.match(/^\/projects\/[^\/]+$/)) {
            // Project detail page (e.g., /projects/uuid)
            this.state.currentPage = 'project-detail';
        }
    },
    
    // Load initial data based on current page
    async loadInitialData() {
        try {
            // Only load projects on dashboard, NOT on projects page (it handles its own loading with sort preference)
            if (this.state.currentPage === 'dashboard') {
                await this.loadProjects();
            }
            // Projects page handles its own loading in projects.html to respect sort preference
        } catch (error) {
            console.error('Failed to load initial data:', error);
            this.showAlert('Failed to load data. Please refresh the page.', 'error');
        }
    },
    
    // API Methods
    async apiCall(endpoint, options = {}) {
        const url = `${this.config.apiBase}${endpoint}`;
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json',
            }
        };
        
        const finalOptions = { ...defaultOptions, ...options };
        
        try {
            const response = await fetch(url, finalOptions);
            
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                
                // Handle different error response formats
                let errorMessage;
                
                if (response.status === 422 && errorData.detail && Array.isArray(errorData.detail)) {
                    // Pydantic validation errors (422 Unprocessable Entity)
                    const validationErrors = errorData.detail.map(err => {
                        let message = err.msg || 'Validation error';
                        
                        // Clean up Pydantic error message format
                        if (message.startsWith('Value error, ')) {
                            message = message.replace('Value error, ', '');
                        }
                        
                        // For field-specific errors, you could add field context
                        if (err.loc && err.loc.length > 0 && err.loc[0] !== 'name') {
                            message = `${err.loc.join('.')}: ${message}`;
                        }
                        
                        return message;
                    });
                    errorMessage = validationErrors.join(', ');
                } else if (errorData.detail) {
                    // Standard FastAPI error format
                    errorMessage = errorData.detail;
                } else if (errorData.message) {
                    // Alternative error format
                    errorMessage = errorData.message;
                } else {
                    // Fallback to HTTP status
                    errorMessage = `HTTP ${response.status}: ${response.statusText}`;
                }
                
                throw new Error(errorMessage);
            }
            
            // Handle empty responses (204 No Content)
            if (response.status === 204) {
                return null;
            }
            
            return await response.json();
        } catch (error) {
            console.error(`API call failed: ${endpoint}`, error);
            throw error;
        }
    },
    
    // Load projects from API
    async loadProjects(sortBy = null) {
        try {
            // Don't show loading indicator for manual refreshes to prevent UI jumping
            // this.setLoading(true);
            
            // Get sort parameter from dropdown if not provided, then localStorage, then default
            if (!sortBy) {
                const sortSelect = document.getElementById('sort-select');
                sortBy = sortSelect ? sortSelect.value : (localStorage.getItem('projectSortBy') || 'status');
            }
            
            const response = await this.apiCall(`/projects?sort_by=${sortBy}`);
            this.state.projects = response.projects || [];
            this.updateProjectsDisplay();
        } catch (error) {
            console.error('Failed to load projects:', error);
            this.showAlert('Failed to load', 'error');
        } finally {
            // this.setLoading(false);
        }
    },
    
    // Update projects display (to be implemented by specific pages)
    updateProjectsDisplay() {
        // This will be overridden by page-specific implementations
        console.log('Projects loaded:', this.state.projects);
    },
    
    // Event Handlers
    async handleFormSubmit(event) {
        const form = event.target;
        if (!form.matches('form[data-api]')) return;
        
        event.preventDefault();
        
        const endpoint = form.getAttribute('data-api');
        const method = form.getAttribute('data-method') || 'POST';
        const formData = new FormData(form);
        
        // Additional validation for project creation forms
        if (endpoint === '/projects' && method === 'POST') {
            const projectName = formData.get('name');
            if (!projectName || !projectName.trim()) {
                this.showAlert('Project name is required and cannot be empty or contain only spaces', 'error');
                return;
            }
        }
        
        try {
            this.setLoading(true);
            
            let body;
            if (form.enctype === 'multipart/form-data') {
                body = formData;
            } else {
                body = JSON.stringify(Object.fromEntries(formData));
            }
            
            const options = {
                method,
                body: form.enctype === 'multipart/form-data' ? formData : body
            };
            
            if (form.enctype !== 'multipart/form-data') {
                options.headers = { 'Content-Type': 'application/json' };
            }
            
            const result = await this.apiCall(endpoint, options);
            
            // Trigger custom event for form success - let the handler show the appropriate message
            form.dispatchEvent(new CustomEvent('formSuccess', { detail: result }));
            
        } catch (error) {
            console.error('Form submission failed:', error);
            this.showAlert(error.message, 'error');
        } finally {
            this.setLoading(false);
        }
    },
    
    async handleButtonClick(event) {
        const button = event.target.closest('button[data-action]');
        if (!button) return;
        
        // Skip handling on projects page - it has its own handler
        if (this.state.currentPage === 'projects') {
            return;
        }
        
        const action = button.getAttribute('data-action');
        const projectId = button.getAttribute('data-project-id');
        
        // Store original button content
        const originalContent = button.innerHTML;
        const originalDisabled = button.disabled;
        
        try {
            // Don't use global loading for quick actions to prevent layout shifts
            // Instead, show loading state on the button itself
            button.disabled = true;
            button.innerHTML = '⏳ ' + button.textContent.replace(/^[^\s]+\s/, '');
            
            switch (action) {
                case 'deploy':
                    await this.deployProject(projectId);
                    break;
                case 'start':
                    await this.startProject(projectId);
                    break;
                case 'stop':
                    await this.stopProject(projectId);
                    break;
                case 'restart':
                    await this.restartProject(projectId);
                    break;
                case 'delete':
                    // Don't handle delete here - it's handled by the onclick in the template
                    // This prevents double handling of the delete action
                    return;
                default:
                    console.warn('Unknown action:', action);
            }
        } catch (error) {
            console.error(`Action ${action} failed:`, error);
            this.showAlert(error.message, 'error');
        } finally {
            // Restore button state
            button.disabled = originalDisabled;
            button.innerHTML = originalContent;
        }
    },
    
    handleFileChange(event) {
        const input = event.target;
        if (!input.matches('input[type="file"]')) return;
        
        // Handle file selection display
        const fileList = input.files;
        const fileInfo = input.parentElement.querySelector('.file-info');
        
        if (fileInfo && fileList.length > 0) {
            const fileNames = Array.from(fileList).map(f => f.name).join(', ');
            fileInfo.textContent = `Selected: ${fileNames}`;
        }
    },
    
    handleKeydown(event) {
        // Handle keyboard shortcuts
        if (event.ctrlKey || event.metaKey) {
            switch (event.key) {
                case 'r':
                    // Ctrl+R: Refresh data
                    event.preventDefault();
                    this.loadInitialData();
                    break;
            }
        }
    },
    
    handleVisibilityChange() {
        // Pause/resume auto-refresh based on page visibility
        if (document.hidden) {
            this.pauseAutoRefresh();
        } else {
            this.resumeAutoRefresh();
        }
    },
    
    // Project Actions
    async deployProject(projectId) {
        const result = await this.apiCall(`/projects/${projectId}/deploy`, { method: 'POST' });
        this.showAlert('Deploying...', 'success');
        await this.loadProjects(); // Refresh project list
        return result;
    },
    
    async startProject(projectId) {
        await this.apiCall(`/projects/${projectId}/start`, { method: 'POST' });
        this.showAlert('Started successfully', 'success');
        await this.loadProjects();
    },
    
    async stopProject(projectId) {
        await this.apiCall(`/projects/${projectId}/stop`, { method: 'POST' });
        this.showAlert('Stopped successfully', 'success');
        await this.loadProjects();
    },
    
    async restartProject(projectId) {
        await this.apiCall(`/projects/${projectId}/restart`, { method: 'POST' });
        this.showAlert('Restarted successfully', 'success');
        await this.loadProjects();
    },
    
    async deleteProject(projectId) {
        await this.apiCall(`/projects/${projectId}`, { method: 'DELETE' });
        this.showAlert('Deleted successfully', 'success');
        await this.loadProjects();
    },
    
    // UI Helper Methods
    setLoading(loading) {
        this.state.loading = loading;
        document.body.classList.toggle('loading', loading);
        
        // Update loading indicators
        const loadingElements = document.querySelectorAll('.loading-indicator');
        loadingElements.forEach(el => {
            el.style.display = loading ? 'block' : 'none';
        });
    },
    
    showAlert(message, type = 'info', duration = 5000) {
        const alertContainer = document.getElementById('alert-container');
        if (!alertContainer) return;
        
        const alert = document.createElement('div');
        alert.className = `alert alert-${type}`;
        alert.style.setProperty('--duration', `${duration}ms`);
        alert.innerHTML = `
            <span>${message}</span>
            <button type="button" class="alert-close" onclick="window.BotDeployer.removeAlert(this.closest('.alert'))">&times;</button>
        `;
        
        // Add to container
        alertContainer.appendChild(alert);
        
        // Auto-remove after duration with animation
        if (duration > 0) {
            setTimeout(() => {
                this.removeAlert(alert);
            }, duration);
        }
        
        // Limit number of notifications (remove oldest if more than 5)
        const alerts = alertContainer.querySelectorAll('.alert');
        if (alerts.length > 5) {
            this.removeAlert(alerts[0]);
        }
    },
    
    removeAlert(alert) {
        if (!alert || !alert.parentElement) return;
        
        alert.classList.add('removing');
        setTimeout(() => {
            if (alert.parentElement) {
                alert.remove();
            }
        }, 300); // Match animation duration
    },
    
    // Auto-refresh functionality (disabled to prevent UI flickering)
    startAutoRefresh() {
        // Auto-refresh disabled to prevent UI jumping/flickering
        // Users can manually refresh using the refresh button or Ctrl+R
        return;
        
        if (this.refreshTimer) return;
        
        // Only auto-refresh on the projects list page, NOT on project detail page
        if (this.state.currentPage !== 'projects') return;
        
        this.refreshTimer = setInterval(() => {
            if (!document.hidden && this.state.currentPage === 'projects') {
                this.loadProjects();
            }
        }, this.config.refreshInterval);
    },
    
    pauseAutoRefresh() {
        if (this.refreshTimer) {
            clearInterval(this.refreshTimer);
            this.refreshTimer = null;
        }
    },
    
    resumeAutoRefresh() {
        // Only resume if we're on a page that should auto-refresh
        if (this.state.currentPage === 'projects') {
            this.startAutoRefresh();
        }
    },
    
    // Utility methods
    formatDate(dateString) {
        if (!dateString) return 'Never';
        return this.formatRelativeTime(dateString);
    },
    
    formatRelativeTime(dateString) {
        if (!dateString) return 'Never';
        
        const date = new Date(dateString);
        const now = new Date();
        const diffMs = now - date;
        
        if (diffMs < 0) return 'just now';
        
        const seconds = Math.floor(diffMs / 1000);
        const minutes = Math.floor(seconds / 60);
        const hours = Math.floor(minutes / 60);
        const days = Math.floor(hours / 24);
        const weeks = Math.floor(days / 7);
        const months = Math.floor(days / 30);
        
        if (seconds < 60) {
            return 'just now';
        } else if (minutes < 60) {
            return `${minutes} minute${minutes !== 1 ? 's' : ''} ago`;
        } else if (hours < 24) {
            const remainingMinutes = minutes % 60;
            if (remainingMinutes > 0) {
                return `${hours} hour${hours !== 1 ? 's' : ''} and ${remainingMinutes} minute${remainingMinutes !== 1 ? 's' : ''} ago`;
            }
            return `${hours} hour${hours !== 1 ? 's' : ''} ago`;
        } else if (days < 7) {
            const remainingHours = hours % 24;
            if (remainingHours > 0) {
                return `${days} day${days !== 1 ? 's' : ''} and ${remainingHours} hour${remainingHours !== 1 ? 's' : ''} ago`;
            }
            return `${days} day${days !== 1 ? 's' : ''} ago`;
        } else if (weeks < 4) {
            const remainingDays = days % 7;
            if (remainingDays > 0) {
                return `${weeks} week${weeks !== 1 ? 's' : ''} and ${remainingDays} day${remainingDays !== 1 ? 's' : ''} ago`;
            }
            return `${weeks} week${weeks !== 1 ? 's' : ''} ago`;
        } else {
            const remainingDays = days % 30;
            if (remainingDays > 0) {
                return `${months} month${months !== 1 ? 's' : ''} and ${remainingDays} day${remainingDays !== 1 ? 's' : ''} ago`;
            }
            return `${months} month${months !== 1 ? 's' : ''} ago`;
        }
    },
    
    formatFileDate(dateString) {
        if (!dateString) return 'Never';
        const date = new Date(dateString);
        return date.toLocaleString('en-GB', { 
            hour12: false,
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    },
    
    formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    },
    
    getStatusClass(status) {
        return `status-${status.toLowerCase()}`;
    },
    
    // Modal helper functions
    initializeModals() {
        // Ensure modal functions are available globally
        if (typeof window.showDeleteProjectModal === 'undefined') {
            window.showDeleteProjectModal = function(projectId, projectName) {
                console.log('Showing delete modal for:', projectId, projectName);
                
                // Set global variable for deletion
                window.currentDeleteProjectId = projectId;
                
                // Update modal content
                const nameElement = document.getElementById('delete-project-name');
                if (nameElement) {
                    nameElement.textContent = projectName;
                }
                
                // Show modal
                const modal = document.getElementById('delete-project-modal');
                if (modal) {
                    modal.classList.remove('hidden');
                } else {
                    // Fallback to confirm dialog
                    if (confirm(`Are you sure you want to delete "${projectName}"? This action cannot be undone.`)) {
                        window.BotDeployer.deleteProject(projectId);
                    }
                }
            };
        }

        if (typeof window.hideDeleteProjectModal === 'undefined') {
            window.hideDeleteProjectModal = function() {
                console.log('Hiding delete modal');
                const modal = document.getElementById('delete-project-modal');
                if (modal) {
                    modal.classList.add('hidden');
                }
                window.currentDeleteProjectId = null;
            };
        }

        if (typeof window.confirmDeleteProject === 'undefined') {
            window.confirmDeleteProject = async function() {
                console.log('Confirming delete for:', window.currentDeleteProjectId);
                
                if (!window.currentDeleteProjectId) {
                    console.error('No project ID set for deletion');
                    return;
                }
                
                try {
                    const deleteBtn = document.getElementById('confirm-delete-btn');
                    if (deleteBtn) {
                        deleteBtn.disabled = true;
                        deleteBtn.textContent = 'Deleting...';
                    }
                    
                    await window.BotDeployer.deleteProject(window.currentDeleteProjectId);
                    window.hideDeleteProjectModal();
                    
                } catch (error) {
                    console.error('Delete failed:', error);
                    window.BotDeployer.showAlert('Failed to delete: ' + error.message, 'error');
                } finally {
                    const deleteBtn = document.getElementById('confirm-delete-btn');
                    if (deleteBtn) {
                        deleteBtn.disabled = false;
                        deleteBtn.textContent = 'Delete project';
                    }
                }
            };
        }
    }
};

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.BotDeployer.init();
});

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = window.BotDeployer;
}