"""Contextual logging with request tracking."""

import logging
import sys
from typing import Any, Dict

from .request_context import current_client_id, current_request_id


class RequestContextFilter(logging.Filter):
    """Logging filter to add request context to log records."""
    
    def filter(self, record: logging.LogRecord) -> bool:
        # Add request context to log record
        record.request_id = current_request_id.get() or "no-request"  # type: ignore
        record.client_id = current_client_id.get() or "unknown-client"  # type: ignore
        return True


class RequestContextFormatter(logging.Formatter):
    """Formatter that includes request context in log messages."""
    
    def __init__(self):
        super().__init__(
            fmt='%(asctime)s - %(name)s - %(levelname)s - '
                '[req:%(request_id)s|client:%(client_id)s] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )


def setup_contextual_logging() -> logging.Logger:
    """Set up logging with request context."""
    # Get root logger
    root_logger = logging.getLogger()
    
    # Remove existing handlers to avoid duplicates
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
def log_request_start(request_id: str, tool_name: str, client_id: str, arguments: Dict[str, Any]) -> None:
    """Log request start with context."""
    logger = logging.getLogger("snowflake_mcp.requests")
    logger.info(f"Starting tool call: {tool_name} with args: {arguments}")


def log_request_complete(request_id: str, duration_ms: float, queries_executed: int) -> None:
    """Log request completion with metrics."""
    logger = logging.getLogger("snowflake_mcp.requests")
    logger.info(f"Request completed in {duration_ms:.2f}ms, executed {queries_executed} queries")


def log_request_error(request_id: str, error: Exception, context: str) -> None:
    """Log request error with context."""
    logger = logging.getLogger("snowflake_mcp.requests")
    logger.error(f"Request error in {context}: {error}")


def log_database_operation(operation: str, database: str = None, schema: str = None, query_preview: str = None) -> None:
    """Log database operation with context."""
    logger = logging.getLogger("snowflake_mcp.database")
    context_info = []
    if database:
        context_info.append(f"db:{database}")
    if schema:
        context_info.append(f"schema:{schema}")
    
    context_str = f"[{','.join(context_info)}]" if context_info else ""
    
    if query_preview:
        logger.debug(f"Database operation: {operation} {context_str} - Query: {query_preview}")
    else:
        logger.debug(f"Database operation: {operation} {context_str}")


def log_connection_event(event: str, connection_id: str = None, pool_stats: Dict[str, Any] = None) -> None:
    """Log connection pool events."""
    logger = logging.getLogger("snowflake_mcp.connections")
    
    if pool_stats:
        logger.debug(f"Connection {event} - ID: {connection_id} - Pool stats: {pool_stats}")
    else:
        logger.debug(f"Connection {event} - ID: {connection_id}")


def log_transaction_event(event: str, auto_commit: bool = None) -> None:
    """Log transaction events."""
    logger = logging.getLogger("snowflake_mcp.transactions")
    
    if auto_commit is not None:
        logger.debug(f"Transaction {event} - Auto-commit: {auto_commit}")
    else:
        logger.debug(f"Transaction {event}")


# Server setup function
def setup_server_logging() -> None:
    """Initialize server with contextual logging."""
    setup_contextual_logging()
    
    # Log server startup
    logger = logging.getLogger("snowflake_mcp.server")
    logger.info("Snowflake MCP Server starting with request isolation")