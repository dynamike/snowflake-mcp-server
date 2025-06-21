"""Request context management for MCP tool calls."""

import asyncio
import logging
import traceback
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

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
    transaction_operations: int = 0
    transaction_commits: int = 0
    transaction_rollbacks: int = 0


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
    errors: List[Dict[str, Any]] = field(default_factory=list)
    
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
    
    def set_database_context(self, database: str, schema: Optional[str] = None) -> None:
        """Set database context for this request."""
        self.database_context = database
        if schema:
            self.schema_context = schema
    
    def increment_query_count(self) -> None:
        """Increment query counter."""
        self.metrics.queries_executed += 1
    
    def increment_transaction_operation(self) -> None:
        """Increment transaction operation counter."""
        self.metrics.transaction_operations += 1
    
    def increment_transaction_commit(self) -> None:
        """Increment transaction commit counter."""
        self.metrics.transaction_commits += 1
    
    def increment_transaction_rollback(self) -> None:
        """Increment transaction rollback counter."""
        self.metrics.transaction_rollbacks += 1
    
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
    
    def __init__(self) -> None:
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
@asynccontextmanager
async def request_context(tool_name: str, arguments: Dict[str, Any], client_id: str = "unknown") -> Any:
    """Context manager for request isolation."""
    context = await request_manager.create_request_context(tool_name, arguments, client_id)
    
    try:
        yield context
    except Exception as e:
        context.add_error(e, f"request_execution_{tool_name}")
        raise
    finally:
        await request_manager.complete_request_context(context.request_id)