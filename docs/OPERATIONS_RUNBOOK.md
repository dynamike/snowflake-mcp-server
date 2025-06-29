# Operations Runbook

This runbook provides comprehensive operational procedures for the Snowflake MCP Server v1.0.0 in production environments.

## 📋 Quick Reference

### Service Endpoints

| Endpoint | Port | Purpose | Health Check |
|----------|------|---------|--------------|
| HTTP API | 8000 | Main MCP service | `GET /health` |
| Metrics | 8001 | Prometheus metrics | `GET /metrics` |
| WebSocket | 8000 | Real-time MCP | WebSocket handshake |

### Key Commands

```bash
# Service Management
systemctl status snowflake-mcp-http
systemctl restart snowflake-mcp-http
journalctl -u snowflake-mcp-http -f

# Docker
docker ps | grep snowflake-mcp
docker logs -f snowflake-mcp
docker restart snowflake-mcp

# Kubernetes
kubectl get pods -n snowflake-mcp
kubectl logs -f deployment/snowflake-mcp-server -n snowflake-mcp
kubectl rollout restart deployment/snowflake-mcp-server -n snowflake-mcp

# Health Checks
curl http://localhost:8000/health
curl http://localhost:8001/metrics
```

## 🚀 Daily Operations

### Morning Health Check

Run this daily checklist every morning:

```bash
#!/bin/bash
# daily_health_check.sh

echo "🌅 Daily Snowflake MCP Server Health Check - $(date)"
echo "================================================"

# 1. Service Status
echo "📊 Service Status:"
systemctl is-active snowflake-mcp-http
# OR: docker ps | grep snowflake-mcp
# OR: kubectl get pods -n snowflake-mcp

# 2. Health Endpoint
echo "🏥 Health Check:"
curl -s http://localhost:8000/health | jq '.'

# 3. Connection Pool Status
echo "🏊 Connection Pool:"
curl -s http://localhost:8001/metrics | grep pool_connections

# 4. Error Rate (last 24h)
echo "❌ Error Rate:"
curl -s http://localhost:8001/metrics | grep mcp_requests_total

# 5. Resource Usage
echo "💾 Resource Usage:"
# Systemd/Docker
systemctl show snowflake-mcp-http --property=MemoryCurrent,CPUUsageNSec
# OR: docker stats snowflake-mcp --no-stream
# OR: kubectl top pods -n snowflake-mcp

# 6. Log Check (last 1 hour)
echo "📝 Recent Errors:"
journalctl -u snowflake-mcp-http --since "1 hour ago" | grep -i error | tail -5
# OR: docker logs snowflake-mcp --since 1h | grep -i error
# OR: kubectl logs deployment/snowflake-mcp-server -n snowflake-mcp --since=1h | grep -i error

echo "✅ Health check completed"
```

### Performance Monitoring

Check these metrics regularly:

```bash
# Response Time Percentiles
curl -s http://localhost:8001/metrics | grep mcp_request_duration_seconds

# Request Rate
curl -s http://localhost:8001/metrics | grep mcp_requests_total

# Connection Pool Utilization
curl -s http://localhost:8001/metrics | grep pool_connections_active

# Active Sessions
curl -s http://localhost:8001/metrics | grep mcp_active_sessions_total
```

### Log Monitoring

```bash
# Real-time error monitoring
journalctl -u snowflake-mcp-http -f | grep -i --color=always "error\|warning\|failed"

# Check for specific issues
journalctl -u snowflake-mcp-http --since "1 hour ago" | grep -E "(timeout|connection|pool|memory)"

# Analyze request patterns
journalctl -u snowflake-mcp-http --since "1 hour ago" | grep "tool_call" | awk '{print $NF}' | sort | uniq -c | sort -nr
```

## 🔧 Routine Maintenance

### Weekly Tasks

