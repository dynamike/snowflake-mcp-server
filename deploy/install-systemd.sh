#!/bin/bash

# Systemd Service Installation Script for Snowflake MCP Server
# Usage: sudo ./deploy/install-systemd.sh [--user snowflake-mcp] [--install-dir /opt/snowflake-mcp-server]

set -e

# Default configuration
DEFAULT_USER="snowflake-mcp"
DEFAULT_INSTALL_DIR="/opt/snowflake-mcp-server"
SYSTEMD_DIR="/etc/systemd/system"

# Configuration
INSTALL_USER="$DEFAULT_USER"
INSTALL_DIR="$DEFAULT_INSTALL_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Help function
show_help() {
    cat << EOF
Systemd Service Installation Script for Snowflake MCP Server

Usage: sudo $0 [OPTIONS]

Options:
    --user USER         User to run the service as (default: $DEFAULT_USER)
    --install-dir DIR   Installation directory (default: $DEFAULT_INSTALL_DIR)
    --help              Show this help message

Examples:
    sudo $0                                          # Install with defaults
    sudo $0 --user mcp-user --install-dir /srv/mcp  # Custom user and directory

This script will:
1. Create a system user for the service (if it doesn't exist)
2. Set up the installation directory with proper permissions
3. Install systemd service files
4. Enable and start the HTTP service
5. Configure log rotation

EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --user)
            INSTALL_USER="$2"
            shift 2
            ;;
        --install-dir)
            INSTALL_DIR="$2"
            shift 2
            ;;
        --help)
            show_help
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check if systemd is available
    if ! command -v systemctl &> /dev/null; then
        log_error "systemd is not available on this system"
        exit 1
    fi
    
    # Check if uv is installed
    if ! command -v uv &> /dev/null; then
        log_error "uv is not installed. Please install uv first."
        log_info "Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
    
    log_success "Prerequisites check completed"
}

# Create system user
create_user() {
    log_info "Setting up system user: $INSTALL_USER"
    
    if id "$INSTALL_USER" &>/dev/null; then
        log_warning "User $INSTALL_USER already exists"
    else
        useradd --system --shell /bin/false --home-dir "$INSTALL_DIR" --create-home "$INSTALL_USER"
        log_success "Created system user: $INSTALL_USER"
    fi
}

