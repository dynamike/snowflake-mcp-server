"""Client isolation boundaries for secure multi-client operation."""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class IsolationLevel(Enum):
    """Defines different levels of client isolation."""
    STRICT = "strict"        # Complete isolation, no resource sharing
    MODERATE = "moderate"    # Controlled resource sharing
    RELAXED = "relaxed"     # Minimal isolation, maximum sharing


@dataclass
class ClientProfile:
    """Client profile defining isolation requirements and resource limits."""
    
    client_id: str
    isolation_level: IsolationLevel
    max_concurrent_requests: int = 10
    max_connections: int = 5
    max_query_duration: float = 300.0  # 5 minutes
    max_result_rows: int = 10000
    allowed_databases: Optional[Set[str]] = None
    allowed_schemas: Optional[Set[str]] = None
    rate_limit_per_minute: int = 60
    memory_limit_mb: int = 100
    priority: int = 1  # 1=low, 5=high
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    
    def __post_init__(self):
        if self.allowed_databases is None:
            self.allowed_databases = set()
        if self.allowed_schemas is None:
            self.allowed_schemas = set()


@dataclass 
class IsolationContext:
    """Context for tracking client isolation state."""
    
    client_id: str
    request_id: str
    profile: ClientProfile
    namespace: str  # Isolated namespace for this client
    active_requests: Set[str] = field(default_factory=set)
    resource_usage: Dict[str, Any] = field(default_factory=dict)
    last_activity: float = field(default_factory=time.time)
    
    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = time.time()
    
    def add_request(self, request_id: str) -> None:
        """Add an active request."""
        self.active_requests.add(request_id)
        self.update_activity()
    
    def remove_request(self, request_id: str) -> None:
        """Remove a completed request."""
        self.active_requests.discard(request_id)
        self.update_activity()


