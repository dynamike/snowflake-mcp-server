"""Prometheus metrics collection for Snowflake MCP server."""

import logging
import time
from functools import wraps
from typing import Dict, Optional

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Enum,
    Gauge,
    Histogram,
    Info,
    generate_latest,
    start_http_server,
)

from ..config import get_config

logger = logging.getLogger(__name__)


class MCPMetrics:
    """Centralized metrics collection for the MCP server."""
    
    def __init__(self, registry: Optional[CollectorRegistry] = None):
        self.registry = registry or CollectorRegistry()
        self.config = get_config()
        
        # Initialize all metrics
        self._init_request_metrics()
        self._init_connection_metrics()
        self._init_database_metrics()
        self._init_client_metrics()
        self._init_resource_metrics()
        self._init_error_metrics()
        self._init_performance_metrics()
        
        logger.info("Prometheus metrics initialized")
    
    def _init_request_metrics(self):
        """Initialize request-related metrics."""
        self.request_total = Counter(
            'mcp_requests_total',
            'Total number of MCP requests',
            ['client_id', 'tool_name', 'status'],
            registry=self.registry
        )
        
        self.request_duration = Histogram(
            'mcp_request_duration_seconds',
            'Request duration in seconds',
            ['client_id', 'tool_name'],
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
            registry=self.registry
        )
        
        self.concurrent_requests = Gauge(
            'mcp_concurrent_requests',
            'Number of concurrent requests',
            ['client_id'],
            registry=self.registry
        )
        
        self.request_size_bytes = Histogram(
            'mcp_request_size_bytes',
            'Request payload size in bytes',
            ['tool_name'],
            buckets=[100, 1000, 10000, 100000, 1000000],
            registry=self.registry
        )
        
        self.response_size_bytes = Histogram(
            'mcp_response_size_bytes', 
            'Response payload size in bytes',
            ['tool_name'],
            buckets=[100, 1000, 10000, 100000, 1000000, 10000000],
            registry=self.registry
        )
    
    def _init_connection_metrics(self):
        """Initialize connection-related metrics."""
        self.active_connections = Gauge(
            'mcp_active_connections',
            'Number of active connections',
            ['connection_type'],  # websocket, http, stdio
            registry=self.registry
        )
        
        self.connection_pool_size = Gauge(
            'mcp_connection_pool_size',
            'Size of Snowflake connection pool',
            ['status'],  # active, idle, total
            registry=self.registry
        )
        
        self.connection_pool_utilization = Gauge(
            'mcp_connection_pool_utilization_percent',
            'Connection pool utilization percentage',
            registry=self.registry
        )
        
        self.connection_acquisition_duration = Histogram(
            'mcp_connection_acquisition_seconds',
            'Time to acquire connection from pool',
            buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
            registry=self.registry
        )
        
        self.connection_lease_duration = Histogram(
            'mcp_connection_lease_seconds',
            'Connection lease duration',
            ['client_id'],
            buckets=[1, 5, 10, 30, 60, 300, 600, 1800],
            registry=self.registry
        )
    
    def _init_database_metrics(self):
        """Initialize database operation metrics."""
        self.query_total = Counter(
            'mcp_queries_total',
            'Total number of database queries',
            ['database', 'query_type', 'status'],
            registry=self.registry
        )
        
        self.query_duration = Histogram(
            'mcp_query_duration_seconds',
            'Database query execution time',
            ['database', 'query_type'],
            buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0],
            registry=self.registry
        )
        
        self.query_rows_returned = Histogram(
            'mcp_query_rows_returned',
            'Number of rows returned by queries',
            ['database'],
            buckets=[1, 10, 100, 1000, 10000, 100000, 1000000],
            registry=self.registry
        )
        
        self.transaction_total = Counter(
            'mcp_transactions_total',
            'Total number of transactions',
            ['status'],  # committed, rolled_back
            registry=self.registry
        )
        
        self.transaction_duration = Histogram(
            'mcp_transaction_duration_seconds',
            'Transaction duration',
            buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0],
            registry=self.registry
        )
    
    def _init_client_metrics(self):
        """Initialize client-related metrics."""
        self.active_clients = Gauge(
            'mcp_active_clients',
            'Number of active clients',
            registry=self.registry
        )
        
        self.client_sessions = Gauge(
            'mcp_client_sessions',
            'Number of client sessions',
            ['client_type'],
            registry=self.registry
        )
        
        self.client_requests_per_minute = Gauge(
            'mcp_client_requests_per_minute',
            'Client request rate per minute',
            ['client_id'],
            registry=self.registry
        )
        
        self.client_isolation_violations = Counter(
            'mcp_client_isolation_violations_total',
            'Number of client isolation violations',
            ['client_id', 'violation_type'],
            registry=self.registry
        )
    
    def _init_resource_metrics(self):
        """Initialize resource utilization metrics."""
        self.memory_usage_bytes = Gauge(
            'mcp_memory_usage_bytes',
            'Memory usage in bytes',
            ['component'],  # server, pool, sessions, cache
            registry=self.registry
        )
        
        self.cpu_usage_percent = Gauge(
            'mcp_cpu_usage_percent',
            'CPU usage percentage',
            registry=self.registry
        )
        
        self.resource_allocation = Gauge(
            'mcp_resource_allocation',
            'Resource allocation per client',
            ['client_id', 'resource_type'],
            registry=self.registry
        )
        
        self.resource_queue_size = Gauge(
            'mcp_resource_queue_size',
            'Number of pending resource requests',
            ['resource_type'],
            registry=self.registry
        )
    
    def _init_error_metrics(self):
        """Initialize error tracking metrics."""
        self.errors_total = Counter(
            'mcp_errors_total',
            'Total number of errors',
            ['error_type', 'component', 'severity'],
            registry=self.registry
        )
        
        self.rate_limit_hits = Counter(
            'mcp_rate_limit_hits_total',
            'Number of rate limit violations',
            ['client_id', 'limit_type'],
            registry=self.registry
        )
        
        self.circuit_breaker_state = Enum(
            'mcp_circuit_breaker_state',
            'Circuit breaker state',
            ['component'],
            states=['closed', 'open', 'half_open'],
            registry=self.registry
        )
        
        self.failed_connections = Counter(
            'mcp_failed_connections_total',
            'Number of failed connection attempts',
            ['reason'],
            registry=self.registry
        )
    
    def _init_performance_metrics(self):
        """Initialize performance metrics."""
        self.server_info = Info(
            'mcp_server_info',
            'Server information',
            registry=self.registry
        )
        
        self.uptime_seconds = Gauge(
            'mcp_uptime_seconds',
            'Server uptime in seconds',
            registry=self.registry
        )
        
        self.health_status = Enum(
            'mcp_health_status',
            'Server health status',
            states=['healthy', 'degraded', 'unhealthy'],
            registry=self.registry
        )
        
        # Set server info
        self.server_info.info({
            'version': self.config.app_version,
            'environment': self.config.environment,
            'python_version': f"{__import__('sys').version_info.major}.{__import__('sys').version_info.minor}",
        })
    
    # Convenience methods for recording metrics
    
    def record_request(self, client_id: str, tool_name: str, duration: float, 
                      status: str = "success", request_size: int = 0, 
                      response_size: int = 0):
        """Record a completed request."""
        self.request_total.labels(
            client_id=client_id, 
            tool_name=tool_name, 
            status=status
        ).inc()
        
        self.request_duration.labels(
            client_id=client_id, 
            tool_name=tool_name
        ).observe(duration)
        
        if request_size > 0:
            self.request_size_bytes.labels(tool_name=tool_name).observe(request_size)
        
        if response_size > 0:
            self.response_size_bytes.labels(tool_name=tool_name).observe(response_size)
    
    def record_query(self, database: str, query_type: str, duration: float,
                    rows_returned: int = 0, status: str = "success"):
        """Record a database query."""
        self.query_total.labels(
            database=database,
            query_type=query_type,
            status=status
        ).inc()
        
        self.query_duration.labels(
            database=database,
            query_type=query_type
        ).observe(duration)
        
        if rows_returned > 0:
            self.query_rows_returned.labels(database=database).observe(rows_returned)
    
    def record_connection_acquisition(self, duration: float):
        """Record connection acquisition time."""
        self.connection_acquisition_duration.observe(duration)
    
    def record_error(self, error_type: str, component: str, severity: str = "error"):
        """Record an error occurrence."""
        self.errors_total.labels(
            error_type=error_type,
            component=component,
            severity=severity
        ).inc()
    
    def update_connection_pool_metrics(self, active: int, idle: int, total: int):
        """Update connection pool metrics."""
        self.connection_pool_size.labels(status="active").set(active)
        self.connection_pool_size.labels(status="idle").set(idle)
        self.connection_pool_size.labels(status="total").set(total)
        
        utilization = (active / total * 100) if total > 0 else 0
        self.connection_pool_utilization.set(utilization)
    
    def update_client_metrics(self, active_clients: int, sessions_by_type: Dict[str, int]):
        """Update client-related metrics."""
        self.active_clients.set(active_clients)
        
        for client_type, count in sessions_by_type.items():
            self.client_sessions.labels(client_type=client_type).set(count)
    
    def update_resource_metrics(self, allocations: Dict[str, Dict[str, float]]):
        """Update resource allocation metrics."""
        for client_id, resources in allocations.items():
            for resource_type, amount in resources.items():
                self.resource_allocation.labels(
                    client_id=client_id,
                    resource_type=resource_type
                ).set(amount)
    
    def set_health_status(self, status: str):
        """Set server health status."""
        self.health_status.state(status)
    
    def get_metrics(self) -> str:
        """Get metrics in Prometheus format."""
        return generate_latest(self.registry).decode('utf-8')


