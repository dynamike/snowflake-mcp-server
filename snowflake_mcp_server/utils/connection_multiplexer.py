"""Connection multiplexing support for efficient resource sharing."""

import asyncio
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .async_pool import get_connection_pool
from .request_context import current_client_id, current_request_id

logger = logging.getLogger(__name__)


@dataclass
class ConnectionLease:
    """Represents a connection lease for a specific client/request."""
    
    lease_id: str
    client_id: str
    request_id: str
    connection_id: str
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    operation_count: int = 0
    
    def update_usage(self) -> None:
        """Update usage statistics."""
        self.last_used = time.time()
        self.operation_count += 1
    
    def get_age(self) -> float:
        """Get lease age in seconds."""
        return time.time() - self.created_at
    
    def get_idle_time(self) -> float:
        """Get idle time since last use in seconds."""
        return time.time() - self.last_used


class ConnectionMultiplexer:
    """Manages connection multiplexing across multiple clients and requests."""
    
    def __init__(self,
                 max_lease_duration: float = 300.0,  # 5 minutes
                 cleanup_interval: float = 60.0,     # 1 minute
                 max_leases_per_client: int = 5):
        self.max_lease_duration = max_lease_duration
        self.cleanup_interval = cleanup_interval
        self.max_leases_per_client = max_leases_per_client
        
        # Connection tracking
        self.active_leases: Dict[str, ConnectionLease] = {}
        self.client_leases: Dict[str, Set[str]] = defaultdict(set)
        self.connection_leases: Dict[str, str] = {}  # connection_id -> lease_id
        
        # Connection affinity - prefer same connection for same client
        self.client_affinity: Dict[str, List[str]] = defaultdict(list)
        
        # Statistics
        self.total_leases_created = 0
        self.total_leases_expired = 0
        self.total_operations = 0
        self.total_cache_hits = 0
        
        # Background task
        self._cleanup_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
    
    async def start(self) -> None:
        """Start the connection multiplexer."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("Connection multiplexer started")
    
    async def stop(self) -> None:
        """Stop the connection multiplexer."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            
        # Clean up all active leases
        async with self._lock:
            self.active_leases.clear()
            self.client_leases.clear()
            self.connection_leases.clear()
            self.client_affinity.clear()
            
        logger.info("Connection multiplexer stopped")
    
    @asynccontextmanager
    async def acquire_connection(self, 
                               client_id: Optional[str] = None,
                               request_id: Optional[str] = None,
                               prefer_new: bool = False):
        """Acquire a multiplexed connection with lease management."""
        
        # Use context variables if not provided
        if client_id is None:
            client_id = current_client_id.get() or "unknown-client"
        if request_id is None:
            request_id = current_request_id.get() or "unknown-request"
        
        lease = None
        connection = None
        
        try:
            # Try to get existing lease for efficiency
            if not prefer_new:
                lease = await self._try_reuse_connection(client_id, request_id)
            
            # If no reusable lease, create new one
            if lease is None:
                lease, connection = await self._create_new_lease(client_id, request_id)
            else:
                # Get connection from existing lease
                connection = await self._get_connection_for_lease(lease)
            
            # Track operation
            lease.update_usage()
            self.total_operations += 1
            
            logger.debug(f"Acquired connection lease {lease.lease_id} for {client_id}")
            
            yield connection
            
        finally:
            if lease:
                # Update lease statistics
                lease.update_usage()
                
                # Don't immediately release - let cleanup handle expiration
                logger.debug(f"Released connection lease {lease.lease_id}")
    
    async def _try_reuse_connection(self, client_id: str, request_id: str) -> Optional[ConnectionLease]:
        """Try to reuse an existing connection lease for the client."""
        async with self._lock:
            # Check if client has any active leases
            client_lease_ids = self.client_leases.get(client_id, set())
            
            for lease_id in client_lease_ids:
                lease = self.active_leases.get(lease_id)
                if lease and lease.get_idle_time() < 30.0:  # Reuse if used within 30 seconds
                    self.total_cache_hits += 1
                    logger.debug(f"Reusing connection lease {lease_id} for client {client_id}")
                    return lease
            
            return None
    
    async def _create_new_lease(self, client_id: str, request_id: str) -> Tuple[ConnectionLease, Any]:
        """Create a new connection lease."""
        import uuid
        
        async with self._lock:
            # Check if client has too many leases
            if len(self.client_leases[client_id]) >= self.max_leases_per_client:
                # Remove oldest lease for this client
                oldest_lease_id = min(
                    self.client_leases[client_id],
                    key=lambda lid: self.active_leases[lid].created_at
                )
                await self._remove_lease(oldest_lease_id)
                logger.debug(f"Removed oldest lease for client {client_id}")
        
        # Get connection from pool
        pool = await get_connection_pool()
        
        # Try to get preferred connection for client affinity
        preferred_connection = await self._get_preferred_connection(client_id)
        
        # Acquire connection from pool
        async with pool.acquire() as connection:
            # Create lease
            lease_id = str(uuid.uuid4())
            connection_id = str(id(connection))  # Use object ID as connection identifier
            
            lease = ConnectionLease(
                lease_id=lease_id,
                client_id=client_id,
                request_id=request_id,
                connection_id=connection_id
            )
            
            async with self._lock:
                # Register lease
                self.active_leases[lease_id] = lease
                self.client_leases[client_id].add(lease_id)
                self.connection_leases[connection_id] = lease_id
                
                # Update client affinity
                if connection_id not in self.client_affinity[client_id]:
                    self.client_affinity[client_id].append(connection_id)
                    # Keep only recent connections (max 3)
                    if len(self.client_affinity[client_id]) > 3:
                        self.client_affinity[client_id].pop(0)
                
                self.total_leases_created += 1
            
            logger.debug(f"Created new connection lease {lease_id} for client {client_id}")
            
            return lease, connection
    
    async def _get_connection_for_lease(self, lease: ConnectionLease) -> Any:
        """Get the actual connection object for a lease."""
        # For now, we'll need to acquire a new connection from the pool
        # In a more sophisticated implementation, we could maintain
        # a mapping of lease to actual connection objects
        pool = await get_connection_pool()
        
        # This is a simplified approach - in production you might want
        # to maintain actual connection objects mapped to leases
        async with pool.acquire() as connection:
            return connection
    
    async def _get_preferred_connection(self, client_id: str) -> Optional[str]:
        """Get preferred connection ID for client affinity."""
        affinity_list = self.client_affinity.get(client_id, [])
        
        # Return most recently used connection if available
        if affinity_list:
            return affinity_list[-1]
        
        return None
    
    async def _remove_lease(self, lease_id: str) -> bool:
        """Remove a connection lease (must be called with lock)."""
        if lease_id not in self.active_leases:
            return False
        
        lease = self.active_leases[lease_id]
        
        # Clean up tracking
        self.client_leases[lease.client_id].discard(lease_id)
        if not self.client_leases[lease.client_id]:
            del self.client_leases[lease.client_id]
        
        self.connection_leases.pop(lease.connection_id, None)
        
        # Remove lease
        del self.active_leases[lease_id]
        
        logger.debug(f"Removed connection lease {lease_id}")
        return True
    
    async def cleanup_expired_leases(self) -> int:
        """Clean up expired connection leases."""
        async with self._lock:
            current_time = time.time()
            expired_leases = []
            
            for lease_id, lease in self.active_leases.items():
                if lease.get_age() > self.max_lease_duration:
                    expired_leases.append(lease_id)
            
            # Remove expired leases
            for lease_id in expired_leases:
                await self._remove_lease(lease_id)
                self.total_leases_expired += 1
            
            if expired_leases:
                logger.info(f"Cleaned up {len(expired_leases)} expired connection leases")
            
            return len(expired_leases)
    
    async def _cleanup_loop(self) -> None:
        """Background cleanup loop for expired leases."""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self.cleanup_expired_leases()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in connection multiplexer cleanup: {e}")
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get connection multiplexer statistics."""
        total_leases = len(self.active_leases)
        
        # Calculate lease age distribution
        lease_ages = [lease.get_age() for lease in self.active_leases.values()]
        avg_lease_age = sum(lease_ages) / max(len(lease_ages), 1)
        
        # Client distribution
        client_distribution = {
            client_id: len(lease_ids) 
            for client_id, lease_ids in self.client_leases.items()
        }
        
        # Connection reuse efficiency
        cache_hit_rate = (
            self.total_cache_hits / max(self.total_leases_created, 1)
        ) if self.total_leases_created > 0 else 0
        
        return {
            'total_active_leases': total_leases,
            'unique_clients': len(self.client_leases),
            'average_lease_age_seconds': avg_lease_age,
            'client_distribution': client_distribution,
            'total_leases_created': self.total_leases_created,
            'total_leases_expired': self.total_leases_expired,
            'total_operations': self.total_operations,
            'cache_hits': self.total_cache_hits,
            'cache_hit_rate': cache_hit_rate,
            'config': {
                'max_lease_duration': self.max_lease_duration,
                'cleanup_interval': self.cleanup_interval,
                'max_leases_per_client': self.max_leases_per_client
            }
        }
    
    async def get_client_leases(self, client_id: str) -> List[Dict[str, Any]]:
        """Get all leases for a specific client."""
        lease_ids = self.client_leases.get(client_id, set())
        
        leases = []
        for lease_id in lease_ids:
            lease = self.active_leases.get(lease_id)
            if lease:
                leases.append({
                    'lease_id': lease.lease_id,
                    'request_id': lease.request_id,
                    'connection_id': lease.connection_id,
                    'age_seconds': lease.get_age(),
                    'idle_seconds': lease.get_idle_time(),
                    'operation_count': lease.operation_count,
                    'created_at': lease.created_at,
                    'last_used': lease.last_used
                })
        
        return leases
    
    async def force_cleanup_client(self, client_id: str) -> int:
        """Force cleanup of all leases for a specific client."""
        async with self._lock:
            lease_ids = list(self.client_leases.get(client_id, set()))
            
            for lease_id in lease_ids:
                await self._remove_lease(lease_id)
            
            # Clean up affinity
            self.client_affinity.pop(client_id, None)
            
            if lease_ids:
                logger.info(f"Force cleaned up {len(lease_ids)} leases for client {client_id}")
            
            return len(lease_ids)


# Global connection multiplexer instance
_connection_multiplexer: Optional[ConnectionMultiplexer] = None


async def get_connection_multiplexer() -> ConnectionMultiplexer:
    """Get the global connection multiplexer instance."""
    global _connection_multiplexer
    if _connection_multiplexer is None:
        _connection_multiplexer = ConnectionMultiplexer()
        await _connection_multiplexer.start()
    return _connection_multiplexer


async def cleanup_connection_multiplexer() -> None:
    """Clean up the global connection multiplexer."""
    global _connection_multiplexer
    if _connection_multiplexer:
        await _connection_multiplexer.stop()
        _connection_multiplexer = None


# Convenience function for getting multiplexed connections
@asynccontextmanager
async def get_multiplexed_connection(client_id: Optional[str] = None,
                                   request_id: Optional[str] = None,
                                   prefer_new: bool = False):
    """Get a multiplexed database connection."""
    multiplexer = await get_connection_multiplexer()
    
    async with multiplexer.acquire_connection(client_id, request_id, prefer_new) as connection:
        yield connection


if __name__ == "__main__":
    # Test connection multiplexer
    async def test_multiplexer():
        multiplexer = ConnectionMultiplexer(
            max_lease_duration=10.0,
            cleanup_interval=3.0
        )
        await multiplexer.start()
        
        try:
            # Simulate multiple clients using connections
            async def client_work(client_id: str, operations: int):
                for i in range(operations):
                    async with multiplexer.acquire_connection(client_id, f"req_{i}") as conn:
                        # Simulate work
                        await asyncio.sleep(0.1)
                        print(f"Client {client_id} completed operation {i}")
            
            # Run concurrent client work
            await asyncio.gather(
                client_work("client1", 5),
                client_work("client2", 3),
                client_work("client1", 2),  # Test reuse
            )
            
            # Get stats
            stats = await multiplexer.get_stats()
            print(f"Multiplexer stats: {stats}")
            
            # Test cleanup
            await asyncio.sleep(12)  # Wait for expiration
            expired = await multiplexer.cleanup_expired_leases()
            print(f"Expired leases: {expired}")
            
        finally:
            await multiplexer.stop()
    
    asyncio.run(test_multiplexer())