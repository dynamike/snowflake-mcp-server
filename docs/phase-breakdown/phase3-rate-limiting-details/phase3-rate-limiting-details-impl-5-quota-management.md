# Phase 3: Rate Limiting & Circuit Breakers Implementation Details

## Context & Overview

The current Snowflake MCP server lacks protection against resource exhaustion and cascading failures. Without proper rate limiting and circuit breaker patterns, a single misbehaving client or database connectivity issues can impact all users and potentially crash the server.

**Current Issues:**
- No protection against client abuse or excessive requests
- Database connection failures can cascade and impact all clients
- No graceful degradation during high load or outages
- Missing backoff strategies for failed operations
- No quota management per client or operation type

**Target Architecture:**
- Token bucket rate limiting per client and globally
- Circuit breaker pattern for database operations
- Adaptive backoff strategies for failed requests
- Quota management with daily/hourly limits
- Graceful degradation modes during overload

## Dependencies Required

Add to `pyproject.toml`:
```toml
dependencies = [
    # Existing dependencies...
    "tenacity>=8.2.0",       # Retry and circuit breaker logic
    "slowapi>=0.1.9",        # Rate limiting middleware
    "limits>=3.6.0",         # Rate limiting backend
    "redis>=5.0.0",          # Optional: distributed rate limiting
]

[project.optional-dependencies]
rate_limiting = [
    "redis>=5.0.0",          # Distributed rate limiting
    "hiredis>=2.2.0",        # Fast Redis client
]
```

## Implementation Plan

### 5. Quota Management {#quota-management}

**Step 1: Client Quota System**

Create `snowflake_mcp_server/rate_limiting/quota_manager.py`:

