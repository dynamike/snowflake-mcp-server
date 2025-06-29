"""Fair resource allocation system for multi-client MCP server."""

import asyncio
import heapq
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class AllocationStrategy(Enum):
    """Resource allocation strategies."""
    FAIR_SHARE = "fair_share"        # Equal allocation per client
    PRIORITY_BASED = "priority_based" # Allocation based on client priority
    WEIGHTED_FAIR = "weighted_fair"   # Fair allocation with weights
    ROUND_ROBIN = "round_robin"       # Round-robin allocation


@dataclass
class ResourceRequest:
    """Represents a resource allocation request."""
    
    request_id: str
    client_id: str
    resource_type: str
    amount: float
    priority: int = 1  # 1=low, 5=high
    max_wait_time: float = 30.0  # Maximum time to wait for allocation
    created_at: float = field(default_factory=time.time)
    callback: Optional[callable] = None
    
    def __lt__(self, other: 'ResourceRequest') -> bool:
        """For priority queue ordering (higher priority first)."""
        return self.priority > other.priority
    
    def get_age(self) -> float:
        """Get request age in seconds."""
        return time.time() - self.created_at
    
    def is_expired(self) -> bool:
        """Check if request has exceeded max wait time."""
        return self.get_age() > self.max_wait_time


@dataclass
class ResourcePool:
    """Represents a pool of resources with allocation tracking."""
    
    resource_type: str
    total_capacity: float
    allocated: float = 0.0
    reserved: float = 0.0  # Reserved for high priority clients
    min_allocation: float = 1.0  # Minimum allocation per client
    allocation_unit: float = 1.0  # Smallest allocation unit
    
    @property
    def available(self) -> float:
        """Get available resources."""
        return max(0.0, self.total_capacity - self.allocated)
    
    @property
    def utilization(self) -> float:
        """Get utilization percentage."""
        return (self.allocated / self.total_capacity) * 100 if self.total_capacity > 0 else 0
    
    def can_allocate(self, amount: float) -> bool:
        """Check if amount can be allocated."""
        return self.available >= amount
    
    def allocate(self, amount: float) -> bool:
        """Allocate resources if available."""
        if self.can_allocate(amount):
            self.allocated += amount
            return True
        return False
    
    def release(self, amount: float) -> None:
        """Release allocated resources."""
        self.allocated = max(0.0, self.allocated - amount)


@dataclass
class ClientAllocation:
    """Tracks resource allocation for a specific client."""
    
    client_id: str
    allocated_resources: Dict[str, float] = field(default_factory=dict)
    priority: int = 1
    weight: float = 1.0
    last_allocation: float = field(default_factory=time.time)
    total_allocated: float = 0.0
    allocation_count: int = 0
    
    def get_allocated(self, resource_type: str) -> float:
        """Get allocated amount for specific resource type."""
        return self.allocated_resources.get(resource_type, 0.0)
    
    def add_allocation(self, resource_type: str, amount: float) -> None:
        """Add resource allocation."""
        current = self.allocated_resources.get(resource_type, 0.0)
        self.allocated_resources[resource_type] = current + amount
        self.total_allocated += amount
        self.allocation_count += 1
        self.last_allocation = time.time()
    
    def remove_allocation(self, resource_type: str, amount: float) -> None:
        """Remove resource allocation."""
        current = self.allocated_resources.get(resource_type, 0.0)
        self.allocated_resources[resource_type] = max(0.0, current - amount)
        self.total_allocated = max(0.0, self.total_allocated - amount)


