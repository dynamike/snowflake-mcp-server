# Phase 1: Connection Pooling Implementation Details

## Context & Overview

The current Snowflake MCP server uses a singleton connection pattern in `snowflake_mcp_server/utils/snowflake_conn.py` with a global `connection_manager` instance. This creates bottlenecks when multiple MCP clients (Claude Desktop, Claude Code, Roo Code) attempt concurrent database operations.

**Current Issues:**
- Single shared connection causes blocking between concurrent requests
- Thread-based locking reduces async performance benefits
- Connection refresh logic happens globally, affecting all clients
- Memory leaks possible due to shared connection state

**Target Architecture:**
- Async connection pool with configurable sizing
- Per-request connection acquisition/release
- Health monitoring with automatic connection replacement
- Proper connection lifecycle management

## Dependencies Required

Add to `pyproject.toml`:
```toml
dependencies = [
    # Existing dependencies...
    "asyncpg>=0.28.0",  # For async connection utilities
    "asyncio-pool>=0.6.0",  # Connection pooling support
    "aiofiles>=23.2.0",  # Async file operations for key loading
]
```

## Implementation Plan

### 3. Health Monitoring Implementation {#health-monitoring}

Create `snowflake_mcp_server/utils/health_monitor.py`:

```python
"""Connection health monitoring utilities."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class HealthMetrics:
    """Health metrics for connection monitoring."""
    timestamp: datetime
    total_connections: int
    healthy_connections: int
    failed_health_checks: int
    average_response_time_ms: float
    errors_last_hour: int


class HealthMonitor:
    """Monitor connection pool health and performance."""
    
    def __init__(self, check_interval: timedelta = timedelta(minutes=1)):
        self.check_interval = check_interval
        self._metrics_history: List[HealthMetrics] = []
        self._error_count = 0
        self._monitoring_task: Optional[asyncio.Task] = None
        self._running = False
    
    async def start_monitoring(self) -> None:
        """Start health monitoring background task."""
        if self._running:
            return
        
        self._running = True
        self._monitoring_task = asyncio.create_task(self._monitoring_loop())
    
    async def stop_monitoring(self) -> None:
        """Stop health monitoring."""
        self._running = False
        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
    
    async def _monitoring_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                await self._collect_metrics()
                await asyncio.sleep(self.check_interval.total_seconds())
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health monitoring error: {e}")
                self._error_count += 1
    
    async def _collect_metrics(self) -> None:
        """Collect current health metrics."""
        try:
            from .async_pool import get_connection_pool
            pool = await get_connection_pool()
            
            # Measure response time with simple query
            start_time = datetime.now()
            async with pool.acquire() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.close()
            response_time = (datetime.now() - start_time).total_seconds() * 1000
            
            # Get pool statistics
            stats = pool.get_stats()
            
            # Create metrics snapshot
            metrics = HealthMetrics(
                timestamp=datetime.now(),
                total_connections=stats["total_connections"],
                healthy_connections=stats["healthy_connections"],
                failed_health_checks=0,  # Will be tracked separately
                average_response_time_ms=response_time,
                errors_last_hour=self._get_recent_errors()
            )
            
            self._metrics_history.append(metrics)
            
            # Keep only last 24 hours of metrics
            cutoff = datetime.now() - timedelta(hours=24)
            self._metrics_history = [
                m for m in self._metrics_history if m.timestamp > cutoff
            ]
            
        except Exception as e:
            logger.error(f"Failed to collect health metrics: {e}")
            self._error_count += 1
    
    def _get_recent_errors(self) -> int:
        """Get error count from last hour."""
        cutoff = datetime.now() - timedelta(hours=1)
        return sum(
            1 for m in self._metrics_history 
            if m.timestamp > cutoff and m.failed_health_checks > 0
        )
    
    def get_current_health(self) -> Dict:
        """Get current health status."""
        if not self._metrics_history:
            return {"status": "unknown", "message": "No metrics available"}
        
        latest = self._metrics_history[-1]
        
        # Determine health status
        if latest.healthy_connections == 0:
            status = "critical"
            message = "No healthy connections available"
        elif latest.healthy_connections < latest.total_connections * 0.5:
            status = "degraded"
            message = f"Only {latest.healthy_connections}/{latest.total_connections} connections healthy"
        elif latest.average_response_time_ms > 5000:  # 5 second threshold
            status = "slow"
            message = f"High response time: {latest.average_response_time_ms:.0f}ms"
        else:
            status = "healthy"
            message = "All systems operational"
        
        return {
            "status": status,
            "message": message,
            "metrics": {
                "total_connections": latest.total_connections,
                "healthy_connections": latest.healthy_connections,
                "response_time_ms": latest.average_response_time_ms,
                "errors_last_hour": latest.errors_last_hour,
                "last_check": latest.timestamp.isoformat()
            }
        }
    
    def get_metrics_history(self, hours: int = 1) -> List[HealthMetrics]:
        """Get metrics history for specified time period."""
        cutoff = datetime.now() - timedelta(hours=hours)
        return [m for m in self._metrics_history if m.timestamp > cutoff]


# Global health monitor instance
health_monitor = HealthMonitor()
```

