# Backup and Recovery Procedures

This document provides comprehensive backup and recovery procedures for the Snowflake MCP Server to ensure business continuity and data protection.

## 📋 Overview

### What Gets Backed Up

| Component | Frequency | Retention | Critical Level |
|-----------|-----------|-----------|----------------|
| Configuration files | Daily | 30 days | Critical |
| Application code | Weekly | 90 days | High |
| Service logs | Daily | 7 days | Medium |
| Metrics snapshots | Daily | 30 days | Low |
| SSL certificates | Weekly | 1 year | High |
| PM2 configurations | Weekly | 90 days | Medium |

### Recovery Objectives

- **RTO (Recovery Time Objective):** 15 minutes for configuration changes, 1 hour for full restoration
- **RPO (Recovery Point Objective):** 24 hours maximum data loss
- **Service Availability Target:** 99.9% uptime

## 🔧 Backup Strategies

### 1. Configuration Backup

Configuration is the most critical component to backup as it contains connection details and service settings.

#### Automated Daily Configuration Backup

```bash
#!/bin/bash
# config_backup.sh - Daily configuration backup

BACKUP_DIR="/backup/snowflake-mcp/config/$(date +%Y/%m/%d)"
RETENTION_DAYS=30

# Create backup directory
mkdir -p "$BACKUP_DIR"

echo "📁 Starting configuration backup: $(date)"

# Backup environment files
if [ -f "/opt/snowflake-mcp-server/.env" ]; then
    # Encrypt sensitive configuration
    gpg --symmetric --cipher-algo AES256 --output "$BACKUP_DIR/env_$(date +%H%M%S).gpg" \
        /opt/snowflake-mcp-server/.env
    echo "✅ Environment file backed up (encrypted)"
fi

# Backup systemd service files
if [ -d "/etc/systemd/system" ]; then
    tar czf "$BACKUP_DIR/systemd_services.tar.gz" \
        /etc/systemd/system/snowflake-mcp-*.service 2>/dev/null
    echo "✅ Systemd services backed up"
fi

# Backup PM2 configuration
if [ -f "/opt/snowflake-mcp-server/ecosystem.config.js" ]; then
    cp /opt/snowflake-mcp-server/ecosystem.config.js "$BACKUP_DIR/"
    echo "✅ PM2 configuration backed up"
fi

# Backup Docker configuration
if [ -f "/opt/snowflake-mcp-server/docker-compose.yml" ]; then
    cp /opt/snowflake-mcp-server/docker-compose.yml "$BACKUP_DIR/"
    echo "✅ Docker configuration backed up"
fi

# Backup Kubernetes manifests
if [ -d "/opt/snowflake-mcp-server/deploy/kubernetes" ]; then
    tar czf "$BACKUP_DIR/kubernetes_manifests.tar.gz" \
        /opt/snowflake-mcp-server/deploy/kubernetes/
    echo "✅ Kubernetes manifests backed up"
fi

# Create backup manifest
cat > "$BACKUP_DIR/manifest.json" << EOF
{
  "backup_type": "configuration",
  "timestamp": "$(date -Iseconds)",
  "hostname": "$(hostname)",
  "version": "$(grep version /opt/snowflake-mcp-server/pyproject.toml 2>/dev/null | cut -d'"' -f2 || echo 'unknown')",
  "files": [
    "env_*.gpg",
    "systemd_services.tar.gz",
    "ecosystem.config.js",
    "docker-compose.yml",
    "kubernetes_manifests.tar.gz"
  ]
}
EOF

# Cleanup old backups
find /backup/snowflake-mcp/config -type d -mtime +$RETENTION_DAYS -exec rm -rf {} + 2>/dev/null

echo "✅ Configuration backup completed: $BACKUP_DIR"
```

#### Schedule with Cron

```bash
# Add to crontab
# Daily at 2 AM
0 2 * * * /opt/snowflake-mcp-server/scripts/config_backup.sh >> /var/log/snowflake-mcp/backup.log 2>&1
```

### 2. Application Code Backup

