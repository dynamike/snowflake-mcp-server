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

### 3. Client Isolation Boundaries {#client-isolation}

**Step 3: Enhanced Client Isolation**

Update `snowflake_mcp_server/utils/async_database.py`:

```python
# Add client-aware database operations

from ..client.session_manager import ClientSession
from ..client.connection_multiplexer import connection_multiplexer

class ClientIsolatedDatabaseOperations(IsolatedDatabaseOperations):
    """Database operations with client-level isolation."""
    
    def __init__(self, connection, request_context: RequestContext, client_session: ClientSession):
        super().__init__(connection, request_context)
        self.client_session = client_session
        self._client_database_context = None
        self._client_schema_context = None
    
    async def __aenter__(self):
        """Enhanced entry with client isolation."""
        await super().__aenter__()
        
        # Load client-specific database context preferences
        if "default_database" in self.client_session.preferences:
            self._client_database_context = self.client_session.preferences["default_database"]
        
        if "default_schema" in self.client_session.preferences:
            self._client_schema_context = self.client_session.preferences["default_schema"]
        
        # Apply client context if available
        if self._client_database_context:
            await self.use_database_isolated(self._client_database_context)
        
        if self._client_schema_context:
            await self.use_schema_isolated(self._client_schema_context)
        
        return self
    
    async def execute_query_with_client_context(self, query: str) -> Tuple[List[Tuple], List[str]]:
        """Execute query with client-specific context and logging."""
        
        # Log query for client
        logger.info(
            f"Client {self.client_session.client_id} executing query",
            extra={
                "client_id": self.client_session.client_id,
                "client_type": self.client_session.client_type,
                "session_id": self.client_session.session_id,
                "query_preview": query[:100]
            }
        )
        
        try:
            result = await self.execute_query_isolated(query)
            
            # Update client metrics
            self.client_session.metrics.bytes_sent += len(query.encode())
            
            return result
            
        except Exception as e:
            logger.error(
                f"Query failed for client {self.client_session.client_id}: {e}",
                extra={
                    "client_id": self.client_session.client_id,
                    "error": str(e)
                }
            )
            raise


@asynccontextmanager
async def get_client_isolated_database_ops(request_context: RequestContext, client_session: ClientSession):
    """Get client-isolated database operations."""
    
    async with connection_multiplexer.acquire_for_request(client_session, request_context) as connection:
        db_ops = ClientIsolatedDatabaseOperations(connection, request_context, client_session)
        async with db_ops:
            yield db_ops
```

