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

### 4. Fair Resource Allocation {#resource-allocation}

**Step 4: Resource Allocation and Queuing**

Create `snowflake_mcp_server/client/resource_allocator.py`:

```python
"""Fair resource allocation for multi-client scenarios."""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from collections import deque
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class Priority(Enum):
    """Request priority levels."""
    HIGH = 1
    NORMAL = 2
    LOW = 3


@dataclass
class QueuedRequest:
    """Queued request with priority and timing."""
    request_id: str
    client_id: str
    priority: Priority
    queued_at: datetime
    estimated_duration: float = 1.0  # seconds
    
    def age_seconds(self) -> float:
        """Get age of request in seconds."""
        return (datetime.now() - self.queued_at).total_seconds()


class FairQueueManager:
    """Fair queuing manager for client requests."""
    
    def __init__(
        self,
        max_concurrent_requests: int = 20,
        max_queue_size: int = 100,
        queue_timeout: timedelta = timedelta(minutes=5)
    ):
        self.max_concurrent_requests = max_concurrent_requests
        self.max_queue_size = max_queue_size
        self.queue_timeout = queue_timeout
        
        # Per-client queues
        self._client_queues: Dict[str, deque[QueuedRequest]] = {}
        
        # Global request tracking
        self._active_requests: Dict[str, QueuedRequest] = {}
        self._client_active_counts: Dict[str, int] = {}
        
        # Round-robin fairness
        self._last_served_client = None
        self._client_order: List[str] = []
        
        # Synchronization
        self._lock = asyncio.Lock()
        self._request_slots = asyncio.Semaphore(max_concurrent_requests)
        self._queue_changed = asyncio.Event()
        
        # Background task
        self._scheduler_task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start the queue manager."""
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Fair queue manager started")
    
    async def stop(self) -> None:
        """Stop the queue manager."""
        if self._scheduler_task:
            self._scheduler_task.cancel()
        logger.info("Fair queue manager stopped")
    
    async def enqueue_request(
        self,
        request_id: str,
        client_id: str,
        priority: Priority = Priority.NORMAL,
        estimated_duration: float = 1.0
    ) -> bool:
        """Enqueue a request for processing."""
        
        async with self._lock:
            # Check global queue limits
            total_queued = sum(len(queue) for queue in self._client_queues.values())
            if total_queued >= self.max_queue_size:
                logger.warning(f"Queue full, rejecting request {request_id}")
                return False
            
            # Create client queue if needed
            if client_id not in self._client_queues:
                self._client_queues[client_id] = deque()
                self._client_active_counts[client_id] = 0
                
                # Add to round-robin order
                if client_id not in self._client_order:
                    self._client_order.append(client_id)
            
            # Create queued request
            queued_request = QueuedRequest(
                request_id=request_id,
                client_id=client_id,
                priority=priority,
                queued_at=datetime.now(),
                estimated_duration=estimated_duration
            )
            
            # Add to client queue (priority-based insertion)
            client_queue = self._client_queues[client_id]
            
            # Insert by priority (higher priority first)
            inserted = False
            for i, existing_request in enumerate(client_queue):
                if priority.value < existing_request.priority.value:
                    client_queue.insert(i, queued_request)
                    inserted = True
                    break
            
            if not inserted:
                client_queue.append(queued_request)
            
            logger.debug(f"Enqueued request {request_id} for client {client_id}")
            self._queue_changed.set()
            return True
    
    @asynccontextmanager
    async def acquire_request_slot(self, request_id: str):
        """Acquire slot for request execution."""
        
        # Wait for available slot
        await self._request_slots.acquire()
        
        try:
            # Move request to active
            async with self._lock:
                if request_id in self._active_requests:
                    request = self._active_requests[request_id]
                    self._client_active_counts[request.client_id] += 1
                    
                    logger.debug(f"Started execution of request {request_id}")
                    yield request
                else:
                    logger.warning(f"Request {request_id} not found in active requests")
                    yield None
        
        finally:
            # Release slot and cleanup
            async with self._lock:
                if request_id in self._active_requests:
                    request = self._active_requests.pop(request_id)
                    self._client_active_counts[request.client_id] = max(
                        0, self._client_active_counts[request.client_id] - 1
                    )
                    logger.debug(f"Completed execution of request {request_id}")
            
            self._request_slots.release()
            self._queue_changed.set()
    
    async def _scheduler_loop(self) -> None:
        """Background scheduler for fair request processing."""
        
        while True:
            try:
                # Wait for queue changes or timeout
                try:
                    await asyncio.wait_for(self._queue_changed.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                
                self._queue_changed.clear()
                
                # Schedule next requests
                await self._schedule_next_requests()
                
                # Cleanup expired requests
                await self._cleanup_expired_requests()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")
    
    async def _schedule_next_requests(self) -> None:
        """Schedule next requests using fair round-robin."""
        
        async with self._lock:
            available_slots = self.max_concurrent_requests - len(self._active_requests)
            
            if available_slots <= 0:
                return
            
            # Round-robin through clients
            scheduled_count = 0
            clients_checked = 0
            
            # Start from next client after last served
            start_index = 0
            if self._last_served_client and self._last_served_client in self._client_order:
                start_index = (self._client_order.index(self._last_served_client) + 1) % len(self._client_order)
            
            while scheduled_count < available_slots and clients_checked < len(self._client_order):
                current_index = (start_index + clients_checked) % len(self._client_order)
                client_id = self._client_order[current_index]
                clients_checked += 1
                
                # Skip clients with no queued requests
                if client_id not in self._client_queues or not self._client_queues[client_id]:
                    continue
                
                # Fair allocation: limit concurrent requests per client
                max_per_client = max(1, self.max_concurrent_requests // len(self._client_order))
                current_active = self._client_active_counts.get(client_id, 0)
                
                if current_active >= max_per_client:
                    continue
                
                # Schedule next request from this client
                client_queue = self._client_queues[client_id]
                request = client_queue.popleft()
                
                # Move to active requests
                self._active_requests[request.request_id] = request
                self._last_served_client = client_id
                scheduled_count += 1
                
                logger.debug(f"Scheduled request {request.request_id} from client {client_id}")
                
                # If client queue is empty, remove from round-robin temporarily
                if not client_queue:
                    # Keep in _client_order for fairness, just empty queue
                    pass
    
    async def _cleanup_expired_requests(self) -> None:
        """Clean up expired queued requests."""
        
        async with self._lock:
            expired_requests = []
            
            for client_id, queue in self._client_queues.items():
                # Check for expired requests in queue
                expired_in_queue = []
                for i, request in enumerate(queue):
                    if request.age_seconds() > self.queue_timeout.total_seconds():
                        expired_in_queue.append(i)
                
                # Remove expired requests (reverse order to maintain indices)
                for i in reversed(expired_in_queue):
                    expired_request = queue[i]
                    del queue[i]
                    expired_requests.append(expired_request.request_id)
            
            if expired_requests:
                logger.warning(f"Cleaned up {len(expired_requests)} expired queued requests")
    
    def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        
        total_queued = sum(len(queue) for queue in self._client_queues.values())
        total_active = len(self._active_requests)
        
        client_stats = {}
        for client_id in self._client_order:
            queued = len(self._client_queues.get(client_id, []))
            active = self._client_active_counts.get(client_id, 0)
            
            client_stats[client_id] = {
                "queued": queued,
                "active": active,
                "total": queued + active
            }
        
        return {
            "total_queued": total_queued,
            "total_active": total_active,
            "available_slots": self.max_concurrent_requests - total_active,
            "clients": client_stats
        }


# Global queue manager
queue_manager = FairQueueManager()
```

