"""Backoff strategies for retry logic and rate limiting."""

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterator, Optional

from ..monitoring import get_structured_logger

logger = logging.getLogger(__name__)


class BackoffStrategy(Enum):
    """Available backoff strategies."""
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    FIBONACCI = "fibonacci"
    POLYNOMIAL = "polynomial"
    CUSTOM = "custom"


@dataclass
class BackoffConfig:
    """Configuration for backoff strategies."""
    
    strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    initial_delay: float = 1.0  # Initial delay in seconds
    max_delay: float = 300.0  # Maximum delay in seconds
    max_attempts: int = 10  # Maximum retry attempts
    
    # Exponential backoff parameters
    exponential_base: float = 2.0
    exponential_cap: float = 300.0
    
    # Linear backoff parameters
    linear_increment: float = 1.0
    
    # Polynomial backoff parameters
    polynomial_degree: int = 2
    
    # Fibonacci backoff parameters
    fibonacci_multiplier: float = 1.0
    
    # Jitter configuration
    jitter: bool = True
    jitter_type: str = "full"  # "full", "equal", "decorrelated"
    jitter_max_ratio: float = 0.1  # Maximum jitter as ratio of delay
    
    # Custom backoff function
    custom_function: Optional[Callable[[int, float], float]] = None
    
    # Timeout configuration
    total_timeout: Optional[float] = None  # Total time limit for all retries


class BackoffError(Exception):
    """Raised when backoff limits are exceeded."""
    
    def __init__(self, message: str, attempts: int, total_time: float):
        super().__init__(message)
        self.attempts = attempts
        self.total_time = total_time