class ClientIsolationManager:
    """Manages client isolation boundaries and resource limits."""
    
    def __init__(self, default_isolation_level: IsolationLevel = IsolationLevel.MODERATE):
        self.default_isolation_level = default_isolation_level
        self.client_profiles: Dict[str, ClientProfile] = {}
        self.isolation_contexts: Dict[str, IsolationContext] = {}
        self.client_namespaces: Dict[str, str] = {}
        
        # Resource tracking
        self.global_resources = {
            'active_connections': 0,
            'active_requests': 0,
            'memory_usage_mb': 0
        }
        
        # Security boundaries
        self.access_validators: List[Callable] = []
        self.resource_limiters: List[Callable] = []
        
        # Statistics
        self.total_access_denials = 0
        self.total_resource_throttles = 0
        
        self._lock = asyncio.Lock()
    
    async def register_client(self, 
                            client_id: str,
                            isolation_level: Optional[IsolationLevel] = None,
                            **profile_kwargs) -> ClientProfile:
        """Register a client with specific isolation requirements."""
        async with self._lock:
            # Create client profile
            profile = ClientProfile(
                client_id=client_id,
                isolation_level=isolation_level or self.default_isolation_level,
                **profile_kwargs
            )
            
            self.client_profiles[client_id] = profile
            
            # Generate namespace for client
            namespace = self._generate_namespace(client_id)
            self.client_namespaces[client_id] = namespace
            
            logger.info(f"Registered client {client_id} with {profile.isolation_level} isolation")
            return profile
    
    async def get_client_profile(self, client_id: str) -> ClientProfile:
        """Get client profile, creating default if not exists."""
        if client_id not in self.client_profiles:
            return await self.register_client(client_id)
        return self.client_profiles[client_id]
    
    async def create_isolation_context(self, client_id: str, request_id: str) -> IsolationContext:
        """Create an isolation context for a client request."""
        profile = await self.get_client_profile(client_id)
        namespace = self.client_namespaces.get(client_id, self._generate_namespace(client_id))
        
        context = IsolationContext(
            client_id=client_id,
            request_id=request_id,
            profile=profile,
            namespace=namespace
        )
        
        context_key = f"{client_id}:{request_id}"
        self.isolation_contexts[context_key] = context
        
        return context
    
    async def validate_database_access(self, client_id: str, database: str) -> bool:
        """Validate if client can access specified database."""
        profile = await self.get_client_profile(client_id)
        
        # Check allowed databases
        if profile.allowed_databases and database not in profile.allowed_databases:
            self.total_access_denials += 1
            logger.warning(f"Access denied: Client {client_id} cannot access database {database}")
            return False
        
        # Run custom access validators
        for validator in self.access_validators:
            try:
                if not await validator(client_id, "database", database):
                    self.total_access_denials += 1
                    return False
            except Exception as e:
                logger.error(f"Access validator error: {e}")
                return False
        
        return True
    
    async def validate_schema_access(self, client_id: str, database: str, schema: str) -> bool:
        """Validate if client can access specified schema."""
        profile = await self.get_client_profile(client_id)
        
        # First check database access
        if not await self.validate_database_access(client_id, database):
            return False
        
        # Check allowed schemas
        schema_key = f"{database}.{schema}"
        if profile.allowed_schemas and schema_key not in profile.allowed_schemas:
            self.total_access_denials += 1
            logger.warning(f"Access denied: Client {client_id} cannot access schema {schema_key}")
            return False
        
        return True
    
    async def check_resource_limits(self, client_id: str, resource_type: str, 
                                  requested_amount: float) -> bool:
        """Check if client can acquire requested resources."""
        profile = await self.get_client_profile(client_id)
        context_key = f"{client_id}:*"  # Check across all requests for this client
        
        # Get current usage for this client
        client_contexts = [
            ctx for key, ctx in self.isolation_contexts.items() 
            if key.startswith(f"{client_id}:")
        ]
        
        current_requests = sum(len(ctx.active_requests) for ctx in client_contexts)
        
        # Check concurrent requests
        if resource_type == "request" and current_requests >= profile.max_concurrent_requests:
            self.total_resource_throttles += 1
            logger.warning(f"Resource limit exceeded: Client {client_id} has {current_requests} active requests")
            return False
        
        # Check memory usage
        if resource_type == "memory":
            current_memory = sum(
                ctx.resource_usage.get('memory_mb', 0) 
                for ctx in client_contexts
            )
            if current_memory + requested_amount > profile.memory_limit_mb:
                self.total_resource_throttles += 1
                logger.warning(f"Memory limit exceeded: Client {client_id} would use {current_memory + requested_amount}MB")
                return False
        
        # Run custom resource limiters
        for limiter in self.resource_limiters:
            try:
                if not await limiter(client_id, resource_type, requested_amount):
                    self.total_resource_throttles += 1
                    return False
            except Exception as e:
                logger.error(f"Resource limiter error: {e}")
                return False
        
        return True
    
    async def acquire_resources(self, client_id: str, request_id: str, 
                              resources: Dict[str, float]) -> bool:
        """Acquire resources for a client request."""
        context_key = f"{client_id}:{request_id}"
        context = self.isolation_contexts.get(context_key)
        
        if not context:
            logger.error(f"No isolation context found for {context_key}")
            return False
        
        # Check all resource limits
        for resource_type, amount in resources.items():
            if not await self.check_resource_limits(client_id, resource_type, amount):
                return False
        
        # Acquire resources
        async with self._lock:
            for resource_type, amount in resources.items():
                current = context.resource_usage.get(resource_type, 0)
                context.resource_usage[resource_type] = current + amount
                
                # Update global tracking
                if resource_type in self.global_resources:
                    self.global_resources[resource_type] += amount
        
        context.update_activity()
        return True
    
    async def release_resources(self, client_id: str, request_id: str, 
                              resources: Dict[str, float]) -> None:
        """Release resources for a client request."""
        context_key = f"{client_id}:{request_id}"
        context = self.isolation_contexts.get(context_key)
        
        if not context:
            return
        
        async with self._lock:
            for resource_type, amount in resources.items():
                current = context.resource_usage.get(resource_type, 0)
                context.resource_usage[resource_type] = max(0, current - amount)
                
                # Update global tracking
                if resource_type in self.global_resources:
                    self.global_resources[resource_type] = max(
                        0, self.global_resources[resource_type] - amount
                    )
        
        context.update_activity()
    
    def _generate_namespace(self, client_id: str) -> str:
        """Generate a unique namespace for client isolation."""
        # Use hash of client_id plus timestamp for uniqueness
        hash_input = f"{client_id}:{time.time()}"
        namespace_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
        return f"ns_{namespace_hash}"
    
    async def get_client_isolation_info(self, client_id: str) -> Dict[str, Any]:
        """Get detailed isolation information for a client."""
        profile = await self.get_client_profile(client_id)
        namespace = self.client_namespaces.get(client_id)
        
        # Get active contexts for this client
        client_contexts = [
            ctx for key, ctx in self.isolation_contexts.items()
            if key.startswith(f"{client_id}:")
        ]
        
        # Calculate resource usage
        total_memory = sum(ctx.resource_usage.get('memory_mb', 0) for ctx in client_contexts)
        total_requests = sum(len(ctx.active_requests) for ctx in client_contexts)
        
        return {
            'client_id': client_id,
            'namespace': namespace,
            'profile': {
                'isolation_level': profile.isolation_level.value,
                'max_concurrent_requests': profile.max_concurrent_requests,
                'max_connections': profile.max_connections,
                'max_query_duration': profile.max_query_duration,
                'max_result_rows': profile.max_result_rows,
                'rate_limit_per_minute': profile.rate_limit_per_minute,
                'memory_limit_mb': profile.memory_limit_mb,
                'priority': profile.priority,
                'allowed_databases': list(profile.allowed_databases),
                'allowed_schemas': list(profile.allowed_schemas),
            },
            'current_usage': {
                'active_requests': total_requests,
                'memory_mb': total_memory,
                'contexts': len(client_contexts)
            },
            'limits_status': {
                'requests_remaining': max(0, profile.max_concurrent_requests - total_requests),
                'memory_remaining_mb': max(0, profile.memory_limit_mb - total_memory),
            }
        }
    
    async def get_global_isolation_stats(self) -> Dict[str, Any]:
        """Get global isolation statistics."""
        return {
            'registered_clients': len(self.client_profiles),
            'active_contexts': len(self.isolation_contexts),
            'global_resources': self.global_resources.copy(),
            'security_stats': {
                'total_access_denials': self.total_access_denials,
                'total_resource_throttles': self.total_resource_throttles,
            },
            'isolation_levels': {
                level.value: sum(1 for p in self.client_profiles.values() 
                               if p.isolation_level == level)
                for level in IsolationLevel
            }
        }
    
    async def cleanup_expired_contexts(self, max_age: float = 3600.0) -> int:
        """Clean up expired isolation contexts."""
        current_time = time.time()
        expired_contexts = []
        
        for key, context in self.isolation_contexts.items():
            if (current_time - context.last_activity) > max_age:
                expired_contexts.append(key)
        
        # Clean up expired contexts
        for key in expired_contexts:
            context = self.isolation_contexts.pop(key)
            
            # Release any remaining resources
            for resource_type, amount in context.resource_usage.items():
                if resource_type in self.global_resources:
                    self.global_resources[resource_type] = max(
                        0, self.global_resources[resource_type] - amount
                    )
        
        if expired_contexts:
            logger.info(f"Cleaned up {len(expired_contexts)} expired isolation contexts")
        
        return len(expired_contexts)
    
    def add_access_validator(self, validator: Callable) -> None:
        """Add a custom access validator function."""
        self.access_validators.append(validator)
    
    def add_resource_limiter(self, limiter: Callable) -> None:
        """Add a custom resource limiter function."""
        self.resource_limiters.append(limiter)


