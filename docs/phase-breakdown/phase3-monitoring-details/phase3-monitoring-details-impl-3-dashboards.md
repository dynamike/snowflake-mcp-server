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

### 3. Performance Monitoring Dashboards {#dashboards}

**Step 1: Grafana Dashboard Configuration**

Create `monitoring/grafana/dashboards/mcp-overview.json`:

```json
{
  "dashboard": {
    "id": null,
    "title": "Snowflake MCP Server Overview",
    "tags": ["mcp", "snowflake"],
    "timezone": "browser",
    "panels": [
      {
        "id": 1,
        "title": "Request Rate",
        "type": "stat",
        "targets": [
          {
            "expr": "rate(mcp_requests_total[5m])",
            "legendFormat": "Requests/sec"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "reqps"
          }
        },
        "gridPos": {"h": 8, "w": 6, "x": 0, "y": 0}
      },
      {
        "id": 2,
        "title": "Response Time",
        "type": "stat",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, rate(mcp_request_duration_seconds_bucket[5m]))",
            "legendFormat": "95th percentile"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "s"
          }
        },
        "gridPos": {"h": 8, "w": 6, "x": 6, "y": 0}
      },
      {
        "id": 3,
        "title": "Active Connections",
        "type": "stat",
        "targets": [
          {
            "expr": "mcp_db_connections_active",
            "legendFormat": "Active"
          }
        ],
        "gridPos": {"h": 8, "w": 6, "x": 12, "y": 0}
      },
      {
        "id": 4,
        "title": "Error Rate",
        "type": "stat",
        "targets": [
          {
            "expr": "rate(mcp_requests_total{status=\"error\"}[5m])",
            "legendFormat": "Errors/sec"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "reqps",
            "color": {"mode": "thresholds"},
            "thresholds": {
              "steps": [
                {"color": "green", "value": 0},
                {"color": "yellow", "value": 0.1},
                {"color": "red", "value": 1}
              ]
            }
          }
        },
        "gridPos": {"h": 8, "w": 6, "x": 18, "y": 0}
      },
      {
        "id": 5,
        "title": "Request Volume by Method",
        "type": "timeseries",
        "targets": [
          {
            "expr": "rate(mcp_requests_total[5m])",
            "legendFormat": "{{method}}"
          }
        ],
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 8}
      },
      {
        "id": 6,
        "title": "Database Query Performance",
        "type": "timeseries",
        "targets": [
          {
            "expr": "histogram_quantile(0.50, rate(mcp_db_query_duration_seconds_bucket[5m]))",
            "legendFormat": "50th percentile"
          },
          {
            "expr": "histogram_quantile(0.95, rate(mcp_db_query_duration_seconds_bucket[5m]))",
            "legendFormat": "95th percentile"
          }
        ],
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": 8}
      },
      {
        "id": 7,
        "title": "Connection Pool Status",
        "type": "timeseries",
        "targets": [
          {
            "expr": "mcp_db_connections_total",
            "legendFormat": "Total"
          },
          {
            "expr": "mcp_db_connections_active",
            "legendFormat": "Active"
          },
          {
            "expr": "mcp_db_connections_idle",
            "legendFormat": "Idle"
          }
        ],
        "gridPos": {"h": 8, "w": 8, "x": 0, "y": 16}
      },
      {
        "id": 8,
        "title": "Client Sessions",
        "type": "timeseries",
        "targets": [
          {
            "expr": "mcp_client_sessions_active",
            "legendFormat": "{{client_type}}"
          }
        ],
        "gridPos": {"h": 8, "w": 8, "x": 8, "y": 16}
      },
      {
        "id": 9,
        "title": "System Health",
        "type": "timeseries",
        "targets": [
          {
            "expr": "mcp_health_status",
            "legendFormat": "{{component}}"
          }
        ],
        "gridPos": {"h": 8, "w": 8, "x": 16, "y": 16}
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

**Step 2: Dashboard Management Script**

Create `scripts/manage_dashboards.py`:

```python
#!/usr/bin/env python3
"""Manage Grafana dashboards for MCP monitoring."""

import json
import os
import requests
from pathlib import Path
from typing import Dict, Any

class DashboardManager:
    """Manage Grafana dashboards."""
    
    def __init__(self, grafana_url: str, api_key: str):
        self.grafana_url = grafana_url.rstrip('/')
        self.headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
    
    def create_dashboard(self, dashboard_config: Dict[str, Any]) -> bool:
        """Create or update dashboard."""
        url = f"{self.grafana_url}/api/dashboards/db"
        
        response = requests.post(url, headers=self.headers, json=dashboard_config)
        
        if response.status_code == 200:
            print(f"Dashboard '{dashboard_config['dashboard']['title']}' created successfully")
            return True
        else:
            print(f"Failed to create dashboard: {response.text}")
            return False
    
    def create_datasource(self, datasource_config: Dict[str, Any]) -> bool:
        """Create Prometheus datasource."""
        url = f"{self.grafana_url}/api/datasources"
        
        response = requests.post(url, headers=self.headers, json=datasource_config)
        
        if response.status_code == 200:
            print("Prometheus datasource created successfully")
            return True
        else:
            print(f"Failed to create datasource: {response.text}")
            return False
    
    def setup_monitoring(self, prometheus_url: str) -> None:
        """Setup complete monitoring stack."""
        
        # Create Prometheus datasource
        datasource_config = {
            "name": "Prometheus",
            "type": "prometheus",
            "url": prometheus_url,
            "access": "proxy",
            "isDefault": True
        }
        
        self.create_datasource(datasource_config)
        
        # Load and create dashboards
        dashboard_dir = Path("monitoring/grafana/dashboards")
        
        for dashboard_file in dashboard_dir.glob("*.json"):
            with open(dashboard_file) as f:
                dashboard_config = json.load(f)
            
            self.create_dashboard(dashboard_config)


def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Manage Grafana dashboards")
    parser.add_argument("--grafana-url", required=True, help="Grafana URL")
    parser.add_argument("--api-key", required=True, help="Grafana API key")
    parser.add_argument("--prometheus-url", default="http://localhost:9090", help="Prometheus URL")
    
    args = parser.parse_args()
    
    manager = DashboardManager(args.grafana_url, args.api_key)
    manager.setup_monitoring(args.prometheus_url)


if __name__ == "__main__":
    main()
```