```bash
#!/bin/bash
# application_backup.sh - Weekly application backup

BACKUP_DIR="/backup/snowflake-mcp/application/$(date +%Y/%m/%d)"
SOURCE_DIR="/opt/snowflake-mcp-server"
RETENTION_WEEKS=12

mkdir -p "$BACKUP_DIR"

echo "📦 Starting application backup: $(date)"

# Create application archive (excluding runtime files)
tar czf "$BACKUP_DIR/application_$(date +%Y%m%d_%H%M%S).tar.gz" \
    --exclude="*.pyc" \
    --exclude="__pycache__" \
    --exclude=".venv" \
    --exclude="logs" \
    --exclude=".env" \
    --exclude="*.log" \
    -C "$(dirname $SOURCE_DIR)" \
    "$(basename $SOURCE_DIR)"

# Backup dependencies list
if [ -f "$SOURCE_DIR/uv.lock" ]; then
    cp "$SOURCE_DIR/uv.lock" "$BACKUP_DIR/"
fi

if [ -f "$SOURCE_DIR/pyproject.toml" ]; then
    cp "$SOURCE_DIR/pyproject.toml" "$BACKUP_DIR/"
fi

# Create checksums for verification
cd "$BACKUP_DIR"
sha256sum *.tar.gz *.lock *.toml > checksums.sha256 2>/dev/null

# Cleanup old backups (keep 12 weeks)
find /backup/snowflake-mcp/application -type d -mtime +$((RETENTION_WEEKS * 7)) -exec rm -rf {} + 2>/dev/null

echo "✅ Application backup completed: $BACKUP_DIR"
```

### 3. Service Logs Backup

```bash
#!/bin/bash
# logs_backup.sh - Daily logs backup

BACKUP_DIR="/backup/snowflake-mcp/logs/$(date +%Y/%m/%d)"
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"

echo "📋 Starting logs backup: $(date)"

# Backup systemd logs (last 24 hours)
journalctl -u snowflake-mcp-http --since "24 hours ago" > "$BACKUP_DIR/service_logs_$(date +%H%M%S).txt"

# Backup application logs
if [ -d "/opt/snowflake-mcp-server/logs" ]; then
    tar czf "$BACKUP_DIR/app_logs_$(date +%H%M%S).tar.gz" \
        /opt/snowflake-mcp-server/logs/*.log 2>/dev/null
fi

# Backup Docker logs
if docker ps --filter "name=snowflake-mcp" --format "table {{.Names}}" | grep -q snowflake-mcp; then
    docker logs snowflake-mcp > "$BACKUP_DIR/docker_logs_$(date +%H%M%S).txt" 2>&1
fi

# Backup PM2 logs
if command -v pm2 > /dev/null && pm2 list | grep -q snowflake-mcp; then
    pm2 logs snowflake-mcp --lines 1000 > "$BACKUP_DIR/pm2_logs_$(date +%H%M%S).txt"
fi

# Cleanup old log backups
find /backup/snowflake-mcp/logs -type d -mtime +$RETENTION_DAYS -exec rm -rf {} + 2>/dev/null

echo "✅ Logs backup completed: $BACKUP_DIR"
```

### 4. Complete System Backup