# Setup installation directory
setup_directory() {
    log_info "Setting up installation directory: $INSTALL_DIR"
    
    # Create directory if it doesn't exist
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR/logs"
    
    # Copy application files (assumes script is run from project root)
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
    
    if [[ ! -f "$PROJECT_DIR/pyproject.toml" ]]; then
        log_error "Cannot find project root. Please run this script from the project directory."
        exit 1
    fi
    
    log_info "Copying application files from $PROJECT_DIR to $INSTALL_DIR"
    
    # Copy application files
    cp -r "$PROJECT_DIR"/* "$INSTALL_DIR/"
    
    # Set up Python virtual environment
    log_info "Setting up Python virtual environment..."
    cd "$INSTALL_DIR"
    uv venv
    uv pip install -e .
    
    # Set ownership
    chown -R "$INSTALL_USER:$INSTALL_USER" "$INSTALL_DIR"
    chmod -R 755 "$INSTALL_DIR"
    chmod -R 775 "$INSTALL_DIR/logs"
    
    log_success "Installation directory setup completed"
}

# Install systemd service files
install_services() {
    log_info "Installing systemd service files..."
    
    # Copy service files
    cp "$INSTALL_DIR/deploy/systemd/snowflake-mcp-http.service" "$SYSTEMD_DIR/"
    cp "$INSTALL_DIR/deploy/systemd/snowflake-mcp-stdio.service" "$SYSTEMD_DIR/"
    
    # Update service files with actual installation directory and user
    sed -i "s|/opt/snowflake-mcp-server|$INSTALL_DIR|g" "$SYSTEMD_DIR/snowflake-mcp-http.service"
    sed -i "s|User=snowflake-mcp|User=$INSTALL_USER|g" "$SYSTEMD_DIR/snowflake-mcp-http.service"
    sed -i "s|Group=snowflake-mcp|Group=$INSTALL_USER|g" "$SYSTEMD_DIR/snowflake-mcp-http.service"
    
    sed -i "s|/opt/snowflake-mcp-server|$INSTALL_DIR|g" "$SYSTEMD_DIR/snowflake-mcp-stdio.service"
    sed -i "s|User=snowflake-mcp|User=$INSTALL_USER|g" "$SYSTEMD_DIR/snowflake-mcp-stdio.service"
    sed -i "s|Group=snowflake-mcp|Group=$INSTALL_USER|g" "$SYSTEMD_DIR/snowflake-mcp-stdio.service"
    
    # Set permissions
    chmod 644 "$SYSTEMD_DIR/snowflake-mcp-http.service"
    chmod 644 "$SYSTEMD_DIR/snowflake-mcp-stdio.service"
    
    # Reload systemd
    systemctl daemon-reload
    
    log_success "Systemd service files installed"
}

# Configure log rotation
configure_logrotate() {
    log_info "Configuring log rotation..."
    
    cat > /etc/logrotate.d/snowflake-mcp << EOF
$INSTALL_DIR/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 644 $INSTALL_USER $INSTALL_USER
    postrotate
        systemctl reload snowflake-mcp-http 2>/dev/null || true
    endscript
}
EOF
    
    log_success "Log rotation configured"
}

# Setup environment file
setup_environment() {
    log_info "Setting up environment configuration..."
    
    if [[ ! -f "$INSTALL_DIR/.env" ]]; then
        if [[ -f "$INSTALL_DIR/.env.example" ]]; then
            cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
            chown "$INSTALL_USER:$INSTALL_USER" "$INSTALL_DIR/.env"
            chmod 600 "$INSTALL_DIR/.env"
            
            log_warning "Created .env file from template. Please edit $INSTALL_DIR/.env with your Snowflake credentials."
        else
            log_warning "No .env file found. Please create $INSTALL_DIR/.env with your configuration."
        fi
    else
        log_info "Environment file already exists: $INSTALL_DIR/.env"
    fi
}

# Enable and start services
enable_services() {
    log_info "Enabling and starting services..."
    
    # Enable HTTP service (main service)
    systemctl enable snowflake-mcp-http
    
    # Don't auto-enable stdio service (runs on-demand)
    log_info "HTTP service enabled for auto-start"
    log_info "stdio service available but not auto-enabled (runs on-demand)"
    
    # Start HTTP service if environment is configured
    if [[ -f "$INSTALL_DIR/.env" ]]; then
        log_info "Starting HTTP service..."
        systemctl start snowflake-mcp-http
        
        # Wait a moment and check status
        sleep 3
        if systemctl is-active --quiet snowflake-mcp-http; then
            log_success "HTTP service started successfully"
        else
            log_warning "HTTP service failed to start. Check logs with: journalctl -u snowflake-mcp-http"
        fi
    else
        log_warning "HTTP service not started. Configure .env file first, then run: systemctl start snowflake-mcp-http"
    fi
}

# Show status and next steps
show_status() {
    log_info "Installation completed!"
    
    echo ""
    log_info "Service Status:"
    systemctl status snowflake-mcp-http --no-pager --lines=5 || true
    
    echo ""
    log_info "Useful Commands:"
    echo "  systemctl status snowflake-mcp-http    # Check HTTP service status"
    echo "  systemctl start snowflake-mcp-http     # Start HTTP service"
    echo "  systemctl stop snowflake-mcp-http      # Stop HTTP service"
    echo "  systemctl restart snowflake-mcp-http   # Restart HTTP service"
    echo "  journalctl -u snowflake-mcp-http -f    # Follow HTTP service logs"
    echo "  systemctl start snowflake-mcp-stdio    # Start stdio service (on-demand)"
    
    echo ""
    log_info "Configuration:"
    echo "  Installation directory: $INSTALL_DIR"
    echo "  Service user: $INSTALL_USER"
    echo "  Environment file: $INSTALL_DIR/.env"
    echo "  Log files: $INSTALL_DIR/logs/"
    
    echo ""
    if [[ ! -f "$INSTALL_DIR/.env" ]] || ! grep -q "SNOWFLAKE_ACCOUNT" "$INSTALL_DIR/.env" 2>/dev/null; then
        log_warning "Next Steps:"
        echo "  1. Edit $INSTALL_DIR/.env with your Snowflake credentials"
        echo "  2. Start the service: systemctl start snowflake-mcp-http"
        echo "  3. Check the service status: systemctl status snowflake-mcp-http"
    else
        log_success "Service should be running! Check status with: systemctl status snowflake-mcp-http"
    fi
}

# Main execution
main() {
    log_info "Installing Snowflake MCP Server systemd services..."
    log_info "Installation directory: $INSTALL_DIR"
    log_info "Service user: $INSTALL_USER"
    
    check_root
    check_prerequisites
    create_user
    setup_directory
    install_services
    configure_logrotate
    setup_environment
    enable_services
    show_status
    
    log_success "Installation completed successfully!"
}

# Run main function
main "$@"