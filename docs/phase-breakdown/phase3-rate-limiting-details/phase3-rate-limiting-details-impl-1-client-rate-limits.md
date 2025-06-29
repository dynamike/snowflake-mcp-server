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

### 1. Client Rate Limiting {#client-rate-limits}

**Step 1: Token Bucket Rate Limiter**

Create `snowflake_mcp_server/rate_limiting/token_bucket.py`:

```python
"""Token bucket rate limiting implementation."""

import asyncio
import time
import logging
from typing import Dict, Optional, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
import math

logger = logging.getLogger(__name__)


@dataclass
class TokenBucketConfig:
    """Configuration for token bucket rate limiter."""
    capacity: int           # Maximum tokens in bucket
    refill_rate: float     # Tokens per second refill rate
    initial_tokens: int    # Initial tokens when bucket is created
    
    def __post_init__(self):
        if self.initial_tokens > self.capacity:
            self.initial_tokens = self.capacity


class TokenBucket:
    """Token bucket rate limiter for individual clients."""
    
    def __init__(self, config: TokenBucketConfig, client_id: str = "unknown"):
        self.config = config
        self.client_id = client_id
        
        self._tokens = float(config.initial_tokens)
        self._last_refill = time.time()
        self._lock = asyncio.Lock()
        
        # Statistics
        self._total_requests = 0
        self._rejected_requests = 0
        self._last_rejection_time: Optional[float] = None
    
    async def consume(self, tokens: int = 1) -> bool:
        """
        Attempt to consume tokens from bucket.
        
        Returns:
            bool: True if tokens were consumed, False if rejected
        """
        async with self._lock:
            await self._refill()
            
            self._total_requests += 1
            
            if self._tokens >= tokens:
                self._tokens -= tokens
                logger.debug(f"Client {self.client_id}: Consumed {tokens} tokens, {self._tokens:.1f} remaining")
                return True
            else:
                self._rejected_requests += 1
                self._last_rejection_time = time.time()
                logger.warning(f"Client {self.client_id}: Rate limit exceeded, {self._tokens:.1f} tokens available")
                return False
    
    async def peek(self) -> float:
        """Get current token count without consuming."""
        async with self._lock:
            await self._refill()
            return self._tokens
    
    async def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self._last_refill
        
        if elapsed > 0:
            tokens_to_add = elapsed * self.config.refill_rate
            self._tokens = min(self.config.capacity, self._tokens + tokens_to_add)
            self._last_refill = now
    
    def get_stats(self) -> Dict[str, Any]:
        """Get rate limiting statistics."""
        rejection_rate = (
            self._rejected_requests / self._total_requests 
            if self._total_requests > 0 else 0
        )
        
        return {
            "client_id": self.client_id,
            "current_tokens": self._tokens,
            "capacity": self.config.capacity,
            "refill_rate": self.config.refill_rate,
            "total_requests": self._total_requests,
            "rejected_requests": self._rejected_requests,
            "rejection_rate": rejection_rate,
            "last_rejection_time": self._last_rejection_time
        }
    
    async def reset(self) -> None:
        """Reset bucket to initial state."""
        async with self._lock:
            self._tokens = float(self.config.initial_tokens)
            self._last_refill = time.time()
            self._total_requests = 0
            self._rejected_requests = 0
            self._last_rejection_time = None


class ClientRateLimiter:
    """Manage rate limiting for multiple clients."""
    
    def __init__(
        self,
        default_config: TokenBucketConfig,
        cleanup_interval: timedelta = timedelta(hours=1)
    ):
        self.default_config = default_config
        self.cleanup_interval = cleanup_interval
        
        self._client_buckets: Dict[str, TokenBucket] = {}
        self._client_configs: Dict[str, TokenBucketConfig] = {}
        self._lock = asyncio.Lock()
        
        # Background cleanup
        self._cleanup_task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start rate limiter background tasks."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Client rate limiter started")
    
    async def stop(self) -> None:
        """Stop rate limiter."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
        logger.info("Client rate limiter stopped")
    
    async def set_client_config(self, client_id: str, config: TokenBucketConfig) -> None:
        """Set custom rate limit configuration for client."""
        async with self._lock:
            self._client_configs[client_id] = config
            
            # Update existing bucket if present
            if client_id in self._client_buckets:
                self._client_buckets[client_id].config = config
        
        logger.info(f"Updated rate limit config for client {client_id}")
    
    async def consume(self, client_id: str, tokens: int = 1) -> bool:
        """Consume tokens for client."""
        bucket = await self._get_or_create_bucket(client_id)
        return await bucket.consume(tokens)
    
    async def check_limit(self, client_id: str, tokens: int = 1) -> bool:
        """Check if client can consume tokens without actually consuming."""
        bucket = await self._get_or_create_bucket(client_id)
        current_tokens = await bucket.peek()
        return current_tokens >= tokens
    
    async def get_client_stats(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Get rate limiting stats for specific client."""
        async with self._lock:
            if client_id in self._client_buckets:
                return self._client_buckets[client_id].get_stats()
            return None
    
    async def get_all_stats(self) -> Dict[str, Any]:
        """Get rate limiting stats for all clients."""
        async with self._lock:
            client_stats = {}
            total_requests = 0
            total_rejected = 0
            
            for client_id, bucket in self._client_buckets.items():
                stats = bucket.get_stats()
                client_stats[client_id] = stats
                total_requests += stats["total_requests"]
                total_rejected += stats["rejected_requests"]
            
            return {
                "total_clients": len(self._client_buckets),
                "total_requests": total_requests,
                "total_rejected": total_rejected,
                "global_rejection_rate": total_rejected / total_requests if total_requests > 0 else 0,
                "clients": client_stats
            }
    
    async def reset_client(self, client_id: str) -> bool:
        """Reset rate limit for specific client."""
        async with self._lock:
            if client_id in self._client_buckets:
                await self._client_buckets[client_id].reset()
                logger.info(f"Reset rate limit for client {client_id}")
                return True
            return False
    
    async def _get_or_create_bucket(self, client_id: str) -> TokenBucket:
        """Get existing bucket or create new one for client."""
        async with self._lock:
            if client_id not in self._client_buckets:
                config = self._client_configs.get(client_id, self.default_config)
                self._client_buckets[client_id] = TokenBucket(config, client_id)
                logger.debug(f"Created rate limit bucket for client {client_id}")
            
            return self._client_buckets[client_id]
    
    async def _cleanup_loop(self) -> None:
        """Background cleanup of unused client buckets."""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval.total_seconds())
                await self._cleanup_inactive_buckets()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in rate limiter cleanup: {e}")
    
    async def _cleanup_inactive_buckets(self) -> None:
        """Remove buckets for inactive clients."""
        inactive_threshold = time.time() - self.cleanup_interval.total_seconds()
        
        async with self._lock:
            inactive_clients = []
            
            for client_id, bucket in self._client_buckets.items():
                # Remove if no recent rejections and no recent activity
                if (bucket._last_rejection_time is None or 
                    bucket._last_rejection_time < inactive_threshold):
                    inactive_clients.append(client_id)
            
            for client_id in inactive_clients:
                self._client_buckets.pop(client_id, None)
                logger.debug(f"Cleaned up inactive rate limit bucket for {client_id}")


# Global client rate limiter
client_rate_limiter = ClientRateLimiter(
    TokenBucketConfig(
        capacity=100,      # 100 requests burst
        refill_rate=10.0,  # 10 requests per second
        initial_tokens=50  # Start with half capacity
    )
)
```

