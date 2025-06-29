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

### 1. Operations Runbook {#operations-runbook}

**Step 1: Service Management Procedures**

Create `docs/operations/service_management.md`:

```markdown
# Service Management Runbook

## Daily Operations

### Service Health Check
```bash
#!/bin/bash
# Daily health check script: scripts/ops/daily_health_check.sh

echo "🏥 Daily Snowflake MCP Health Check - $(date)"
echo "================================================"

# 1. Check service status
echo "📊 Service Status:"
curl -s http://localhost:8000/health | jq '.'
echo ""

# 2. Check connection pool health
echo "🔗 Connection Pool Status:"
curl -s http://localhost:8000/health/detailed | jq '.connection_pool'
echo ""

# 3. Check active sessions
echo "👥 Active Sessions:"
curl -s http://localhost:8000/api/sessions | jq '.active_sessions | length'
echo ""

# 4. Check error rates (last 24h)
echo "❌ Error Rate (24h):"
curl -s http://localhost:8000/metrics | grep 'mcp_errors_total' | \
    awk '{sum+=$2} END {print "Total Errors: " sum}'
echo ""

# 5. Check resource usage
echo "💾 Resource Usage:"
ps aux | grep snowflake-mcp | grep -v grep
echo ""

# 6. Check log file size
echo "📝 Log File Size:"
ls -lh /var/log/snowflake-mcp/application.log
echo ""

# 7. Connection to Snowflake test
echo "❄️  Snowflake Connectivity:"
curl -s -X POST http://localhost:8000/api/test-connection | jq '.'
echo ""

echo "✅ Health check complete!"
```

### Service Restart Procedures
```bash
#!/bin/bash
# Safe service restart: scripts/ops/safe_restart.sh

echo "🔄 Starting safe restart procedure..."