# Global isolation manager
_isolation_manager: Optional[ClientIsolationManager] = None


def get_isolation_manager() -> ClientIsolationManager:
    """Get the global client isolation manager."""
    global _isolation_manager
    if _isolation_manager is None:
        _isolation_manager = ClientIsolationManager()
    return _isolation_manager


# Convenience functions for isolation checks
async def validate_client_database_access(client_id: str, database: str) -> bool:
    """Validate client database access."""
    manager = get_isolation_manager()
    return await manager.validate_database_access(client_id, database)


async def validate_client_schema_access(client_id: str, database: str, schema: str) -> bool:
    """Validate client schema access."""
    manager = get_isolation_manager()
    return await manager.validate_schema_access(client_id, database, schema)


async def check_client_resource_limits(client_id: str, resource_type: str, amount: float) -> bool:
    """Check client resource limits."""
    manager = get_isolation_manager()
    return await manager.check_resource_limits(client_id, resource_type, amount)


if __name__ == "__main__":
    # Test client isolation
    async def test_isolation():
        manager = ClientIsolationManager()
        
        # Register clients with different isolation levels
        await manager.register_client(
            "client1", 
            IsolationLevel.STRICT,
            max_concurrent_requests=2,
            allowed_databases={"DB1", "DB2"}
        )
        
        await manager.register_client(
            "client2",
            IsolationLevel.RELAXED,
            max_concurrent_requests=5
        )
        
        # Test access validation
        print(f"Client1 DB1 access: {await manager.validate_database_access('client1', 'DB1')}")
        print(f"Client1 DB3 access: {await manager.validate_database_access('client1', 'DB3')}")
        
        # Test resource limits
        print(f"Client1 request limit: {await manager.check_resource_limits('client1', 'request', 1)}")
        
        # Get isolation info
        info = await manager.get_client_isolation_info("client1")
        print(f"Client1 isolation info: {info}")
        
        # Get global stats
        stats = await manager.get_global_isolation_stats()
        print(f"Global isolation stats: {stats}")
    
    asyncio.run(test_isolation())