```bash
#!/bin/bash
# weekly_maintenance.sh

echo "🗓️ Weekly Maintenance - $(date)"
echo "==============================="

# 1. Log Rotation Check
echo "📋 Log Rotation Status:"
logrotate -d /etc/logrotate.d/snowflake-mcp

# 2. Certificate Expiry Check (if using TLS)
echo "🔒 Certificate Check:"
echo | openssl s_client -servername localhost -connect localhost:8000 2>/dev/null | openssl x509 -noout -dates

# 3. Dependency Updates Check
echo "📦 Dependencies:"
cd /opt/snowflake-mcp-server
uv pip list --outdated

# 4. Disk Space Check
echo "💽 Disk Usage:"
df -h /opt/snowflake-mcp-server
df -h /var/log

# 5. Network Connectivity Test
echo "🌐 Snowflake Connectivity:"
python3 -c "
import asyncio
from snowflake_mcp_server.main import test_snowflake_connection
result = asyncio.run(test_snowflake_connection())
print('✅ Connected' if result else '❌ Connection Failed')
"

# 6. Backup Verification
echo "💾 Backup Status:"
ls -la /backup/snowflake-mcp/ | tail -5

echo "✅ Weekly maintenance completed"
```

### Monthly Tasks

```bash
#!/bin/bash
# monthly_maintenance.sh

echo "📅 Monthly Maintenance - $(date)"
echo "================================"

# 1. Performance Report
echo "📊 Performance Report:"
curl -s http://localhost:8001/metrics | grep -E "mcp_requests_total|mcp_request_duration_seconds|pool_connections" > /tmp/monthly_metrics.txt
echo "Metrics saved to /tmp/monthly_metrics.txt"

# 2. Security Audit
echo "🔐 Security Audit:"
# Check for exposed secrets
grep -r "password\|key\|secret" /opt/snowflake-mcp-server/ --include="*.py" --include="*.yaml" | grep -v "example"

# 3. Configuration Review
echo "⚙️ Configuration Review:"
# Compare current config with baseline
diff /opt/snowflake-mcp-server/.env.baseline /opt/snowflake-mcp-server/.env || echo "Config changes detected"

# 4. Capacity Planning Data
echo "📈 Capacity Planning:"
echo "Peak connection pool usage: $(curl -s http://localhost:8001/metrics | grep pool_connections_peak | awk '{print $2}')"
echo "Average request rate: $(curl -s http://localhost:8001/metrics | grep mcp_requests_per_second | awk '{print $2}')"

echo "✅ Monthly maintenance completed"
```

## 🚨 Incident Response

### Severity Levels

| Level | Description | Response Time | Examples |
|-------|-------------|---------------|----------|
| **P0 - Critical** | Complete service outage | 15 minutes | Service down, no responses |
| **P1 - High** | Major functionality impacted | 1 hour | High error rates, slow responses |
| **P2 - Medium** | Partial functionality affected | 4 hours | Some features failing |
| **P3 - Low** | Minor issues, workarounds available | 24 hours | Non-critical features affected |

### Common Incidents and Resolution

#### 1. Service Not Responding (P0)

**Symptoms:**
- Health check fails: `curl http://localhost:8000/health` times out
- No response from MCP clients

**Immediate Actions:**

```bash
# 1. Check service status
systemctl status snowflake-mcp-http
# OR: docker ps | grep snowflake-mcp
# OR: kubectl get pods -n snowflake-mcp

# 2. Check recent logs
journalctl -u snowflake-mcp-http --since "10 minutes ago" | tail -20
# OR: docker logs snowflake-mcp --tail 20
# OR: kubectl logs deployment/snowflake-mcp-server -n snowflake-mcp --tail=20

# 3. Check system resources
top -p $(pgrep -f snowflake-mcp)
df -h

# 4. Restart service
systemctl restart snowflake-mcp-http
# OR: docker restart snowflake-mcp
# OR: kubectl rollout restart deployment/snowflake-mcp-server -n snowflake-mcp

# 5. Verify recovery
curl http://localhost:8000/health
```

**Root Cause Analysis:**
- Check for OOM kills: `dmesg | grep -i "killed process"`
- Review application logs for exceptions
- Analyze metrics for resource exhaustion patterns

#### 2. High Error Rate (P1)

**Symptoms:**
- Error rate > 5% in metrics
- MCP clients reporting failures

**Investigation Steps:**