class FairResourceAllocator:
    """Fair resource allocation manager for multi-client scenarios."""
    
    def __init__(self, strategy: AllocationStrategy = AllocationStrategy.WEIGHTED_FAIR):
        self.strategy = strategy
        self.resource_pools: Dict[str, ResourcePool] = {}
        self.client_allocations: Dict[str, ClientAllocation] = {}
        self.pending_requests: List[ResourceRequest] = []  # Priority queue
        self.allocation_history: deque = deque(maxlen=1000)  # Recent allocations
        
        # Allocation tracking
        self.total_requests = 0
        self.successful_allocations = 0
        self.failed_allocations = 0
        self.expired_requests = 0
        
        # Background task for processing requests
        self._allocation_task: Optional[asyncio.Task] = None
        self._running = False
        self._lock = asyncio.Lock()
    
    async def start(self) -> None:
        """Start the resource allocator."""
        self._running = True
        if self._allocation_task is None:
            self._allocation_task = asyncio.create_task(self._allocation_loop())
            logger.info(f"Resource allocator started with {self.strategy.value} strategy")
    
    async def stop(self) -> None:
        """Stop the resource allocator."""
        self._running = False
        if self._allocation_task:
            self._allocation_task.cancel()
            try:
                await self._allocation_task
            except asyncio.CancelledError:
                pass
            self._allocation_task = None
            logger.info("Resource allocator stopped")
    
    def add_resource_pool(self, resource_type: str, total_capacity: float,
                         reserved_percent: float = 0.1, **kwargs) -> None:
        """Add a resource pool for allocation management."""
        pool = ResourcePool(
            resource_type=resource_type,
            total_capacity=total_capacity,
            reserved=total_capacity * reserved_percent,
            **kwargs
        )
        self.resource_pools[resource_type] = pool
        logger.info(f"Added resource pool: {resource_type} with capacity {total_capacity}")
    
    async def request_resources(self, 
                              client_id: str,
                              resource_type: str,
                              amount: float,
                              priority: int = 1,
                              max_wait_time: float = 30.0) -> Tuple[bool, str]:
        """Request resource allocation for a client."""
        
        request_id = f"{client_id}_{resource_type}_{time.time()}"
        
        # Check if resource pool exists
        if resource_type not in self.resource_pools:
            return False, f"Resource type {resource_type} not available"
        
        pool = self.resource_pools[resource_type]
        
        # Check if amount is valid
        if amount <= 0 or amount > pool.total_capacity:
            return False, f"Invalid allocation amount: {amount}"
        
        # Create resource request
        request = ResourceRequest(
            request_id=request_id,
            client_id=client_id,
            resource_type=resource_type,
            amount=amount,
            priority=priority,
            max_wait_time=max_wait_time
        )
        
        self.total_requests += 1
        
        # Try immediate allocation for high priority or if resources available
        async with self._lock:
            if await self._try_immediate_allocation(request):
                return True, request_id
            
            # Add to pending queue
            heapq.heappush(self.pending_requests, request)
            logger.debug(f"Queued resource request {request_id} for client {client_id}")
        
        return True, request_id  # Queued for later allocation
    
    async def _try_immediate_allocation(self, request: ResourceRequest) -> bool:
        """Try to allocate resources immediately."""
        pool = self.resource_pools[request.resource_type]
        
        # Check basic availability
        if not pool.can_allocate(request.amount):
            return False
        
        # Apply allocation strategy
        if await self._can_allocate_by_strategy(request):
            return await self._perform_allocation(request)
        
        return False
    
    async def _can_allocate_by_strategy(self, request: ResourceRequest) -> bool:
        """Check if allocation is allowed by current strategy."""
        
        if self.strategy == AllocationStrategy.FAIR_SHARE:
            return await self._check_fair_share(request)
        elif self.strategy == AllocationStrategy.PRIORITY_BASED:
            return await self._check_priority_based(request)
        elif self.strategy == AllocationStrategy.WEIGHTED_FAIR:
            return await self._check_weighted_fair(request)
        elif self.strategy == AllocationStrategy.ROUND_ROBIN:
            return await self._check_round_robin(request)
        
        return True  # Default allow
    
    async def _check_fair_share(self, request: ResourceRequest) -> bool:
        """Check fair share allocation rules."""
        pool = self.resource_pools[request.resource_type]
        active_clients = len(self.client_allocations)
        
        # Calculate fair share per client
        fair_share = pool.total_capacity / max(active_clients + 1, 1)
        
        # Check if client would exceed fair share
        client_id = request.client_id
        if client_id in self.client_allocations:
            current_allocation = self.client_allocations[client_id].get_allocated(request.resource_type)
            if current_allocation + request.amount > fair_share * 1.1:  # 10% tolerance
                return False
        
        return True
    
    async def _check_priority_based(self, request: ResourceRequest) -> bool:
        """Check priority-based allocation rules."""
        # High priority requests can use reserved capacity
        pool = self.resource_pools[request.resource_type]
        
        if request.priority >= 4:  # High priority
            available_with_reserved = pool.available + pool.reserved
            return available_with_reserved >= request.amount
        
        return pool.available >= request.amount
    
    async def _check_weighted_fair(self, request: ResourceRequest) -> bool:
        """Check weighted fair allocation rules."""
        # Get client weight (could be based on subscription tier, etc.)
        client_weight = self._get_client_weight(request.client_id)
        total_weights = sum(
            self._get_client_weight(alloc.client_id) 
            for alloc in self.client_allocations.values()
        ) + client_weight
        
        pool = self.resource_pools[request.resource_type]
        weighted_share = (client_weight / total_weights) * pool.total_capacity
        
        # Check if within weighted share
        if request.client_id in self.client_allocations:
            current = self.client_allocations[request.client_id].get_allocated(request.resource_type)
            return current + request.amount <= weighted_share * 1.2  # 20% tolerance
        
        return True
    
    async def _check_round_robin(self, request: ResourceRequest) -> bool:
        """Check round-robin allocation rules."""
        # Simple round-robin: allow if it's client's turn or no recent allocation
        if not self.allocation_history:
            return True
        
        # Check if client had recent allocation
        recent_allocations = list(self.allocation_history)[-10:]  # Last 10 allocations
        client_recent_count = sum(1 for alloc in recent_allocations if alloc['client_id'] == request.client_id)
        
        # Allow if client hasn't had many recent allocations
        return client_recent_count < 3
    
    def _get_client_weight(self, client_id: str) -> float:
        """Get client weight for weighted fair allocation."""
        if client_id in self.client_allocations:
            return self.client_allocations[client_id].weight
        return 1.0  # Default weight
    
    async def _perform_allocation(self, request: ResourceRequest) -> bool:
        """Perform the actual resource allocation."""
        pool = self.resource_pools[request.resource_type]
        
        # Allocate from pool
        if pool.allocate(request.amount):
            # Track client allocation
            if request.client_id not in self.client_allocations:
                self.client_allocations[request.client_id] = ClientAllocation(client_id=request.client_id)
            
            client_alloc = self.client_allocations[request.client_id]
            client_alloc.add_allocation(request.resource_type, request.amount)
            
            # Record allocation
            allocation_record = {
                'request_id': request.request_id,
                'client_id': request.client_id,
                'resource_type': request.resource_type,
                'amount': request.amount,
                'timestamp': time.time(),
                'wait_time': request.get_age()
            }
            self.allocation_history.append(allocation_record)
            
            self.successful_allocations += 1
            logger.debug(f"Allocated {request.amount} {request.resource_type} to {request.client_id}")
            
            # Call callback if provided
            if request.callback:
                try:
                    await request.callback(True, request.request_id)
                except Exception as e:
                    logger.error(f"Callback error for request {request.request_id}: {e}")
            
            return True
        
        return False
    
    async def release_resources(self, client_id: str, resource_type: str, amount: float) -> bool:
        """Release allocated resources."""
        async with self._lock:
            if client_id not in self.client_allocations:
                return False
            
            client_alloc = self.client_allocations[client_id]
            current = client_alloc.get_allocated(resource_type)
            
            if current < amount:
                logger.warning(f"Trying to release more than allocated: {amount} > {current}")
                amount = current
            
            # Release from pool
            pool = self.resource_pools[resource_type]
            pool.release(amount)
            
            # Update client allocation
            client_alloc.remove_allocation(resource_type, amount)
            
            # Clean up empty allocations
            if sum(client_alloc.allocated_resources.values()) == 0:
                del self.client_allocations[client_id]
            
            logger.debug(f"Released {amount} {resource_type} from {client_id}")
            return True
    
    async def _allocation_loop(self) -> None:
        """Background loop for processing pending allocation requests."""
        while self._running:
            try:
                await asyncio.sleep(0.1)  # Process every 100ms
                await self._process_pending_requests()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in allocation loop: {e}")
    
    async def _process_pending_requests(self) -> None:
        """Process pending allocation requests."""
        async with self._lock:
            processed_requests = []
            
            # Process high priority requests first
            while self.pending_requests:
                request = heapq.heappop(self.pending_requests)
                
                # Check if request expired
                if request.is_expired():
                    self.expired_requests += 1
                    logger.debug(f"Request {request.request_id} expired")
                    continue
                
                # Try allocation
                if await self._try_immediate_allocation(request):
                    processed_requests.append(request)
                else:
                    # Put back in queue if not expired
                    heapq.heappush(self.pending_requests, request)
                    break  # Stop processing to avoid infinite loop
            
            # Handle failed allocations
            if processed_requests:
                logger.debug(f"Processed {len(processed_requests)} pending requests")
    
    async def get_resource_stats(self) -> Dict[str, Any]:
        """Get resource allocation statistics."""
        pool_stats = {}
        for resource_type, pool in self.resource_pools.items():
            pool_stats[resource_type] = {
                'total_capacity': pool.total_capacity,
                'allocated': pool.allocated,
                'available': pool.available,
                'utilization_percent': pool.utilization,
                'reserved': pool.reserved
            }
        
        client_stats = {}
        for client_id, alloc in self.client_allocations.items():
            client_stats[client_id] = {
                'allocated_resources': alloc.allocated_resources.copy(),
                'total_allocated': alloc.total_allocated,
                'allocation_count': alloc.allocation_count,
                'priority': alloc.priority,
                'weight': alloc.weight
            }
        
        return {
            'strategy': self.strategy.value,
            'resource_pools': pool_stats,
            'client_allocations': client_stats,
            'pending_requests': len(self.pending_requests),
            'allocation_stats': {
                'total_requests': self.total_requests,
                'successful_allocations': self.successful_allocations,
                'failed_allocations': self.failed_allocations,
                'expired_requests': self.expired_requests,
                'success_rate': self.successful_allocations / max(self.total_requests, 1)
            }
        }
    
    async def set_client_priority(self, client_id: str, priority: int) -> None:
        """Set client priority for allocation."""
        if client_id not in self.client_allocations:
            self.client_allocations[client_id] = ClientAllocation(client_id=client_id)
        
        self.client_allocations[client_id].priority = priority
        logger.info(f"Set client {client_id} priority to {priority}")
    
    async def set_client_weight(self, client_id: str, weight: float) -> None:
        """Set client weight for weighted fair allocation."""
        if client_id not in self.client_allocations:
            self.client_allocations[client_id] = ClientAllocation(client_id=client_id)
        
        self.client_allocations[client_id].weight = weight
        logger.info(f"Set client {client_id} weight to {weight}")


