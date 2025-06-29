# Phase 2: Multi-Client Architecture Implementation Details

## Context & Overview

The current Snowflake MCP server architecture creates bottlenecks when multiple MCP clients (Claude Desktop, Claude Code, Roo Code) attempt to connect simultaneously. The shared connection state and lack of client isolation cause performance degradation and potential data inconsistency issues.

**Current Issues:**
- Single connection shared across all clients
- Client requests can interfere with each other's database context
- No client identification or session management
- Resource contention leads to blocking operations
- No fair resource allocation between clients

**Target Architecture:**
- Client session management with unique identification
- Connection multiplexing with per-client isolation
- Fair resource allocation and queuing
- Client-specific rate limiting and quotas
- Session persistence across reconnections

## Current State Analysis

### Client Connection Problems in `main.py`

The stdio server only supports one client connection:
```python
def run_stdio_server() -> None:
    """Run the MCP server using stdin/stdout for communication."""
    # Only supports single client via stdio
```

Connection manager singleton shared across all requests:
```python
# In utils/snowflake_conn.py line 311
connection_manager = SnowflakeConnectionManager()  # Global singleton
```

## Implementation Plan

### 2. Connection Multiplexing {#connection-multiplexing}

**Step 2: Connection Multiplexing Implementation**

Create `snowflake_mcp_server/client/connection_multiplexer.py`:

```python
"""Connection multiplexing for multi-client support."""

import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple
from contextlib import asynccontextmanager
from datetime import datetime

from ..utils.async_pool import get_connection_pool
from ..utils.request_context import RequestContext
from .session_manager import ClientSession, session_manager

logger = logging.getLogger(__name__)


class ClientConnectionPool:
    """Per-client connection management with multiplexing."""
    
    def __init__(self, client_id: str, pool_size: int = 3):
        self.client_id = client_id
        self.pool_size = pool_size
        self._active_connections: Dict[str, Any] = {}  # request_id -> connection
        self._connection_usage: Dict[str, int] = {}     # connection_id -> usage_count
        self._lock = asyncio.Lock()
    
    @asynccontextmanager
    async def acquire_connection(self, request_id: str):
        """Acquire connection for client request with multiplexing."""
        connection = None
        connection_id = None
        
        try:
            async with self._lock:
                # Try to reuse existing connection if under limit
                if len(self._active_connections) < self.pool_size:
                    pool = await get_connection_pool()
                    async with pool.acquire() as conn:
                        connection = conn
                        connection_id = f"{self.client_id}_{id(conn)}"
                        self._active_connections[request_id] = connection
                        self._connection_usage[connection_id] = self._connection_usage.get(connection_id, 0) + 1
                        
                        logger.debug(f"Acquired connection {connection_id} for request {request_id}")
                        
                        yield connection
                else:
                    # Pool exhausted, wait for available connection
                    logger.warning(f"Connection pool exhausted for client {self.client_id}")
                    raise RuntimeError("Client connection pool exhausted")
        
        finally:
            if connection and connection_id:
                async with self._lock:
                    self._active_connections.pop(request_id, None)
                    if connection_id in self._connection_usage:
                        self._connection_usage[connection_id] -= 1
                        if self._connection_usage[connection_id] <= 0:
                            self._connection_usage.pop(connection_id, None)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get connection pool statistics for this client."""
        return {
            "client_id": self.client_id,
            "active_connections": len(self._active_connections),
            "pool_size": self.pool_size,
            "connection_usage": dict(self._connection_usage)
        }


class ConnectionMultiplexer:
    """Manage multiplexed connections across multiple clients."""
    
    def __init__(self):
        self._client_pools: Dict[str, ClientConnectionPool] = {}
        self._global_lock = asyncio.Lock()
        self._stats = {
            "total_clients": 0,
            "active_connections": 0,
            "requests_served": 0
        }
    
    async def get_client_pool(self, client_id: str, pool_size: int = 3) -> ClientConnectionPool:
        """Get or create connection pool for client."""
        async with self._global_lock:
            if client_id not in self._client_pools:
                self._client_pools[client_id] = ClientConnectionPool(client_id, pool_size)
                self._stats["total_clients"] += 1
                logger.info(f"Created connection pool for client {client_id}")
            
            return self._client_pools[client_id]
    
    @asynccontextmanager
    async def acquire_for_request(self, session: ClientSession, request_context: RequestContext):
        """Acquire connection for specific request with client isolation."""
        
        # Check rate limiting
        if not session.consume_rate_limit_token():
            raise RuntimeError(f"Rate limit exceeded for client {session.client_id}")
        
        # Check quota
        if not session.consume_quota():
            raise RuntimeError(f"Quota exceeded for client {session.client_id}")
        
        # Get client connection pool
        client_pool = await self.get_client_pool(session.client_id)
        
        # Track request start
        session.add_active_request(request_context.request_id)
        start_time = datetime.now()
        
        try:
            async with client_pool.acquire_connection(request_context.request_id) as connection:
                self._stats["active_connections"] += 1
                self._stats["requests_served"] += 1
                
                yield connection
                
                # Track successful completion
                session.remove_active_request(request_context.request_id, success=True)
                
        except Exception as e:
            # Track failed completion
            session.remove_active_request(request_context.request_id, success=False)
            logger.error(f"Connection error for client {session.client_id}: {e}")
            raise
        
        finally:
            # Update response time metrics
            duration_ms = (datetime.now() - start_time).total_seconds() * 1000
            session.metrics.update_response_time(duration_ms)
            
            self._stats["active_connections"] = max(0, self._stats["active_connections"] - 1)
    
    async def cleanup_client(self, client_id: str) -> None:
        """Cleanup resources for disconnected client."""
        async with self._global_lock:
            if client_id in self._client_pools:
                client_pool = self._client_pools.pop(client_id)
                self._stats["total_clients"] = max(0, self._stats["total_clients"] - 1)
                logger.info(f"Cleaned up connection pool for client {client_id}")
    
    def get_global_stats(self) -> Dict[str, Any]:
        """Get global multiplexer statistics."""
        client_stats = {}
        for client_id, pool in self._client_pools.items():
            client_stats[client_id] = pool.get_stats()
        
        return {
            "global_stats": self._stats.copy(),
            "client_pools": client_stats
        }


# Global connection multiplexer
connection_multiplexer = ConnectionMultiplexer()
```