```bash
# 1. Check current error rate
curl -s http://localhost:8001/metrics | grep mcp_requests_total

# 2. Analyze error patterns
journalctl -u snowflake-mcp-http --since "1 hour ago" | grep -i error | sort | uniq -c | sort -nr

# 3. Check Snowflake connectivity
python3 -c "
import asyncio
from snowflake_mcp_server.main import test_snowflake_connection
print(asyncio.run(test_snowflake_connection()))
"

# 4. Check connection pool health
curl -s http://localhost:8001/metrics | grep pool_connections

# 5. Monitor real-time errors
journalctl -u snowflake-mcp-http -f | grep -i error
```

**Possible Causes and Solutions:**

| Cause | Solution |
|-------|----------|
| Snowflake connection issues | Check credentials, network connectivity |
| Connection pool exhaustion | Increase pool size, investigate connection leaks |
| Rate limiting | Adjust rate limits, identify heavy clients |
| Resource constraints | Scale up resources, optimize queries |

#### 3. Memory Leak (P1)

**Symptoms:**
- Memory usage continuously increasing
- System becoming slow or unresponsive

**Investigation:**

```bash
# 1. Check current memory usage
ps aux | grep snowflake-mcp
systemctl show snowflake-mcp-http --property=MemoryCurrent

# 2. Monitor memory growth
watch -n 30 'ps aux | grep snowflake-mcp | grep -v grep'

# 3. Check for connection pool issues
curl -s http://localhost:8001/metrics | grep pool_connections_total

# 4. Analyze Python memory usage (if possible)
# Add memory profiling to application logs

# 5. Restart service as immediate mitigation
systemctl restart snowflake-mcp-http
```

#### 4. Connection Pool Exhaustion (P2)

**Symptoms:**
- "Connection pool exhausted" errors in logs
- Slow response times

**Resolution:**

```bash
# 1. Check pool metrics
curl -s http://localhost:8001/metrics | grep pool_connections

# 2. Increase pool size temporarily
# Edit /opt/snowflake-mcp-server/.env
CONNECTION_POOL_MAX_SIZE=20  # Increase from 10

# 3. Restart service
systemctl restart snowflake-mcp-http

# 4. Monitor improvement
watch -n 10 'curl -s http://localhost:8001/metrics | grep pool_connections_active'

# 5. Investigate connection leaks
journalctl -u snowflake-mcp-http --since "1 hour ago" | grep -i "connection.*leak\|connection.*timeout"
```

#### 5. Snowflake Authentication Failure (P2)

**Symptoms:**
- Authentication errors in logs
- All queries failing

**Resolution:**

```bash
# 1. Test credentials manually
snowsql -a $SNOWFLAKE_ACCOUNT -u $SNOWFLAKE_USER

# 2. Check credential expiry (for key-based auth)
openssl rsa -in /path/to/private_key.pem -text -noout | grep "Exponent"

# 3. Verify environment variables
env | grep SNOWFLAKE_ | sed 's/PASSWORD=.*/PASSWORD=***/'

# 4. Check Snowflake account status
# Contact Snowflake support if needed

# 5. Update credentials and restart
# Edit .env file with new credentials
systemctl restart snowflake-mcp-http
```

### Escalation Procedures

#### Internal Escalation

1. **L1 → L2 Escalation (30 minutes)**
   - If issue not resolved using runbook procedures
   - Document all attempted solutions
   - Provide logs and metrics

2. **L2 → L3 Escalation (2 hours)**
   - Complex issues requiring development expertise
   - Potential bugs or design issues
   - Need for emergency configuration changes

#### External Escalation

1. **Snowflake Support**
   - Connection or authentication issues
   - Snowflake service outages
   - Performance issues on Snowflake side

2. **Infrastructure Support**
   - Network connectivity issues
   - Hardware or VM problems
   - Kubernetes cluster issues

### Emergency Contacts

```bash
# On-call rotation
echo "Current on-call: $(cat /etc/oncall.txt)"

# Emergency contacts
cat << 'EOF'
🚨 Emergency Contacts:
- L2 Engineer: +1-555-0123
- L3 Lead: +1-555-0456
- Infrastructure: +1-555-0789
- Snowflake Support: Case via portal
EOF
```