class Backoff:
    """Implements various backoff strategies."""
    
    def __init__(self, config: BackoffConfig):
        self.config = config
        self.logger = get_structured_logger().get_logger("backoff")
        self._attempt_count = 0
        self._start_time = time.time()
    
    def __iter__(self) -> Iterator[float]:
        """Make this class iterable for easy use in retry loops."""
        self._attempt_count = 0
        self._start_time = time.time()
        return self
    
    def __next__(self) -> float:
        """Get the next delay value."""
        if self._attempt_count >= self.config.max_attempts:
            raise StopIteration("Maximum attempts reached")
        
        if (self.config.total_timeout and 
            time.time() - self._start_time >= self.config.total_timeout):
            raise StopIteration("Total timeout reached")
        
        delay = self._calculate_delay(self._attempt_count)
        self._attempt_count += 1
        
        return delay
    
    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay for the given attempt number."""
        if self.config.strategy == BackoffStrategy.FIXED:
            delay = self.config.initial_delay
        elif self.config.strategy == BackoffStrategy.LINEAR:
            delay = self.config.initial_delay + (attempt * self.config.linear_increment)
        elif self.config.strategy == BackoffStrategy.EXPONENTIAL:
            delay = self.config.initial_delay * (self.config.exponential_base ** attempt)
            delay = min(delay, self.config.exponential_cap)
        elif self.config.strategy == BackoffStrategy.FIBONACCI:
            delay = self._fibonacci_delay(attempt)
        elif self.config.strategy == BackoffStrategy.POLYNOMIAL:
            delay = self.config.initial_delay * (attempt ** self.config.polynomial_degree)
        elif self.config.strategy == BackoffStrategy.CUSTOM:
            if self.config.custom_function:
                delay = self.config.custom_function(attempt, self.config.initial_delay)
            else:
                delay = self.config.initial_delay
        else:
            delay = self.config.initial_delay
        
        # Apply maximum delay limit
        delay = min(delay, self.config.max_delay)
        
        # Apply jitter if enabled
        if self.config.jitter:
            delay = self._apply_jitter(delay, attempt)
        
        return max(0, delay)
    
    def _fibonacci_delay(self, attempt: int) -> float:
        """Calculate Fibonacci-based delay."""
        def fibonacci(n):
            if n <= 1:
                return n
            a, b = 0, 1
            for _ in range(2, n + 1):
                a, b = b, a + b
            return b
        
        fib_value = fibonacci(attempt + 1)
        return self.config.initial_delay * self.config.fibonacci_multiplier * fib_value
    
    def _apply_jitter(self, delay: float, attempt: int) -> float:
        """Apply jitter to the delay."""
        if not self.config.jitter:
            return delay
        
        if self.config.jitter_type == "full":
            # Full jitter: random value between 0 and delay
            return random.uniform(0, delay)
        elif self.config.jitter_type == "equal":
            # Equal jitter: delay/2 + random(0, delay/2)
            return delay / 2 + random.uniform(0, delay / 2)
        elif self.config.jitter_type == "decorrelated":
            # Decorrelated jitter: more complex randomization
            if attempt == 0:
                return delay
            prev_delay = self._calculate_delay(attempt - 1)
            return random.uniform(self.config.initial_delay, delay * 3)
        else:
            # Limited jitter: delay * (1 ± jitter_max_ratio)
            jitter_amount = delay * self.config.jitter_max_ratio
            return delay + random.uniform(-jitter_amount, jitter_amount)
    
    async def wait(self) -> None:
        """Wait for the next delay period."""
        try:
            delay = next(self)
            self.logger.debug(
                f"Backing off for {delay:.2f} seconds",
                attempt=self._attempt_count,
                delay_seconds=delay,
                strategy=self.config.strategy.value
            )
            await asyncio.sleep(delay)
        except StopIteration as e:
            raise BackoffError(
                str(e),
                attempts=self._attempt_count,
                total_time=time.time() - self._start_time
            )
    
    def get_next_delay(self) -> Optional[float]:
        """Get the next delay without incrementing the counter."""
        if self._attempt_count >= self.config.max_attempts:
            return None
        
        if (self.config.total_timeout and 
            time.time() - self._start_time >= self.config.total_timeout):
            return None
        
        return self._calculate_delay(self._attempt_count)
    
    def reset(self):
        """Reset the backoff state."""
        self._attempt_count = 0
        self._start_time = time.time()
    
    def get_stats(self) -> dict:
        """Get backoff statistics."""
        return {
            "attempt_count": self._attempt_count,
            "elapsed_time": time.time() - self._start_time,
            "strategy": self.config.strategy.value,
            "max_attempts": self.config.max_attempts,
            "remaining_attempts": max(0, self.config.max_attempts - self._attempt_count),
        }


class RetryWithBackoff:
    """Retry decorator with configurable backoff strategies."""
    
    def __init__(self, 
                 config: BackoffConfig,
                 retry_on: tuple = (Exception,),
                 stop_on: tuple = (),
                 before_retry: Optional[Callable] = None,
                 after_retry: Optional[Callable] = None):
        self.config = config
        self.retry_on = retry_on
        self.stop_on = stop_on
        self.before_retry = before_retry
        self.after_retry = after_retry
        self.logger = get_structured_logger().get_logger("retry_backoff")
    
    def __call__(self, func: Callable) -> Callable:
        """Decorator implementation."""
        import asyncio
        from functools import wraps
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            backoff = Backoff(self.config)
            last_exception = None
            
            for attempt in range(self.config.max_attempts):
                try:
                    if attempt > 0:
                        # Wait for backoff delay
                        await backoff.wait()
                    
                    # Call before_retry hook
                    if self.before_retry and attempt > 0:
                        await self._call_hook(self.before_retry, attempt, last_exception)
                    
                    # Execute the function
                    if asyncio.iscoroutinefunction(func):
                        result = await func(*args, **kwargs)
                    else:
                        result = func(*args, **kwargs)
                    
                    # Success - call after_retry hook if we retried
                    if self.after_retry and attempt > 0:
                        await self._call_hook(self.after_retry, attempt, None)
                    
                    # Log successful retry
                    if attempt > 0:
                        self.logger.info(
                            f"Function {func.__name__} succeeded after {attempt} retries",
                            function=func.__name__,
                            attempts=attempt + 1,
                            total_time=time.time() - backoff._start_time
                        )
                    
                    return result
                
                except Exception as e:
                    last_exception = e
                    
                    # Check if we should stop retrying on this exception
                    if isinstance(e, self.stop_on):
                        self.logger.info(
                            f"Stopping retries for {func.__name__} due to stop condition",
                            function=func.__name__,
                            exception=str(e),
                            attempts=attempt + 1
                        )
                        raise
                    
                    # Check if we should retry on this exception
                    if not isinstance(e, self.retry_on):
                        self.logger.info(
                            f"Not retrying {func.__name__} for exception type {type(e).__name__}",
                            function=func.__name__,
                            exception=str(e),
                            attempts=attempt + 1
                        )
                        raise
                    
                    # Log retry attempt
                    next_delay = backoff.get_next_delay()
                    if next_delay is not None and attempt < self.config.max_attempts - 1:
                        self.logger.warning(
                            f"Function {func.__name__} failed, retrying in {next_delay:.2f}s",
                            function=func.__name__,
                            exception=str(e),
                            attempt=attempt + 1,
                            next_delay=next_delay
                        )
                    else:
                        self.logger.error(
                            f"Function {func.__name__} failed, no more retries",
                            function=func.__name__,
                            exception=str(e),
                            total_attempts=attempt + 1
                        )
            
            # All retries exhausted
            raise BackoffError(
                f"Function {func.__name__} failed after {self.config.max_attempts} attempts",
                attempts=self.config.max_attempts,
                total_time=time.time() - backoff._start_time
            ) from last_exception
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # Similar implementation for synchronous functions
            backoff = Backoff(self.config)
            last_exception = None
            
            for attempt in range(self.config.max_attempts):
                try:
                    if attempt > 0:
                        # Synchronous sleep
                        delay = next(backoff)
                        time.sleep(delay)
                    
                    return func(*args, **kwargs)
                
                except Exception as e:
                    last_exception = e
                    
                    if isinstance(e, self.stop_on) or not isinstance(e, self.retry_on):
                        raise
                    
                    if attempt == self.config.max_attempts - 1:
                        break
            
            raise BackoffError(
                f"Function {func.__name__} failed after {self.config.max_attempts} attempts",
                attempts=self.config.max_attempts,
                total_time=time.time() - backoff._start_time
            ) from last_exception
        
        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    async def _call_hook(self, hook: Callable, attempt: int, exception: Optional[Exception]):
        """Call a hook function safely."""
        try:
            if asyncio.iscoroutinefunction(hook):
                await hook(attempt, exception)
            else:
                hook(attempt, exception)
        except Exception as e:
            self.logger.warning(f"Hook function failed: {e}")


class AdaptiveBackoff:
    """Adaptive backoff that adjusts based on success/failure patterns."""
    
    def __init__(self, base_config: BackoffConfig):
        self.base_config = base_config
        self.current_config = base_config
        self.success_count = 0
        self.failure_count = 0
        self.recent_outcomes = []  # Track recent success/failure pattern
        self.max_history = 100
        self.logger = get_structured_logger().get_logger("adaptive_backoff")
    
    def record_outcome(self, success: bool):
        """Record the outcome of an operation."""
        self.recent_outcomes.append(success)
        if len(self.recent_outcomes) > self.max_history:
            self.recent_outcomes.pop(0)
        
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        
        self._adapt_config()
    
    def _adapt_config(self):
        """Adapt the backoff configuration based on recent outcomes."""
        if len(self.recent_outcomes) < 10:
            return  # Need more data
        
        recent_success_rate = sum(self.recent_outcomes[-20:]) / min(20, len(self.recent_outcomes))
        
        # Adjust backoff aggressiveness based on success rate
        if recent_success_rate > 0.8:
            # High success rate - reduce backoff
            multiplier = 0.8
        elif recent_success_rate > 0.5:
            # Moderate success rate - keep current backoff
            multiplier = 1.0
        else:
            # Low success rate - increase backoff
            multiplier = 1.5
        
        # Create adapted config
        self.current_config = BackoffConfig(
            strategy=self.base_config.strategy,
            initial_delay=self.base_config.initial_delay * multiplier,
            max_delay=self.base_config.max_delay,
            max_attempts=self.base_config.max_attempts,
            exponential_base=self.base_config.exponential_base,
            exponential_cap=self.base_config.exponential_cap * multiplier,
            linear_increment=self.base_config.linear_increment * multiplier,
            jitter=self.base_config.jitter,
            jitter_type=self.base_config.jitter_type,
            total_timeout=self.base_config.total_timeout,
        )
        
        self.logger.debug(
            "Adapted backoff config",
            success_rate=recent_success_rate,
            multiplier=multiplier,
            new_initial_delay=self.current_config.initial_delay
        )
    
    def get_backoff(self) -> Backoff:
        """Get a Backoff instance with current adaptive configuration."""
        return Backoff(self.current_config)
    
    def get_stats(self) -> dict:
        """Get adaptive backoff statistics."""
        recent_success_rate = 0
        if self.recent_outcomes:
            recent_success_rate = sum(self.recent_outcomes) / len(self.recent_outcomes)
        
        return {
            "total_operations": self.success_count + self.failure_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "overall_success_rate": self.success_count / max(1, self.success_count + self.failure_count),
            "recent_success_rate": recent_success_rate,
            "current_initial_delay": self.current_config.initial_delay,
            "base_initial_delay": self.base_config.initial_delay,
            "adaptation_ratio": self.current_config.initial_delay / self.base_config.initial_delay,
        }


# Predefined backoff configurations
def get_default_configs() -> dict:
    """Get predefined backoff configurations for common use cases."""
    return {
        "connection_retry": BackoffConfig(
            strategy=BackoffStrategy.EXPONENTIAL,
            initial_delay=1.0,
            max_delay=60.0,
            max_attempts=5,
            exponential_base=2.0,
            jitter=True,
            jitter_type="full"
        ),
        
        "query_retry": BackoffConfig(
            strategy=BackoffStrategy.EXPONENTIAL,
            initial_delay=0.5,
            max_delay=30.0,
            max_attempts=3,
            exponential_base=2.0,
            jitter=True,
            jitter_type="equal"
        ),
        
        "rate_limit_backoff": BackoffConfig(
            strategy=BackoffStrategy.LINEAR,
            initial_delay=1.0,
            max_delay=300.0,
            max_attempts=10,
            linear_increment=2.0,
            jitter=True,
            jitter_type="full"
        ),
        
        "circuit_breaker_recovery": BackoffConfig(
            strategy=BackoffStrategy.FIBONACCI,
            initial_delay=5.0,
            max_delay=300.0,
            max_attempts=8,
            fibonacci_multiplier=1.0,
            jitter=True,
            jitter_type="decorrelated"
        ),
        
        "aggressive_retry": BackoffConfig(
            strategy=BackoffStrategy.EXPONENTIAL,
            initial_delay=0.1,
            max_delay=10.0,
            max_attempts=10,
            exponential_base=1.5,
            jitter=True
        ),
        
        "conservative_retry": BackoffConfig(
            strategy=BackoffStrategy.LINEAR,
            initial_delay=5.0,
            max_delay=120.0,
            max_attempts=5,
            linear_increment=10.0,
            jitter=False
        )
    }


# Convenience functions
def exponential_backoff(initial_delay: float = 1.0, max_delay: float = 60.0, 
                       max_attempts: int = 5, base: float = 2.0) -> BackoffConfig:
    """Create exponential backoff configuration."""
    return BackoffConfig(
        strategy=BackoffStrategy.EXPONENTIAL,
        initial_delay=initial_delay,
        max_delay=max_delay,
        max_attempts=max_attempts,
        exponential_base=base,
        jitter=True
    )


def linear_backoff(initial_delay: float = 1.0, max_delay: float = 60.0,
                  max_attempts: int = 5, increment: float = 1.0) -> BackoffConfig:
    """Create linear backoff configuration."""
    return BackoffConfig(
        strategy=BackoffStrategy.LINEAR,
        initial_delay=initial_delay,
        max_delay=max_delay,
        max_attempts=max_attempts,
        linear_increment=increment,
        jitter=True
    )


def fixed_backoff(delay: float = 1.0, max_attempts: int = 5) -> BackoffConfig:
    """Create fixed backoff configuration."""
    return BackoffConfig(
        strategy=BackoffStrategy.FIXED,
        initial_delay=delay,
        max_delay=delay,
        max_attempts=max_attempts,
        jitter=False
    )


# Decorators for common retry patterns
def retry_on_connection_error(max_attempts: int = 5, initial_delay: float = 1.0):
    """Decorator for retrying on connection errors."""
    config = exponential_backoff(initial_delay, max_attempts=max_attempts)
    return RetryWithBackoff(
        config=config,
        retry_on=(ConnectionError, TimeoutError, OSError),
        stop_on=(KeyboardInterrupt, SystemExit)
    )


def retry_on_rate_limit(max_attempts: int = 10, initial_delay: float = 1.0):
    """Decorator for retrying on rate limit errors."""
    from .rate_limiter import RateLimitError
    config = linear_backoff(initial_delay, max_attempts=max_attempts, increment=2.0)
    return RetryWithBackoff(
        config=config,
        retry_on=(RateLimitError,),
        stop_on=(KeyboardInterrupt, SystemExit)
    )


if __name__ == "__main__":
    # Test backoff strategies
    import asyncio
    
    async def test_backoff():
        # Test exponential backoff
        config = exponential_backoff(initial_delay=0.1, max_attempts=5)
        backoff = Backoff(config)
        
        print("Exponential backoff delays:")
        try:
            for delay in backoff:
                print(f"  Delay: {delay:.2f}s")
                await asyncio.sleep(delay)
        except StopIteration:
            print("  Max attempts reached")
        
        # Test adaptive backoff
        adaptive = AdaptiveBackoff(config)
        
        # Simulate some operations
        for i in range(20):
            success = random.random() > 0.3  # 70% success rate
            adaptive.record_outcome(success)
        
        print(f"\nAdaptive backoff stats: {adaptive.get_stats()}")
        
        # Test retry decorator
        @RetryWithBackoff(
            config=exponential_backoff(0.1, max_attempts=3),
            retry_on=(ValueError,)
        )
        async def unreliable_function():
            if random.random() < 0.7:
                raise ValueError("Random failure")
            return "Success!"
        
        try:
            result = await unreliable_function()
            print(f"\nRetry decorator result: {result}")
        except BackoffError as e:
            print(f"\nRetry failed: {e}")
    
    asyncio.run(test_backoff())