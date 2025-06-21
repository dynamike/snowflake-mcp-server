#!/bin/bash

# Snowflake MCP Server Daemon Stop Script
# Usage: ./scripts/stop-daemon.sh [http|stdio|all]

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Default values
MODE="all"

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
Snowflake MCP Server Daemon Stop Script

Usage: $0 [MODE] [OPTIONS]

Modes:
    http        Stop HTTP/WebSocket server
    stdio       Stop stdio server  
    all         Stop both HTTP and stdio servers (default)

Options:
    --help      Show this help message

Examples:
    $0                          # Stop all servers
    $0 http                     # Stop only HTTP server
    $0 stdio                    # Stop only stdio server

EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        http|stdio|all)
            MODE="$1"
            shift
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

# Check if PM2 is installed
check_pm2() {
    if ! command -v pm2 &> /dev/null; then
        log_error "PM2 is not installed."
        exit 1
    fi
}

# Stop HTTP server
stop_http_server() {
    log_info "Stopping Snowflake MCP HTTP server..."
    
    if pm2 list | grep -q "snowflake-mcp-http"; then
        pm2 stop snowflake-mcp-http
        pm2 delete snowflake-mcp-http
        log_success "HTTP server stopped and removed from PM2"
    else
        log_warning "HTTP server is not running"
    fi
}

# Stop stdio server
stop_stdio_server() {
    log_info "Stopping Snowflake MCP stdio server..."
    
    if pm2 list | grep -q "snowflake-mcp-stdio"; then
        pm2 stop snowflake-mcp-stdio
        pm2 delete snowflake-mcp-stdio
        log_success "stdio server stopped and removed from PM2"
    else
        log_warning "stdio server is not running"
    fi
}

# Show final status
show_status() {
    log_info "Final status:"
    pm2 list | grep snowflake-mcp || log_info "No Snowflake MCP servers running"
}

# Main execution
main() {
    log_info "Stopping Snowflake MCP Server daemon..."
    log_info "Mode: $MODE"
    
    check_pm2
    
    case $MODE in
        http)
            stop_http_server
            ;;
        stdio)
            stop_stdio_server
            ;;
        all)
            stop_http_server
            stop_stdio_server
            ;;
    esac
    
    show_status
    log_success "Daemon stop completed!"
}

# Run main function
main "$@"