```bash
#!/bin/bash
# full_backup.sh - Complete system backup

BACKUP_DIR="/backup/snowflake-mcp/full/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "🗃️ Starting full system backup: $(date)"

# Run all backup components
echo "📁 Backing up configuration..."
/opt/snowflake-mcp-server/scripts/config_backup.sh

echo "📦 Backing up application..."
/opt/snowflake-mcp-server/scripts/application_backup.sh

echo "📋 Backing up logs..."
/opt/snowflake-mcp-server/scripts/logs_backup.sh

# Backup current system state
echo "🖥️ Capturing system state..."
cat > "$BACKUP_DIR/system_state.json" << EOF
{
  "timestamp": "$(date -Iseconds)",
  "hostname": "$(hostname)",
  "system": {
    "os": "$(uname -a)",
    "uptime": "$(uptime)",
    "disk_usage": "$(df -h | grep -E '^/dev')",
    "memory": "$(free -h)"
  },
  "service": {
    "status": "$(systemctl is-active snowflake-mcp-http 2>/dev/null || echo 'unknown')",
    "enabled": "$(systemctl is-enabled snowflake-mcp-http 2>/dev/null || echo 'unknown')",
    "pid": "$(pgrep -f snowflake-mcp || echo 'not running')"
  },
  "network": {
    "listening_ports": "$(ss -tlnp | grep :800)",
    "connections": "$(ss -tn | grep :800 | wc -l)"
  }
}
EOF

# Capture current health status
if curl -f -m 10 http://localhost:8000/health > /dev/null 2>&1; then
    curl -s http://localhost:8000/health > "$BACKUP_DIR/health_snapshot.json"
    curl -s http://localhost:8001/metrics > "$BACKUP_DIR/metrics_snapshot.txt"
fi

# Create master manifest
cat > "$BACKUP_DIR/backup_manifest.json" << EOF
{
  "backup_type": "full_system",
  "timestamp": "$(date -Iseconds)",
  "hostname": "$(hostname)",
  "components": [
    "configuration",
    "application",
    "logs",
    "system_state",
    "health_snapshot",
    "metrics_snapshot"
  ],
  "retention_policy": "30 days",
  "compression": "gzip",
  "encryption": "gpg (configuration only)"
}
EOF

echo "✅ Full system backup completed: $BACKUP_DIR"
```

## 🔄 Recovery Procedures

### 1. Quick Configuration Recovery

Use this for rapid recovery from configuration issues:

```bash
#!/bin/bash
# quick_config_recovery.sh - Quick configuration recovery

BACKUP_DATE=${1:-$(ls -1 /backup/snowflake-mcp/config/ | tail -1 | tr '/' '-')}

if [ -z "$BACKUP_DATE" ]; then
    echo "❌ No backup date specified and no backups found"
    echo "Usage: $0 [YYYY-MM-DD]"
    exit 1
fi

BACKUP_PATH="/backup/snowflake-mcp/config/$(echo $BACKUP_DATE | tr '-' '/')"

if [ ! -d "$BACKUP_PATH" ]; then
    echo "❌ Backup not found: $BACKUP_PATH"
    exit 1
fi

echo "🔄 Starting quick configuration recovery from $BACKUP_DATE"

# Stop service
echo "⏹️ Stopping service..."
systemctl stop snowflake-mcp-http || docker stop snowflake-mcp || pm2 stop snowflake-mcp

# Backup current configuration
echo "💾 Backing up current configuration..."
if [ -f "/opt/snowflake-mcp-server/.env" ]; then
    cp /opt/snowflake-mcp-server/.env /opt/snowflake-mcp-server/.env.pre-recovery.$(date +%Y%m%d_%H%M%S)
fi

# Restore configuration files
echo "📁 Restoring configuration..."

# Decrypt and restore environment file
if [ -f "$BACKUP_PATH"/env_*.gpg ]; then
    gpg --quiet --batch --output /opt/snowflake-mcp-server/.env --decrypt "$BACKUP_PATH"/env_*.gpg
    if [ $? -eq 0 ]; then
        echo "✅ Environment file restored"
        chown snowflake-mcp:snowflake-mcp /opt/snowflake-mcp-server/.env
        chmod 600 /opt/snowflake-mcp-server/.env
    else
        echo "❌ Failed to decrypt environment file"
        exit 1
    fi
fi

# Restore systemd services
if [ -f "$BACKUP_PATH/systemd_services.tar.gz" ]; then
    tar xzf "$BACKUP_PATH/systemd_services.tar.gz" -C /
    systemctl daemon-reload
    echo "✅ Systemd services restored"
fi

# Restore PM2 configuration
if [ -f "$BACKUP_PATH/ecosystem.config.js" ]; then
    cp "$BACKUP_PATH/ecosystem.config.js" /opt/snowflake-mcp-server/
    echo "✅ PM2 configuration restored"
fi

# Start service
echo "🚀 Starting service..."
systemctl start snowflake-mcp-http || docker start snowflake-mcp || pm2 start snowflake-mcp

# Verify recovery
echo "✅ Verifying recovery..."
sleep 10

if curl -f -m 10 http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ Quick configuration recovery completed successfully"
    echo "🏥 Service health: $(curl -s http://localhost:8000/health | jq -r '.status')"
else
    echo "❌ Service not responding after recovery"
    echo "📋 Check logs: journalctl -u snowflake-mcp-http -n 20"
    exit 1
fi
```

