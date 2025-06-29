# Phase 4: Operations Documentation Implementation Details

## Context & Overview

The new multi-client, async, daemon-capable architecture requires comprehensive operational procedures to ensure reliable production deployment, monitoring, and maintenance. This differs significantly from the simple stdio process management of v0.2.0.

**Operational Challenges:**
- Complex daemon lifecycle management vs simple stdio process
- Connection pool health monitoring and maintenance
- Multi-client session tracking and troubleshooting
- Performance monitoring across concurrent workloads
- Scaling decisions based on usage patterns and capacity metrics

**Target Operations:**
- Comprehensive runbook for all operational procedures
- Automated monitoring setup with alerting
- Backup and disaster recovery procedures
- Scaling guidelines based on usage patterns
- Capacity planning tools and recommendations

## Implementation Plan

### 2. Backup and Recovery Procedures {#backup-recovery}

Create `docs/operations/backup_recovery.md`:

```markdown
# Backup and Recovery Procedures

## Configuration Backup

### Daily Configuration Backup
```bash
#!/bin/bash
# Daily backup script: scripts/ops/backup_config.sh

BACKUP_DIR="/opt/backups/snowflake-mcp"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="$BACKUP_DIR/config_backup_$DATE"

echo "💾 Starting configuration backup..."

# Create backup directory
mkdir -p "$BACKUP_PATH"

# Backup configuration files
cp -r /etc/snowflake-mcp/ "$BACKUP_PATH/"
cp .env.production "$BACKUP_PATH/"
cp pyproject.toml "$BACKUP_PATH/"

# Backup monitoring configuration
cp -r /opt/prometheus/ "$BACKUP_PATH/prometheus/"
cp -r /etc/grafana/ "$BACKUP_PATH/grafana/"

# Backup service definitions
cp /etc/systemd/system/snowflake-mcp.service "$BACKUP_PATH/"

# Create backup manifest
cat > "$BACKUP_PATH/backup_manifest.txt" << EOF
Backup Date: $(date)
Hostname: $(hostname)
Service Version: $(grep version pyproject.toml | cut -d'"' -f2)
Backup Contents:
- Configuration files
- Environment variables
- Monitoring configuration
- Service definitions
EOF

# Compress backup
tar -czf "$BACKUP_PATH.tar.gz" -C "$BACKUP_DIR" "config_backup_$DATE"
rm -rf "$BACKUP_PATH"

# Clean old backups (keep 30 days)
find "$BACKUP_DIR" -name "config_backup_*.tar.gz" -mtime +30 -delete

echo "✅ Configuration backup complete: $BACKUP_PATH.tar.gz"
```

### Application State Backup
```bash
#!/bin/bash
# Application state backup: scripts/ops/backup_state.sh

BACKUP_DIR="/opt/backups/snowflake-mcp"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="$BACKUP_DIR/state_backup_$DATE"

echo "🗄️ Starting application state backup..."

mkdir -p "$BACKUP_PATH"

# Backup application logs
cp -r /var/log/snowflake-mcp/ "$BACKUP_PATH/logs/"

# Backup metrics data (if running Prometheus locally)
if [ -d "/opt/prometheus/data" ]; then
    cp -r /opt/prometheus/data/ "$BACKUP_PATH/prometheus_data/"
fi

# Backup session data and cache
curl -s http://localhost:8000/admin/export-state > "$BACKUP_PATH/session_state.json"

# Backup current metrics snapshot
curl -s http://localhost:8000/metrics > "$BACKUP_PATH/current_metrics.txt"

# System state information
ps aux | grep snowflake-mcp > "$BACKUP_PATH/process_info.txt"
netstat -tulpn | grep :8000 > "$BACKUP_PATH/network_info.txt"

# Compress backup
tar -czf "$BACKUP_PATH.tar.gz" -C "$BACKUP_DIR" "state_backup_$DATE"
rm -rf "$BACKUP_PATH"

echo "✅ Application state backup complete: $BACKUP_PATH.tar.gz"
```

## Recovery Procedures

### Configuration Recovery
```bash
#!/bin/bash
# Configuration recovery: scripts/ops/recover_config.sh

