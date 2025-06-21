#!/bin/bash

# Snowflake MCP Server Daemon Startup Script
# Usage: ./scripts/start-daemon.sh [http|stdio|all] [--dev]

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs"

# Default values
MODE="http"
ENVIRONMENT="production"
HOST="0.0.0.0"
PORT="8000"

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
Snowflake MCP Server Daemon Startup Script

Usage: $0 [MODE] [OPTIONS]

Modes:
    http        Start HTTP/WebSocket server (default)
    stdio       Start stdio server (for Claude Desktop)
    all         Start both HTTP and stdio servers

Options:
    --dev       Run in development mode
    --host      Host to bind to (default: 0.0.0.0)
    --port      Port to bind to (default: 8000)
    --help      Show this help message

Examples:
    $0                          # Start HTTP server in production mode
    $0 http --dev               # Start HTTP server in development mode
    $0 all                      # Start both HTTP and stdio servers
    $0 http --host 127.0.0.1 --port 9000  # Custom host and port

Environment Variables:
    SNOWFLAKE_ACCOUNT          # Snowflake account identifier (required)
    SNOWFLAKE_USER            # Snowflake username (required)
    SNOWFLAKE_PRIVATE_KEY     # Private key for authentication
    SNOWFLAKE_CONN_REFRESH_HOURS  # Connection refresh interval (default: 8)

EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        http|stdio|all)
            MODE="$1"
            shift
            ;;
        --dev)
            ENVIRONMENT="development"
            shift
            ;;
        --host)
            HOST="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
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

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check if uv is installed
    if ! command -v uv &> /dev/null; then
        log_error "uv is not installed. Please install uv first."
        log_info "Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
    
    # Check if PM2 is installed
    if ! command -v pm2 &> /dev/null; then
        log_error "PM2 is not installed. Please install PM2 first."
        log_info "Install PM2: npm install -g pm2"
        exit 1
    fi
    
    # Check if we're in the right directory
    if [[ ! -f "$PROJECT_DIR/pyproject.toml" ]]; then
        log_error "Not in snowflake-mcp-server project directory"
        exit 1
    fi
    
    # Check environment variables for Snowflake connection
    if [[ -z "$SNOWFLAKE_ACCOUNT" ]]; then
        log_warning "SNOWFLAKE_ACCOUNT environment variable is not set"
        log_info "Make sure to set up your .env file with Snowflake credentials"
    fi
    
    log_success "Prerequisites check completed"
}

# Setup logging directory
setup_logging() {
    log_info "Setting up logging directory..."
    mkdir -p "$LOG_DIR"
    touch "$LOG_DIR/snowflake-mcp-http.log"
    touch "$LOG_DIR/snowflake-mcp-stdio.log"
    log_success "Logging directory setup completed"
}

# Install dependencies
install_dependencies() {
    log_info "Installing/updating dependencies..."
    cd "$PROJECT_DIR"
    uv pip install -e .
    log_success "Dependencies installed"
}

# Start HTTP server
start_http_server() {
    log_info "Starting Snowflake MCP HTTP server..."
    
    if pm2 list | grep -q "snowflake-mcp-http"; then
        log_warning "HTTP server already running. Restarting..."
        pm2 restart snowflake-mcp-http
    else
        pm2 start ecosystem.config.js --only snowflake-mcp-http --env $ENVIRONMENT
    fi
    
    # Wait for server to start
    sleep 3
    
    # Check if server is healthy
    if curl -f -s "http://$HOST:$PORT/health" > /dev/null; then
        log_success "HTTP server started successfully on http://$HOST:$PORT"
        log_info "Health check: http://$HOST:$PORT/health"
        log_info "API docs: http://$HOST:$PORT/docs"
        log_info "Status endpoint: http://$HOST:$PORT/status"
    else
        log_error "HTTP server health check failed"
        pm2 logs snowflake-mcp-http --lines 10
        exit 1
    fi
}

# Start stdio server
start_stdio_server() {
    log_info "Starting Snowflake MCP stdio server..."
    
    if pm2 list | grep -q "snowflake-mcp-stdio"; then
        log_warning "stdio server already running. Restarting..."
        pm2 restart snowflake-mcp-stdio
    else
        pm2 start ecosystem.config.js --only snowflake-mcp-stdio --env $ENVIRONMENT
    fi
    
    log_success "stdio server started successfully"
    log_info "stdio server is ready for Claude Desktop connections"
}

# Show status
show_status() {
    log_info "Server status:"
    pm2 list | grep snowflake-mcp || log_warning "No Snowflake MCP servers running"
    
    echo ""
    log_info "Useful PM2 commands:"
    echo "  pm2 list                    # List all processes"
    echo "  pm2 logs snowflake-mcp-http # View HTTP server logs"
    echo "  pm2 logs snowflake-mcp-stdio # View stdio server logs"
    echo "  pm2 restart snowflake-mcp-http # Restart HTTP server"
    echo "  pm2 stop snowflake-mcp-http # Stop HTTP server"
    echo "  pm2 monit                   # Monitor processes"
}

# Main execution
main() {
    log_info "Starting Snowflake MCP Server daemon..."
    log_info "Mode: $MODE"
    log_info "Environment: $ENVIRONMENT"
    
    if [[ "$MODE" == "http" ]] || [[ "$MODE" == "all" ]]; then
        log_info "Host: $HOST"
        log_info "Port: $PORT"
    fi
    
    check_prerequisites
    setup_logging
    install_dependencies
    
    case $MODE in
        http)
            start_http_server
            ;;
        stdio)
            start_stdio_server
            ;;
        all)
            start_http_server
            start_stdio_server
            ;;
    esac
    
    show_status
    log_success "Daemon startup completed!"
}

# Run main function
main "$@"