### 2. Full Application Recovery

```bash
#!/bin/bash
# full_recovery.sh - Complete application recovery

BACKUP_DATE=${1:-$(ls -1 /backup/snowflake-mcp/application/ | tail -1 | tr '/' '-')}

if [ -z "$BACKUP_DATE" ]; then
    echo "❌ No backup date specified"
    echo "Usage: $0 [YYYY-MM-DD]"
    exit 1
fi

BACKUP_PATH="/backup/snowflake-mcp/application/$(echo $BACKUP_DATE | tr '-' '/')"
INSTALL_DIR="/opt/snowflake-mcp-server"

echo "🔄 Starting full application recovery from $BACKUP_DATE"

# Pre-recovery checks
if [ ! -d "$BACKUP_PATH" ]; then
    echo "❌ Backup not found: $BACKUP_PATH"
    exit 1
fi

# Stop all services
echo "⏹️ Stopping all services..."
systemctl stop snowflake-mcp-http 2>/dev/null
docker stop snowflake-mcp 2>/dev/null
pm2 stop snowflake-mcp 2>/dev/null

# Backup current installation
echo "💾 Backing up current installation..."
if [ -d "$INSTALL_DIR" ]; then
    mv "$INSTALL_DIR" "$INSTALL_DIR.backup.$(date +%Y%m%d_%H%M%S)"
fi

# Restore application
echo "📦 Restoring application..."
mkdir -p "$(dirname $INSTALL_DIR)"

# Find and extract application archive
APP_ARCHIVE=$(find "$BACKUP_PATH" -name "application_*.tar.gz" | head -1)
if [ -n "$APP_ARCHIVE" ]; then
    tar xzf "$APP_ARCHIVE" -C "$(dirname $INSTALL_DIR)"
    echo "✅ Application files restored"
else
    echo "❌ No application archive found in backup"
    exit 1
fi

# Restore dependencies
echo "📦 Restoring dependencies..."
cd "$INSTALL_DIR"

if [ -f "uv.lock" ]; then
    # Use uv for dependency management
    uv venv
    uv pip install -e .
    echo "✅ Dependencies installed with uv"
elif [ -f "pyproject.toml" ]; then
    # Fallback to pip
    python -m venv .venv
    source .venv/bin/activate
    pip install -e .
    echo "✅ Dependencies installed with pip"
else
    echo "⚠️ No dependency files found, manual installation may be required"
fi

# Restore configuration
echo "📁 Restoring configuration..."
CONFIG_BACKUP_PATH="/backup/snowflake-mcp/config/$(date +%Y/%m/%d)"
if [ -d "$CONFIG_BACKUP_PATH" ]; then
    /opt/snowflake-mcp-server/scripts/quick_config_recovery.sh $(date +%Y-%m-%d)
else
    echo "⚠️ No recent configuration backup found, manual configuration required"
fi

# Fix permissions
echo "🔧 Fixing permissions..."
chown -R snowflake-mcp:snowflake-mcp "$INSTALL_DIR"
chmod +x "$INSTALL_DIR/.venv/bin/"*

# Verify installation
echo "✅ Verifying installation..."
if [ -f "$INSTALL_DIR/.venv/bin/python" ]; then
    "$INSTALL_DIR/.venv/bin/python" -c "import snowflake_mcp_server; print('Import successful')"
    if [ $? -eq 0 ]; then
        echo "✅ Application installation verified"
    else
        echo "❌ Application import failed"
        exit 1
    fi
fi

# Start service
echo "🚀 Starting service..."
systemctl start snowflake-mcp-http

# Final verification
echo "✅ Final verification..."
sleep 15

if curl -f -m 10 http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ Full application recovery completed successfully"
    
    # Display recovery summary
    cat << EOF

📊 Recovery Summary:
- Backup Date: $BACKUP_DATE
- Recovery Time: $(date)
- Service Status: $(systemctl is-active snowflake-mcp-http)
- Health Check: $(curl -s http://localhost:8000/health | jq -r '.status')

🎉 Recovery completed successfully!
EOF
else
    echo "❌ Service not responding after full recovery"
    echo "📋 Troubleshooting steps:"
    echo "   1. Check logs: journalctl -u snowflake-mcp-http -n 50"
    echo "   2. Verify configuration: cat $INSTALL_DIR/.env"
    echo "   3. Check permissions: ls -la $INSTALL_DIR"
    echo "   4. Test manually: $INSTALL_DIR/.venv/bin/python -m snowflake_mcp_server.main"
    exit 1
fi
```

