"""Circuit breaker implementation for fault tolerance."""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional

from ..config import get_config
from ..monitoring import get_metrics, get_structured_logger

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, blocking requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for a circuit breaker."""
    
    failure_threshold: int = 5  # Number of failures to open circuit
    recovery_timeout: float = 60.0  # Seconds before trying to recover
    success_threshold: int = 3  # Successes needed to close circuit from half-open
    timeout: float = 30.0  # Request timeout in seconds
    monitoring_window: int = 60  # Window for monitoring failures (seconds)
    
    # Advanced configuration
    exponential_backoff: bool = True
    max_recovery_timeout: float = 300.0  # Maximum backoff time
    half_open_max_calls: int = 5  # Max calls allowed in half-open state


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""
    
    def __init__(self, message: str, circuit_name: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.circuit_name = circuit_name
        self.retry_after = retry_after


class CircuitBreakerMetrics:
    """Tracks metrics for a circuit breaker."""
    
    def __init__(self):
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.rejected_requests = 0
        self.state_changes = 0
        self.last_failure_time: Optional[float] = None
        self.last_success_time: Optional[float] = None
        
        # Sliding window for recent failures
        self.recent_failures: deque = deque()
        self.recent_successes: deque = deque()
    
    def record_success(self, timestamp: float = None):
        """Record a successful request."""
        if timestamp is None:
            timestamp = time.time()
        
        self.successful_requests += 1
        self.total_requests += 1
        self.last_success_time = timestamp
        self.recent_successes.append(timestamp)
    
    def record_failure(self, timestamp: float = None):
        """Record a failed request."""
        if timestamp is None:
            timestamp = time.time()
        
        self.failed_requests += 1
        self.total_requests += 1
        self.last_failure_time = timestamp
        self.recent_failures.append(timestamp)
    
    def record_rejection(self):
        """Record a rejected request (circuit open)."""
        self.rejected_requests += 1
    
    def record_state_change(self):
        """Record a state change."""
        self.state_changes += 1
    
    def get_failure_rate(self, window_seconds: int = 60) -> float:
        """Get failure rate in the specified window."""
        now = time.time()
        cutoff = now - window_seconds
        
        # Clean old entries
        while self.recent_failures and self.recent_failures[0] < cutoff:
            self.recent_failures.popleft()
        while self.recent_successes and self.recent_successes[0] < cutoff:
            self.recent_successes.popleft()
        
        total_recent = len(self.recent_failures) + len(self.recent_successes)
        if total_recent == 0:
            return 0.0
        
        return len(self.recent_failures) / total_recent
    
    def get_recent_failure_count(self, window_seconds: int = 60) -> int:
        """Get number of failures in the specified window."""
        now = time.time()
        cutoff = now - window_seconds
        
        # Clean old entries
        while self.recent_failures and self.recent_failures[0] < cutoff:
            self.recent_failures.popleft()
        
        return len(self.recent_failures)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "rejected_requests": self.rejected_requests,
            "state_changes": self.state_changes,
            "success_rate": self.successful_requests / max(1, self.total_requests),
            "failure_rate": self.failed_requests / max(1, self.total_requests),
            "recent_failure_rate": self.get_failure_rate(),
            "last_failure_time": self.last_failure_time,
            "last_success_time": self.last_success_time,
        }


class CircuitBreaker:
    """Circuit breaker implementation for fault tolerance."""
    
    def __init__(self, name: str, config: CircuitBreakerConfig):
        self.name = name
        self.config = config
        self.state = CircuitState.CLOSED
        self.state_changed_at = time.time()
        self.failure_count = 0
        self.success_count = 0
        self.half_open_calls = 0
        
        # Metrics tracking
        self.metrics = CircuitBreakerMetrics()
        
        # Locks for thread safety
        self._state_lock = asyncio.Lock()
        
        # Logging
        self.logger = get_structured_logger().get_logger("circuit_breaker")
        self.prometheus_metrics = get_metrics()
        
        # Set initial circuit breaker state in Prometheus
        self.prometheus_metrics.circuit_breaker_state.labels(component=self.name).state(self.state.value)
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function through the circuit breaker.
        
        Args:
            func: The function to execute
            *args, **kwargs: Arguments to pass to the function
            
        Returns:
            The result of the function call
            
        Raises:
            CircuitBreakerOpenError: If the circuit is open
            Any exception raised by the function
        """
        async with self._state_lock:
            # Check if we can make the call
            if not await self._can_execute():
                self.metrics.record_rejection()
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self.name}' is open",
                    circuit_name=self.name,
                    retry_after=await self._get_retry_after()
                )
            
            # Increment call count for half-open state
            if self.state == CircuitState.HALF_OPEN:
                self.half_open_calls += 1
        
        # Execute the function with timeout
        try:
            result = await asyncio.wait_for(
                func(*args, **kwargs) if asyncio.iscoroutinefunction(func) else func(*args, **kwargs),
                timeout=self.config.timeout
            )
            
            # Record success
            await self._on_success()
            return result
            
        except asyncio.TimeoutError:
            await self._on_failure("timeout")
            raise
        except Exception as e:
            await self._on_failure(str(type(e).__name__))
            raise
    
    async def _can_execute(self) -> bool:
        """Check if we can execute a request based on current state."""
        now = time.time()
        
        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.OPEN:
            # Check if we should transition to half-open
            if now - self.state_changed_at >= await self._get_recovery_timeout():
                await self._transition_to_half_open()
                return True
            return False
        elif self.state == CircuitState.HALF_OPEN:
            # Allow limited calls in half-open state
            return self.half_open_calls < self.config.half_open_max_calls
        
        return False
    
    async def _on_success(self):
        """Handle successful execution."""
        async with self._state_lock:
            self.metrics.record_success()
            
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                
                # Check if we have enough successes to close the circuit
                if self.success_count >= self.config.success_threshold:
                    await self._transition_to_closed()
            elif self.state == CircuitState.CLOSED:
                # Reset failure count on success
                self.failure_count = 0
    
    async def _on_failure(self, error_type: str):
        """Handle failed execution."""
        async with self._state_lock:
            self.metrics.record_failure()
            
            if self.state == CircuitState.CLOSED:
                self.failure_count += 1
                
                # Check failure rate and count
                failure_rate = self.metrics.get_failure_rate(self.config.monitoring_window)
                recent_failures = self.metrics.get_recent_failure_count(self.config.monitoring_window)
                
                if (self.failure_count >= self.config.failure_threshold or 
                    recent_failures >= self.config.failure_threshold):
                    await self._transition_to_open()
                    
            elif self.state == CircuitState.HALF_OPEN:
                # Any failure in half-open state opens the circuit
                await self._transition_to_open()
        
        # Log the failure
        self.logger.warning(
            f"Circuit breaker '{self.name}' recorded failure",
            circuit_name=self.name,
            error_type=error_type,
            state=self.state.value,
            failure_count=self.failure_count,
            event_type="circuit_breaker_failure"
        )
    
    async def _transition_to_open(self):
        """Transition circuit breaker to OPEN state."""
        if self.state != CircuitState.OPEN:
            self.state = CircuitState.OPEN
            self.state_changed_at = time.time()
            self.success_count = 0
            self.half_open_calls = 0
            self.metrics.record_state_change()
            
            # Update Prometheus metrics
            self.prometheus_metrics.circuit_breaker_state.labels(component=self.name).state("open")
            
            self.logger.error(
                f"Circuit breaker '{self.name}' opened",
                circuit_name=self.name,
                failure_count=self.failure_count,
                state_duration=0,
                event_type="circuit_breaker_opened"
            )
    
    async def _transition_to_half_open(self):
        """Transition circuit breaker to HALF_OPEN state."""
        if self.state != CircuitState.HALF_OPEN:
            self.state = CircuitState.HALF_OPEN
            self.state_changed_at = time.time()
            self.success_count = 0
            self.half_open_calls = 0
            self.metrics.record_state_change()
            
            # Update Prometheus metrics
            self.prometheus_metrics.circuit_breaker_state.labels(component=self.name).state("half_open")
            
            self.logger.info(
                f"Circuit breaker '{self.name}' half-opened",
                circuit_name=self.name,
                event_type="circuit_breaker_half_opened"
            )
    
    async def _transition_to_closed(self):
        """Transition circuit breaker to CLOSED state."""
        if self.state != CircuitState.CLOSED:
            old_state = self.state
            self.state = CircuitState.CLOSED
            self.state_changed_at = time.time()
            self.failure_count = 0
            self.success_count = 0
            self.half_open_calls = 0
            self.metrics.record_state_change()
            
            # Update Prometheus metrics
            self.prometheus_metrics.circuit_breaker_state.labels(component=self.name).state("closed")
            
            self.logger.info(
                f"Circuit breaker '{self.name}' closed",
                circuit_name=self.name,
                previous_state=old_state.value,
                event_type="circuit_breaker_closed"
            )
    
    async def _get_recovery_timeout(self) -> float:
        """Get the recovery timeout, potentially with exponential backoff."""
        if not self.config.exponential_backoff:
            return self.config.recovery_timeout
        
        # Exponential backoff based on number of state changes
        backoff_multiplier = min(2 ** (self.metrics.state_changes // 2), 
                               self.config.max_recovery_timeout / self.config.recovery_timeout)
        
        return min(self.config.recovery_timeout * backoff_multiplier, 
                  self.config.max_recovery_timeout)
    
    async def _get_retry_after(self) -> float:
        """Get the retry-after time for open circuit."""
        recovery_timeout = await self._get_recovery_timeout()
        elapsed = time.time() - self.state_changed_at
        return max(0, recovery_timeout - elapsed)
    
    async def force_open(self):
        """Manually force the circuit breaker open."""
        async with self._state_lock:
            await self._transition_to_open()
            
        self.logger.warning(
            f"Circuit breaker '{self.name}' manually forced open",
            circuit_name=self.name,
            event_type="circuit_breaker_forced_open"
        )
    
    async def force_close(self):
        """Manually force the circuit breaker closed."""
        async with self._state_lock:
            await self._transition_to_closed()
            
        self.logger.info(
            f"Circuit breaker '{self.name}' manually forced closed",
            circuit_name=self.name,
            event_type="circuit_breaker_forced_closed"
        )
    
    async def reset(self):
        """Reset the circuit breaker to initial state."""
        async with self._state_lock:
            self.state = CircuitState.CLOSED
            self.state_changed_at = time.time()
            self.failure_count = 0
            self.success_count = 0
            self.half_open_calls = 0
            self.metrics = CircuitBreakerMetrics()
            
            # Update Prometheus metrics
            self.prometheus_metrics.circuit_breaker_state.labels(component=self.name).state("closed")
        
        self.logger.info(
            f"Circuit breaker '{self.name}' reset",
            circuit_name=self.name,
            event_type="circuit_breaker_reset"
        )
    
    async def get_status(self) -> Dict[str, Any]:
        """Get current circuit breaker status."""
        now = time.time()
        state_duration = now - self.state_changed_at
        
        status = {
            "name": self.name,
            "state": self.state.value,
            "state_duration_seconds": state_duration,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "half_open_calls": self.half_open_calls,
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "recovery_timeout": self.config.recovery_timeout,
                "success_threshold": self.config.success_threshold,
                "timeout": self.config.timeout,
                "monitoring_window": self.config.monitoring_window,
            },
            "metrics": self.metrics.to_dict(),
        }
        
        if self.state == CircuitState.OPEN:
            status["retry_after_seconds"] = await self._get_retry_after()
        
        return status


