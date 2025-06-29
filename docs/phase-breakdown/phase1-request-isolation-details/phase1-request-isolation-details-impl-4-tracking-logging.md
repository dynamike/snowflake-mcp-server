# Phase 1: Request Isolation Implementation Details

## Context & Overview

The current Snowflake MCP server shares connection state across all MCP tool calls, creating potential race conditions and data consistency issues when multiple clients or concurrent requests modify database/schema context or transaction state.

**Current Issues:**
- Global connection state shared between all tool calls
- `USE DATABASE` and `USE SCHEMA` commands affect all subsequent operations
- No request boundaries or isolation between MCP tool calls
- Transaction state shared across concurrent operations
- Session parameters can be modified by one request affecting others

**Target Architecture:**
- Per-request connection isolation from connection pool
- Request context tracking with unique IDs
- Isolated database/schema context per tool call
- Transaction boundary management per operation
- Request-level logging and error tracking

## Current State Analysis

### Problematic State Sharing in `main.py`

Lines 145-148 in `handle_list_views`:
```python
# GLOBAL STATE CHANGE: Affects all future requests
if database:
    conn.cursor().execute(f"USE DATABASE {database}")
if schema:
    conn.cursor().execute(f"USE SCHEMA {schema}")
```

Lines 433-436 in `handle_execute_query`:
```python
# GLOBAL STATE CHANGE: Persists beyond current request
if database:
    conn.cursor().execute(f"USE DATABASE {database}")
if schema:
    conn.cursor().execute(f"USE SCHEMA {schema}")
```

## Implementation Plan

### 4. Request ID Tracking and Logging {#tracking-logging}

**Enhanced Logging with Request Context**

Create `snowflake_mcp_server/utils/contextual_logging.py`:

```python
"""Contextual logging with request tracking."""

import logging
import sys
from typing import Any, Dict, Optional
from .request_context import current_request_id, current_client_id


class RequestContextFilter(logging.Filter):
    """Logging filter to add request context to log records."""
    
    def filter(self, record: logging.LogRecord) -> bool:
        # Add request context to log record
        record.request_id = current_request_id.get() or "no-request"
        record.client_id = current_client_id.get() or "unknown-client"
        return True


class RequestContextFormatter(logging.Formatter):
    """Formatter that includes request context in log messages."""
    
    def __init__(self):
        super().__init__(
            fmt='%(asctime)s - %(name)s - %(levelname)s - '
                '[req:%(request_id)s|client:%(client_id)s] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )


def setup_contextual_logging():
    """Set up logging with request context."""
    # Get root logger
    root_logger = logging.getLogger()
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create console handler with context formatter
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(RequestContextFormatter())
    console_handler.addFilter(RequestContextFilter())
    
    # Add handler to root logger
    root_logger.addHandler(console_handler)
    root_logger.setLevel(logging.INFO)
    
    # Create request-specific logger
    request_logger = logging.getLogger("snowflake_mcp.requests")
    request_logger.setLevel(logging.DEBUG)
    
    return request_logger


# Request-specific logging functions
def log_request_start(request_id: str, tool_name: str, client_id: str, arguments: Dict[str, Any]):
    """Log request start with context."""
    logger = logging.getLogger("snowflake_mcp.requests")
    logger.info(f"Starting tool call: {tool_name} with args: {arguments}")


def log_request_complete(request_id: str, duration_ms: float, queries_executed: int):
    """Log request completion with metrics."""
    logger = logging.getLogger("snowflake_mcp.requests")
    logger.info(f"Request completed in {duration_ms:.2f}ms, executed {queries_executed} queries")


def log_request_error(request_id: str, error: Exception, context: str):
    """Log request error with context."""
    logger = logging.getLogger("snowflake_mcp.requests")
    logger.error(f"Request error in {context}: {error}")


# Update main.py to use contextual logging
def setup_server_logging():
    """Initialize server with contextual logging."""
    setup_contextual_logging()
    
    # Log server startup
    logger = logging.getLogger("snowflake_mcp.server")
    logger.info("Snowflake MCP Server starting with request isolation")
```

