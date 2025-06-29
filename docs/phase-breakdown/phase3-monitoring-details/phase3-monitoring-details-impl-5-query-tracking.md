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

### 5. Query Performance Tracking {#query-tracking}

**Step 1: Query Performance Analyzer**

Create `snowflake_mcp_server/monitoring/query_analyzer.py`:

```python
"""Query performance analysis and tracking."""

import asyncio
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from collections import defaultdict, deque
import statistics
import re

from ..monitoring.metrics import metrics

logger = logging.getLogger(__name__)


@dataclass
class QueryExecution:
    """Single query execution record."""
    query_id: str
    query_text: str
    query_type: str
    database: str
    schema: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_ms: Optional[float] = None
    rows_returned: int = 0
    bytes_processed: int = 0
    client_id: str = "unknown"
    request_id: str = "unknown"
    status: str = "running"  # running, completed, failed, timeout
    error_message: Optional[str] = None


@dataclass
class QueryStats:
    """Aggregated query statistics."""
    query_pattern: str
    execution_count: int = 0
    total_duration_ms: float = 0.0
    avg_duration_ms: float = 0.0
    min_duration_ms: float = float('inf')
    max_duration_ms: float = 0.0
    p95_duration_ms: float = 0.0
    total_rows: int = 0
    total_bytes: int = 0
    success_count: int = 0
    error_count: int = 0
    recent_executions: deque = field(default_factory=lambda: deque(maxlen=100))
    
    def update(self, execution: QueryExecution) -> None:
        """Update stats with new execution."""
        if execution.duration_ms is None:
            return
        
        self.execution_count += 1
        self.total_duration_ms += execution.duration_ms
        self.avg_duration_ms = self.total_duration_ms / self.execution_count
        
        self.min_duration_ms = min(self.min_duration_ms, execution.duration_ms)
        self.max_duration_ms = max(self.max_duration_ms, execution.duration_ms)
        
        self.total_rows += execution.rows_returned
        self.total_bytes += execution.bytes_processed
        
        if execution.status == "completed":
            self.success_count += 1
        else:
            self.error_count += 1
        
        self.recent_executions.append(execution.duration_ms)
        
        # Calculate P95
        if len(self.recent_executions) >= 20:
            sorted_durations = sorted(self.recent_executions)
            p95_index = int(0.95 * len(sorted_durations))
            self.p95_duration_ms = sorted_durations[p95_index]


class QueryPerformanceAnalyzer:
    """Analyze and track query performance patterns."""
    
    def __init__(self, retention_hours: int = 24):
        self.retention_hours = retention_hours
        self._active_queries: Dict[str, QueryExecution] = {}
        self._query_history: deque = deque(maxlen=10000)
        self._pattern_stats: Dict[str, QueryStats] = {}
        self._lock = asyncio.Lock()
        
        # Query pattern matching
        self._patterns = {
            'SHOW_DATABASES': r'SHOW\s+DATABASES',
            'SHOW_TABLES': r'SHOW\s+TABLES',
            'SHOW_VIEWS': r'SHOW\s+VIEWS',
            'DESCRIBE_TABLE': r'DESCRIBE\s+TABLE',
            'DESCRIBE_VIEW': r'DESCRIBE\s+VIEW',
            'SELECT_SIMPLE': r'SELECT\s+\*\s+FROM\s+\w+\s*(?:LIMIT\s+\d+)?',
            'SELECT_COMPLEX': r'SELECT\s+.*\s+FROM\s+.*\s+(?:WHERE|JOIN|GROUP BY|ORDER BY)',
            'GET_DDL': r'SELECT\s+GET_DDL',
            'CURRENT_CONTEXT': r'SELECT\s+CURRENT_(?:DATABASE|SCHEMA)',
        }
        
        # Performance thresholds
        self.slow_query_threshold_ms = 5000  # 5 seconds
        self.very_slow_query_threshold_ms = 30000  # 30 seconds
        
        # Background cleanup task
        self._cleanup_task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start the analyzer."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Query performance analyzer started")
    
    async def stop(self) -> None:
        """Stop the analyzer."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
        logger.info("Query performance analyzer stopped")
    
    def _classify_query(self, query_text: str) -> str:
        """Classify query into performance pattern."""
        query_upper = query_text.upper().strip()
        
        for pattern_name, pattern_regex in self._patterns.items():
            if re.search(pattern_regex, query_upper, re.IGNORECASE):
                return pattern_name
        
        # Default classification based on first word
        first_word = query_upper.split()[0] if query_upper.split() else 'UNKNOWN'
        return f"{first_word}_QUERY"
    
    async def start_query(
        self,
        query_id: str,
        query_text: str,
        database: str = "unknown",
        schema: str = "unknown",
        client_id: str = "unknown",
        request_id: str = "unknown"
    ) -> None:
        """Record query start."""
        
        query_type = self._classify_query(query_text)
        
        execution = QueryExecution(
            query_id=query_id,
            query_text=query_text,
            query_type=query_type,
            database=database,
            schema=schema,
            start_time=datetime.now(),
            client_id=client_id,
            request_id=request_id
        )
        
        async with self._lock:
            self._active_queries[query_id] = execution
        
        logger.debug(f"Started tracking query {query_id}: {query_type}")
    
    async def complete_query(
        self,
        query_id: str,
        rows_returned: int = 0,
        bytes_processed: int = 0,
        status: str = "completed",
        error_message: Optional[str] = None
    ) -> None:
        """Record query completion."""
        
        async with self._lock:
            if query_id not in self._active_queries:
                logger.warning(f"Query {query_id} not found in active queries")
                return
            
            execution = self._active_queries.pop(query_id)
            execution.end_time = datetime.now()
            execution.duration_ms = (execution.end_time - execution.start_time).total_seconds() * 1000
            execution.rows_returned = rows_returned
            execution.bytes_processed = bytes_processed
            execution.status = status
            execution.error_message = error_message
            
            # Add to history
            self._query_history.append(execution)
            
            # Update pattern statistics
            pattern = execution.query_type
            if pattern not in self._pattern_stats:
                self._pattern_stats[pattern] = QueryStats(query_pattern=pattern)
            
            self._pattern_stats[pattern].update(execution)
            
            # Record metrics
            metrics.record_query(
                execution.query_type,
                execution.database,
                execution.duration_ms / 1000,  # Convert to seconds
                execution.rows_returned,
                execution.status
            )
            
            # Check for slow queries
            if execution.duration_ms > self.very_slow_query_threshold_ms:
                logger.warning(
                    f"Very slow query detected",
                    extra={
                        'query_id': query_id,
                        'duration_ms': execution.duration_ms,
                        'query_type': execution.query_type,
                        'query_preview': execution.query_text[:100]
                    }
                )
            elif execution.duration_ms > self.slow_query_threshold_ms:
                logger.info(
                    f"Slow query detected",
                    extra={
                        'query_id': query_id,
                        'duration_ms': execution.duration_ms,
                        'query_type': execution.query_type
                    }
                )
    
    async def get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary statistics."""
        
        async with self._lock:
            # Overall statistics
            total_queries = len(self._query_history)
            active_queries = len(self._active_queries)
            
            if not self._query_history:
                return {
                    "total_queries": 0,
                    "active_queries": active_queries,
                    "patterns": {}
                }
            
            # Time-based metrics
            recent_queries = [
                q for q in self._query_history
                if q.end_time and q.end_time > datetime.now() - timedelta(hours=1)
            ]
            
            avg_duration = statistics.mean([
                q.duration_ms for q in recent_queries if q.duration_ms
            ]) if recent_queries else 0
            
            slow_queries = len([
                q for q in recent_queries 
                if q.duration_ms and q.duration_ms > self.slow_query_threshold_ms
            ])
            
            # Pattern-based statistics
            pattern_summary = {}
            for pattern, stats in self._pattern_stats.items():
                pattern_summary[pattern] = {
                    "execution_count": stats.execution_count,
                    "avg_duration_ms": stats.avg_duration_ms,
                    "p95_duration_ms": stats.p95_duration_ms,
                    "success_rate": stats.success_count / stats.execution_count if stats.execution_count > 0 else 0,
                    "total_rows": stats.total_rows
                }
            
            return {
                "total_queries": total_queries,
                "active_queries": active_queries,
                "recent_hour_queries": len(recent_queries),
                "avg_duration_ms": avg_duration,
                "slow_queries_count": slow_queries,
                "patterns": pattern_summary
            }
    
    async def get_slow_queries(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get slowest recent queries."""
        
        async with self._lock:
            # Get completed queries with duration
            completed_queries = [
                q for q in self._query_history
                if q.duration_ms is not None and q.status == "completed"
            ]
            
            # Sort by duration (descending)
            slow_queries = sorted(
                completed_queries,
                key=lambda q: q.duration_ms,
                reverse=True
            )[:limit]
            
            return [
                {
                    "query_id": q.query_id,
                    "query_type": q.query_type,
                    "query_preview": q.query_text[:200],
                    "duration_ms": q.duration_ms,
                    "rows_returned": q.rows_returned,
                    "database": q.database,
                    "client_id": q.client_id,
                    "start_time": q.start_time.isoformat()
                }
                for q in slow_queries
            ]
    
    async def _cleanup_loop(self) -> None:
        """Background cleanup of old query data."""
        while True:
            try:
                await asyncio.sleep(3600)  # Cleanup every hour
                await self._cleanup_old_data()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in query analyzer cleanup: {e}")
    
    async def _cleanup_old_data(self) -> None:
        """Clean up old query execution data."""
        cutoff_time = datetime.now() - timedelta(hours=self.retention_hours)
        
        async with self._lock:
            # Clean up history
            old_count = len(self._query_history)
            self._query_history = deque([
                q for q in self._query_history
                if q.start_time > cutoff_time
            ], maxlen=10000)
            
            cleaned_count = old_count - len(self._query_history)
            if cleaned_count > 0:
                logger.info(f"Cleaned up {cleaned_count} old query records")


# Global query analyzer
query_analyzer = QueryPerformanceAnalyzer()
```