class CircuitBreakerManager:
    """Manages multiple circuit breakers."""
    
    def __init__(self):
        self.config = get_config()
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.logger = get_structured_logger().get_logger("circuit_breaker_manager")
        
        # Create default circuit breakers
        self._create_default_breakers()
    
    def _create_default_breakers(self):
        """Create default circuit breakers for common services."""
        # Snowflake connection circuit breaker
        snowflake_config = CircuitBreakerConfig(
            failure_threshold=getattr(self.config.circuit_breaker, 'snowflake_failure_threshold', 5),
            recovery_timeout=getattr(self.config.circuit_breaker, 'snowflake_recovery_timeout', 60.0),
            success_threshold=getattr(self.config.circuit_breaker, 'snowflake_success_threshold', 3),
            timeout=getattr(self.config.circuit_breaker, 'snowflake_timeout', 30.0),
            monitoring_window=getattr(self.config.circuit_breaker, 'snowflake_monitoring_window', 60),
        )
        
        self.circuit_breakers['snowflake_connection'] = CircuitBreaker(
            'snowflake_connection', 
            snowflake_config
        )
        
        # Database query circuit breaker
        query_config = CircuitBreakerConfig(
            failure_threshold=getattr(self.config.circuit_breaker, 'query_failure_threshold', 10),
            recovery_timeout=getattr(self.config.circuit_breaker, 'query_recovery_timeout', 30.0),
            success_threshold=getattr(self.config.circuit_breaker, 'query_success_threshold', 5),
            timeout=getattr(self.config.circuit_breaker, 'query_timeout', 60.0),
            monitoring_window=getattr(self.config.circuit_breaker, 'query_monitoring_window', 120),
        )
        
        self.circuit_breakers['database_query'] = CircuitBreaker(
            'database_query', 
            query_config
        )
    
    def get_circuit_breaker(self, name: str) -> Optional[CircuitBreaker]:
        """Get a circuit breaker by name."""
        return self.circuit_breakers.get(name)
    
    def create_circuit_breaker(self, name: str, config: CircuitBreakerConfig) -> CircuitBreaker:
        """Create a new circuit breaker."""
        if name in self.circuit_breakers:
            raise ValueError(f"Circuit breaker '{name}' already exists")
        
        circuit_breaker = CircuitBreaker(name, config)
        self.circuit_breakers[name] = circuit_breaker
        
        self.logger.info(
            f"Created circuit breaker '{name}'",
            circuit_name=name,
            config=config.__dict__
        )
        
        return circuit_breaker
    
    def remove_circuit_breaker(self, name: str) -> bool:
        """Remove a circuit breaker."""
        if name in self.circuit_breakers:
            del self.circuit_breakers[name]
            self.logger.info(f"Removed circuit breaker '{name}'")
            return True
        return False
    
    async def get_all_status(self) -> Dict[str, Any]:
        """Get status of all circuit breakers."""
        statuses = {}
        for name, breaker in self.circuit_breakers.items():
            statuses[name] = await breaker.get_status()
        
        return {
            "circuit_breakers": statuses,
            "total_count": len(self.circuit_breakers),
            "timestamp": time.time(),
        }
    
    async def reset_all(self):
        """Reset all circuit breakers."""
        for breaker in self.circuit_breakers.values():
            await breaker.reset()
        
        self.logger.info("Reset all circuit breakers")