## 📊 Performance Tuning

### Connection Pool Optimization

```bash
# Monitor pool utilization
watch -n 5 'curl -s http://localhost:8001/metrics | grep pool_connections'

# Optimal pool sizing guidelines:
# Min Size = (Baseline Concurrent Users) / 2
# Max Size = (Peak Concurrent Users) * 1.5

# Example calculation for 50 concurrent users:
# CONNECTION_POOL_MIN_SIZE=25
# CONNECTION_POOL_MAX_SIZE=75
```

### Rate Limiting Tuning

```bash
# Check current rate limit hits
curl -s http://localhost:8001/metrics | grep rate_limit_exceeded_total

# Analyze request patterns
journalctl -u snowflake-mcp-http --since "1 hour ago" | grep "rate_limit" | awk '{print $1}' | sort | uniq -c

# Adjust rate limits based on analysis
# Per-client limits should be lower than global limits
```

### Query Performance Optimization

```bash
# Identify slow queries
journalctl -u snowflake-mcp-http --since "1 hour ago" | grep "query_duration_ms" | sort -k6 -nr | head -10

# Monitor query metrics
curl -s http://localhost:8001/metrics | grep query_duration_seconds

# Optimization recommendations:
# 1. Add LIMIT clauses to large result sets
# 2. Use appropriate Snowflake warehouse size
# 3. Cache frequently accessed data
# 4. Optimize SQL queries
```

## 🔧 Configuration Management

### Environment Variables Validation

```bash
#!/bin/bash
# validate_config.sh

required_vars=(
    "SNOWFLAKE_ACCOUNT"
    "SNOWFLAKE_USER"
    "SNOWFLAKE_WAREHOUSE"
    "SNOWFLAKE_DATABASE"
    "SNOWFLAKE_SCHEMA"
)

echo "🔍 Validating Configuration..."
for var in "${required_vars[@]}"; do
    if [[ -z "${!var}" ]]; then
        echo "❌ Missing: $var"
        exit 1
    else
        echo "✅ Present: $var"
    fi
done

# Test database connectivity
echo "🔌 Testing Snowflake Connection..."
python3 -c "
import asyncio
from snowflake_mcp_server.main import test_snowflake_connection
result = asyncio.run(test_snowflake_connection())
if result:
    print('✅ Connection successful')
else:
    print('❌ Connection failed')
    exit(1)
"

echo "✅ Configuration validation passed"
```

### Configuration Backup

```bash
#!/bin/bash
# backup_config.sh

BACKUP_DIR="/backup/snowflake-mcp/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# Backup configuration files
cp /opt/snowflake-mcp-server/.env "$BACKUP_DIR/env_$(date +%H%M%S)"
cp /etc/systemd/system/snowflake-mcp-*.service "$BACKUP_DIR/"

# Backup with encryption (for sensitive data)
tar czf - /opt/snowflake-mcp-server/.env | gpg --symmetric --output "$BACKUP_DIR/config_encrypted.tar.gz.gpg"

echo "✅ Configuration backed up to $BACKUP_DIR"
```

### Configuration Changes

```bash
#!/bin/bash
# apply_config_change.sh

CONFIG_FILE="/opt/snowflake-mcp-server/.env"
BACKUP_FILE="/opt/snowflake-mcp-server/.env.backup.$(date +%Y%m%d_%H%M%S)"

echo "🔧 Applying Configuration Change..."

# 1. Backup current configuration
cp "$CONFIG_FILE" "$BACKUP_FILE"
echo "✅ Backed up current config to $BACKUP_FILE"

# 2. Apply new configuration
# (Manual step - edit the file)
echo "📝 Edit $CONFIG_FILE with your changes"
read -p "Press Enter when configuration is updated..."

# 3. Validate configuration
./validate_config.sh
if [ $? -ne 0 ]; then
    echo "❌ Configuration validation failed"
    echo "🔄 Restoring backup..."
    cp "$BACKUP_FILE" "$CONFIG_FILE"
    exit 1
fi

# 4. Restart service
echo "🔄 Restarting service..."
systemctl restart snowflake-mcp-http

# 5. Verify service health
sleep 10
curl -f http://localhost:8000/health > /dev/null
if [ $? -eq 0 ]; then
    echo "✅ Configuration change applied successfully"
else
    echo "❌ Service unhealthy after change"
    echo "🔄 Restoring backup..."
    cp "$BACKUP_FILE" "$CONFIG_FILE"
    systemctl restart snowflake-mcp-http
    exit 1
fi
```

