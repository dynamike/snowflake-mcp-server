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

### 5. Dependency Injection Updates {#dependency-injection}

Create dependency injection pattern for pool usage in handlers:

```python
# In snowflake_mcp_server/main.py

from contextlib import asynccontextmanager

@asynccontextmanager
async def get_database_connection():
    """Dependency injection for database connections."""
    from .utils.async_pool import get_connection_pool
    
    pool = await get_connection_pool()
    async with pool.acquire() as connection:
        yield connection


# Update handler example
async def handle_list_databases(
    name: str, arguments: Optional[Dict[str, Any]] = None
) -> Sequence[Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]]:
    """Tool handler to list all accessible Snowflake databases."""
    try:
        async with get_database_connection() as conn:
            # Execute query in executor to avoid blocking
            loop = asyncio.get_event_loop()
            
            def _execute_query():
                cursor = conn.cursor()
                cursor.execute("SHOW DATABASES")
                results = cursor.fetchall()
                cursor.close()
                return results
            
            results = await loop.run_in_executor(None, _execute_query)
            
            # Process results
            databases = [row[1] for row in results]
            
            return [
                mcp_types.TextContent(
                    type="text",
                    text="Available Snowflake databases:\n" + "\n".join(databases),
                )
            ]

    except Exception as e:
        logger.error(f"Error querying databases: {e}")
        return [
            mcp_types.TextContent(
                type="text", text=f"Error querying databases: {str(e)}"
            )
        ]
```

## Testing Strategy

### Unit Tests

Create `tests/test_connection_pool.py`:

```python
import pytest
import asyncio
from datetime import timedelta
from snowflake_mcp_server.utils.async_pool import AsyncConnectionPool, ConnectionPoolConfig
from snowflake_mcp_server.utils.snowflake_conn import SnowflakeConfig, AuthType


@pytest.fixture
def pool_config():
    return ConnectionPoolConfig(
        min_size=1,
        max_size=3,
        max_inactive_time=timedelta(minutes=5),
        health_check_interval=timedelta(seconds=30),
    )


@pytest.fixture
def snowflake_config():
    return SnowflakeConfig(
        account="test_account",
        user="test_user",
        auth_type=AuthType.EXTERNAL_BROWSER,
    )


@pytest.mark.asyncio
async def test_pool_initialization(snowflake_config, pool_config):
    """Test pool initializes with minimum connections."""
    pool = AsyncConnectionPool(snowflake_config, pool_config)
    await pool.initialize()
    
    stats = pool.get_stats()
    assert stats["total_connections"] >= pool_config.min_size
    
    await pool.close()


@pytest.mark.asyncio
async def test_connection_acquisition(snowflake_config, pool_config):
    """Test connection acquisition and release."""
    pool = AsyncConnectionPool(snowflake_config, pool_config)
    await pool.initialize()
    
    async with pool.acquire() as conn:
        assert conn is not None
        # Test that connection works
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        cursor.close()
        assert result[0] == 1
    
    await pool.close()


@pytest.mark.asyncio
async def test_concurrent_connections(snowflake_config, pool_config):
    """Test multiple concurrent connection acquisitions."""
    pool = AsyncConnectionPool(snowflake_config, pool_config)
    await pool.initialize()
    
    async def use_connection(pool, delay=0.1):
        async with pool.acquire() as conn:
            await asyncio.sleep(delay)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            cursor.close()
            return result[0]
    
    # Test concurrent usage
    tasks = [use_connection(pool) for _ in range(5)]
    results = await asyncio.gather(*tasks)
    
    assert all(r == 1 for r in results)
    
    await pool.close()
```

### Integration Tests

Create `tests/test_pool_integration.py`:

```python
@pytest.mark.asyncio
async def test_mcp_handler_with_pool():
    """Test MCP handlers work with connection pool."""
    # Initialize pool
    await initialize_async_infrastructure()
    
    # Test database listing
    result = await handle_list_databases("list_databases")
    
    assert len(result) == 1
    assert "Available Snowflake databases:" in result[0].text
    
    # Cleanup
    from snowflake_mcp_server.utils.async_pool import close_connection_pool
    await close_connection_pool()
```

## Performance Validation

### Load Testing Script

Create `scripts/test_pool_performance.py`:

```python
#!/usr/bin/env python3

import asyncio
import time
import statistics
from concurrent.futures import ThreadPoolExecutor

async def test_connection_pool_performance():
    """Performance test for connection pool under load."""
    
    await initialize_async_infrastructure()
    
    # Test concurrent database operations
    async def database_operation():
        start_time = time.time()
        async with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.DATABASES")
            result = cursor.fetchone()
            cursor.close()
        return time.time() - start_time
    
    # Run 50 concurrent operations
    tasks = [database_operation() for _ in range(50)]
    times = await asyncio.gather(*tasks)
    
    print(f"Completed 50 concurrent operations")
    print(f"Average time: {statistics.mean(times):.3f}s")
    print(f"Median time: {statistics.median(times):.3f}s")
    print(f"95th percentile: {sorted(times)[int(0.95 * len(times))]:.3f}s")
    
    # Cleanup
    from snowflake_mcp_server.utils.async_pool import close_connection_pool
    await close_connection_pool()

if __name__ == "__main__":
    asyncio.run(test_connection_pool_performance())
```

## Verification Steps

1. **Pool Initialization**: Verify pool creates minimum connections on startup
2. **Connection Health**: Confirm health checks detect and replace failed connections
3. **Concurrent Access**: Test 10+ simultaneous connection acquisitions without blocking
4. **Resource Cleanup**: Ensure connections are properly released and pool can be closed
5. **Performance**: Measure 50%+ improvement in concurrent operation throughput
6. **Memory Usage**: Verify no connection leaks after extended operation

## Completion Criteria

- [ ] Connection pool maintains configured min/max sizes
- [ ] Health monitoring detects and replaces failed connections within 1 minute
- [ ] 10 concurrent MCP clients can operate without connection timeouts
- [ ] Pool statistics endpoint reports accurate connection states
- [ ] Load test shows 5x improvement in concurrent throughput vs singleton pattern
- [ ] Memory usage remains stable over 1-hour test period