# Global metrics instance
_metrics: Optional[MCPMetrics] = None


def get_metrics() -> MCPMetrics:
    """Get the global metrics instance."""
    global _metrics
    if _metrics is None:
        _metrics = MCPMetrics()
    return _metrics


def metrics_middleware(func):
    """Decorator to automatically collect metrics for functions."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        metrics = get_metrics()
        start_time = time.time()
        
        # Extract client_id and tool_name from function context
        client_id = kwargs.get('client_id', 'unknown')
        tool_name = getattr(func, '__name__', 'unknown')
        
        try:
            result = await func(*args, **kwargs)
            duration = time.time() - start_time
            
            metrics.record_request(
                client_id=client_id,
                tool_name=tool_name,
                duration=duration,
                status="success"
            )
            
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            
            metrics.record_request(
                client_id=client_id,
                tool_name=tool_name,
                duration=duration,
                status="error"
            )
            
            metrics.record_error(
                error_type=type(e).__name__,
                component="handler",
                severity="error"
            )
            
            raise
    
    return wrapper


class MetricsCollector:
    """Background metrics collector for system-wide metrics."""
    
    def __init__(self, metrics: MCPMetrics):
        self.metrics = metrics
        self.start_time = time.time()
        self._running = False
        self._task: Optional = None
    
    async def start(self):
        """Start the metrics collector."""
        self._running = True
        self._task = __import__('asyncio').create_task(self._collection_loop())
        logger.info("Metrics collector started")
    
    async def stop(self):
        """Stop the metrics collector."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except __import__('asyncio').CancelledError:
                pass
        logger.info("Metrics collector stopped")
    
    async def _collection_loop(self):
        """Main collection loop."""
        import asyncio
        import os

        import psutil
        
        process = psutil.Process(os.getpid())
        
        while self._running:
            try:
                # Update uptime
                uptime = time.time() - self.start_time
                self.metrics.uptime_seconds.set(uptime)
                
                # Update memory usage
                memory_info = process.memory_info()
                self.metrics.memory_usage_bytes.labels(component="server").set(memory_info.rss)
                
                # Update CPU usage
                cpu_percent = process.cpu_percent()
                self.metrics.cpu_usage_percent.set(cpu_percent)
                
                # Update connection pool metrics
                try:
                    from ..utils.async_pool import get_pool_status
                    pool_status = await get_pool_status()
                    
                    if pool_status['status'] == 'active':
                        self.metrics.update_connection_pool_metrics(
                            active=pool_status['active_connections'],
                            idle=pool_status['total_connections'] - pool_status['active_connections'],
                            total=pool_status['total_connections']
                        )
                except Exception as e:
                    logger.debug(f"Could not collect pool metrics: {e}")
                
                # Update session metrics
                try:
                    from ..utils.session_manager import get_session_manager
                    session_manager = await get_session_manager()
                    stats = await session_manager.get_session_stats()
                    
                    self.metrics.update_client_metrics(
                        active_clients=stats['unique_clients'],
                        sessions_by_type=stats['sessions_by_type']
                    )
                except Exception as e:
                    logger.debug(f"Could not collect session metrics: {e}")
                
                # Sleep for collection interval
                await asyncio.sleep(30)  # Collect every 30 seconds
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in metrics collection: {e}")
                await asyncio.sleep(30)