# 1. Check current connections
ACTIVE_SESSIONS=$(curl -s http://localhost:8000/api/sessions | jq '.active_sessions | length')
echo "Active sessions: $ACTIVE_SESSIONS"

if [ "$ACTIVE_SESSIONS" -gt 0 ]; then
    echo "⚠️  Active sessions detected. Initiating graceful shutdown..."
    
    # 2. Stop accepting new connections
    curl -X POST http://localhost:8000/admin/maintenance-mode
    
    # 3. Wait for sessions to complete (max 5 minutes)
    for i in {1..30}; do
        CURRENT_SESSIONS=$(curl -s http://localhost:8000/api/sessions | jq '.active_sessions | length')
        if [ "$CURRENT_SESSIONS" -eq 0 ]; then
            echo "✅ All sessions completed"
            break
        fi
        echo "Waiting for $CURRENT_SESSIONS sessions to complete... ($i/30)"
        sleep 10
    done
fi

# 4. Stop service
echo "🛑 Stopping service..."
uv run snowflake-mcp-daemon stop

# 5. Wait for clean shutdown
sleep 5

# 6. Start service
echo "🚀 Starting service..."
uv run snowflake-mcp-daemon start

# 7. Wait for service to be ready
for i in {1..12}; do
    if curl -s http://localhost:8000/health >/dev/null 2>&1; then
        echo "✅ Service is ready"
        break
    fi
    echo "Waiting for service to start... ($i/12)"
    sleep 5
done

# 8. Validate service
echo "🔍 Validating service..."
python scripts/ops/validate_service.py
echo "✅ Restart complete!"
```

## Emergency Procedures

### Service Down Recovery
```bash
#!/bin/bash
# Emergency recovery: scripts/ops/emergency_recovery.sh

echo "🚨 Emergency Recovery Procedure"
echo "=============================="

# 1. Check if process is running
if ! pgrep -f snowflake-mcp >/dev/null; then
    echo "❌ Service is not running"
    
    # 2. Check for PID file conflicts
    if [ -f /var/run/snowflake-mcp.pid ]; then
        echo "🧹 Cleaning stale PID file"
        rm /var/run/snowflake-mcp.pid
    fi
    
    # 3. Check for port conflicts
    if netstat -tulpn | grep :8000 >/dev/null; then
        echo "⚠️  Port 8000 is in use, checking process..."
        netstat -tulpn | grep :8000
    fi
    
    # 4. Start service
    echo "🚀 Starting service..."
    uv run snowflake-mcp-daemon start
    
    # 5. Monitor startup
    tail -f /var/log/snowflake-mcp/application.log &
    TAIL_PID=$!
    
    # Wait for startup
    for i in {1..24}; do
        if curl -s http://localhost:8000/health >/dev/null 2>&1; then
            echo "✅ Service started successfully"
            kill $TAIL_PID
            break
        fi
        sleep 5
    done
    
else
    echo "✅ Service is running"
    curl -s http://localhost:8000/health | jq '.'
fi
```

### Database Connection Issues
```bash
#!/bin/bash
# Database connection recovery: scripts/ops/db_recovery.sh

echo "🔗 Database Connection Recovery"
echo "============================="

# 1. Test direct Snowflake connection
echo "1. Testing direct Snowflake connection..."
python scripts/ops/test_snowflake_direct.py

# 2. Check connection pool status
echo "2. Checking connection pool..."
POOL_STATUS=$(curl -s http://localhost:8000/health/detailed | jq '.connection_pool.status')
echo "Pool status: $POOL_STATUS"

if [ "$POOL_STATUS" != "\"healthy\"" ]; then
    echo "❌ Connection pool unhealthy, restarting pool..."
    
    # 3. Reset connection pool
    curl -X POST http://localhost:8000/admin/reset-connection-pool
    
    # 4. Wait for pool recovery
    sleep 10
    
    # 5. Test pool again
    POOL_STATUS=$(curl -s http://localhost:8000/health/detailed | jq '.connection_pool.status')
    echo "Pool status after reset: $POOL_STATUS"
fi

# 6. Test MCP tool functionality
echo "3. Testing MCP tool functionality..."
python scripts/ops/test_mcp_tools.py
```

## Performance Issues

### High Response Times
```bash
#!/bin/bash
# Performance investigation: scripts/ops/investigate_performance.sh

echo "📊 Performance Investigation"
echo "=========================="

# 1. Check current metrics
echo "1. Current response times:"
curl -s http://localhost:8000/metrics | grep 'mcp_request_duration_seconds' | tail -5

# 2. Check connection pool utilization
echo "2. Connection pool utilization:"
curl -s http://localhost:8000/health/detailed | jq '.connection_pool.utilization'

# 3. Check active queries
echo "3. Active queries in pool:"
curl -s http://localhost:8000/api/debug/active-queries | jq '.'

# 4. Check system resources
echo "4. System resources:"
top -bn1 | grep snowflake-mcp
free -h
df -h

# 5. Check Snowflake warehouse status
echo "5. Snowflake warehouse status:"
python scripts/ops/check_warehouse_status.py

# 6. Recommendations
echo "6. Performance recommendations:"
python scripts/ops/performance_recommendations.py
```

### Memory Issues
```bash
#!/bin/bash
# Memory usage investigation: scripts/ops/investigate_memory.sh

echo "💾 Memory Usage Investigation"
echo "==========================="

# 1. Current memory usage
echo "1. Process memory usage:"
ps aux | grep snowflake-mcp | grep -v grep

# 2. Connection pool memory
echo "2. Connection pool memory usage:"
curl -s http://localhost:8000/health/detailed | jq '.memory_usage'

# 3. Session memory tracking
echo "3. Active session memory:"
curl -s http://localhost:8000/api/debug/session-memory | jq '.'

# 4. System memory
echo "4. System memory:"
free -h
cat /proc/meminfo | grep -E "(MemTotal|MemAvailable|MemFree)"

# 5. Check for memory leaks
echo "5. Memory leak check (requires monitoring over time):"
python scripts/ops/memory_leak_detector.py --duration 300
```
```

**Step 2: Monitoring Setup Procedures**

Create `docs/operations/monitoring_setup.md`:

```markdown
# Monitoring Setup Guide

## Prometheus Configuration

### 1. Install Prometheus
```bash
# Download and install Prometheus
cd /opt
sudo wget https://github.com/prometheus/prometheus/releases/download/v2.45.0/prometheus-2.45.0.linux-amd64.tar.gz
sudo tar xvfz prometheus-2.45.0.linux-amd64.tar.gz
sudo mv prometheus-2.45.0.linux-amd64 prometheus
sudo chown -R prometheus:prometheus /opt/prometheus
```

### 2. Prometheus Configuration
Create `/opt/prometheus/prometheus.yml`:
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "snowflake_mcp_rules.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - alertmanager:9093

scrape_configs:
  - job_name: 'snowflake-mcp'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
    scrape_interval: 30s
    
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'node'
    static_configs:
      - targets: ['localhost:9100']
```

### 3. Alert Rules
Create `/opt/prometheus/snowflake_mcp_rules.yml`:
```yaml
groups:
- name: snowflake_mcp_alerts
  rules:
  
  # Service availability
  - alert: SnowflakeMCPDown
    expr: up{job="snowflake-mcp"} == 0
    for: 1m
    labels:
      severity: critical
    annotations:
      summary: "Snowflake MCP server is down"
      description: "Snowflake MCP server has been down for more than 1 minute"

  # High error rate  
  - alert: HighErrorRate
    expr: rate(mcp_errors_total[5m]) > 0.1
    for: 2m
    labels:
      severity: warning
    annotations:
      summary: "High error rate detected"
      description: "Error rate is {{ $value }} errors per second"

  # Connection pool exhaustion
  - alert: ConnectionPoolExhausted
    expr: mcp_db_connections_active / mcp_db_connections_max > 0.9
    for: 1m
    labels:
      severity: critical
    annotations:
      summary: "Connection pool nearly exhausted"
      description: "{{ $value }}% of connection pool is in use"

  # High response times
  - alert: HighResponseTime
    expr: histogram_quantile(0.95, rate(mcp_request_duration_seconds_bucket[5m])) > 10
    for: 2m
    labels:
      severity: warning
    annotations:
      summary: "High response times detected"
      description: "95th percentile response time is {{ $value }} seconds"

  # Memory usage
  - alert: HighMemoryUsage
    expr: mcp_memory_usage_bytes / mcp_memory_limit_bytes > 0.8
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "High memory usage"
      description: "Memory usage is {{ $value }}% of limit"
```

### 4. Systemd Service
Create `/etc/systemd/system/prometheus.service`:
```ini
[Unit]
Description=Prometheus
Wants=network-online.target
After=network-online.target

[Service]
User=prometheus
Group=prometheus
Type=simple
ExecStart=/opt/prometheus/prometheus \
  --config.file=/opt/prometheus/prometheus.yml \
  --storage.tsdb.path=/opt/prometheus/data \
  --web.console.templates=/opt/prometheus/consoles \
  --web.console.libraries=/opt/prometheus/console_libraries \
  --web.listen-address=0.0.0.0:9090 \
  --web.enable-lifecycle

[Install]
WantedBy=multi-user.target
```

## Grafana Dashboard Setup

### 1. Install Grafana
```bash
sudo apt-get install -y software-properties-common
sudo add-apt-repository "deb https://packages.grafana.com/oss/deb stable main"
wget -q -O - https://packages.grafana.com/gpg.key | sudo apt-key add -
sudo apt-get update
sudo apt-get install grafana
```

### 2. Dashboard Configuration
Create `configs/grafana/snowflake_mcp_dashboard.json`:
```json
{
  "dashboard": {
    "id": null,
    "title": "Snowflake MCP Server",
    "tags": ["snowflake", "mcp"],
    "timezone": "browser",
    "panels": [
      {
        "id": 1,
        "title": "Service Status",
        "type": "stat",
        "targets": [
          {
            "expr": "up{job=\"snowflake-mcp\"}",
            "legendFormat": "Service Up"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "mappings": [
              {"options": {"0": {"text": "Down"}}, "type": "value"},
              {"options": {"1": {"text": "Up"}}, "type": "value"}
            ]
          }
        }
      },
      {
        "id": 2,
        "title": "Request Rate",
        "type": "graph",
        "targets": [
          {
            "expr": "rate(mcp_requests_total[5m])",
            "legendFormat": "Requests/sec"
          }
        ]
      },
      {
        "id": 3,
        "title": "Response Times",
        "type": "graph",
        "targets": [
          {
            "expr": "histogram_quantile(0.50, rate(mcp_request_duration_seconds_bucket[5m]))",
            "legendFormat": "50th percentile"
          },
          {
            "expr": "histogram_quantile(0.95, rate(mcp_request_duration_seconds_bucket[5m]))",
            "legendFormat": "95th percentile"
          },
          {
            "expr": "histogram_quantile(0.99, rate(mcp_request_duration_seconds_bucket[5m]))",
            "legendFormat": "99th percentile"
          }
        ]
      },
      {
        "id": 4,
        "title": "Connection Pool",
        "type": "graph",
        "targets": [
          {
            "expr": "mcp_db_connections_active",
            "legendFormat": "Active Connections"
          },
          {
            "expr": "mcp_db_connections_idle",
            "legendFormat": "Idle Connections"
          },
          {
            "expr": "mcp_db_connections_max",
            "legendFormat": "Max Connections"
          }
        ]
      },
      {
        "id": 5,
        "title": "Error Rate",
        "type": "graph",
        "targets": [
          {
            "expr": "rate(mcp_errors_total[5m])",
            "legendFormat": "Errors/sec"
          }
        ]
      },
      {
        "id": 6,
        "title": "Active Sessions",
        "type": "stat",
        "targets": [
          {
            "expr": "mcp_active_sessions",
            "legendFormat": "Sessions"
          }
        ]
      }
    ],
    "time": {
      "from": "now-1h",
      "to": "now"
    },
    "refresh": "30s"
  }
}
```

### 3. Setup Script
Create `scripts/ops/setup_monitoring.py`:
```python
#!/usr/bin/env python3
"""Setup monitoring infrastructure."""

import os
import json
import subprocess
import requests
from pathlib import Path

class MonitoringSetup:
    """Setup monitoring infrastructure."""
    
    def __init__(self):
        self.grafana_url = "http://localhost:3000"
        self.grafana_user = "admin"
        self.grafana_password = "admin"
        
    def setup_all(self):
        """Setup complete monitoring stack."""
        print("🔧 Setting up monitoring infrastructure...")
        
        self.setup_prometheus()
        self.setup_grafana()
        self.setup_alertmanager()
        self.create_dashboards()
        self.configure_alerts()
        
        print("✅ Monitoring setup complete!")
    
    def setup_prometheus(self):
        """Setup Prometheus."""
        print("📊 Setting up Prometheus...")
        
        # Start Prometheus service
        subprocess.run(["sudo", "systemctl", "enable", "prometheus"])
        subprocess.run(["sudo", "systemctl", "start", "prometheus"])
        
        # Validate Prometheus is running
        try:
            response = requests.get("http://localhost:9090/api/v1/query?query=up")
            if response.status_code == 200:
                print("   ✅ Prometheus is running")
            else:
                print("   ❌ Prometheus health check failed")
        except:
            print("   ❌ Cannot connect to Prometheus")
    
    def setup_grafana(self):
        """Setup Grafana."""
        print("📈 Setting up Grafana...")
        
        # Start Grafana service
        subprocess.run(["sudo", "systemctl", "enable", "grafana-server"])
        subprocess.run(["sudo", "systemctl", "start", "grafana-server"])
        
        # Wait for Grafana to start
        import time
        time.sleep(10)
        
        # Add Prometheus data source
        self.add_prometheus_datasource()
        
        print("   ✅ Grafana is running")
    
    def add_prometheus_datasource(self):
        """Add Prometheus as data source."""
        datasource_config = {
            "name": "Prometheus",
            "type": "prometheus",
            "url": "http://localhost:9090",
            "access": "proxy",
            "isDefault": True
        }
        
        try:
            response = requests.post(
                f"{self.grafana_url}/api/datasources",
                json=datasource_config,
                auth=(self.grafana_user, self.grafana_password)
            )
            if response.status_code in [200, 409]:  # 409 = already exists
                print("   ✅ Prometheus datasource configured")
            else:
                print(f"   ❌ Failed to add datasource: {response.status_code}")
        except:
            print("   ❌ Cannot connect to Grafana API")
    
    def create_dashboards(self):
        """Create Grafana dashboards."""
        print("📊 Creating Grafana dashboards...")
        
        dashboard_file = Path("configs/grafana/snowflake_mcp_dashboard.json")
        if dashboard_file.exists():
            with open(dashboard_file) as f:
                dashboard_config = json.load(f)
            
            try:
                response = requests.post(
                    f"{self.grafana_url}/api/dashboards/db",
                    json=dashboard_config,
                    auth=(self.grafana_user, self.grafana_password)
                )
                if response.status_code == 200:
                    print("   ✅ Dashboard created successfully")
                else:
                    print(f"   ❌ Failed to create dashboard: {response.status_code}")
            except:
                print("   ❌ Cannot connect to Grafana API")
        else:
            print("   ❌ Dashboard configuration file not found")
    
    def setup_alertmanager(self):
        """Setup Alertmanager."""
        print("🚨 Setting up Alertmanager...")
        
        # Create Alertmanager configuration
        alertmanager_config = {
            "global": {
                "smtp_smarthost": "localhost:587",
                "smtp_from": "alerts@company.com"
            },
            "route": {
                "group_by": ["alertname"],
                "group_wait": "10s",
                "group_interval": "10s",
                "repeat_interval": "1h",
                "receiver": "web.hook"
            },
            "receivers": [
                {
                    "name": "web.hook",
                    "email_configs": [
                        {
                            "to": "admin@company.com",
                            "subject": "Snowflake MCP Alert: {{ .GroupLabels.alertname }}",
                            "body": "{{ range .Alerts }}{{ .Annotations.description }}{{ end }}"
                        }
                    ]
                }
            ]
        }
        
        # Save configuration
        config_path = Path("/opt/alertmanager/alertmanager.yml")
        config_path.parent.mkdir(exist_ok=True)
        
        import yaml
        with open(config_path, 'w') as f:
            yaml.dump(alertmanager_config, f)
        
        print("   ✅ Alertmanager configured")
    
    def configure_alerts(self):
        """Configure alert rules."""
        print("🔔 Configuring alert rules...")
        
        # Reload Prometheus configuration
        try:
            response = requests.post("http://localhost:9090/-/reload")
            if response.status_code == 200:
                print("   ✅ Alert rules loaded")
            else:
                print("   ❌ Failed to reload Prometheus config")
        except:
            print("   ❌ Cannot connect to Prometheus")

if __name__ == "__main__":
    setup = MonitoringSetup()
    setup.setup_all()
```

## Log Management

### 1. Log Rotation Configuration
Create `/etc/logrotate.d/snowflake-mcp`:
```bash
/var/log/snowflake-mcp/*.log {
    daily
    missingok
    rotate 52
    compress
    delaycompress
    notifempty
    create 644 snowflake-mcp snowflake-mcp
    postrotate
        systemctl reload snowflake-mcp
    endscript
}
```

### 2. Centralized Logging
```bash
# Setup rsyslog for centralized logging
echo "# Snowflake MCP logging" >> /etc/rsyslog.conf
echo "local0.* /var/log/snowflake-mcp/application.log" >> /etc/rsyslog.conf
systemctl restart rsyslog
```
```

