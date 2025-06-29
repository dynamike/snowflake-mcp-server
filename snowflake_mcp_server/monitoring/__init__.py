"""Monitoring and observability components for Snowflake MCP server."""

from .alerts import get_alert_manager, start_alerting, stop_alerting
from .dashboards import get_dashboard_manager
from .metrics import (
    get_metrics,
    metrics_middleware,
    start_metrics_collection,
    stop_metrics_collection,
)
from .query_tracker import get_query_tracker, track_query_execution
from .structured_logging import (
    LoggingContext,
    get_audit_logger,
    get_performance_logger,
    get_structured_logger,
    setup_structured_logging,
    with_correlation_id,
)

__all__ = [
    'get_metrics', 'start_metrics_collection', 'stop_metrics_collection', 'metrics_middleware',
    'get_structured_logger', 'get_audit_logger', 'get_performance_logger', 
    'setup_structured_logging', 'LoggingContext', 'with_correlation_id',
    'get_dashboard_manager',
    'get_alert_manager', 'start_alerting', 'stop_alerting',
    'get_query_tracker', 'track_query_execution'
]