## 📈 Monitoring and Alerting

### Key Metrics to Monitor

| Metric | Threshold | Alert Level |
|--------|-----------|-------------|
| Service availability | < 99.9% | P0 |
| Error rate | > 5% | P1 |
| Response time (95th percentile) | > 5s | P1 |
| Connection pool utilization | > 90% | P2 |
| Memory usage | > 80% | P2 |
| Disk space | > 85% | P2 |
| Snowflake connection errors | > 1% | P1 |

### Prometheus Alerting Rules

```yaml
# alerts.yml
groups:
- name: snowflake-mcp-alerts
  rules:
  - alert: ServiceDown
    expr: up{job="snowflake-mcp-server"} == 0
    for: 1m
    labels:
      severity: critical
    annotations:
      summary: "Snowflake MCP Server is down"
      description: "Service has been down for more than 1 minute"

  - alert: HighErrorRate
    expr: rate(mcp_requests_total{status=~"4..|5.."}[5m]) / rate(mcp_requests_total[5m]) > 0.05
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "High error rate detected"
      description: "Error rate is {{ $value | humanizePercentage }} over the last 5 minutes"

  - alert: HighResponseTime
    expr: histogram_quantile(0.95, rate(mcp_request_duration_seconds_bucket[5m])) > 5
    for: 10m
    labels:
      severity: warning
    annotations:
      summary: "High response time"
      description: "95th percentile response time is {{ $value }}s"

  - alert: ConnectionPoolExhaustion
    expr: pool_connections_active / pool_connections_max > 0.9
    for: 2m
    labels:
      severity: warning
    annotations:
      summary: "Connection pool nearly exhausted"
      description: "Connection pool utilization is {{ $value | humanizePercentage }}"
```

### Health Check Script

```bash
#!/bin/bash
# health_check.sh

HEALTH_URL="http://localhost:8000/health"
METRICS_URL="http://localhost:8001/metrics"

# Check service responsiveness
if ! curl -f -m 10 "$HEALTH_URL" > /dev/null 2>&1; then
    echo "❌ CRITICAL: Service not responding"
    exit 2
fi

# Check health status
health_status=$(curl -s "$HEALTH_URL" | jq -r '.status')
if [ "$health_status" != "healthy" ]; then
    echo "❌ WARNING: Service unhealthy: $health_status"
    exit 1
fi

# Check Snowflake connection
sf_status=$(curl -s "$HEALTH_URL" | jq -r '.snowflake_connection')
if [ "$sf_status" != "healthy" ]; then
    echo "❌ WARNING: Snowflake connection unhealthy: $sf_status"
    exit 1
fi

# Check connection pool
active_connections=$(curl -s "$METRICS_URL" | grep pool_connections_active | awk '{print $2}')
max_connections=$(curl -s "$METRICS_URL" | grep pool_connections_max | awk '{print $2}')

if [ -n "$active_connections" ] && [ -n "$max_connections" ]; then
    utilization=$(echo "scale=2; $active_connections / $max_connections * 100" | bc)
    if (( $(echo "$utilization > 90" | bc -l) )); then
        echo "❌ WARNING: High connection pool utilization: ${utilization}%"
        exit 1
    fi
fi

echo "✅ All health checks passed"
exit 0
```

## 🔄 Backup and Recovery

### Automated Backup Script