# Global collector instance
_collector: Optional[MetricsCollector] = None


async def start_metrics_collection():
    """Start background metrics collection."""
    global _collector
    if _collector is None:
        metrics = get_metrics()
        _collector = MetricsCollector(metrics)
        await _collector.start()


async def stop_metrics_collection():
    """Stop background metrics collection."""
    global _collector
    if _collector:
        await _collector.stop()
        _collector = None


def create_metrics_server(port: int = 9090, host: str = "0.0.0.0"):
    """Create standalone Prometheus metrics server."""
    try:
        start_http_server(port, host)
        logger.info(f"Prometheus metrics server started on {host}:{port}")
    except Exception as e:
        logger.error(f"Failed to start metrics server: {e}")


if __name__ == "__main__":
    # Test metrics
    import asyncio
    
    async def test_metrics():
        metrics = get_metrics()
        
        # Record some test metrics
        metrics.record_request("test_client", "execute_query", 0.5, "success")
        metrics.record_query("TEST_DB", "SELECT", 0.3, 100, "success")
        metrics.record_error("ConnectionError", "database", "error")
        
        # Start collector
        await start_metrics_collection()
        
        # Wait a bit
        await asyncio.sleep(2)
        
        # Stop collector
        await stop_metrics_collection()
        
        # Print metrics
        print(metrics.get_metrics())
    
    asyncio.run(test_metrics())