```python
"""Client quota management system."""

import asyncio
import time
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class QuotaType(Enum):
    """Types of quotas."""
    REQUESTS_PER_HOUR = "requests_per_hour"
    REQUESTS_PER_DAY = "requests_per_day"
    QUERIES_PER_HOUR = "queries_per_hour"
    QUERIES_PER_DAY = "queries_per_day"
    DATA_BYTES_PER_DAY = "data_bytes_per_day"


@dataclass
class QuotaLimit:
    """Quota limit configuration."""
    quota_type: QuotaType
    limit: int
    reset_period: timedelta
    
    def __post_init__(self):
        if self.limit <= 0:
            raise ValueError("Quota limit must be positive")


@dataclass
class QuotaUsage:
    """Current quota usage tracking."""
    quota_type: QuotaType
    used: int = 0
    limit: int = 0
    reset_time: Optional[datetime] = None
    
    def remaining(self) -> int:
        """Get remaining quota."""
        return max(0, self.limit - self.used)
    
    def is_exceeded(self) -> bool:
        """Check if quota is exceeded."""
        return self.used >= self.limit
    
    def usage_percentage(self) -> float:
        """Get usage as percentage."""
        if self.limit == 0:
            return 0.0
        return (self.used / self.limit) * 100


class ClientQuotaManager:
    """Manage quotas for individual clients."""
    
    def __init__(self, client_id: str, quota_limits: List[QuotaLimit]):
        self.client_id = client_id
        self.quota_limits = {limit.quota_type: limit for limit in quota_limits}
        self.usage: Dict[QuotaType, QuotaUsage] = {}
        self._lock = asyncio.Lock()
        
        # Initialize usage tracking
        for quota_type, limit in self.quota_limits.items():
            self.usage[quota_type] = QuotaUsage(
                quota_type=quota_type,
                limit=limit.limit,
                reset_time=self._calculate_reset_time(limit.reset_period)
            )
    
    async def consume(self, quota_type: QuotaType, amount: int = 1) -> bool:
        """
        Attempt to consume quota.
        
        Returns:
            bool: True if quota was consumed, False if limit would be exceeded
        """
        async with self._lock:
            if quota_type not in self.usage:
                return True  # No limit configured for this quota type
            
            usage = self.usage[quota_type]
            
            # Check if quota period has reset
            await self._check_and_reset_quota(quota_type)
            
            # Check if consumption would exceed limit
            if usage.used + amount > usage.limit:
                logger.warning(
                    f"Quota exceeded for client {self.client_id}: "
                    f"{quota_type.value} ({usage.used + amount}/{usage.limit})"
                )
                return False
            
            # Consume quota
            usage.used += amount
            logger.debug(
                f"Quota consumed for client {self.client_id}: "
                f"{quota_type.value} ({usage.used}/{usage.limit})"
            )
            
            return True
    
    async def check_quota(self, quota_type: QuotaType, amount: int = 1) -> bool:
        """Check if quota can be consumed without actually consuming."""
        async with self._lock:
            if quota_type not in self.usage:
                return True
            
            usage = self.usage[quota_type]
            await self._check_and_reset_quota(quota_type)
            
            return usage.used + amount <= usage.limit
    
    async def get_usage(self, quota_type: QuotaType) -> Optional[QuotaUsage]:
        """Get current usage for quota type."""
        async with self._lock:
            if quota_type not in self.usage:
                return None
            
            await self._check_and_reset_quota(quota_type)
            return self.usage[quota_type]
    
    async def get_all_usage(self) -> Dict[QuotaType, QuotaUsage]:
        """Get usage for all quota types."""
        async with self._lock:
            result = {}
            for quota_type in self.usage:
                await self._check_and_reset_quota(quota_type)
                result[quota_type] = self.usage[quota_type]
            return result
    
    async def reset_quota(self, quota_type: QuotaType) -> None:
        """Manually reset specific quota."""
        async with self._lock:
            if quota_type in self.usage:
                limit_config = self.quota_limits[quota_type]
                self.usage[quota_type] = QuotaUsage(
                    quota_type=quota_type,
                    limit=limit_config.limit,
                    reset_time=self._calculate_reset_time(limit_config.reset_period)
                )
                logger.info(f"Reset quota {quota_type.value} for client {self.client_id}")
    
    async def _check_and_reset_quota(self, quota_type: QuotaType) -> None:
        """Check if quota period has expired and reset if needed."""
        usage = self.usage[quota_type]
        
        if usage.reset_time and datetime.now() >= usage.reset_time:
            # Reset quota
            limit_config = self.quota_limits[quota_type]
            usage.used = 0
            usage.reset_time = self._calculate_reset_time(limit_config.reset_period)
            
            logger.info(f"Auto-reset quota {quota_type.value} for client {self.client_id}")
    
    def _calculate_reset_time(self, reset_period: timedelta) -> datetime:
        """Calculate next reset time based on period."""
        return datetime.now() + reset_period


class GlobalQuotaManager:
    """Manage quotas for all clients."""
    
    def __init__(self):
        self._client_managers: Dict[str, ClientQuotaManager] = {}
        self._default_quotas: List[QuotaLimit] = []
        self._lock = asyncio.Lock()
        
        # Background cleanup task
        self._cleanup_task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start quota manager."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Global quota manager started")
    
    async def stop(self) -> None:
        """Stop quota manager."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
        logger.info("Global quota manager stopped")
    
    def set_default_quotas(self, quotas: List[QuotaLimit]) -> None:
        """Set default quotas for new clients."""
        self._default_quotas = quotas
        logger.info(f"Set default quotas: {[q.quota_type.value for q in quotas]}")
    
    async def set_client_quotas(self, client_id: str, quotas: List[QuotaLimit]) -> None:
        """Set specific quotas for a client."""
        async with self._lock:
            self._client_managers[client_id] = ClientQuotaManager(client_id, quotas)
        
        logger.info(f"Set custom quotas for client {client_id}")
    
    async def consume_quota(self, client_id: str, quota_type: QuotaType, amount: int = 1) -> bool:
        """Consume quota for client."""
        manager = await self._get_or_create_client_manager(client_id)
        return await manager.consume(quota_type, amount)
    
    async def check_quota(self, client_id: str, quota_type: QuotaType, amount: int = 1) -> bool:
        """Check quota for client."""
        manager = await self._get_or_create_client_manager(client_id)
        return await manager.check_quota(quota_type, amount)
    
    async def get_client_usage(self, client_id: str) -> Dict[QuotaType, QuotaUsage]:
        """Get quota usage for client."""
        if client_id in self._client_managers:
            manager = self._client_managers[client_id]
            return await manager.get_all_usage()
        return {}
    
    async def get_all_clients_usage(self) -> Dict[str, Dict[QuotaType, QuotaUsage]]:
        """Get quota usage for all clients."""
        result = {}
        async with self._lock:
            for client_id, manager in self._client_managers.items():
                result[client_id] = await manager.get_all_usage()
        return result
    
    async def reset_client_quota(self, client_id: str, quota_type: QuotaType) -> bool:
        """Reset specific quota for client."""
        if client_id in self._client_managers:
            await self._client_managers[client_id].reset_quota(quota_type)
            return True
        return False
    
    async def _get_or_create_client_manager(self, client_id: str) -> ClientQuotaManager:
        """Get or create client quota manager."""
        async with self._lock:
            if client_id not in self._client_managers:
                self._client_managers[client_id] = ClientQuotaManager(
                    client_id, self._default_quotas
                )
                logger.debug(f"Created quota manager for client {client_id}")
            
            return self._client_managers[client_id]
    
    async def _cleanup_loop(self) -> None:
        """Background cleanup of inactive client managers."""
        while True:
            try:
                await asyncio.sleep(3600)  # Cleanup every hour
                await self._cleanup_inactive_clients()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in quota cleanup: {e}")
    
    async def _cleanup_inactive_clients(self) -> None:
        """Remove quota managers for inactive clients."""
        # This would integrate with session management to identify inactive clients
        # For now, keep all managers
        pass


# Global quota manager
quota_manager = GlobalQuotaManager()

# Default quotas
DEFAULT_QUOTAS = [
    QuotaLimit(QuotaType.REQUESTS_PER_HOUR, 3600, timedelta(hours=1)),    # 1 request per second
    QuotaLimit(QuotaType.REQUESTS_PER_DAY, 86400, timedelta(days=1)),     # 1 request per second
    QuotaLimit(QuotaType.QUERIES_PER_HOUR, 1800, timedelta(hours=1)),     # 30 queries per minute
    QuotaLimit(QuotaType.QUERIES_PER_DAY, 43200, timedelta(days=1)),      # 0.5 queries per second
    QuotaLimit(QuotaType.DATA_BYTES_PER_DAY, 1073741824, timedelta(days=1)), # 1GB per day
]


# Decorator for quota enforcement
def enforce_quota(quota_type: QuotaType, amount: int = 1):
    """Decorator to enforce quotas on functions."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            from ..utils.request_context import current_client_id
            client_id = current_client_id.get() or "unknown"
            
            # Check quota
            if not await quota_manager.consume_quota(client_id, quota_type, amount):
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Quota exceeded: {quota_type.value}"
                )
            
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator
```