# Global circuit breaker manager
_circuit_breaker_manager: Optional[CircuitBreakerManager] = None


def get_circuit_breaker_manager() -> CircuitBreakerManager:
    """Get the global circuit breaker manager."""
    global _circuit_breaker_manager
    if _circuit_breaker_manager is None:
        _circuit_breaker_manager = CircuitBreakerManager()
    return _circuit_breaker_manager


def get_circuit_breaker(name: str) -> Optional[CircuitBreaker]:
    """Get a circuit breaker by name."""
    manager = get_circuit_breaker_manager()
    return manager.get_circuit_breaker(name)


def circuit_breaker(name: str, config: Optional[CircuitBreakerConfig] = None):
    """Decorator to apply circuit breaker to functions."""
    def decorator(func):
        from functools import wraps
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            manager = get_circuit_breaker_manager()
            breaker = manager.get_circuit_breaker(name)
            
            if breaker is None:
                # Create circuit breaker with default config if not exists
                breaker_config = config or CircuitBreakerConfig()
                breaker = manager.create_circuit_breaker(name, breaker_config)
            
            return await breaker.call(func, *args, **kwargs)
        
        return wrapper
    
    return decorator


# FastAPI endpoints for circuit breaker management
async def get_circuit_breaker_status_endpoint(name: Optional[str] = None) -> Dict[str, Any]:
    """API endpoint to get circuit breaker status."""
    manager = get_circuit_breaker_manager()
    
    if name:
        breaker = manager.get_circuit_breaker(name)
        if breaker:
            return await breaker.get_status()
        else:
            return {"error": f"Circuit breaker '{name}' not found"}
    else:
        return await manager.get_all_status()


