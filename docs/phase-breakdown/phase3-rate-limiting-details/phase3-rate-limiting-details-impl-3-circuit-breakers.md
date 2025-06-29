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

### 3. Circuit Breaker Implementation {#circuit-breakers}

**Step 1: Circuit Breaker Pattern**

Create `snowflake_mcp_server/circuit_breaker/breaker.py`:

```python
"""Circuit breaker implementation for fault tolerance."""

import asyncio
import time
import logging
from typing import Optional, Callable, Any, Dict
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta
import statistics

from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, RetryError
)

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"       # Normal operation
    OPEN = "open"          # Failing, rejecting requests
    HALF_OPEN = "half_open" # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""
    failure_threshold: int = 5          # Failures before opening
    recovery_timeout: float = 60.0      # Seconds before trying to close
    success_threshold: int = 3          # Successes needed to close from half-open
    timeout: float = 30.0               # Request timeout
    
    # Thresholds for different failure types
    error_rate_threshold: float = 0.5   # 50% error rate threshold
    slow_request_threshold: float = 5.0  # 5 second threshold for slow requests
    
    # Window for calculating statistics
    stats_window_size: int = 100        # Last N requests to consider


class CircuitBreakerStats:
    """Statistics tracking for circuit breaker."""
    
    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self._requests = []  # List of (timestamp, success, duration) tuples
        self._lock = asyncio.Lock()
    
    async def record_request(self, success: bool, duration: float) -> None:
        """Record request result."""
        async with self._lock:
            self._requests.append((time.time(), success, duration))
            
            # Keep only recent requests
            if len(self._requests) > self.window_size:
                self._requests = self._requests[-self.window_size:]
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get current statistics."""
        async with self._lock:
            if not self._requests:
                return {
                    "total_requests": 0,
                    "success_rate": 1.0,
                    "error_rate": 0.0,
                    "avg_duration": 0.0,
                    "slow_requests": 0
                }
            
            total_requests = len(self._requests)
            successful_requests = sum(1 for _, success, _ in self._requests if success)
            success_rate = successful_requests / total_requests
            error_rate = 1.0 - success_rate
            
            durations = [duration for _, _, duration in self._requests]
            avg_duration = statistics.mean(durations)
            slow_requests = sum(1 for duration in durations if duration > 5.0)
            
            return {
                "total_requests": total_requests,
                "successful_requests": successful_requests,
                "success_rate": success_rate,
                "error_rate": error_rate,
                "avg_duration": avg_duration,
                "slow_requests": slow_requests,
                "slow_request_rate": slow_requests / total_requests
            }


class CircuitBreaker:
    """Circuit breaker for protecting against cascading failures."""
    
    def __init__(self, name: str, config: CircuitBreakerConfig):
        self.name = name
        self.config = config
        self.state = CircuitState.CLOSED
        self.stats = CircuitBreakerStats(config.stats_window_size)
        
        # State tracking
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._state_change_time = time.time()
        
        self._lock = asyncio.Lock()
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection."""
        
        # Check if we should allow the request
        if not await self._should_allow_request():
            raise CircuitBreakerOpenError(f"Circuit breaker {self.name} is OPEN")
        
        start_time = time.time()
        success = False
        
        try:
            # Execute with timeout
            result = await asyncio.wait_for(
                func(*args, **kwargs),
                timeout=self.config.timeout
            )
            success = True
            await self._on_success()
            return result
            
        except asyncio.TimeoutError:
            await self._on_failure("timeout")
            raise CircuitBreakerTimeoutError(f"Request timed out after {self.config.timeout}s")
        
        except Exception as e:
            await self._on_failure("error")
            raise
        
        finally:
            duration = time.time() - start_time
            await self.stats.record_request(success, duration)
    
    async def _should_allow_request(self) -> bool:
        """Determine if request should be allowed based on current state."""
        async with self._lock:
            
            if self.state == CircuitState.CLOSED:
                return True
            
            elif self.state == CircuitState.OPEN:
                # Check if we should transition to half-open
                time_since_failure = time.time() - (self._last_failure_time or 0)
                if time_since_failure >= self.config.recovery_timeout:
                    await self._transition_to_half_open()
                    return True
                return False
            
            elif self.state == CircuitState.HALF_OPEN:
                # Allow limited requests to test recovery
                return True
            
            return False
    
    async def _on_success(self) -> None:
        """Handle successful request."""
        async with self._lock:
            
            if self.state == CircuitState.HALF_OPEN:
                self._success_count += 1
                
                if self._success_count >= self.config.success_threshold:
                    await self._transition_to_closed()
            
            elif self.state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0
    
    async def _on_failure(self, failure_type: str) -> None:
        """Handle failed request."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            if self.state == CircuitState.HALF_OPEN:
                # Go back to open on any failure in half-open state
                await self._transition_to_open()
            
            elif self.state == CircuitState.CLOSED:
                # Check if we should open the circuit
                if await self._should_open_circuit():
                    await self._transition_to_open()
    
    async def _should_open_circuit(self) -> bool:
        """Determine if circuit should be opened."""
        
        # Simple failure count threshold
        if self._failure_count >= self.config.failure_threshold:
            return True
        
        # Check error rate threshold
        stats = await self.stats.get_stats()
        if (stats["total_requests"] >= 10 and 
            stats["error_rate"] >= self.config.error_rate_threshold):
            return True
        
        return False
    
    async def _transition_to_open(self) -> None:
        """Transition to OPEN state."""
        old_state = self.state
        self.state = CircuitState.OPEN
        self._state_change_time = time.time()
        self._success_count = 0
        
        logger.warning(f"Circuit breaker {self.name}: {old_state.value} -> OPEN")
        
        # Record state change metric
        from ..monitoring.metrics import metrics
        metrics.record_error("circuit_breaker_opened", self.name)
    
    async def _transition_to_half_open(self) -> None:
        """Transition to HALF_OPEN state."""
        old_state = self.state
        self.state = CircuitState.HALF_OPEN
        self._state_change_time = time.time()
        self._success_count = 0
        
        logger.info(f"Circuit breaker {self.name}: {old_state.value} -> HALF_OPEN")
    
    async def _transition_to_closed(self) -> None:
        """Transition to CLOSED state."""
        old_state = self.state
        self.state = CircuitState.CLOSED
        self._state_change_time = time.time()
        self._failure_count = 0
        self._success_count = 0
        
        logger.info(f"Circuit breaker {self.name}: {old_state.value} -> CLOSED")
    
    async def get_status(self) -> Dict[str, Any]:
        """Get current circuit breaker status."""
        stats = await self.stats.get_stats()
        
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure_time": self._last_failure_time,
            "state_change_time": self._state_change_time,
            "time_in_current_state": time.time() - self._state_change_time,
            "statistics": stats
        }
    
    async def reset(self) -> None:
        """Reset circuit breaker to CLOSED state."""
        async with self._lock:
            self.state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None
            self._state_change_time = time.time()
        
        logger.info(f"Circuit breaker {self.name} manually reset to CLOSED")


class CircuitBreakerOpenError(Exception):
    """Exception raised when circuit breaker is open."""
    pass


class CircuitBreakerTimeoutError(Exception):
    """Exception raised when request times out."""
    pass


# Circuit breaker manager
class CircuitBreakerManager:
    """Manage multiple circuit breakers."""
    
    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
    
    def get_breaker(self, name: str, config: CircuitBreakerConfig = None) -> CircuitBreaker:
        """Get or create circuit breaker."""
        if name not in self._breakers:
            if config is None:
                config = CircuitBreakerConfig()
            self._breakers[name] = CircuitBreaker(name, config)
        
        return self._breakers[name]
    
    async def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all circuit breakers."""
        status = {}
        for name, breaker in self._breakers.items():
            status[name] = await breaker.get_status()
        return status


# Global circuit breaker manager
circuit_breaker_manager = CircuitBreakerManager()


# Decorator for circuit breaker protection
def circuit_breaker(name: str, config: CircuitBreakerConfig = None):
    """Decorator to protect functions with circuit breaker."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            breaker = circuit_breaker_manager.get_breaker(name, config)
            return await breaker.call(func, *args, **kwargs)
        return wrapper
    return decorator


# Database-specific circuit breaker
database_circuit_breaker = circuit_breaker_manager.get_breaker(
    "snowflake_database",
    CircuitBreakerConfig(
        failure_threshold=3,
        recovery_timeout=30.0,
        success_threshold=2,
        timeout=15.0,
        error_rate_threshold=0.3
    )
)
```