if [ $# -ne 1 ]; then
    echo "Usage: $0 <backup_file.tar.gz>"
    exit 1
fi

BACKUP_FILE="$1"
RECOVERY_DIR="/tmp/mcp_recovery_$(date +%Y%m%d_%H%M%S)"

echo "🔧 Starting configuration recovery..."

# Validate backup file
if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ Backup file not found: $BACKUP_FILE"
    exit 1
fi

# Extract backup
mkdir -p "$RECOVERY_DIR"
tar -xzf "$BACKUP_FILE" -C "$RECOVERY_DIR"

# Stop service
echo "🛑 Stopping service..."
systemctl stop snowflake-mcp

# Backup current configuration
echo "💾 Backing up current configuration..."
cp -r /etc/snowflake-mcp/ /etc/snowflake-mcp.backup.$(date +%Y%m%d_%H%M%S)/

# Restore configuration
echo "📁 Restoring configuration..."
EXTRACTED_DIR=$(find "$RECOVERY_DIR" -name "config_backup_*" -type d)

if [ -d "$EXTRACTED_DIR/etc/snowflake-mcp" ]; then
    cp -r "$EXTRACTED_DIR/etc/snowflake-mcp"/* /etc/snowflake-mcp/
fi

if [ -f "$EXTRACTED_DIR/.env.production" ]; then
    cp "$EXTRACTED_DIR/.env.production" ./
fi

# Restore service definition
if [ -f "$EXTRACTED_DIR/snowflake-mcp.service" ]; then
    cp "$EXTRACTED_DIR/snowflake-mcp.service" /etc/systemd/system/
    systemctl daemon-reload
fi

# Restore monitoring configuration
if [ -d "$EXTRACTED_DIR/prometheus" ]; then
    echo "📊 Restoring Prometheus configuration..."
    cp -r "$EXTRACTED_DIR/prometheus"/* /opt/prometheus/
    systemctl restart prometheus
fi

if [ -d "$EXTRACTED_DIR/grafana" ]; then
    echo "📈 Restoring Grafana configuration..."
    cp -r "$EXTRACTED_DIR/grafana"/* /etc/grafana/
    systemctl restart grafana-server
fi

# Start service
echo "🚀 Starting service..."
systemctl start snowflake-mcp

# Validate recovery
echo "🔍 Validating recovery..."
sleep 10

if curl -s http://localhost:8000/health >/dev/null; then
    echo "✅ Configuration recovery successful!"
else
    echo "❌ Recovery validation failed. Check logs."
    exit 1
fi

# Cleanup
rm -rf "$RECOVERY_DIR"
```

### Disaster Recovery
```bash
#!/bin/bash
# Full disaster recovery: scripts/ops/disaster_recovery.sh

echo "🚨 Starting disaster recovery procedure..."

# 1. Stop all services
echo "🛑 Stopping all services..."
systemctl stop snowflake-mcp
systemctl stop prometheus
systemctl stop grafana-server

# 2. Clean installation
echo "🧹 Performing clean installation..."
rm -rf /opt/snowflake-mcp/
mkdir -p /opt/snowflake-mcp/

# 3. Reinstall application
echo "📦 Reinstalling application..."
cd /opt/snowflake-mcp/
git clone https://github.com/your-org/snowflake-mcp-server.git .
uv install

# 4. Restore from latest backup
echo "📁 Restoring from backup..."
LATEST_BACKUP=$(ls -t /opt/backups/snowflake-mcp/config_backup_*.tar.gz | head -1)
if [ -n "$LATEST_BACKUP" ]; then
    ./scripts/ops/recover_config.sh "$LATEST_BACKUP"
else
    echo "❌ No backup found for recovery"
    exit 1
fi

# 5. Validate all services
echo "🔍 Validating all services..."
systemctl start prometheus
systemctl start grafana-server
systemctl start snowflake-mcp

sleep 30

# Check all services
SERVICES=("snowflake-mcp" "prometheus" "grafana-server")
for service in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$service"; then
        echo "✅ $service is running"
    else
        echo "❌ $service failed to start"
    fi
done

# Test functionality
python scripts/ops/validate_service.py

echo "✅ Disaster recovery complete!"
```

## Database Connection Recovery

### Connection Pool Reset
```python
#!/usr/bin/env python3
"""Database connection recovery procedures."""

import asyncio
import aiohttp
import logging
from datetime import datetime

class DatabaseRecovery:
    """Database connection recovery operations."""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.logger = logging.getLogger(__name__)
    
    async def full_recovery(self):
        """Perform full database connection recovery."""
        print("🔗 Starting database connection recovery...")
        
        # 1. Test current connection status
        connection_status = await self.test_connection_status()
        
        if connection_status["healthy"]:
            print("✅ Database connections are healthy")
            return True
        
        # 2. Reset connection pool
        print("♻️  Resetting connection pool...")
        await self.reset_connection_pool()
        
        # 3. Wait for pool recovery
        await asyncio.sleep(10)
        
        # 4. Test again
        connection_status = await self.test_connection_status()
        
        if connection_status["healthy"]:
            print("✅ Database recovery successful")
            return True
        else:
            print("❌ Database recovery failed")
            return False
    
    async def test_connection_status(self) -> dict:
        """Test database connection status."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{self.base_url}/health/detailed") as response:
                    if response.status == 200:
                        data = await response.json()
                        return {
                            "healthy": data.get("connection_pool", {}).get("status") == "healthy",
                            "active_connections": data.get("connection_pool", {}).get("active", 0),
                            "idle_connections": data.get("connection_pool", {}).get("idle", 0)
                        }
            except Exception as e:
                self.logger.error(f"Connection test failed: {e}")
                return {"healthy": False, "error": str(e)}
    
    async def reset_connection_pool(self):
        """Reset the connection pool."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(f"{self.base_url}/admin/reset-connection-pool") as response:
                    if response.status == 200:
                        print("✅ Connection pool reset successfully")
                    else:
                        print(f"❌ Connection pool reset failed: {response.status}")
            except Exception as e:
                print(f"❌ Connection pool reset error: {e}")

async def main():
    recovery = DatabaseRecovery()
    success = await recovery.full_recovery()
    exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())
```
```