async def force_circuit_breaker_state_endpoint(name: str, state: str) -> Dict[str, Any]:
    """API endpoint to force circuit breaker state."""
    manager = get_circuit_breaker_manager()
    breaker = manager.get_circuit_breaker(name)
    
    if not breaker:
        return {"success": False, "error": f"Circuit breaker '{name}' not found"}
    
    try:
        if state.lower() == "open":
            await breaker.force_open()
        elif state.lower() == "closed":
            await breaker.force_close()
        elif state.lower() == "reset":
            await breaker.reset()
        else:
            return {"success": False, "error": f"Invalid state '{state}'. Use 'open', 'closed', or 'reset'"}
        
        return {"success": True, "message": f"Circuit breaker '{name}' state changed to {state}"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    # Test circuit breaker
    import asyncio
    
    async def unreliable_function(fail_rate: float = 0.5):
        """Simulate an unreliable function."""
        import random
        await asyncio.sleep(0.1)  # Simulate work
        
        if random.random() < fail_rate:
            raise Exception("Simulated failure")
        
        return "Success!"
    
    async def test_circuit_breaker():
        manager = CircuitBreakerManager()
        
        # Create test circuit breaker
        config = CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=5.0,
            success_threshold=2,
            timeout=1.0
        )
        
        breaker = manager.create_circuit_breaker("test", config)
        
        # Test with high failure rate
        print("Testing with high failure rate...")
        for i in range(10):
            try:
                result = await breaker.call(unreliable_function, fail_rate=0.8)
                print(f"Call {i+1}: {result}")
            except CircuitBreakerOpenError as e:
                print(f"Call {i+1}: Circuit breaker open - {e}")
            except Exception as e:
                print(f"Call {i+1}: Failed - {e}")
            
            await asyncio.sleep(0.5)
        
        # Check status
        status = await breaker.get_status()
        print(f"\nCircuit breaker status: {status['state']}")
        print(f"Metrics: {status['metrics']}")
    
    asyncio.run(test_circuit_breaker())