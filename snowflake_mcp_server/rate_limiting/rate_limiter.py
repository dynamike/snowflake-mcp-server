"""Per-client and global rate limiting implementation."""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ..config import get_config
from ..monitoring import get_metrics, get_structured_logger

logger = logging.getLogger(__name__)


class RateLimitType(Enum):
    """Types of rate limits."""
    REQUESTS_PER_SECOND = "requests_per_second"
    REQUESTS_PER_MINUTE = "requests_per_minute"
    REQUESTS_PER_HOUR = "requests_per_hour"
    QUERIES_PER_MINUTE = "queries_per_minute"
    QUERIES_PER_HOUR = "queries_per_hour"
    CONCURRENT_REQUESTS = "concurrent_requests"


@dataclass
class RateLimit:
    """Defines a rate limit rule."""
    
    limit_type: RateLimitType
    limit: int
    window_seconds: int
    burst_allowance: int = 0  # Allow brief bursts above limit
    
    @property
    def window_ms(self) -> int:
        """Get window in milliseconds."""
        return self.window_seconds * 1000


class RateLimitError(Exception):
    """Raised when rate limit is exceeded."""
    
    def __init__(self, message: str, retry_after: Optional[float] = None, 
                 limit_type: Optional[str] = None, current_usage: Optional[int] = None,
                 limit_value: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.limit_type = limit_type
        self.current_usage = current_usage
        self.limit_value = limit_value


class TokenBucket:
    """Token bucket algorithm implementation for rate limiting."""
    
    def __init__(self, capacity: int, refill_rate: float, initial_tokens: Optional[int] = None):
        self.capacity = capacity
        self.refill_rate = refill_rate  # tokens per second
        self.tokens = initial_tokens if initial_tokens is not None else capacity
        self.last_refill = time.time()
        self._lock = asyncio.Lock()
    
    async def consume(self, tokens: int = 1) -> Tuple[bool, float]:
        """
        Try to consume tokens from the bucket.
        Returns (success, retry_after_seconds).
        """
        async with self._lock:
            await self._refill()
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True, 0.0
            else:
                # Calculate when we'll have enough tokens
                tokens_needed = tokens - self.tokens
                retry_after = tokens_needed / self.refill_rate
                return False, retry_after
    
    async def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill
        
        if elapsed > 0:
            tokens_to_add = elapsed * self.refill_rate
            self.tokens = min(self.capacity, self.tokens + tokens_to_add)
            self.last_refill = now
    
    async def get_available_tokens(self) -> int:
        """Get number of available tokens."""
        async with self._lock:
            await self._refill()
            return int(self.tokens)


class SlidingWindowCounter:
    """Sliding window counter for rate limiting."""
    
    def __init__(self, window_size: int, max_requests: int):
        self.window_size = window_size  # in seconds
        self.max_requests = max_requests
        self.requests = deque()
        self._lock = asyncio.Lock()
    
    async def is_allowed(self) -> Tuple[bool, Optional[float]]:
        """
        Check if a request is allowed.
        Returns (allowed, retry_after_seconds).
        """
        async with self._lock:
            now = time.time()
            
            # Remove old requests outside the window
            while self.requests and self.requests[0] < now - self.window_size:
                self.requests.popleft()
            
            if len(self.requests) < self.max_requests:
                self.requests.append(now)
                return True, None
            else:
                # Calculate when the oldest request will expire
                oldest_request = self.requests[0]
                retry_after = (oldest_request + self.window_size) - now
                return False, max(0, retry_after)
    
    async def get_current_count(self) -> int:
        """Get current request count in the window."""
        async with self._lock:
            now = time.time()
            
            # Remove old requests
            while self.requests and self.requests[0] < now - self.window_size:
                self.requests.popleft()
            
            return len(self.requests)


class ClientRateLimit:
    """Rate limiting for a specific client."""
    
    def __init__(self, client_id: str, limits: Dict[RateLimitType, RateLimit]):
        self.client_id = client_id
        self.limits = limits
        
        # Initialize rate limiting mechanisms
        self.token_buckets: Dict[RateLimitType, TokenBucket] = {}
        self.sliding_windows: Dict[RateLimitType, SlidingWindowCounter] = {}
        self.concurrent_requests = 0
        self.concurrent_lock = asyncio.Lock()
        
        # Statistics
        self.total_requests = 0
        self.blocked_requests = 0
        self.last_request_time = time.time()
        
        self._init_limiters()
    
    def _init_limiters(self):
        """Initialize rate limiting mechanisms for each limit."""
        for limit_type, limit_config in self.limits.items():
            if limit_type == RateLimitType.CONCURRENT_REQUESTS:
                # Concurrent limits are handled separately
                continue
            elif limit_type in [RateLimitType.REQUESTS_PER_SECOND, RateLimitType.QUERIES_PER_MINUTE]:
                # Use token bucket for smooth rate limiting
                refill_rate = limit_config.limit / limit_config.window_seconds
                capacity = limit_config.limit + limit_config.burst_allowance
                self.token_buckets[limit_type] = TokenBucket(capacity, refill_rate)
            else:
                # Use sliding window for other types
                self.sliding_windows[limit_type] = SlidingWindowCounter(
                    limit_config.window_seconds,
                    limit_config.limit
                )
    
    async def check_limits(self, request_type: str = "request") -> None:
        """
        Check all rate limits for this client.
        Raises RateLimitError if any limit is exceeded.
        """
        self.total_requests += 1
        self.last_request_time = time.time()
        
        # Check concurrent request limit
        if RateLimitType.CONCURRENT_REQUESTS in self.limits:
            async with self.concurrent_lock:
                limit_config = self.limits[RateLimitType.CONCURRENT_REQUESTS]
                if self.concurrent_requests >= limit_config.limit:
                    self.blocked_requests += 1
                    raise RateLimitError(
                        f"Concurrent request limit exceeded for client {self.client_id}",
                        limit_type="concurrent_requests",
                        current_usage=self.concurrent_requests,
                        limit_value=limit_config.limit
                    )
        
        # Check token bucket limits
        for limit_type, bucket in self.token_buckets.items():
            success, retry_after = await bucket.consume()
            if not success:
                self.blocked_requests += 1
                raise RateLimitError(
                    f"{limit_type.value} limit exceeded for client {self.client_id}",
                    retry_after=retry_after,
                    limit_type=limit_type.value,
                    limit_value=self.limits[limit_type].limit
                )
        
        # Check sliding window limits
        for limit_type, window in self.sliding_windows.items():
            allowed, retry_after = await window.is_allowed()
            if not allowed:
                self.blocked_requests += 1
                raise RateLimitError(
                    f"{limit_type.value} limit exceeded for client {self.client_id}",
                    retry_after=retry_after,
                    limit_type=limit_type.value,
                    current_usage=await window.get_current_count(),
                    limit_value=self.limits[limit_type].limit
                )
    
    async def acquire_concurrent_slot(self):
        """Acquire a concurrent request slot."""
        if RateLimitType.CONCURRENT_REQUESTS in self.limits:
            async with self.concurrent_lock:
                self.concurrent_requests += 1
    
    async def release_concurrent_slot(self):
        """Release a concurrent request slot."""
        if RateLimitType.CONCURRENT_REQUESTS in self.limits:
            async with self.concurrent_lock:
                self.concurrent_requests = max(0, self.concurrent_requests - 1)
    
    async def get_status(self) -> Dict[str, Any]:
        """Get current rate limiting status for this client."""
        status = {
            "client_id": self.client_id,
            "total_requests": self.total_requests,
            "blocked_requests": self.blocked_requests,
            "block_rate": self.blocked_requests / max(1, self.total_requests),
            "concurrent_requests": self.concurrent_requests,
            "last_request_time": self.last_request_time,
            "limits": {}
        }
        
        # Get status for each limit type
        for limit_type, limit_config in self.limits.items():
            limit_status = {
                "limit": limit_config.limit,
                "window_seconds": limit_config.window_seconds,
                "burst_allowance": limit_config.burst_allowance,
            }
            
            if limit_type in self.token_buckets:
                bucket = self.token_buckets[limit_type]
                limit_status["available_tokens"] = await bucket.get_available_tokens()
                limit_status["capacity"] = bucket.capacity
            elif limit_type in self.sliding_windows:
                window = self.sliding_windows[limit_type]
                limit_status["current_count"] = await window.get_current_count()
            elif limit_type == RateLimitType.CONCURRENT_REQUESTS:
                limit_status["current_count"] = self.concurrent_requests
            
            status["limits"][limit_type.value] = limit_status
        
        return status


class GlobalRateLimit:
    """Global rate limiting across all clients."""
    
    def __init__(self, limits: Dict[RateLimitType, RateLimit]):
        self.limits = limits
        self.token_buckets: Dict[RateLimitType, TokenBucket] = {}
        self.sliding_windows: Dict[RateLimitType, SlidingWindowCounter] = {}
        self.concurrent_requests = 0
        self.concurrent_lock = asyncio.Lock()
        
        # Statistics
        self.total_requests = 0
        self.blocked_requests = 0
        
        self._init_limiters()
    
    def _init_limiters(self):
        """Initialize global rate limiting mechanisms."""
        for limit_type, limit_config in self.limits.items():
            if limit_type == RateLimitType.CONCURRENT_REQUESTS:
                continue
            elif limit_type in [RateLimitType.REQUESTS_PER_SECOND, RateLimitType.QUERIES_PER_MINUTE]:
                refill_rate = limit_config.limit / limit_config.window_seconds
                capacity = limit_config.limit + limit_config.burst_allowance
                self.token_buckets[limit_type] = TokenBucket(capacity, refill_rate)
            else:
                self.sliding_windows[limit_type] = SlidingWindowCounter(
                    limit_config.window_seconds,
                    limit_config.limit
                )
    
    async def check_limits(self) -> None:
        """Check global rate limits."""
        self.total_requests += 1
        
        # Check concurrent request limit
        if RateLimitType.CONCURRENT_REQUESTS in self.limits:
            async with self.concurrent_lock:
                limit_config = self.limits[RateLimitType.CONCURRENT_REQUESTS]
                if self.concurrent_requests >= limit_config.limit:
                    self.blocked_requests += 1
                    raise RateLimitError(
                        "Global concurrent request limit exceeded",
                        limit_type="global_concurrent_requests",
                        current_usage=self.concurrent_requests,
                        limit_value=limit_config.limit
                    )
        
        # Check other limits
        for limit_type, bucket in self.token_buckets.items():
            success, retry_after = await bucket.consume()
            if not success:
                self.blocked_requests += 1
                raise RateLimitError(
                    f"Global {limit_type.value} limit exceeded",
                    retry_after=retry_after,
                    limit_type=f"global_{limit_type.value}",
                    limit_value=self.limits[limit_type].limit
                )
        
        for limit_type, window in self.sliding_windows.items():
            allowed, retry_after = await window.is_allowed()
            if not allowed:
                self.blocked_requests += 1
                raise RateLimitError(
                    f"Global {limit_type.value} limit exceeded",
                    retry_after=retry_after,
                    limit_type=f"global_{limit_type.value}",
                    current_usage=await window.get_current_count(),
                    limit_value=self.limits[limit_type].limit
                )
    
    async def acquire_concurrent_slot(self):
        """Acquire a global concurrent request slot."""
        if RateLimitType.CONCURRENT_REQUESTS in self.limits:
            async with self.concurrent_lock:
                self.concurrent_requests += 1
    
    async def release_concurrent_slot(self):
        """Release a global concurrent request slot."""
        if RateLimitType.CONCURRENT_REQUESTS in self.limits:
            async with self.concurrent_lock:
                self.concurrent_requests = max(0, self.concurrent_requests - 1)


class RateLimiter:
    """Main rate limiter managing per-client and global limits."""
    
    def __init__(self):
        self.config = get_config()
        self.metrics = get_metrics()
        self.logger = get_structured_logger().get_logger("rate_limiter")
        
        # Client rate limits
        self.client_limits: Dict[str, ClientRateLimit] = {}
        self.default_client_limits = self._get_default_client_limits()
        
        # Global rate limits
        self.global_limits = GlobalRateLimit(self._get_global_limits())
        
        # Cleanup task
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
    
    def _get_default_client_limits(self) -> Dict[RateLimitType, RateLimit]:
        """Get default per-client rate limits from configuration."""
        return {
            RateLimitType.REQUESTS_PER_SECOND: RateLimit(
                RateLimitType.REQUESTS_PER_SECOND,
                limit=getattr(self.config.rate_limiting, 'client_requests_per_second', 10),
                window_seconds=1,
                burst_allowance=5
            ),
            RateLimitType.REQUESTS_PER_MINUTE: RateLimit(
                RateLimitType.REQUESTS_PER_MINUTE,
                limit=getattr(self.config.rate_limiting, 'client_requests_per_minute', 300),
                window_seconds=60,
                burst_allowance=50
            ),
            RateLimitType.QUERIES_PER_MINUTE: RateLimit(
                RateLimitType.QUERIES_PER_MINUTE,
                limit=getattr(self.config.rate_limiting, 'client_queries_per_minute', 100),
                window_seconds=60,
                burst_allowance=20
            ),
            RateLimitType.CONCURRENT_REQUESTS: RateLimit(
                RateLimitType.CONCURRENT_REQUESTS,
                limit=getattr(self.config.rate_limiting, 'client_concurrent_requests', 5),
                window_seconds=0  # Not applicable for concurrent limits
            ),
        }
    
    def _get_global_limits(self) -> Dict[RateLimitType, RateLimit]:
        """Get global rate limits from configuration."""
        return {
            RateLimitType.REQUESTS_PER_SECOND: RateLimit(
                RateLimitType.REQUESTS_PER_SECOND,
                limit=getattr(self.config.rate_limiting, 'global_requests_per_second', 100),
                window_seconds=1,
                burst_allowance=50
            ),
            RateLimitType.QUERIES_PER_MINUTE: RateLimit(
                RateLimitType.QUERIES_PER_MINUTE,
                limit=getattr(self.config.rate_limiting, 'global_queries_per_minute', 1000),
                window_seconds=60,
                burst_allowance=200
            ),
            RateLimitType.CONCURRENT_REQUESTS: RateLimit(
                RateLimitType.CONCURRENT_REQUESTS,
                limit=getattr(self.config.rate_limiting, 'global_concurrent_requests', 50),
                window_seconds=0
            ),
        }
    
    def get_client_limiter(self, client_id: str) -> ClientRateLimit:
        """Get or create rate limiter for a client."""
        if client_id not in self.client_limits:
            # Check if client has custom limits (could be loaded from database)
            custom_limits = self._get_custom_client_limits(client_id)
            limits = custom_limits if custom_limits else self.default_client_limits
            
            self.client_limits[client_id] = ClientRateLimit(client_id, limits)
            
            self.logger.info(
                f"Created rate limiter for client {client_id}",
                client_id=client_id,
                limits={k.value: v.limit for k, v in limits.items()}
            )
        
        return self.client_limits[client_id]
    
    def _get_custom_client_limits(self, client_id: str) -> Optional[Dict[RateLimitType, RateLimit]]:
        """Get custom rate limits for a specific client."""
        # This would typically load from a database or configuration
        # For now, return None to use default limits
        return None
    
    async def check_rate_limits(self, client_id: str, request_type: str = "request") -> None:
        """
        Check both global and client-specific rate limits.
        Raises RateLimitError if any limit is exceeded.
        """
        try:
            # Check global limits first
            await self.global_limits.check_limits()
            
            # Check client-specific limits
            client_limiter = self.get_client_limiter(client_id)
            await client_limiter.check_limits(request_type)
            
            # Record successful rate limit check
            self.metrics.record_request(
                client_id=client_id,
                tool_name="rate_limit_check",
                duration=0.001,  # Minimal duration for rate limit check
                status="success"
            )
            
        except RateLimitError as e:
            # Record rate limit violation
            self.metrics.rate_limit_hits.labels(
                client_id=client_id,
                limit_type=e.limit_type or "unknown"
            ).inc()
            
            self.logger.warning(
                f"Rate limit exceeded for client {client_id}",
                client_id=client_id,
                limit_type=e.limit_type,
                current_usage=e.current_usage,
                limit_value=e.limit_value,
                retry_after=e.retry_after
            )
            
            raise
    
    async def acquire_request_slot(self, client_id: str) -> None:
        """Acquire concurrent request slots."""
        await self.global_limits.acquire_concurrent_slot()
        
        client_limiter = self.get_client_limiter(client_id)
        await client_limiter.acquire_concurrent_slot()
    
    async def release_request_slot(self, client_id: str) -> None:
        """Release concurrent request slots."""
        await self.global_limits.release_concurrent_slot()
        
        if client_id in self.client_limits:
            await self.client_limits[client_id].release_concurrent_slot()
    
    def set_custom_limits(self, client_id: str, limits: Dict[RateLimitType, RateLimit]):
        """Set custom rate limits for a specific client."""
        if client_id in self.client_limits:
            # Remove existing limiter
            del self.client_limits[client_id]
        
        # Create new limiter with custom limits
        self.client_limits[client_id] = ClientRateLimit(client_id, limits)
        
        self.logger.info(
            f"Updated custom rate limits for client {client_id}",
            client_id=client_id,
            limits={k.value: v.limit for k, v in limits.items()}
        )
    
    async def get_client_status(self, client_id: str) -> Dict[str, Any]:
        """Get rate limiting status for a client."""
        if client_id not in self.client_limits:
            return {"error": "Client not found"}
        
        return await self.client_limits[client_id].get_status()
    
    async def get_global_status(self) -> Dict[str, Any]:
        """Get global rate limiting status."""
        return {
            "total_requests": self.global_limits.total_requests,
            "blocked_requests": self.global_limits.blocked_requests,
            "block_rate": self.global_limits.blocked_requests / max(1, self.global_limits.total_requests),
            "concurrent_requests": self.global_limits.concurrent_requests,
            "active_clients": len(self.client_limits),
        }
    
    async def get_all_clients_status(self) -> List[Dict[str, Any]]:
        """Get rate limiting status for all clients."""
        statuses = []
        for client_id in self.client_limits:
            status = await self.get_client_status(client_id)
            statuses.append(status)
        return statuses
    
    async def start_cleanup(self):
        """Start background cleanup of inactive clients."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def stop_cleanup(self):
        """Stop background cleanup."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
    
    async def _cleanup_loop(self):
        """Background task to clean up inactive clients."""
        while self._running:
            try:
                current_time = time.time()
                cleanup_threshold = current_time - 3600  # 1 hour
                
                clients_to_remove = []
                for client_id, client_limiter in self.client_limits.items():
                    if client_limiter.last_request_time < cleanup_threshold:
                        clients_to_remove.append(client_id)
                
                for client_id in clients_to_remove:
                    del self.client_limits[client_id]
                    self.logger.info(f"Cleaned up inactive client rate limiter: {client_id}")
                
                await asyncio.sleep(600)  # Check every 10 minutes
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in rate limiter cleanup: {e}")
                await asyncio.sleep(60)


# Global rate limiter instance
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def rate_limit_middleware(func):
    """Decorator to apply rate limiting to functions."""
    from functools import wraps
    
    @wraps(func)
    async def wrapper(*args, **kwargs):
        rate_limiter = get_rate_limiter()
        client_id = kwargs.get('client_id', 'unknown')
        
        # Check rate limits
        await rate_limiter.check_rate_limits(client_id)
        
        # Acquire concurrent slot
        await rate_limiter.acquire_request_slot(client_id)
        
        try:
            return await func(*args, **kwargs)
        finally:
            # Always release the slot
            await rate_limiter.release_request_slot(client_id)
    
    return wrapper


# FastAPI endpoints for rate limiting management
async def get_rate_limit_status_endpoint(client_id: Optional[str] = None) -> Dict[str, Any]:
    """API endpoint to get rate limiting status."""
    rate_limiter = get_rate_limiter()
    
    if client_id:
        return {
            "client_status": await rate_limiter.get_client_status(client_id),
            "timestamp": time.time(),
        }
    else:
        return {
            "global_status": await rate_limiter.get_global_status(),
            "all_clients": await rate_limiter.get_all_clients_status(),
            "timestamp": time.time(),
        }


async def update_client_limits_endpoint(client_id: str, limits: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """API endpoint to update client rate limits."""
    rate_limiter = get_rate_limiter()
    
    try:
        # Convert API format to internal format
        internal_limits = {}
        for limit_type_str, limit_config in limits.items():
            limit_type = RateLimitType(limit_type_str)
            internal_limits[limit_type] = RateLimit(
                limit_type=limit_type,
                limit=limit_config["limit"],
                window_seconds=limit_config["window_seconds"],
                burst_allowance=limit_config.get("burst_allowance", 0)
            )
        
        rate_limiter.set_custom_limits(client_id, internal_limits)
        
        return {"success": True, "message": f"Updated rate limits for client {client_id}"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    # Test rate limiting
    import asyncio
    
    async def test_rate_limiting():
        limiter = RateLimiter()
        
        # Test client rate limiting
        client_id = "test_client"
        
        try:
            for i in range(15):  # Should exceed per-second limit
                await limiter.check_rate_limits(client_id)
                print(f"Request {i+1} allowed")
                await asyncio.sleep(0.05)  # 50ms between requests
        except RateLimitError as e:
            print(f"Rate limit exceeded: {e}")
            print(f"Retry after: {e.retry_after} seconds")
        
        # Check status
        status = await limiter.get_client_status(client_id)
        print(f"Client status: {status}")
    
    asyncio.run(test_rate_limiting())