## Testing Strategy

Create `tests/test_monitoring.py`:

```python
import pytest
import asyncio
from prometheus_client import CollectorRegistry

from snowflake_mcp_server.monitoring.metrics import MCPMetrics
from snowflake_mcp_server.monitoring.query_analyzer import QueryPerformanceAnalyzer

def test_metrics_collection():
    """Test basic metrics collection."""
    registry = CollectorRegistry()
    metrics = MCPMetrics(registry)
    
    # Record some metrics
    metrics.record_request("list_databases", "claude_desktop", 0.5)
    metrics.record_query("show", "test_db", 1.2, 10)
    metrics.update_connection_pool_metrics(5, 2, 3)
    
    # Get metrics text
    metrics_text = metrics.get_metrics_text()
    
    assert "mcp_requests_total" in metrics_text
    assert "mcp_db_query_duration_seconds" in metrics_text
    assert "mcp_db_connections_total" in metrics_text

@pytest.mark.asyncio
async def test_query_performance_analyzer():
    """Test query performance tracking."""
    analyzer = QueryPerformanceAnalyzer()
    await analyzer.start()
    
    try:
        # Start and complete a query
        await analyzer.start_query(
            "test_query_1",
            "SELECT * FROM test_table",
            "test_db",
            "public",
            "test_client",
            "test_request"
        )
        
        await asyncio.sleep(0.1)  # Simulate query execution
        
        await analyzer.complete_query(
            "test_query_1",
            rows_returned=100,
            bytes_processed=1024,
            status="completed"
        )
        
        # Get performance summary
        summary = await analyzer.get_performance_summary()
        
        assert summary["total_queries"] == 1
        assert "SELECT_SIMPLE" in summary["patterns"]
        
    finally:
        await analyzer.stop()

@pytest.mark.asyncio
async def test_structured_logging():
    """Test structured logging configuration."""
    from snowflake_mcp_server.monitoring.logging_config import StructuredLogger
    
    logger = StructuredLogger("test")
    
    # Test various log methods
    logger.info("Test info message", test_field="value")
    logger.log_request_start("test_tool", {"arg1": "value1"})
    logger.log_database_operation("SELECT", "SELECT * FROM test", 150.5, 10)
```

## Verification Steps

1. **Metrics Collection**: Verify Prometheus metrics are properly collected and exposed
2. **Structured Logging**: Confirm logs include correlation IDs and proper context
3. **Dashboard Functionality**: Test Grafana dashboards display accurate data
4. **Alert Rules**: Verify alerts trigger under appropriate conditions
5. **Query Performance**: Confirm slow query detection and tracking works
6. **Health Monitoring**: Test health check endpoints return accurate status

## Completion Criteria

- [ ] Prometheus metrics endpoint exposes comprehensive server metrics
- [ ] Structured logging includes request correlation IDs and client context
- [ ] Grafana dashboards provide real-time visibility into server performance
- [ ] Alert rules trigger notifications for critical issues
- [ ] Query performance analyzer tracks slow queries and patterns
- [ ] Health monitoring detects and reports component failures
- [ ] Performance metrics help identify bottlenecks and optimization opportunities
- [ ] Automated alerting reduces mean time to detection for issues
- [ ] Log analysis supports effective debugging and troubleshooting
- [ ] Monitoring overhead is under 5% of total server performance