### 3. Disaster Recovery

```bash
#!/bin/bash
# disaster_recovery.sh - Complete disaster recovery from bare metal

BACKUP_SOURCE=${1:-"/backup/snowflake-mcp"}
TARGET_HOST=${2:-"localhost"}

echo "🚨 Starting disaster recovery..."
echo "Source: $BACKUP_SOURCE"
echo "Target: $TARGET_HOST"

# Find latest full backup
LATEST_BACKUP=$(find "$BACKUP_SOURCE/full" -type d -name "*_*" | sort | tail -1)

if [ -z "$LATEST_BACKUP" ]; then
    echo "❌ No full backup found in $BACKUP_SOURCE/full"
    exit 1
fi

echo "📂 Using backup: $LATEST_BACKUP"

# System preparation
echo "🔧 Preparing system..."

# Install system dependencies
if command -v apt-get > /dev/null; then
    # Ubuntu/Debian
    apt-get update
    apt-get install -y python3 python3-venv python3-pip curl jq systemd
elif command -v yum > /dev/null; then
    # CentOS/RHEL
    yum update -y
    yum install -y python3 python3-pip curl jq systemd
fi

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# Create system user
if ! id snowflake-mcp > /dev/null 2>&1; then
    useradd --system --shell /bin/false --home-dir /opt/snowflake-mcp-server --create-home snowflake-mcp
fi

# Create directory structure
mkdir -p /opt/snowflake-mcp-server
mkdir -p /var/log/snowflake-mcp
mkdir -p /backup/snowflake-mcp

# Restore application
echo "📦 Restoring application from backup..."
FULL_BACKUP_DATE=$(basename "$LATEST_BACKUP" | cut -d'_' -f1)
/opt/snowflake-mcp-server/scripts/full_recovery.sh "$FULL_BACKUP_DATE"

# Install and configure service
echo "🔧 Installing service..."
if [ -f "/opt/snowflake-mcp-server/deploy/install-systemd.sh" ]; then
    /opt/snowflake-mcp-server/deploy/install-systemd.sh
fi

# Verify disaster recovery
echo "✅ Verifying disaster recovery..."
sleep 20

if curl -f -m 30 http://localhost:8000/health > /dev/null 2>&1; then
    echo "🎉 Disaster recovery completed successfully!"
    
    # Recovery report
    cat << EOF

📊 Disaster Recovery Report:
=============================
- Recovery Date: $(date)
- Backup Used: $LATEST_BACKUP
- Target Host: $TARGET_HOST
- Service Status: $(systemctl is-active snowflake-mcp-http)
- Health Status: $(curl -s http://localhost:8000/health | jq -r '.status')

✅ System fully restored and operational
EOF
else
    echo "❌ Disaster recovery failed - service not responding"
    echo "📋 Manual intervention required"
    exit 1
fi
```

## 🧪 Testing Recovery Procedures

### Recovery Testing Schedule

| Test Type | Frequency | Environment | Duration |
|-----------|-----------|-------------|----------|
| Configuration recovery | Monthly | Test | 15 minutes |
| Application recovery | Quarterly | Test | 1 hour |
| Disaster recovery | Annually | Staging | 4 hours |

### Automated Recovery Test

