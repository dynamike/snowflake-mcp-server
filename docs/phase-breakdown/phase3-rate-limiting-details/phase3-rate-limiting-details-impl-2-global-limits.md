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

### 2. Global Rate Limits {#global-limits}

**Step 1: Global Rate Limiting**

Create `snowflake_mcp_server/rate_limiting/global_limiter.py`:

```python
"""Global rate limiting to protect server resources."""

import asyncio
import time
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class GlobalRateConfig:
    """Global rate limiting configuration."""
    max_requests_per_second: float = 100.0
    max_concurrent_requests: int = 50
    max_database_queries_per_second: float = 20.0
    max_concurrent_database_operations: int = 20
    
    # Burst allowances
    request_burst_size: int = 200
    query_burst_size: int = 50


class GlobalRateLimiter:
    """Global rate limiter to protect server resources."""
    
    def __init__(self, config: GlobalRateConfig):
        self.config = config
        
        # Request tracking
        self._request_tokens = float(config.request_burst_size)
        self._last_request_refill = time.time()
        
        # Query tracking
        self._query_tokens = float(config.query_burst_size)
        self._last_query_refill = time.time()
        
        # Concurrent operation tracking
        self._concurrent_requests = 0
        self._concurrent_queries = 0
        
        # Semaphores for concurrent limits
        self._request_semaphore = asyncio.Semaphore(config.max_concurrent_requests)
        self._query_semaphore = asyncio.Semaphore(config.max_concurrent_database_operations)
        
        # Statistics
        self._stats = {
            "total_requests": 0,
            "rejected_requests": 0,
            "total_queries": 0,
            "rejected_queries": 0,
            "max_concurrent_requests": 0,
            "max_concurrent_queries": 0
        }
        
        self._lock = asyncio.Lock()
    
    async def acquire_request_permission(self) -> bool:
        """Request permission for HTTP/MCP request."""
        async with self._lock:
            await self._refill_request_tokens()
            
            self._stats["total_requests"] += 1
            
            # Check token bucket
            if self._request_tokens < 1:
                self._stats["rejected_requests"] += 1
                logger.warning("Global request rate limit exceeded")
                return False
            
            # Check concurrent limit
            if self._concurrent_requests >= self.config.max_concurrent_requests:
                self._stats["rejected_requests"] += 1
                logger.warning("Global concurrent request limit exceeded")
                return False
            
            # Grant permission
            self._request_tokens -= 1
            self._concurrent_requests += 1
            self._stats["max_concurrent_requests"] = max(
                self._stats["max_concurrent_requests"],
                self._concurrent_requests
            )
            
            return True
    
    async def release_request_permission(self) -> None:
        """Release request permission."""
        async with self._lock:
            self._concurrent_requests = max(0, self._concurrent_requests - 1)
    
    async def acquire_query_permission(self) -> bool:
        """Request permission for database query."""
        async with self._lock:
            await self._refill_query_tokens()
            
            self._stats["total_queries"] += 1
            
            # Check token bucket
            if self._query_tokens < 1:
                self._stats["rejected_queries"] += 1
                logger.warning("Global query rate limit exceeded")
                return False
            
            # Check concurrent limit
            if self._concurrent_queries >= self.config.max_concurrent_database_operations:
                self._stats["rejected_queries"] += 1
                logger.warning("Global concurrent query limit exceeded")
                return False
            
            # Grant permission
            self._query_tokens -= 1
            self._concurrent_queries += 1
            self._stats["max_concurrent_queries"] = max(
                self._stats["max_concurrent_queries"],
                self._concurrent_queries
            )
            
            return True
    
    async def release_query_permission(self) -> None:
        """Release query permission."""
        async with self._lock:
            self._concurrent_queries = max(0, self._concurrent_queries - 1)
    
    async def _refill_request_tokens(self) -> None:
        """Refill request tokens based on configured rate."""
        now = time.time()
        elapsed = now - self._last_request_refill
        
        if elapsed > 0:
            tokens_to_add = elapsed * self.config.max_requests_per_second
            self._request_tokens = min(
                self.config.request_burst_size,
                self._request_tokens + tokens_to_add
            )
            self._last_request_refill = now
    
    async def _refill_query_tokens(self) -> None:
        """Refill query tokens based on configured rate."""
        now = time.time()
        elapsed = now - self._last_query_refill
        
        if elapsed > 0:
            tokens_to_add = elapsed * self.config.max_database_queries_per_second
            self._query_tokens = min(
                self.config.query_burst_size,
                self._query_tokens + tokens_to_add
            )
            self._last_query_refill = now
    
    def get_stats(self) -> Dict[str, Any]:
        """Get global rate limiting statistics."""
        request_rejection_rate = (
            self._stats["rejected_requests"] / self._stats["total_requests"]
            if self._stats["total_requests"] > 0 else 0
        )
        
        query_rejection_rate = (
            self._stats["rejected_queries"] / self._stats["total_queries"]
            if self._stats["total_queries"] > 0 else 0
        )
        
        return {
            "config": {
                "max_requests_per_second": self.config.max_requests_per_second,
                "max_concurrent_requests": self.config.max_concurrent_requests,
                "max_queries_per_second": self.config.max_database_queries_per_second,
                "max_concurrent_queries": self.config.max_concurrent_database_operations
            },
            "current_state": {
                "request_tokens": self._request_tokens,
                "query_tokens": self._query_tokens,
                "concurrent_requests": self._concurrent_requests,
                "concurrent_queries": self._concurrent_queries
            },
            "statistics": {
                **self._stats,
                "request_rejection_rate": request_rejection_rate,
                "query_rejection_rate": query_rejection_rate
            }
        }


# Context managers for resource protection
from contextlib import asynccontextmanager

@asynccontextmanager
async def global_request_limit():
    """Context manager for global request rate limiting."""
    from ..rate_limiting.global_limiter import global_rate_limiter
    
    allowed = await global_rate_limiter.acquire_request_permission()
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server is currently overloaded. Please try again later."
        )
    
    try:
        yield
    finally:
        await global_rate_limiter.release_request_permission()


@asynccontextmanager
async def global_query_limit():
    """Context manager for global query rate limiting."""
    from ..rate_limiting.global_limiter import global_rate_limiter
    
    allowed = await global_rate_limiter.acquire_query_permission()
    if not allowed:
        raise RuntimeError("Database query rate limit exceeded")
    
    try:
        yield
    finally:
        await global_rate_limiter.release_query_permission()


# Global rate limiter instance
global_rate_limiter = GlobalRateLimiter(GlobalRateConfig())
```