**Step 2: Rate Limiting Middleware**

Create `snowflake_mcp_server/rate_limiting/middleware.py`:

```python
"""Rate limiting middleware for FastAPI."""

import logging
from typing import Optional, Callable, Dict, Any
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse

from .token_bucket import client_rate_limiter
from ..monitoring.metrics import metrics

logger = logging.getLogger(__name__)


class RateLimitMiddleware:
    """Rate limiting middleware for HTTP requests."""
    
    def __init__(
        self,
        requests_per_second: float = 10.0,
        burst_size: int = 100,
        key_func: Optional[Callable[[Request], str]] = None
    ):
        self.requests_per_second = requests_per_second
        self.burst_size = burst_size
        self.key_func = key_func or self._default_key_func
    
    def _default_key_func(self, request: Request) -> str:
        """Default function to extract client identifier."""
        # Try to get client ID from various sources
        client_id = None
        
        # 1. From request body (MCP requests)
        if hasattr(request, '_json') and request._json:
            params = request._json.get('params', {})
            client_id = params.get('_client_id')
        
        # 2. From headers
        if not client_id:
            client_id = request.headers.get('X-Client-ID')
        
        # 3. From query parameters
        if not client_id:
            client_id = request.query_params.get('client_id')
        
        # 4. Fall back to IP address
        if not client_id:
            forwarded_for = request.headers.get('X-Forwarded-For')
            client_id = forwarded_for.split(',')[0] if forwarded_for else request.client.host
        
        return client_id or 'unknown'
    
    async def __call__(self, request: Request, call_next):
        """Process request with rate limiting."""
        client_id = self.key_func(request)
        
        # Check rate limit
        allowed = await client_rate_limiter.consume(client_id, tokens=1)
        
        if not allowed:
            # Record rate limit violation
            metrics.record_rate_limit_violation(client_id, "http_request")
            
            logger.warning(f"Rate limit exceeded for client {client_id}")
            
            # Return 429 Too Many Requests
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": {
                        "code": 429,
                        "message": "Rate limit exceeded",
                        "retry_after": 60  # Suggest retry after 60 seconds
                    }
                },
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Limit": str(self.burst_size),
                    "X-RateLimit-Remaining": "0"
                }
            )
        
        # Get current token count for headers
        current_tokens = await client_rate_limiter._get_or_create_bucket(client_id)
        remaining_tokens = int(await current_tokens.peek())
        
        # Process request
        response = await call_next(request)
        
        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(self.burst_size)
        response.headers["X-RateLimit-Remaining"] = str(remaining_tokens)
        response.headers["X-RateLimit-Reset"] = str(int(time.time() + 60))
        
        return response


# Rate limiting decorator for handlers
def rate_limit(requests_per_minute: int = 60):
    """Decorator for rate limiting individual handlers."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract client ID from request context
            from ..utils.request_context import current_client_id
            client_id = current_client_id.get() or "unknown"
            
            # Check rate limit
            allowed = await client_rate_limiter.consume(client_id, tokens=1)
            
            if not allowed:
                metrics.record_rate_limit_violation(client_id, f"handler_{func.__name__}")
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded for this operation"
                )
            
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator
```