```bash
#!/bin/bash
# backup.sh

BACKUP_ROOT="/backup/snowflake-mcp"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="$BACKUP_ROOT/$DATE"
RETENTION_DAYS=30

mkdir -p "$BACKUP_DIR"

echo "💾 Starting backup: $DATE"

# 1. Configuration backup
echo "📁 Backing up configuration..."
tar czf "$BACKUP_DIR/config.tar.gz" \
    /opt/snowflake-mcp-server/.env \
    /opt/snowflake-mcp-server/pyproject.toml \
    /etc/systemd/system/snowflake-mcp-*.service

# 2. Application code backup
echo "📦 Backing up application..."
tar czf "$BACKUP_DIR/application.tar.gz" \
    --exclude=".venv" \
    --exclude="logs" \
    --exclude="__pycache__" \
    /opt/snowflake-mcp-server/

# 3. Logs backup (last 7 days)
echo "📋 Backing up logs..."
journalctl -u snowflake-mcp-http --since "7 days ago" > "$BACKUP_DIR/service_logs.txt"

# 4. Metrics snapshot
echo "📊 Backing up metrics..."
curl -s http://localhost:8001/metrics > "$BACKUP_DIR/metrics_snapshot.txt"

# 5. Health status
echo "🏥 Backing up health status..."
curl -s http://localhost:8000/health > "$BACKUP_DIR/health_status.json"

# 6. Create manifest
echo "📄 Creating backup manifest..."
cat > "$BACKUP_DIR/manifest.json" << EOF
{
  "backup_date": "$DATE",
  "version": "$(grep version /opt/snowflake-mcp-server/pyproject.toml | cut -d'"' -f2)",
  "hostname": "$(hostname)",
  "files": [
    "config.tar.gz",
    "application.tar.gz",
    "service_logs.txt",
    "metrics_snapshot.txt",
    "health_status.json"
  ]
}
EOF

# 7. Cleanup old backups
echo "🧹 Cleaning up old backups..."
find "$BACKUP_ROOT" -type d -mtime +$RETENTION_DAYS -exec rm -rf {} +

# 8. Verify backup
echo "✅ Verifying backup..."
if [ -f "$BACKUP_DIR/config.tar.gz" ] && [ -f "$BACKUP_DIR/application.tar.gz" ]; then
    echo "✅ Backup completed successfully: $BACKUP_DIR"
else
    echo "❌ Backup failed"
    exit 1
fi
```

### Recovery Procedures

#### Quick Recovery (Configuration Only)

```bash
#!/bin/bash
# quick_recovery.sh

BACKUP_DATE=$1
if [ -z "$BACKUP_DATE" ]; then
    echo "Usage: $0 <backup_date>"
    echo "Available backups:"
    ls -1 /backup/snowflake-mcp/ | tail -5
    exit 1
fi

BACKUP_DIR="/backup/snowflake-mcp/$BACKUP_DATE"

echo "🔄 Starting quick recovery from $BACKUP_DATE"

# 1. Stop service
echo "⏹️ Stopping service..."
systemctl stop snowflake-mcp-http

# 2. Backup current state
echo "💾 Backing up current state..."
cp /opt/snowflake-mcp-server/.env /opt/snowflake-mcp-server/.env.pre-recovery

# 3. Restore configuration
echo "📁 Restoring configuration..."
tar xzf "$BACKUP_DIR/config.tar.gz" -C /

# 4. Restart service
echo "🚀 Starting service..."
systemctl start snowflake-mcp-http

# 5. Verify recovery
echo "✅ Verifying recovery..."
sleep 10
if curl -f http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ Quick recovery completed successfully"
else
    echo "❌ Recovery failed, manual intervention required"
    exit 1
fi
```

#### Full Recovery

```bash
#!/bin/bash
# full_recovery.sh

BACKUP_DATE=$1
if [ -z "$BACKUP_DATE" ]; then
    echo "Usage: $0 <backup_date>"
    exit 1
fi

BACKUP_DIR="/backup/snowflake-mcp/$BACKUP_DATE"
INSTALL_DIR="/opt/snowflake-mcp-server"

echo "🔄 Starting full recovery from $BACKUP_DATE"

# 1. Stop service
systemctl stop snowflake-mcp-http

# 2. Backup current installation
mv "$INSTALL_DIR" "$INSTALL_DIR.backup.$(date +%Y%m%d_%H%M%S)"

# 3. Restore application
echo "📦 Restoring application..."
mkdir -p "$INSTALL_DIR"
tar xzf "$BACKUP_DIR/application.tar.gz" -C /

# 4. Restore configuration
echo "📁 Restoring configuration..."
tar xzf "$BACKUP_DIR/config.tar.gz" -C /

# 5. Reinstall dependencies
echo "📦 Reinstalling dependencies..."
cd "$INSTALL_DIR"
uv venv
uv pip install -e .

# 6. Fix permissions
chown -R snowflake-mcp:snowflake-mcp "$INSTALL_DIR"

# 7. Restart service
systemctl start snowflake-mcp-http

# 8. Verify recovery
sleep 15
if curl -f http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ Full recovery completed successfully"
else
    echo "❌ Recovery failed, check logs"
    journalctl -u snowflake-mcp-http --since "5 minutes ago"
    exit 1
fi
```

