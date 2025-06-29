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

### 1. Prometheus Metrics Collection {#prometheus-metrics}

**Step 1: Core Metrics Framework**

Create `snowflake_mcp_server/monitoring/metrics.py`:

```python
"""Prometheus metrics collection for Snowflake MCP server."""

import time
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from functools import wraps
import asyncio

from prometheus_client import (
    Counter, Histogram, Gauge, Summary, Info,
    CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST
)

logger = logging.getLogger(__name__)


class MCPMetrics:
    """Centralized metrics collection for MCP server."""
    
    def __init__(self, registry: Optional[CollectorRegistry] = None):
        self.registry = registry or CollectorRegistry()
        
        # Request metrics
        self.requests_total = Counter(
            'mcp_requests_total',
            'Total number of MCP requests',
            ['method', 'client_type', 'status'],
            registry=self.registry
        )
        
        self.request_duration = Histogram(
            'mcp_request_duration_seconds',
            'Time spent processing MCP requests',
            ['method', 'client_type'],
            buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
            registry=self.registry
        )
        
        self.request_size = Histogram(
            'mcp_request_size_bytes',
            'Size of MCP request payloads',
            ['method'],
            buckets=[100, 500, 1000, 5000, 10000, 50000],
            registry=self.registry
        )
        
        self.response_size = Histogram(
            'mcp_response_size_bytes',
            'Size of MCP response payloads',
            ['method'],
            buckets=[100, 500, 1000, 5000, 10000, 50000, 100000],
            registry=self.registry
        )
        
        # Connection pool metrics
        self.db_connections_total = Gauge(
            'mcp_db_connections_total',
            'Total number of database connections',
            registry=self.registry
        )
        
        self.db_connections_active = Gauge(
            'mcp_db_connections_active',
            'Number of active database connections',
            registry=self.registry
        )
        
        self.db_connections_idle = Gauge(
            'mcp_db_connections_idle',
            'Number of idle database connections',
            registry=self.registry
        )
        
        self.db_connection_acquire_duration = Histogram(
            'mcp_db_connection_acquire_duration_seconds',
            'Time to acquire database connection',
            buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
            registry=self.registry
        )
        
        # Query metrics
        self.db_queries_total = Counter(
            'mcp_db_queries_total',
            'Total database queries executed',
            ['query_type', 'database', 'status'],
            registry=self.registry
        )
        
        self.db_query_duration = Histogram(
            'mcp_db_query_duration_seconds',
            'Database query execution time',
            ['query_type', 'database'],
            buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0],
            registry=self.registry
        )
        
        self.db_query_rows = Histogram(
            'mcp_db_query_rows_returned',
            'Number of rows returned by queries',
            ['query_type'],
            buckets=[1, 10, 50, 100, 500, 1000, 5000, 10000],
            registry=self.registry
        )
        
        # Client session metrics
        self.client_sessions_total = Gauge(
            'mcp_client_sessions_total',
            'Total number of client sessions',
            ['client_type'],
            registry=self.registry
        )
        
        self.client_sessions_active = Gauge(
            'mcp_client_sessions_active',
            'Number of active client sessions',
            ['client_type'],
            registry=self.registry
        )
        
        self.client_session_duration = Summary(
            'mcp_client_session_duration_seconds',
            'Client session duration',
            ['client_type'],
            registry=self.registry
        )
        
        # Error metrics
        self.errors_total = Counter(
            'mcp_errors_total',
            'Total number of errors',
            ['error_type', 'component'],
            registry=self.registry
        )
        
        # Health metrics
        self.health_status = Gauge(
            'mcp_health_status',
            'Health status (1=healthy, 0=unhealthy)',
            ['component'],
            registry=self.registry
        )
        
        self.uptime_seconds = Gauge(
            'mcp_uptime_seconds',
            'Server uptime in seconds',
            registry=self.registry
        )
        
        # Rate limiting metrics
        self.rate_limit_violations = Counter(
            'mcp_rate_limit_violations_total',
            'Rate limit violations',
            ['client_id', 'limit_type'],
            registry=self.registry
        )
        
        self.rate_limit_tokens_remaining = Gauge(
            'mcp_rate_limit_tokens_remaining',
            'Remaining rate limit tokens',
            ['client_id'],
            registry=self.registry
        )
        
        # Server info
        self.server_info = Info(
            'mcp_server_info',
            'Server information',
            registry=self.registry
        )
        
        # Initialize server info
        self.server_info.info({
            'version': '0.2.0',
            'python_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            'start_time': datetime.now().isoformat()
        })
        
        # Track server start time for uptime calculation
        self._start_time = time.time()
    
    def record_request(self, method: str, client_type: str, duration: float, status: str = 'success') -> None:
        """Record MCP request metrics."""
        self.requests_total.labels(method=method, client_type=client_type, status=status).inc()
        self.request_duration.labels(method=method, client_type=client_type).observe(duration)
    
    def record_request_size(self, method: str, size_bytes: int) -> None:
        """Record request payload size."""
        self.request_size.labels(method=method).observe(size_bytes)
    
    def record_response_size(self, method: str, size_bytes: int) -> None:
        """Record response payload size."""
        self.response_size.labels(method=method).observe(size_bytes)
    
    def update_connection_pool_metrics(self, total: int, active: int, idle: int) -> None:
        """Update connection pool metrics."""
        self.db_connections_total.set(total)
        self.db_connections_active.set(active)
        self.db_connections_idle.set(idle)
    
    def record_connection_acquire(self, duration: float) -> None:
        """Record connection acquisition time."""
        self.db_connection_acquire_duration.observe(duration)
    
    def record_query(self, query_type: str, database: str, duration: float, rows: int, status: str = 'success') -> None:
        """Record database query metrics."""
        self.db_queries_total.labels(query_type=query_type, database=database, status=status).inc()
        self.db_query_duration.labels(query_type=query_type, database=database).observe(duration)
        self.db_query_rows.labels(query_type=query_type).observe(rows)
    
    def update_session_metrics(self, client_type: str, total: int, active: int) -> None:
        """Update client session metrics."""
        self.client_sessions_total.labels(client_type=client_type).set(total)
        self.client_sessions_active.labels(client_type=client_type).set(active)
    
    def record_session_duration(self, client_type: str, duration: float) -> None:
        """Record client session duration."""
        self.client_session_duration.labels(client_type=client_type).observe(duration)
    
    def record_error(self, error_type: str, component: str) -> None:
        """Record error occurrence."""
        self.errors_total.labels(error_type=error_type, component=component).inc()
    
    def update_health_status(self, component: str, is_healthy: bool) -> None:
        """Update component health status."""
        self.health_status.labels(component=component).set(1 if is_healthy else 0)
    
    def record_rate_limit_violation(self, client_id: str, limit_type: str) -> None:
        """Record rate limit violation."""
        self.rate_limit_violations.labels(client_id=client_id, limit_type=limit_type).inc()
    
    def update_rate_limit_tokens(self, client_id: str, tokens_remaining: int) -> None:
        """Update remaining rate limit tokens."""
        self.rate_limit_tokens_remaining.labels(client_id=client_id).set(tokens_remaining)
    
    def update_uptime(self) -> None:
        """Update server uptime."""
        uptime = time.time() - self._start_time
        self.uptime_seconds.set(uptime)
    
    def get_metrics_text(self) -> str:
        """Get metrics in Prometheus text format."""
        self.update_uptime()
        return generate_latest(self.registry).decode('utf-8')


# Global metrics instance
metrics = MCPMetrics()


def track_request_metrics(method: str, client_type: str = 'unknown'):
    """Decorator to track request metrics."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status = 'success'
            
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = 'error'
                metrics.record_error(type(e).__name__, 'request_handler')
                raise
            finally:
                duration = time.time() - start_time
                metrics.record_request(method, client_type, duration, status)
        
        return wrapper
    return decorator


def track_query_metrics(query_type: str, database: str = 'unknown'):
    """Decorator to track database query metrics."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status = 'success'
            rows = 0
            
            try:
                result = await func(*args, **kwargs)
                
                # Extract row count from result
                if isinstance(result, tuple) and len(result) >= 1:
                    if isinstance(result[0], list):
                        rows = len(result[0])
                
                return result
            except Exception as e:
                status = 'error'
                metrics.record_error(type(e).__name__, 'database_query')
                raise
            finally:
                duration = time.time() - start_time
                metrics.record_query(query_type, database, duration, rows, status)
        
        return wrapper
    return decorator
```

