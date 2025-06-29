# Phase 3: Monitoring & Observability Implementation Details

## Context & Overview

The current Snowflake MCP server lacks comprehensive monitoring capabilities, making it difficult to diagnose performance issues, track usage patterns, or identify potential problems before they impact users. Production deployments require robust observability to ensure reliability and performance.

**Current Limitations:**
- No metrics collection or monitoring endpoints
- Basic logging without structured format or correlation IDs
- No performance tracking or alerting capabilities
- No visibility into connection pool health or query performance
- Missing operational dashboards and alerting

**Target Architecture:**
- Prometheus metrics collection with custom metrics
- Structured logging with correlation IDs and request tracing
- Performance monitoring dashboards with Grafana
- Automated alerting for critical issues
- Query performance tracking and analysis

## Dependencies Required

Add to `pyproject.toml`:
```toml
dependencies = [
    # Existing dependencies...
    "prometheus-client>=0.18.0",  # Metrics collection
    "structlog>=23.2.0",         # Structured logging
    "opentelemetry-api>=1.20.0", # Tracing support
    "opentelemetry-sdk>=1.20.0", # Tracing implementation
    "opentelemetry-instrumentation-asyncio>=0.41b0",  # Async tracing
]

[project.optional-dependencies]
monitoring = [
    "grafana-client>=3.6.0",     # Dashboard management
    "alertmanager-client>=0.1.0", # Alert management  
    "pystatsd>=0.4.0",           # StatsD metrics
]
```

## Implementation Plan

### 4. Automated Alerting {#alerting}

**Step 1: Alert Rules Configuration**

Create `monitoring/prometheus/alerts.yml`:

```yaml
groups:
  - name: mcp_server_alerts
    rules:
      - alert: MCPServerDown
        expr: up{job="mcp-server"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "MCP Server is down"
          description: "The MCP server instance {{ $labels.instance }} has been down for more than 1 minute."
      
      - alert: HighErrorRate
        expr: rate(mcp_requests_total{status="error"}[5m]) > 0.1
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High error rate detected"
          description: "Error rate is {{ $value }} errors per second over the last 5 minutes."
      
      - alert: HighResponseTime
        expr: histogram_quantile(0.95, rate(mcp_request_duration_seconds_bucket[5m])) > 5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High response time"
          description: "95th percentile response time is {{ $value }}s over the last 5 minutes."
      
      - alert: DatabaseConnectionPoolExhausted
        expr: mcp_db_connections_active / mcp_db_connections_total > 0.9
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "Database connection pool nearly exhausted"
          description: "Connection pool utilization is {{ $value | humanizePercentage }}."
      
      - alert: DatabaseQueryTimeout
        expr: increase(mcp_db_queries_total{status="timeout"}[5m]) > 5
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "Database query timeouts detected"
          description: "{{ $value }} query timeouts in the last 5 minutes."
      
      - alert: UnhealthyComponent
        expr: mcp_health_status < 1
        for: 3m
        labels:
          severity: critical
        annotations:
          summary: "Unhealthy component detected"
          description: "Component {{ $labels.component }} is unhealthy."
      
      - alert: MemoryUsageHigh
        expr: process_resident_memory_bytes / 1024 / 1024 > 500
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High memory usage"
          description: "Memory usage is {{ $value }}MB."
      
      - alert: TooManyActiveClients
        expr: sum(mcp_client_sessions_active) > 50
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High number of active clients"
          description: "{{ $value }} active client sessions."

  - name: mcp_performance_alerts
    rules:
      - alert: SlowDatabaseQueries
        expr: histogram_quantile(0.90, rate(mcp_db_query_duration_seconds_bucket[10m])) > 10
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Slow database queries detected"
          description: "90th percentile query time is {{ $value }}s over the last 10 minutes."
      
      - alert: ConnectionAcquisitionSlow
        expr: histogram_quantile(0.95, rate(mcp_db_connection_acquire_duration_seconds_bucket[5m])) > 1
        for: 3m
        labels:
          severity: warning
        annotations:
          summary: "Slow connection acquisition"
          description: "95th percentile connection acquisition time is {{ $value }}s."
      
      - alert: RateLimitViolations
        expr: rate(mcp_rate_limit_violations_total[5m]) > 1
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "Rate limit violations detected"
          description: "{{ $value }} rate limit violations per second."
```

**Step 2: Alertmanager Configuration**

Create `monitoring/alertmanager/config.yml`:

```yaml
global:
  smtp_smarthost: 'localhost:587'
  smtp_from: 'alerts@example.com'

route:
  group_by: ['alertname']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h
  receiver: 'web.hook'
  routes:
    - match:
        severity: critical
      receiver: 'critical-alerts'
    - match:
        severity: warning
      receiver: 'warning-alerts'

receivers:
  - name: 'web.hook'
    webhook_configs:
      - url: 'http://localhost:5001/webhook'
  
  - name: 'critical-alerts'
    email_configs:
      - to: 'ops-team@example.com'
        subject: 'CRITICAL: MCP Server Alert'
        body: |
          Alert: {{ .GroupLabels.alertname }}
          Summary: {{ range .Alerts }}{{ .Annotations.summary }}{{ end }}
          Description: {{ range .Alerts }}{{ .Annotations.description }}{{ end }}
    slack_configs:
      - api_url: 'YOUR_SLACK_WEBHOOK_URL'
        channel: '#alerts-critical'
        title: 'CRITICAL: MCP Server Alert'
        text: '{{ range .Alerts }}{{ .Annotations.summary }}{{ end }}'
  
  - name: 'warning-alerts'
    email_configs:
      - to: 'dev-team@example.com'
        subject: 'WARNING: MCP Server Alert'
        body: |
          Alert: {{ .GroupLabels.alertname }}
          Summary: {{ range .Alerts }}{{ .Annotations.summary }}{{ end }}

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname', 'instance']
```