## 📞 Support and Escalation

### Log Collection for Support

```bash
#!/bin/bash
# collect_support_logs.sh

SUPPORT_DIR="/tmp/snowflake-mcp-support-$(date +%Y%m%d_%H%M%S)"
mkdir -p "$SUPPORT_DIR"

echo "📊 Collecting support information..."

# 1. System information
echo "🖥️ System info..."
uname -a > "$SUPPORT_DIR/system_info.txt"
df -h >> "$SUPPORT_DIR/system_info.txt"
free -h >> "$SUPPORT_DIR/system_info.txt"

# 2. Service status
echo "🔧 Service status..."
systemctl status snowflake-mcp-http > "$SUPPORT_DIR/service_status.txt"

# 3. Configuration (sanitized)
echo "⚙️ Configuration..."
env | grep SNOWFLAKE_ | sed 's/PASSWORD=.*/PASSWORD=***/' > "$SUPPORT_DIR/config.txt"

# 4. Recent logs
echo "📋 Recent logs..."
journalctl -u snowflake-mcp-http --since "2 hours ago" > "$SUPPORT_DIR/service_logs.txt"

# 5. Health and metrics
echo "🏥 Health status..."
curl -s http://localhost:8000/health > "$SUPPORT_DIR/health.json"
curl -s http://localhost:8001/metrics > "$SUPPORT_DIR/metrics.txt"

# 6. Create archive
echo "📦 Creating support archive..."
tar czf "${SUPPORT_DIR}.tar.gz" -C /tmp "$(basename "$SUPPORT_DIR")"
rm -rf "$SUPPORT_DIR"

echo "✅ Support logs collected: ${SUPPORT_DIR}.tar.gz"
echo "📧 Please send this file to the support team"
```

### Emergency Recovery Plan

1. **Service Completely Down**
   ```bash
   # Try standard restart first
   systemctl restart snowflake-mcp-http
   
   # If that fails, try configuration recovery
   ./quick_recovery.sh <latest_backup_date>
   
   # If still failing, try full recovery
   ./full_recovery.sh <latest_backup_date>
   
   # Last resort: clean installation
   ./install-systemd.sh
   # Then restore configuration manually
   ```

2. **Data Corruption**
   ```bash
   # Stop service immediately
   systemctl stop snowflake-mcp-http
   
   # Move corrupted data
   mv /opt/snowflake-mcp-server /opt/snowflake-mcp-server.corrupted
   
   # Restore from backup
   ./full_recovery.sh <latest_good_backup>
   ```

3. **Security Breach**
   ```bash
   # Immediately stop service
   systemctl stop snowflake-mcp-http
   
   # Rotate all credentials
   # - Generate new Snowflake password/keys
   # - Update .env file
   # - Update any API keys
   
   # Review logs for suspicious activity
   journalctl -u snowflake-mcp-http --since "24 hours ago" | grep -E "authentication|authorization|security"
   
   # Start service with new credentials
   systemctl start snowflake-mcp-http
   ```

---

## 📚 Additional Resources

- **[Configuration Guide](CONFIGURATION_GUIDE.md):** Detailed configuration options
- **[Migration Guide](MIGRATION_GUIDE.md):** Upgrading procedures
- **[Deployment Guide](deploy/DEPLOYMENT_README.md):** Deployment scenarios
- **[Architecture Overview](CLAUDE.md):** Technical architecture

**📞 Emergency Hotline:** Check `/etc/oncall.txt` for current on-call engineer