```bash
#!/bin/bash
# test_recovery.sh - Automated recovery testing

TEST_ENV=${1:-"test"}
TEST_TYPE=${2:-"config"}

echo "🧪 Starting recovery test: $TEST_TYPE in $TEST_ENV environment"

# Create test backup
echo "💾 Creating test backup..."
CURRENT_TIME=$(date +%Y%m%d_%H%M%S)
TEST_BACKUP_DIR="/tmp/recovery_test_$CURRENT_TIME"

# Backup current state
mkdir -p "$TEST_BACKUP_DIR"
cp /opt/snowflake-mcp-server/.env "$TEST_BACKUP_DIR/env_original" 2>/dev/null
systemctl status snowflake-mcp-http > "$TEST_BACKUP_DIR/service_status_original.txt"

# Perform recovery test
case "$TEST_TYPE" in
    "config")
        echo "🔧 Testing configuration recovery..."
        
        # Simulate config corruption
        cp /opt/snowflake-mcp-server/.env /opt/snowflake-mcp-server/.env.backup
        echo "INVALID_CONFIG=true" >> /opt/snowflake-mcp-server/.env
        systemctl restart snowflake-mcp-http
        
        # Wait for failure
        sleep 10
        
        # Test recovery
        /opt/snowflake-mcp-server/scripts/quick_config_recovery.sh
        
        ;;
    "application")
        echo "📦 Testing application recovery..."
        
        # Simulate application corruption
        mv /opt/snowflake-mcp-server /opt/snowflake-mcp-server.test_backup
        
        # Test recovery
        /opt/snowflake-mcp-server/scripts/full_recovery.sh
        
        ;;
    *)
        echo "❌ Unknown test type: $TEST_TYPE"
        exit 1
        ;;
esac

# Verify recovery
echo "✅ Verifying recovery..."
sleep 15

SUCCESS=true

# Check service status
if ! systemctl is-active --quiet snowflake-mcp-http; then
    echo "❌ Service not active"
    SUCCESS=false
fi

# Check health endpoint
if ! curl -f -m 10 http://localhost:8000/health > /dev/null 2>&1; then
    echo "❌ Health check failed"
    SUCCESS=false
fi

# Generate test report
cat > "$TEST_BACKUP_DIR/test_report.json" << EOF
{
  "test_type": "$TEST_TYPE",
  "environment": "$TEST_ENV",
  "timestamp": "$(date -Iseconds)",
  "success": $SUCCESS,
  "duration": "$(( $(date +%s) - $(date -d "$CURRENT_TIME" +%s 2>/dev/null || echo 0) )) seconds",
  "service_status": "$(systemctl is-active snowflake-mcp-http)",
  "health_status": "$(curl -s http://localhost:8000/health 2>/dev/null | jq -r '.status' 2>/dev/null || echo 'unknown')"
}
EOF

if [ "$SUCCESS" = true ]; then
    echo "✅ Recovery test passed"
    rm -rf "$TEST_BACKUP_DIR"
else
    echo "❌ Recovery test failed"
    echo "📁 Test artifacts saved to: $TEST_BACKUP_DIR"
    exit 1
fi
```

## 📊 Backup Monitoring

### Backup Verification Script

```bash
#!/bin/bash
# verify_backups.sh - Verify backup integrity

BACKUP_ROOT="/backup/snowflake-mcp"

echo "🔍 Verifying backup integrity..."

# Check recent backups exist
DAYS_TO_CHECK=7
ISSUES=0

for i in $(seq 0 $DAYS_TO_CHECK); do
    CHECK_DATE=$(date -d "$i days ago" +%Y/%m/%d)
    
    # Check configuration backup
    CONFIG_PATH="$BACKUP_ROOT/config/$CHECK_DATE"
    if [ ! -d "$CONFIG_PATH" ]; then
        echo "⚠️ Missing configuration backup for $CHECK_DATE"
        ((ISSUES++))
    else
        # Verify encrypted files can be decrypted (test only)
        if [ -f "$CONFIG_PATH"/env_*.gpg ]; then
            echo "✅ Configuration backup exists for $CHECK_DATE"
        else
            echo "⚠️ Missing environment backup for $CHECK_DATE"
            ((ISSUES++))
        fi
    fi
done

# Check application backups (weekly)
WEEKS_TO_CHECK=4
for i in $(seq 0 $WEEKS_TO_CHECK); do
    CHECK_DATE=$(date -d "$((i * 7)) days ago" +%Y/%m/%d)
    APP_PATH="$BACKUP_ROOT/application/$CHECK_DATE"
    
    if [ ! -d "$APP_PATH" ]; then
        echo "⚠️ Missing application backup for week of $CHECK_DATE"
        ((ISSUES++))
    else
        # Verify archive integrity
        if find "$APP_PATH" -name "application_*.tar.gz" -exec tar -tzf {} \; > /dev/null 2>&1; then
            echo "✅ Application backup verified for week of $CHECK_DATE"
        else
            echo "❌ Corrupted application backup for week of $CHECK_DATE"
            ((ISSUES++))
        fi
    fi
done

# Check disk space
BACKUP_USAGE=$(df "$BACKUP_ROOT" | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$BACKUP_USAGE" -gt 80 ]; then
    echo "⚠️ Backup disk usage high: ${BACKUP_USAGE}%"
    ((ISSUES++))
fi

# Summary
if [ $ISSUES -eq 0 ]; then
    echo "✅ All backup verifications passed"
else
    echo "❌ Found $ISSUES backup issues"
    exit 1
fi
```

