"""Structured logging with correlation IDs for enhanced observability."""

import logging
import time
import uuid
from contextvars import ContextVar
from functools import wraps
from typing import Any, Dict, Optional

import structlog
from structlog.types import FilteringBoundLogger

from ..config import get_config

# Context variables for correlation IDs
correlation_id: ContextVar[str] = ContextVar('correlation_id', default='')
trace_id: ContextVar[str] = ContextVar('trace_id', default='')
span_id: ContextVar[str] = ContextVar('span_id', default='')
user_id: ContextVar[str] = ContextVar('user_id', default='')
session_id: ContextVar[str] = ContextVar('session_id', default='')


class CorrelationIDProcessor:
    """Processor to add correlation IDs to log records."""
    
    def __call__(self, logger: FilteringBoundLogger, method_name: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Add correlation context to log events."""
        # Add correlation IDs
        event_dict['correlation_id'] = correlation_id.get() or self._generate_correlation_id()
        event_dict['trace_id'] = trace_id.get()
        event_dict['span_id'] = span_id.get()
        event_dict['user_id'] = user_id.get()
        event_dict['session_id'] = session_id.get()
        
        # Add timestamp if not present
        if 'timestamp' not in event_dict:
            event_dict['timestamp'] = time.time()
        
        return event_dict
    
    def _generate_correlation_id(self) -> str:
        """Generate a new correlation ID."""
        new_id = str(uuid.uuid4())
        correlation_id.set(new_id)
        return new_id


class RequestContextProcessor:
    """Processor to add request context information."""
    
    def __call__(self, logger: FilteringBoundLogger, method_name: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Add request context to log events."""
        # Import here to avoid circular imports
        try:
            from ..utils.request_context import current_client_id, current_request_id
            
            request_id = current_request_id.get()
            client_id = current_client_id.get()
            
            if request_id:
                event_dict['request_id'] = request_id
            if client_id:
                event_dict['client_id'] = client_id
                
        except ImportError:
            pass  # Request context not available
        
        return event_dict


class PerformanceProcessor:
    """Processor to add performance metrics to log events."""
    
    def __call__(self, logger: FilteringBoundLogger, method_name: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Add performance metrics to log events."""
        # Add process info
        import os

        import psutil
        
        try:
            process = psutil.Process(os.getpid())
            event_dict['process'] = {
                'pid': os.getpid(),
                'memory_mb': round(process.memory_info().rss / 1024 / 1024, 2),
                'cpu_percent': process.cpu_percent(),
            }
        except Exception:
            pass  # Performance info not available
        
        return event_dict


class SensitiveDataFilter:
    """Filter sensitive data from log records."""
    
    SENSITIVE_KEYS = {
        'password', 'token', 'secret', 'key', 'credential', 'auth',
        'private_key', 'passphrase', 'api_key', 'access_token'
    }
    
    def __call__(self, logger: FilteringBoundLogger, method_name: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Filter sensitive data from log events."""
        return self._filter_dict(event_dict)
    
    def _filter_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively filter sensitive data from dictionaries."""
        filtered = {}
        
        for key, value in data.items():
            if isinstance(key, str) and any(sensitive in key.lower() for sensitive in self.SENSITIVE_KEYS):
                filtered[key] = '[REDACTED]'
            elif isinstance(value, dict):
                filtered[key] = self._filter_dict(value)
            elif isinstance(value, list):
                filtered[key] = [self._filter_dict(item) if isinstance(item, dict) else item for item in value]
            else:
                filtered[key] = value
        
        return filtered


class StructuredLogger:
    """Enhanced structured logger with correlation support."""
    
    def __init__(self):
        self.config = get_config()
        self._configure_structlog()
    
    def _configure_structlog(self):
        """Configure structlog with processors and formatters."""
        processors = [
            CorrelationIDProcessor(),
            RequestContextProcessor(),
            SensitiveDataFilter(),
            structlog.processors.TimeStamper(fmt="ISO"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
        ]
        
        # Add performance processor if enabled
        if self.config.development.enable_profiling:
            processors.append(PerformanceProcessor())
        
        # Configure output format
        if self.config.logging.format == "json":
            processors.append(structlog.processors.JSONRenderer())
        else:
            processors.extend([
                structlog.dev.ConsoleRenderer(colors=True),
            ])
        
        # Configure structlog
        structlog.configure(
            processors=processors,
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, self.config.logging.level)
            ),
            logger_factory=structlog.WriteLoggerFactory(),
            cache_logger_on_first_use=True,
        )
    
    def get_logger(self, name: str = None) -> FilteringBoundLogger:
        """Get a configured structured logger."""
        return structlog.get_logger(name)


class LoggingContext:
    """Context manager for setting correlation IDs and context."""
    
    def __init__(self, **context_data):
        self.context_data = context_data
        self.tokens = {}
    
    def __enter__(self):
        """Set context variables."""
        for key, value in self.context_data.items():
            if key == 'correlation_id':
                self.tokens['correlation_id'] = correlation_id.set(str(value))
            elif key == 'trace_id':
                self.tokens['trace_id'] = trace_id.set(str(value))
            elif key == 'span_id':
                self.tokens['span_id'] = span_id.set(str(value))
            elif key == 'user_id':
                self.tokens['user_id'] = user_id.set(str(value))
            elif key == 'session_id':
                self.tokens['session_id'] = session_id.set(str(value))
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Reset context variables."""
        for var_name, token in self.tokens.items():
            if var_name == 'correlation_id':
                correlation_id.reset(token)
            elif var_name == 'trace_id':
                trace_id.reset(token)
            elif var_name == 'span_id':
                span_id.reset(token)
            elif var_name == 'user_id':
                user_id.reset(token)
            elif var_name == 'session_id':
                session_id.reset(token)


def with_correlation_id(func):
    """Decorator to automatically generate correlation IDs for functions."""
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        cid = str(uuid.uuid4())
        with LoggingContext(correlation_id=cid):
            return await func(*args, **kwargs)
    
    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        cid = str(uuid.uuid4())
        with LoggingContext(correlation_id=cid):
            return func(*args, **kwargs)
    
    # Return appropriate wrapper based on function type
    import asyncio
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper


def with_trace_context(trace_id_val: str, span_id_val: str = None):
    """Decorator to set trace context for functions."""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            context = {'trace_id': trace_id_val}
            if span_id_val:
                context['span_id'] = span_id_val
            else:
                context['span_id'] = str(uuid.uuid4())
            
            with LoggingContext(**context):
                return await func(*args, **kwargs)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            context = {'trace_id': trace_id_val}
            if span_id_val:
                context['span_id'] = span_id_val
            else:
                context['span_id'] = str(uuid.uuid4())
            
            with LoggingContext(**context):
                return func(*args, **kwargs)
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


class AuditLogger:
    """Specialized logger for audit events."""
    
    def __init__(self):
        self.logger = structlog.get_logger("audit")
    
    def log_authentication(self, user_id: str, client_id: str, success: bool, 
                          method: str, ip_address: str = None):
        """Log authentication events."""
        self.logger.info(
            "authentication_attempt",
            user_id=user_id,
            client_id=client_id,
            success=success,
            method=method,
            ip_address=ip_address,
            event_type="authentication"
        )
    
    def log_authorization(self, user_id: str, resource: str, action: str, 
                         granted: bool, reason: str = None):
        """Log authorization events."""
        self.logger.info(
            "authorization_check",
            user_id=user_id,
            resource=resource,
            action=action,
            granted=granted,
            reason=reason,
            event_type="authorization"
        )
    
    def log_data_access(self, user_id: str, database: str, table: str = None,
                       query: str = None, rows_affected: int = 0):
        """Log data access events."""
        self.logger.info(
            "data_access",
            user_id=user_id,
            database=database,
            table=table,
            query=query,
            rows_affected=rows_affected,
            event_type="data_access"
        )
    
    def log_error(self, error_type: str, error_message: str, component: str,
                 user_id: str = None, additional_context: Dict[str, Any] = None):
        """Log error events."""
        log_data = {
            "error_event": error_message,
            "error_type": error_type,
            "component": component,
            "event_type": "error"
        }
        
        if user_id:
            log_data["user_id"] = user_id
        
        if additional_context:
            log_data.update(additional_context)
        
        self.logger.error(**log_data)


class PerformanceLogger:
    """Specialized logger for performance tracking."""
    
    def __init__(self):
        self.logger = structlog.get_logger("performance")
    
    def log_request_performance(self, endpoint: str, method: str, duration: float,
                              status_code: int, client_id: str = None):
        """Log request performance metrics."""
        self.logger.info(
            "request_performance",
            endpoint=endpoint,
            method=method,
            duration_ms=round(duration * 1000, 2),
            status_code=status_code,
            client_id=client_id,
            event_type="performance"
        )
    
    def log_database_performance(self, query_type: str, database: str, 
                               duration: float, rows_processed: int = 0):
        """Log database performance metrics."""
        self.logger.info(
            "database_performance",
            query_type=query_type,
            database=database,
            duration_ms=round(duration * 1000, 2),
            rows_processed=rows_processed,
            event_type="performance"
        )
    
    def log_resource_usage(self, component: str, metric_name: str, 
                          value: float, unit: str = None):
        """Log resource usage metrics."""
        log_data = {
            "resource_usage": metric_name,
            "component": component,
            "value": value,
            "event_type": "resource"
        }
        
        if unit:
            log_data["unit"] = unit
        
        self.logger.info(**log_data)


# Global instances
_structured_logger: Optional[StructuredLogger] = None
_audit_logger: Optional[AuditLogger] = None
_performance_logger: Optional[PerformanceLogger] = None


def get_structured_logger() -> StructuredLogger:
    """Get the global structured logger instance."""
    global _structured_logger
    if _structured_logger is None:
        _structured_logger = StructuredLogger()
    return _structured_logger


def get_audit_logger() -> AuditLogger:
    """Get the global audit logger instance."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def get_performance_logger() -> PerformanceLogger:
    """Get the global performance logger instance."""
    global _performance_logger
    if _performance_logger is None:
        _performance_logger = PerformanceLogger()
    return _performance_logger


def setup_structured_logging():
    """Initialize structured logging for the application."""
    structured_logger = get_structured_logger()
    
    # Configure standard library logging to use structlog
    logging.basicConfig(
        format="%(message)s",
        stream=__import__('sys').stdout,
        level=getattr(logging, structured_logger.config.logging.level),
    )
    
    # Get a logger instance to test configuration
    logger = structlog.get_logger("snowflake_mcp")
    logger.info("Structured logging initialized", component="logging")


if __name__ == "__main__":
    # Test structured logging
    setup_structured_logging()
    
    logger = structlog.get_logger("test")
    audit = get_audit_logger()
    perf = get_performance_logger()
    
    # Test correlation context
    with LoggingContext(correlation_id="test-123", user_id="test_user"):
        logger.info("Test message with correlation", test_data={"key": "value"})
        
        audit.log_authentication("test_user", "test_client", True, "api_key")
        perf.log_request_performance("/api/test", "POST", 0.152, 200)
    
    # Test with decorator
    @with_correlation_id
    def test_function():
        logger.info("Function with auto correlation ID")
    
    test_function()
    
    print("Structured logging test completed")