## Testing Strategy

Create `tests/test_rate_limiting.py`:

```python
import pytest
import asyncio
from datetime import timedelta

from snowflake_mcp_server.rate_limiting.token_bucket import TokenBucket, TokenBucketConfig
from snowflake_mcp_server.rate_limiting.quota_manager import QuotaLimit, QuotaType, GlobalQuotaManager
from snowflake_mcp_server.circuit_breaker.breaker import CircuitBreaker, CircuitBreakerConfig

@pytest.mark.asyncio
async def test_token_bucket_rate_limiting():
    """Test token bucket rate limiting."""
    config = TokenBucketConfig(capacity=5, refill_rate=1.0, initial_tokens=5)
    bucket = TokenBucket(config, "test_client")
    
    # Should allow initial burst
    for _ in range(5):
        assert await bucket.consume() == True
    
    # Should reject next request
    assert await bucket.consume() == False
    
    # Wait for refill
    await asyncio.sleep(2)
    
    # Should allow requests again
    assert await bucket.consume() == True


@pytest.mark.asyncio
async def test_quota_management():
    """Test quota management system."""
    quota_manager = GlobalQuotaManager()
    await quota_manager.start()
    
    # Set quotas
    quotas = [
        QuotaLimit(QuotaType.REQUESTS_PER_HOUR, 10, timedelta(hours=1))
    ]
    quota_manager.set_default_quotas(quotas)
    
    try:
        # Consume quota
        client_id = "test_client"
        
        # Should allow up to limit
        for _ in range(10):
            assert await quota_manager.consume_quota(client_id, QuotaType.REQUESTS_PER_HOUR) == True
        
        # Should reject when limit exceeded
        assert await quota_manager.consume_quota(client_id, QuotaType.REQUESTS_PER_HOUR) == False
        
        # Check usage
        usage = await quota_manager.get_client_usage(client_id)
        assert usage[QuotaType.REQUESTS_PER_HOUR].used == 10
        assert usage[QuotaType.REQUESTS_PER_HOUR].remaining() == 0
    
    finally:
        await quota_manager.stop()


@pytest.mark.asyncio
async def test_circuit_breaker():
    """Test circuit breaker functionality."""
    config = CircuitBreakerConfig(failure_threshold=3, recovery_timeout=1.0)
    breaker = CircuitBreaker("test_breaker", config)
    
    # Function that always fails
    async def failing_function():
        raise Exception("Test failure")
    
    # Should fail and eventually open circuit
    for i in range(5):
        try:
            await breaker.call(failing_function)
        except Exception:
            pass
    
    # Circuit should be open now
    status = await breaker.get_status()
    assert status["state"] == "open"
    
    # Should reject immediately
    with pytest.raises(Exception):
        await breaker.call(failing_function)
```

## Verification Steps

1. **Token Bucket Rate Limiting**: Verify clients are limited to configured rates
2. **Global Rate Limits**: Test server-wide protection against overload
3. **Circuit Breaker**: Confirm circuit opens/closes based on failure patterns
4. **Backoff Strategies**: Test exponential and adaptive backoff work correctly
5. **Quota Management**: Verify daily/hourly quotas are enforced properly
6. **Integration**: Test all components work together without conflicts

## Completion Criteria

- [ ] Token bucket rate limiting prevents client abuse
- [ ] Global rate limits protect server from overload
- [ ] Circuit breakers prevent cascading failures during database issues
- [ ] Backoff strategies provide graceful degradation under load
- [ ] Quota management enforces fair usage policies
- [ ] Rate limiting metrics are collected and monitored
- [ ] Performance impact of rate limiting is under 10ms per request
- [ ] Configuration allows tuning limits based on client types
- [ ] Error messages provide clear guidance on retry timing
- [ ] Integration tests demonstrate protection under various failure scenarios