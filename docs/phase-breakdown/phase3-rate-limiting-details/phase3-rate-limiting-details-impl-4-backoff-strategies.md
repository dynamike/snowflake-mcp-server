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

### 4. Backoff Strategies {#backoff-strategies}

**Step 1: Adaptive Backoff Implementation**

Create `snowflake_mcp_server/rate_limiting/backoff.py`:

```python
"""Adaptive backoff strategies for failed operations."""

import asyncio
import random
import time
import logging
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import math

logger = logging.getLogger(__name__)


@dataclass
class BackoffConfig:
    """Configuration for backoff strategies."""
    initial_delay: float = 1.0          # Initial delay in seconds
    max_delay: float = 300.0            # Maximum delay in seconds
    multiplier: float = 2.0             # Exponential multiplier
    jitter: bool = True                 # Add random jitter
    max_attempts: int = 10              # Maximum retry attempts


class BackoffStrategy:
    """Base class for backoff strategies."""
    
    def __init__(self, config: BackoffConfig):
        self.config = config
        self._attempt_count = 0
        self._last_attempt_time: Optional[float] = None
    
    async def delay(self) -> float:
        """Calculate and apply delay before next attempt."""
        delay = self.calculate_delay()
        
        if delay > 0:
            logger.debug(f"Backoff delay: {delay:.2f}s (attempt {self._attempt_count})")
            await asyncio.sleep(delay)
        
        self._attempt_count += 1
        self._last_attempt_time = time.time()
        
        return delay
    
    def calculate_delay(self) -> float:
        """Calculate delay without applying it."""
        raise NotImplementedError
    
    def should_retry(self) -> bool:
        """Check if we should attempt another retry."""
        return self._attempt_count < self.config.max_attempts
    
    def reset(self) -> None:
        """Reset backoff state."""
        self._attempt_count = 0
        self._last_attempt_time = None


class ExponentialBackoff(BackoffStrategy):
    """Exponential backoff with optional jitter."""
    
    def calculate_delay(self) -> float:
        """Calculate exponential backoff delay."""
        if self._attempt_count == 0:
            return 0  # No delay on first attempt
        
        # Exponential backoff: initial_delay * (multiplier ^ (attempt - 1))
        delay = self.config.initial_delay * (self.config.multiplier ** (self._attempt_count - 1))
        
        # Cap at maximum delay
        delay = min(delay, self.config.max_delay)
        
        # Add jitter to avoid thundering herd
        if self.config.jitter:
            jitter_range = delay * 0.1  # 10% jitter
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0, delay)


class LinearBackoff(BackoffStrategy):
    """Linear backoff strategy."""
    
    def calculate_delay(self) -> float:
        """Calculate linear backoff delay."""
        if self._attempt_count == 0:
            return 0
        
        delay = self.config.initial_delay * self._attempt_count
        delay = min(delay, self.config.max_delay)
        
        if self.config.jitter:
            jitter_range = delay * 0.1
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0, delay)


class AdaptiveBackoff(BackoffStrategy):
    """Adaptive backoff that adjusts based on success/failure patterns."""
    
    def __init__(self, config: BackoffConfig):
        super().__init__(config)
        self._recent_failures = []  # Track recent failure times
        self._success_count = 0
        self._total_attempts = 0
    
    def calculate_delay(self) -> float:
        """Calculate adaptive delay based on recent failure patterns."""
        if self._attempt_count == 0:
            return 0
        
        # Clean old failures (older than 1 hour)
        cutoff_time = time.time() - 3600
        self._recent_failures = [f for f in self._recent_failures if f > cutoff_time]
        
        # Base exponential backoff
        base_delay = self.config.initial_delay * (self.config.multiplier ** (self._attempt_count - 1))
        
        # Adjust based on recent failure rate
        failure_rate = len(self._recent_failures) / max(1, self._total_attempts)
        
        if failure_rate > 0.5:  # High failure rate
            base_delay *= 2.0  # Increase delay
        elif failure_rate < 0.1:  # Low failure rate
            base_delay *= 0.5  # Decrease delay
        
        delay = min(base_delay, self.config.max_delay)
        
        if self.config.jitter:
            jitter_range = delay * 0.1
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0, delay)
    
    def record_failure(self) -> None:
        """Record a failure for adaptive calculation."""
        self._recent_failures.append(time.time())
        self._total_attempts += 1
    
    def record_success(self) -> None:
        """Record a success for adaptive calculation."""
        self._success_count += 1
        self._total_attempts += 1


class RetryExecutor:
    """Execute operations with configurable retry and backoff."""
    
    def __init__(self, backoff_strategy: BackoffStrategy):
        self.backoff = backoff_strategy
    
    async def execute(
        self,
        operation: Callable,
        *args,
        retry_on: tuple = (Exception,),
        **kwargs
    ) -> Any:
        """Execute operation with retry and backoff."""
        
        self.backoff.reset()
        last_exception = None
        
        while self.backoff.should_retry():
            try:
                # Apply backoff delay
                await self.backoff.delay()
                
                # Execute operation
                result = await operation(*args, **kwargs)
                
                # Record success if using adaptive backoff
                if isinstance(self.backoff, AdaptiveBackoff):
                    self.backoff.record_success()
                
                return result
                
            except retry_on as e:
                last_exception = e
                
                # Record failure if using adaptive backoff
                if isinstance(self.backoff, AdaptiveBackoff):
                    self.backoff.record_failure()
                
                logger.warning(f"Operation failed (attempt {self.backoff._attempt_count}): {e}")
                
                # Check if we should continue retrying
                if not self.backoff.should_retry():
                    break
        
        # All retries exhausted
        logger.error(f"Operation failed after {self.backoff._attempt_count} attempts")
        raise last_exception or Exception("Maximum retry attempts exceeded")


# Predefined backoff strategies
database_backoff = ExponentialBackoff(BackoffConfig(
    initial_delay=1.0,
    max_delay=60.0,
    multiplier=2.0,
    jitter=True,
    max_attempts=5
))

connection_backoff = ExponentialBackoff(BackoffConfig(
    initial_delay=2.0,
    max_delay=120.0,
    multiplier=1.5,
    jitter=True,
    max_attempts=3
))

adaptive_backoff = AdaptiveBackoff(BackoffConfig(
    initial_delay=0.5,
    max_delay=30.0,
    multiplier=1.8,
    jitter=True,
    max_attempts=8
))


# Decorator for retry with backoff
def retry_with_backoff(backoff_strategy: BackoffStrategy, retry_on: tuple = (Exception,)):
    """Decorator for retrying operations with backoff."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            executor = RetryExecutor(backoff_strategy)
            return await executor.execute(func, *args, retry_on=retry_on, **kwargs)
        return wrapper
    return decorator
```

