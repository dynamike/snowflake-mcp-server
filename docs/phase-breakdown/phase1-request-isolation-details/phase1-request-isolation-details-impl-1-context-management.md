# Phase 1: Request Isolation Implementation Details

## Context & Overview

The current Snowflake MCP server shares connection state across all MCP tool calls, creating potential race conditions and data consistency issues when multiple clients or concurrent requests modify database/schema context or transaction state.

**Current Issues:**
- Global connection state shared between all tool calls
- `USE DATABASE` and `USE SCHEMA` commands affect all subsequent operations
- No request boundaries or isolation between MCP tool calls
- Transaction state shared across concurrent operations
- Session parameters can be modified by one request affecting others

**Target Architecture:**
- Per-request connection isolation from connection pool
- Request context tracking with unique IDs
- Isolated database/schema context per tool call
- Transaction boundary management per operation
- Request-level logging and error tracking

## Current State Analysis

### Problematic State Sharing in `main.py`

Lines 145-148 in `handle_list_views`:
```python
# GLOBAL STATE CHANGE: Affects all future requests
if database:
    conn.cursor().execute(f"USE DATABASE {database}")
if schema:
    conn.cursor().execute(f"USE SCHEMA {schema}")
```

Lines 433-436 in `handle_execute_query`:
```python
# GLOBAL STATE CHANGE: Persists beyond current request
if database:
    conn.cursor().execute(f"USE DATABASE {database}")
if schema:
    conn.cursor().execute(f"USE SCHEMA {schema}")
```

## Implementation Plan

### 1. Request Context Management {#context-management}

**Step 1: Create Request Context Framework**

Create `snowflake_mcp_server/utils/request_context.py`:

```python
"""Request context management for MCP tool calls."""

import asyncio
import uuid
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Set
from contextvars import ContextVar
from dataclasses import dataclass, field
import traceback

logger = logging.getLogger(__name__)

# Context variables for request tracking
current_request_id: ContextVar[Optional[str]] = ContextVar('current_request_id', default=None)
current_client_id: ContextVar[Optional[str]] = ContextVar('current_client_id', default=None)


@dataclass
class RequestMetrics:
    """Metrics for a specific request."""
    start_time: datetime
    end_time: Optional[datetime] = None
    database_operations: int = 0
    queries_executed: int = 0
    errors: int = 0
    connection_id: Optional[str] = None


@dataclass  
class RequestContext:
    """Context information for an MCP tool call request."""
    request_id: str
    client_id: str
    tool_name: str
    arguments: Dict[str, Any]
    start_time: datetime
    database_context: Optional[str] = None
    schema_context: Optional[str] = None
    metrics: RequestMetrics = field(default_factory=lambda: RequestMetrics(start_time=datetime.now()))
    errors: list = field(default_factory=list)
    
    def add_error(self, error: Exception, context: str = "") -> None:
        """Add error to request context."""
        self.errors.append({
            "timestamp": datetime.now(),
            "error": str(error),
            "error_type": type(error).__name__,
            "context": context,
            "traceback": traceback.format_exc()
        })
        self.metrics.errors += 1
    
    def set_database_context(self, database: str, schema: str = None) -> None:
        """Set database context for this request."""
        self.database_context = database
        if schema:
            self.schema_context = schema
    
    def increment_query_count(self) -> None:
        """Increment query counter."""
        self.metrics.queries_executed += 1
    
    def complete_request(self) -> None:
        """Mark request as completed."""
        self.metrics.end_time = datetime.now()
    
    def get_duration_ms(self) -> Optional[float]:
        """Get request duration in milliseconds."""
        if self.metrics.end_time:
            return (self.metrics.end_time - self.start_time).total_seconds() * 1000
        return None


class RequestContextManager:
    """Manage request contexts for concurrent operations."""
    
    def __init__(self):
        self._active_requests: Dict[str, RequestContext] = {}
        self._completed_requests: Dict[str, RequestContext] = {}
        self._lock = asyncio.Lock()
        self._max_completed_requests = 1000  # Keep limited history
    
    async def create_request_context(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        client_id: str = "unknown"
    ) -> RequestContext:
        """Create a new request context."""
        request_id = str(uuid.uuid4())
        
        context = RequestContext(
            request_id=request_id,
            client_id=client_id,
            tool_name=tool_name,
            arguments=arguments.copy() if arguments else {},
            start_time=datetime.now()
        )
        
        async with self._lock:
            self._active_requests[request_id] = context
        
        # Set context variables
        current_request_id.set(request_id)
        current_client_id.set(client_id)
        
        logger.debug(f"Created request context {request_id} for tool {tool_name}")
        return context
    
    async def complete_request_context(self, request_id: str) -> None:
        """Complete a request context and move to history."""
        async with self._lock:
            if request_id in self._active_requests:
                context = self._active_requests.pop(request_id)
                context.complete_request()
                
                # Add to completed requests with size limit
                self._completed_requests[request_id] = context
                
                # Trim completed requests if too many
                if len(self._completed_requests) > self._max_completed_requests:
                    # Remove oldest requests
                    oldest_requests = sorted(
                        self._completed_requests.items(),
                        key=lambda x: x[1].start_time
                    )
                    for old_id, _ in oldest_requests[:100]:  # Remove 100 oldest
                        self._completed_requests.pop(old_id, None)
                
                duration = context.get_duration_ms()
                logger.info(f"Completed request {request_id} in {duration:.2f}ms")
    
    async def get_request_context(self, request_id: str) -> Optional[RequestContext]:
        """Get request context by ID."""
        async with self._lock:
            return (
                self._active_requests.get(request_id) or 
                self._completed_requests.get(request_id)
            )
    
    async def get_active_requests(self) -> Dict[str, RequestContext]:
        """Get all active request contexts."""
        async with self._lock:
            return self._active_requests.copy()
    
    async def get_client_requests(self, client_id: str) -> Dict[str, RequestContext]:
        """Get all requests for a specific client."""
        async with self._lock:
            client_requests = {}
            for req_id, context in self._active_requests.items():
                if context.client_id == client_id:
                    client_requests[req_id] = context
            return client_requests
    
    def get_current_context(self) -> Optional[RequestContext]:
        """Get current request context from context variable."""
        request_id = current_request_id.get()
        if request_id and request_id in self._active_requests:
            return self._active_requests[request_id]
        return None
    
    async def cleanup_stale_requests(self, max_age_minutes: int = 60) -> None:
        """Clean up requests that have been active too long."""
        cutoff_time = datetime.now() - timedelta(minutes=max_age_minutes)
        
        async with self._lock:
            stale_requests = [
                req_id for req_id, context in self._active_requests.items()
                if context.start_time < cutoff_time
            ]
            
            for req_id in stale_requests:
                context = self._active_requests.pop(req_id)
                context.add_error(
                    Exception("Request timeout - cleaned up by manager"),
                    "stale_request_cleanup"
                )
                context.complete_request()
                self._completed_requests[req_id] = context
                logger.warning(f"Cleaned up stale request {req_id}")


# Global request context manager
request_manager = RequestContextManager()


# Context manager for request isolation
from contextlib import asynccontextmanager

@asynccontextmanager
async def request_context(tool_name: str, arguments: Dict[str, Any], client_id: str = "unknown"):
    """Context manager for request isolation."""
    context = await request_manager.create_request_context(tool_name, arguments, client_id)
    
    try:
        yield context
    except Exception as e:
        context.add_error(e, f"request_execution_{tool_name}")
        raise
    finally:
        await request_manager.complete_request_context(context.request_id)
```

