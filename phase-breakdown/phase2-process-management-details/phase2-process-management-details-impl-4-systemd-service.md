# Phase 2: Process Management & Deployment Details

## Context & Overview

The current Snowflake MCP server requires a terminal window to remain open and cannot run as a background daemon service. To enable production deployment with automatic restart, process monitoring, and log management, we need to implement proper process management using PM2 and systemd.

**Current Limitations:**
- Requires terminal window to stay open
- No process monitoring or automatic restart
- No log rotation or centralized logging
- Cannot survive system reboots
- No cluster mode for high availability

**Target Architecture:**
- PM2 ecosystem for process management
- Daemon mode operation without terminal dependency
- Automatic restart on failures
- Log rotation and management
- Environment-based configuration
- Systemd integration for system boot

## Dependencies Required

Add to `pyproject.toml`:
```toml
[project.optional-dependencies]
production = [
    "gunicorn>=21.2.0",  # WSGI server for production
    "uvloop>=0.19.0",    # High-performance event loop (Unix only)
    "setproctitle>=1.3.0",  # Process title setting
]

[project.scripts]
snowflake-mcp = "snowflake_mcp_server.main:run_stdio_server"
snowflake-mcp-http = "snowflake_mcp_server.transports.http_server:main"
snowflake-mcp-daemon = "snowflake_mcp_server.daemon:main"
```

## Implementation Plan

### 4. Systemd Service Integration {#systemd-service}

**Step 1: Create Systemd Service File**

Create `scripts/systemd/snowflake-mcp.service`:

```ini
[Unit]
Description=Snowflake MCP Server
Documentation=https://github.com/your-org/snowflake-mcp-server
After=network.target
Wants=network.target

[Service]
Type=forking
User=mcp-server
Group=mcp-server
WorkingDirectory=/var/lib/snowflake-mcp

# Environment
Environment=ENVIRONMENT=production
EnvironmentFile=/etc/snowflake-mcp/production.env

# Service execution
ExecStart=/usr/local/bin/uv run snowflake-mcp-daemon start --pid-file /var/run/snowflake-mcp.pid
ExecStop=/usr/local/bin/uv run snowflake-mcp-daemon stop --pid-file /var/run/snowflake-mcp.pid
ExecReload=/usr/local/bin/uv run snowflake-mcp-daemon restart --pid-file /var/run/snowflake-mcp.pid

# Process management
PIDFile=/var/run/snowflake-mcp.pid
TimeoutStartSec=30
TimeoutStopSec=30
Restart=on-failure
RestartSec=5
KillMode=mixed
KillSignal=SIGTERM

# Security
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/snowflake-mcp /var/log/snowflake-mcp /var/run
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictRealtime=true
RestrictSUIDSGID=true

# Resource limits
LimitNOFILE=65536
LimitNPROC=4096

[Install]
WantedBy=multi-user.target
```

**Step 2: Installation Scripts**

Create `scripts/install.sh`:

```bash
#!/bin/bash
set -e

# Installation script for Snowflake MCP Server

# Configuration
SERVICE_USER="mcp-server"
SERVICE_GROUP="mcp-server"
INSTALL_DIR="/opt/snowflake-mcp-server"
CONFIG_DIR="/etc/snowflake-mcp"
LOG_DIR="/var/log/snowflake-mcp"
LIB_DIR="/var/lib/snowflake-mcp"
SERVICE_FILE="/etc/systemd/system/snowflake-mcp.service"

echo "Installing Snowflake MCP Server..."

# Create service user
if ! id "$SERVICE_USER" &>/dev/null; then
    echo "Creating service user: $SERVICE_USER"
    sudo useradd --system --shell /bin/false --home-dir "$LIB_DIR" --create-home "$SERVICE_USER"
fi

# Create directories
echo "Creating directories..."
sudo mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$LOG_DIR" "$LIB_DIR"

# Set ownership and permissions
sudo chown -R "$SERVICE_USER:$SERVICE_GROUP" "$LOG_DIR" "$LIB_DIR"
sudo chmod 755 "$LOG_DIR" "$LIB_DIR"
sudo chmod 750 "$CONFIG_DIR"

# Install application files
echo "Installing application..."
sudo cp -r . "$INSTALL_DIR/"
sudo chown -R root:root "$INSTALL_DIR"
sudo chmod -R 755 "$INSTALL_DIR"

# Install systemd service
echo "Installing systemd service..."
sudo cp "scripts/systemd/snowflake-mcp.service" "$SERVICE_FILE"
sudo systemctl daemon-reload

# Install configuration files
if [ ! -f "$CONFIG_DIR/production.env" ]; then
    echo "Installing default configuration..."
    sudo cp ".env.production" "$CONFIG_DIR/production.env"
    sudo chown root:$SERVICE_GROUP "$CONFIG_DIR/production.env"
    sudo chmod 640 "$CONFIG_DIR/production.env"
    
    echo "Please edit $CONFIG_DIR/production.env with your configuration"
fi

# Setup log rotation
echo "Setting up log rotation..."
sudo tee /etc/logrotate.d/snowflake-mcp > /dev/null <<EOF
$LOG_DIR/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 644 $SERVICE_USER $SERVICE_GROUP
    postrotate
        systemctl reload snowflake-mcp || true
    endscript
}
EOF

echo "Installation complete!"
echo ""
echo "Next steps:"
echo "1. Edit configuration: sudo nano $CONFIG_DIR/production.env"
echo "2. Enable service: sudo systemctl enable snowflake-mcp"
echo "3. Start service: sudo systemctl start snowflake-mcp"
echo "4. Check status: sudo systemctl status snowflake-mcp"
```

