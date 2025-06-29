# Phase 3: Monitoring & Observability Implementation Details

## Context & Overview

The current Snowflake MCP server lacks comprehensive monitoring capabilities, making it difficult to diagnose performance issues, track usage patterns, or identify potential problems before they impact users. Production deployments require robust observability to ensure reliability and performance.

**Current Limitations:**
- No metrics collection or monitoring endpoints
- Basic logging without structured format or correlation IDs
- No performance tracking or alerting capabilities
- No visibility into connection pool health or query performance
- Missing operational dashboards and alerting

**Target Architecture:**
- Prometheus metrics collection with custom metrics
- Structured logging with correlation IDs and request tracing
- Performance monitoring dashboards with Grafana
- Automated alerting for critical issues
- Query performance tracking and analysis

## Dependencies Required

Add to `pyproject.toml`:
```toml
dependencies = [
    # Existing dependencies...
    "prometheus-client>=0.18.0",  # Metrics collection
    "structlog>=23.2.0",         # Structured logging
    "opentelemetry-api>=1.20.0", # Tracing support
    "opentelemetry-sdk>=1.20.0", # Tracing implementation
    "opentelemetry-instrumentation-asyncio>=0.41b0",  # Async tracing
]

[project.optional-dependencies]
monitoring = [
    "grafana-client>=3.6.0",     # Dashboard management
    "alertmanager-client>=0.1.0", # Alert management  
    "pystatsd>=0.4.0",           # StatsD metrics
]
```

## Implementation Plan

### 2. Structured Logging Implementation {#structured-logging}

**Step 1: Structured Logging Framework**

Create `snowflake_mcp_server/monitoring/logging_config.py`:

```python
"""Advanced structured logging with correlation IDs."""

import structlog
import logging
import sys
from typing import Any, Dict, Optional
from datetime import datetime
import json
import traceback

from ..utils.request_context import current_request_id, current_client_id


def add_request_context(logger, method_name, event_dict):
    """Add request context to log entries."""
    request_id = current_request_id.get()
    client_id = current_client_id.get()
    
    if request_id:
        event_dict['request_id'] = request_id
    if client_id:
        event_dict['client_id'] = client_id
    
    return event_dict


def add_timestamp(logger, method_name, event_dict):
    """Add timestamp to log entries."""
    event_dict['timestamp'] = datetime.now().isoformat()
    return event_dict


def add_severity_level(logger, method_name, event_dict):
    """Add severity level for better log analysis."""
    level = event_dict.get('level', 'info').upper()
    
    # Map to numeric severity for filtering
    severity_map = {
        'DEBUG': 10,
        'INFO': 20,
        'WARNING': 30,
        'ERROR': 40,
        'CRITICAL': 50
    }
    
    event_dict['severity'] = severity_map.get(level, 20)
    return event_dict


def configure_structured_logging(
    level: str = "INFO",
    json_format: bool = True,
    include_tracing: bool = True
) -> None:
    """Configure structured logging for the application."""
    
    # Configure structlog
    processors = [
        structlog.stdlib.filter_by_level,
        add_timestamp,
        add_request_context,
        add_severity_level,
        structlog.processors.add_logger_name,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
    ]
    
    if json_format:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.extend([
            structlog.dev.ConsoleRenderer(colors=True),
        ])
    
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        context_class=dict,
        cache_logger_on_first_use=True,
    )
    
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper()),
    )


class StructuredLogger:
    """Structured logger with enhanced capabilities."""
    
    def __init__(self, name: str):
        self.logger = structlog.get_logger(name)
    
    def info(self, message: str, **kwargs) -> None:
        """Log info message with structured context."""
        self.logger.info(message, **kwargs)
    
    def warning(self, message: str, **kwargs) -> None:
        """Log warning message with structured context."""
        self.logger.warning(message, **kwargs)
    
    def error(self, message: str, error: Optional[Exception] = None, **kwargs) -> None:
        """Log error message with exception details."""
        if error:
            kwargs.update({
                'error_type': type(error).__name__,
                'error_message': str(error),
                'traceback': traceback.format_exc() if error else None
            })
        
        self.logger.error(message, **kwargs)
    
    def debug(self, message: str, **kwargs) -> None:
        """Log debug message with structured context."""
        self.logger.debug(message, **kwargs)
    
    def log_request_start(self, tool_name: str, arguments: Dict[str, Any]) -> None:
        """Log request start with context."""
        self.info(
            "Request started",
            tool_name=tool_name,
            arguments_preview=str(arguments)[:200] if arguments else None,
            event_type="request_start"
        )
    
    def log_request_complete(self, tool_name: str, duration_ms: float, success: bool) -> None:
        """Log request completion."""
        self.info(
            "Request completed",
            tool_name=tool_name,
            duration_ms=duration_ms,
            success=success,
            event_type="request_complete"
        )
    
    def log_database_operation(
        self, 
        operation_type: str, 
        query_preview: str, 
        duration_ms: float,
        rows_affected: int = 0
    ) -> None:
        """Log database operation with details."""
        self.info(
            "Database operation",
            operation_type=operation_type,
            query_preview=query_preview,
            duration_ms=duration_ms,
            rows_affected=rows_affected,
            event_type="database_operation"
        )
    
    def log_connection_pool_event(self, event: str, pool_stats: Dict[str, Any]) -> None:
        """Log connection pool events."""
        self.info(
            "Connection pool event",
            pool_event=event,
            **pool_stats,
            event_type="connection_pool"
        )
    
    def log_client_session_event(self, event: str, session_info: Dict[str, Any]) -> None:
        """Log client session events."""
        self.info(
            "Client session event",
            session_event=event,
            **session_info,
            event_type="client_session"
        )
    
    def log_performance_metric(self, metric_name: str, value: float, tags: Dict[str, str] = None) -> None:
        """Log performance metrics."""
        self.info(
            "Performance metric",
            metric_name=metric_name,
            metric_value=value,
            tags=tags or {},
            event_type="performance_metric"
        )


# Create logger instances for different components
request_logger = StructuredLogger("mcp.requests")
database_logger = StructuredLogger("mcp.database")
connection_logger = StructuredLogger("mcp.connections")
session_logger = StructuredLogger("mcp.sessions")
performance_logger = StructuredLogger("mcp.performance")
```

