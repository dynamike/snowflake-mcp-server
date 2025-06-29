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

### 4. Configuration Management {#configuration}

Update `snowflake_mcp_server/main.py` to use environment-based pool configuration:

```python
import os
from datetime import timedelta

def get_pool_config() -> ConnectionPoolConfig:
    """Load connection pool configuration from environment."""
    return ConnectionPoolConfig(
        min_size=int(os.getenv("SNOWFLAKE_POOL_MIN_SIZE", "2")),
        max_size=int(os.getenv("SNOWFLAKE_POOL_MAX_SIZE", "10")),
        max_inactive_time=timedelta(minutes=int(os.getenv("SNOWFLAKE_POOL_MAX_INACTIVE_MINUTES", "30"))),
        health_check_interval=timedelta(minutes=int(os.getenv("SNOWFLAKE_POOL_HEALTH_CHECK_MINUTES", "5"))),
        connection_timeout=float(os.getenv("SNOWFLAKE_POOL_CONNECTION_TIMEOUT", "30.0")),
        retry_attempts=int(os.getenv("SNOWFLAKE_POOL_RETRY_ATTEMPTS", "3")),
    )


async def initialize_async_infrastructure():
    """Initialize async connection infrastructure."""
    snowflake_config = get_snowflake_config()
    pool_config = get_pool_config()
    
    from .utils.async_pool import initialize_connection_pool
    from .utils.health_monitor import health_monitor
    
    await initialize_connection_pool(snowflake_config, pool_config)
    await health_monitor.start_monitoring()
```

