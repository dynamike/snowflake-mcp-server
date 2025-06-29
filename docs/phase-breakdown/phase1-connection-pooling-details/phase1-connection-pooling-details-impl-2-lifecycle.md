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

### 2. Connection Lifecycle Management {#lifecycle}

Update `snowflake_mcp_server/utils/snowflake_conn.py`:

```python
# Add async connection management functions

async def create_async_connection(config: SnowflakeConfig) -> SnowflakeConnection:
    """Create a Snowflake connection asynchronously."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_snowflake_connection, config)


async def test_connection_health(connection: SnowflakeConnection) -> bool:
    """Test if a connection is healthy asynchronously."""
    try:
        loop = asyncio.get_event_loop()
        
        def _test_connection():
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            cursor.close()
            return result is not None
        
        return await loop.run_in_executor(None, _test_connection)
    except Exception:
        return False


# Update the singleton manager to work with async pool
class LegacyConnectionManager:
    """Legacy connection manager for backwards compatibility."""
    
    def __init__(self):
        self._pool: Optional[AsyncConnectionPool] = None
        self._config: Optional[SnowflakeConfig] = None
    
    def initialize(self, config: SnowflakeConfig) -> None:
        """Initialize with async pool."""
        self._config = config
        # Pool initialization happens asynchronously
    
    async def get_async_connection(self):
        """Get connection from async pool."""
        if self._pool is None:
            from .async_pool import get_connection_pool
            self._pool = await get_connection_pool()
        
        return self._pool.acquire()
    
    def get_connection(self) -> SnowflakeConnection:
        """Legacy sync method - deprecated."""
        import warnings
        warnings.warn(
            "Synchronous get_connection is deprecated. Use get_async_connection().",
            DeprecationWarning,
            stacklevel=2
        )
        # Fallback implementation for compatibility
        if self._config is None:
            raise ValueError("Connection manager not initialized")
        return get_snowflake_connection(self._config)
```