# Global resource allocator
_resource_allocator: Optional[FairResourceAllocator] = None


async def get_resource_allocator() -> FairResourceAllocator:
    """Get the global resource allocator instance."""
    global _resource_allocator
    if _resource_allocator is None:
        _resource_allocator = FairResourceAllocator()
        await _resource_allocator.start()
        
        # Add default resource pools
        _resource_allocator.add_resource_pool("connections", 20.0)
        _resource_allocator.add_resource_pool("memory_mb", 1000.0)
        _resource_allocator.add_resource_pool("cpu_cores", 4.0)
        
    return _resource_allocator


async def cleanup_resource_allocator() -> None:
    """Clean up the global resource allocator."""
    global _resource_allocator
    if _resource_allocator:
        await _resource_allocator.stop()
        _resource_allocator = None


if __name__ == "__main__":
    # Test resource allocator
    async def test_allocator():
        allocator = FairResourceAllocator(AllocationStrategy.WEIGHTED_FAIR)
        await allocator.start()
        
        try:
            # Add resource pools
            allocator.add_resource_pool("connections", 10.0)
            allocator.add_resource_pool("memory_mb", 100.0)
            
            # Set client weights
            await allocator.set_client_weight("client1", 2.0)
            await allocator.set_client_weight("client2", 1.0)
            
            # Request resources
            success1, req1 = await allocator.request_resources("client1", "connections", 3.0, priority=3)
            success2, req2 = await allocator.request_resources("client2", "connections", 2.0, priority=2)
            success3, req3 = await allocator.request_resources("client1", "memory_mb", 40.0, priority=1)
            
            print(f"Allocation results: {success1}, {success2}, {success3}")
            
            # Get stats
            stats = await allocator.get_resource_stats()
            print(f"Resource stats: {stats}")
            
            # Release resources
            await allocator.release_resources("client1", "connections", 1.0)
            
            # Final stats
            final_stats = await allocator.get_resource_stats()
            print(f"Final stats: {final_stats}")
            
        finally:
            await allocator.stop()
    
    asyncio.run(test_allocator())