### Backup Alert Script

```bash
#!/bin/bash
# backup_alerts.sh - Alert on backup issues

ALERT_EMAIL="ops@company.com"
BACKUP_ROOT="/backup/snowflake-mcp"

# Check if today's backup exists
TODAY=$(date +%Y/%m/%d)
CONFIG_BACKUP="$BACKUP_ROOT/config/$TODAY"

if [ ! -d "$CONFIG_BACKUP" ]; then
    # Send alert
    cat << EOF | mail -s "❌ Snowflake MCP Backup Missing" "$ALERT_EMAIL"
ALERT: Daily backup missing for Snowflake MCP Server

Date: $(date)
Host: $(hostname)
Missing: Configuration backup for $TODAY

Action Required:
1. Check backup script: /opt/snowflake-mcp-server/scripts/config_backup.sh
2. Verify backup disk space: df $BACKUP_ROOT
3. Check cron job: crontab -l | grep backup

Last successful backup: $(ls -1 $BACKUP_ROOT/config/ | tail -1)
EOF
    
    echo "❌ Backup alert sent to $ALERT_EMAIL"
    exit 1
fi

echo "✅ Backup monitoring passed"
```

## 📋 Backup Checklist

### Daily Checklist
- [ ] Configuration backup completed
- [ ] Log backup completed
- [ ] Backup disk space < 80%
- [ ] Backup verification passed

### Weekly Checklist
- [ ] Application backup completed
- [ ] Test configuration recovery
- [ ] Review backup retention
- [ ] Update backup documentation

### Monthly Checklist
- [ ] Full recovery test in test environment
- [ ] Backup storage cleanup
- [ ] Review backup performance
- [ ] Update recovery procedures

### Annual Checklist
- [ ] Disaster recovery test
- [ ] Backup strategy review
- [ ] Recovery time objective validation
- [ ] Staff training on recovery procedures

---

## 📞 Emergency Procedures

### Backup System Failure

1. **Immediate Actions:**
   ```bash
   # Check backup system status
   df -h /backup
   systemctl status backup-system
   
   # Manual backup if needed
   /opt/snowflake-mcp-server/scripts/full_backup.sh
   ```

2. **Temporary Backup Location:**
   ```bash
   # Use alternative location
   export BACKUP_DIR="/tmp/emergency_backup"
   /opt/snowflake-mcp-server/scripts/config_backup.sh
   ```

### Recovery System Failure

1. **Manual Recovery Steps:**
   ```bash
   # Stop service
   systemctl stop snowflake-mcp-http
   
   # Manual configuration restore
   gpg --decrypt /backup/snowflake-mcp/config/latest/env_*.gpg > /opt/snowflake-mcp-server/.env
   
   # Start service
   systemctl start snowflake-mcp-http
   ```

---

## 📚 Related Documentation

- **[Operations Runbook](OPERATIONS_RUNBOOK.md):** Daily operations procedures
- **[Configuration Guide](CONFIGURATION_GUIDE.md):** Configuration management
- **[Deployment Guide](deploy/DEPLOYMENT_README.md):** Deployment procedures
- **[Migration Guide](MIGRATION_GUIDE.md):** Version migration procedures