**Step 2: Metrics Integration with Handlers**

Update `snowflake_mcp_server/main.py`:

```python
from .monitoring.metrics import metrics, track_request_metrics

# Update handlers with metrics tracking
@track_request_metrics("list_databases")
async def handle_list_databases(
    name: str, arguments: Optional[Dict[str, Any]] = None,
    request_ctx: Optional[RequestContext] = None
) -> Sequence[Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]]:
    """Tool handler with metrics tracking."""
    
    # Record request size
    if arguments:
        import json
        request_size = len(json.dumps(arguments).encode())
        metrics.record_request_size("list_databases", request_size)
    
    try:
        async with get_isolated_database_ops(request_ctx) as db_ops:
            results, _ = await db_ops.execute_query_isolated("SHOW DATABASES")
            
            databases = [row[1] for row in results]
            result_text = "Available Snowflake databases:\n" + "\n".join(databases)
            
            # Record response size
            response_size = len(result_text.encode())
            metrics.record_response_size("list_databases", response_size)
            
            return [
                mcp_types.TextContent(
                    type="text",
                    text=result_text,
                )
            ]

    except Exception as e:
        logger.error(f"Error querying databases: {e}")
        return [
            mcp_types.TextContent(
                type="text", text=f"Error querying databases: {str(e)}"
            )
        ]


# Update database operations with query metrics
from .monitoring.metrics import track_query_metrics

class MetricsAwareDatabaseOperations(ClientIsolatedDatabaseOperations):
    """Database operations with metrics tracking."""
    
    @track_query_metrics("show", "system")
    async def execute_show_query(self, query: str) -> Tuple[List[Tuple], List[str]]:
        """Execute SHOW query with metrics."""
        return await self.execute_query_isolated(query)
    
    @track_query_metrics("select", "user_data")
    async def execute_select_query(self, query: str) -> Tuple[List[Tuple], List[str]]:
        """Execute SELECT query with metrics."""
        return await self.execute_query_isolated(query)
    
    @track_query_metrics("describe", "metadata")
    async def execute_describe_query(self, query: str) -> Tuple[List[Tuple], List[str]]:
        """Execute DESCRIBE query with metrics."""
        return await self.execute_query_isolated(query)
```