**Step 2: Update Async Database Operations for Isolation**

Modify `snowflake_mcp_server/utils/async_database.py`:

```python
# Add to AsyncDatabaseOperations class

class IsolatedDatabaseOperations(AsyncDatabaseOperations):
    """Database operations with request isolation."""
    
    def __init__(self, connection: SnowflakeConnection, request_context: RequestContext):
        super().__init__(connection)
        self.request_context = request_context
        self._original_database = None
        self._original_schema = None
        self._context_changed = False
    
    async def __aenter__(self):
        """Async context entry - capture original context."""
        # Capture current database/schema context
        try:
            current_db, current_schema = await self.get_current_context()
            self._original_database = current_db
            self._original_schema = current_schema
            
            logger.debug(f"Request {self.request_context.request_id}: "
                        f"Original context: {current_db}.{current_schema}")
        except Exception as e:
            logger.warning(f"Could not capture original context: {e}")
        
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context exit - restore original context."""
        try:
            # Restore original context if it was changed
            if self._context_changed and self._original_database:
                await self._restore_original_context()
        except Exception as e:
            logger.warning(f"Error restoring context: {e}")
        finally:
            await self.cleanup()
    
    async def use_database_isolated(self, database: str) -> None:
        """Switch database with isolation tracking."""
        await self.use_database(database)
        self.request_context.set_database_context(database)
        self._context_changed = True
        
        logger.debug(f"Request {self.request_context.request_id}: "
                    f"Changed to database {database}")
    
    async def use_schema_isolated(self, schema: str) -> None:
        """Switch schema with isolation tracking."""
        await self.use_schema(schema)
        if self.request_context.database_context:
            self.request_context.set_database_context(
                self.request_context.database_context, 
                schema
            )
        self._context_changed = True
        
        logger.debug(f"Request {self.request_context.request_id}: "
                    f"Changed to schema {schema}")
    
    async def execute_query_isolated(self, query: str) -> Tuple[List[Tuple], List[str]]:
        """Execute query with request tracking."""
        try:
            self.request_context.increment_query_count()
            
            logger.debug(f"Request {self.request_context.request_id}: "
                        f"Executing query: {query[:100]}...")
            
            start_time = datetime.now()
            result = await self.execute_query(query)
            duration = (datetime.now() - start_time).total_seconds() * 1000
            
            logger.debug(f"Request {self.request_context.request_id}: "
                        f"Query completed in {duration:.2f}ms")
            
            return result
            
        except Exception as e:
            self.request_context.add_error(e, f"query_execution: {query[:100]}")
            logger.error(f"Request {self.request_context.request_id}: "
                        f"Query failed: {e}")
            raise
    
    async def _restore_original_context(self) -> None:
        """Restore original database/schema context."""
        if self._original_database and self._original_database != "Unknown":
            await self.use_database(self._original_database)
            
            if self._original_schema and self._original_schema != "Unknown":
                await self.use_schema(self._original_schema)
            
            logger.debug(f"Request {self.request_context.request_id}: "
                        f"Restored context to {self._original_database}.{self._original_schema}")


@asynccontextmanager
async def get_isolated_database_ops(request_context: RequestContext):
    """Get isolated database operations for a request."""
    from .async_pool import get_connection_pool
    
    pool = await get_connection_pool()
    async with pool.acquire() as connection:
        # Set connection ID in metrics
        request_context.metrics.connection_id = str(id(connection))
        
        db_ops = IsolatedDatabaseOperations(connection, request_context)
        async with db_ops:
            yield db_ops
```

