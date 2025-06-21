"""Query performance tracking and analysis."""

import asyncio
import json
import logging
import statistics
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..config import get_config
from .metrics import get_metrics
from .structured_logging import get_performance_logger, get_structured_logger

logger = logging.getLogger(__name__)


@dataclass
class QueryMetrics:
    """Metrics for a single query execution."""
    
    query_id: str
    client_id: str
    database: str
    schema: str
    query_type: str  # SELECT, INSERT, UPDATE, etc.
    query_text: str
    start_time: float
    end_time: float
    duration_seconds: float
    rows_returned: int
    rows_examined: int
    bytes_processed: int
    connection_time: float
    execution_time: float
    status: str  # success, error, timeout
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "query_id": self.query_id,
            "client_id": self.client_id,
            "database": self.database,
            "schema": self.schema,
            "query_type": self.query_type,
            "query_text": self.query_text[:1000] + "..." if len(self.query_text) > 1000 else self.query_text,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self.duration_seconds,
            "rows_returned": self.rows_returned,
            "rows_examined": self.rows_examined,
            "bytes_processed": self.bytes_processed,
            "connection_time": self.connection_time,
            "execution_time": self.execution_time,
            "status": self.status,
            "error_message": self.error_message,
        }


@dataclass
class QueryPattern:
    """Represents a query pattern for analysis."""
    
    pattern_id: str
    normalized_query: str
    query_type: str
    execution_count: int = 0
    total_duration: float = 0.0
    avg_duration: float = 0.0
    min_duration: float = float('inf')
    max_duration: float = 0.0
    avg_rows_returned: float = 0.0
    failure_count: int = 0
    failure_rate: float = 0.0
    last_seen: Optional[datetime] = None
    
    def update_stats(self, metrics: QueryMetrics):
        """Update pattern statistics with new query metrics."""
        self.execution_count += 1
        self.total_duration += metrics.duration_seconds
        self.avg_duration = self.total_duration / self.execution_count
        self.min_duration = min(self.min_duration, metrics.duration_seconds)
        self.max_duration = max(self.max_duration, metrics.duration_seconds)
        
        # Update average rows (running average)
        self.avg_rows_returned = (
            (self.avg_rows_returned * (self.execution_count - 1) + metrics.rows_returned) 
            / self.execution_count
        )
        
        if metrics.status != "success":
            self.failure_count += 1
        
        self.failure_rate = self.failure_count / self.execution_count
        self.last_seen = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "pattern_id": self.pattern_id,
            "normalized_query": self.normalized_query,
            "query_type": self.query_type,
            "execution_count": self.execution_count,
            "avg_duration": round(self.avg_duration, 3),
            "min_duration": round(self.min_duration, 3),
            "max_duration": round(self.max_duration, 3),
            "avg_rows_returned": round(self.avg_rows_returned, 2),
            "failure_count": self.failure_count,
            "failure_rate": round(self.failure_rate, 3),
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }


class QueryNormalizer:
    """Normalizes SQL queries for pattern detection."""
    
    @staticmethod
    def normalize_query(query: str) -> str:
        """Normalize a SQL query by removing literals and formatting."""
        import re
        
        # Convert to uppercase and remove extra whitespace
        normalized = re.sub(r'\s+', ' ', query.upper().strip())
        
        # Replace string literals
        normalized = re.sub(r"'[^']*'", "'?'", normalized)
        
        # Replace numeric literals
        normalized = re.sub(r'\b\d+\b', '?', normalized)
        
        # Replace IN clauses with multiple values
        normalized = re.sub(r'IN\s*\([^)]+\)', 'IN (?)', normalized)
        
        # Replace specific table/column names with placeholders in common patterns
        # This is a simplified version - a full implementation would use SQL parsing
        
        return normalized
    
    @staticmethod
    def extract_query_type(query: str) -> str:
        """Extract the query type (SELECT, INSERT, etc.)."""
        import re
        
        # Match common SQL keywords at the start
        match = re.match(r'\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|SHOW|DESCRIBE|EXPLAIN)', 
                        query.upper().strip())
        
        if match:
            return match.group(1)
        
        return "UNKNOWN"
    
    @staticmethod
    def generate_pattern_id(normalized_query: str) -> str:
        """Generate a unique pattern ID for a normalized query."""
        import hashlib
        
        return hashlib.md5(normalized_query.encode()).hexdigest()[:16]


class SlowQueryDetector:
    """Detects and analyzes slow queries."""
    
    def __init__(self, slow_threshold: float = 5.0):
        self.slow_threshold = slow_threshold
        self.slow_queries: deque = deque(maxlen=1000)  # Keep last 1000 slow queries
        self.logger = get_performance_logger()
    
    def check_query(self, metrics: QueryMetrics) -> bool:
        """Check if a query is slow and log it."""
        if metrics.duration_seconds >= self.slow_threshold:
            self.slow_queries.append(metrics)
            
            self.logger.log_database_performance(
                query_type=metrics.query_type,
                database=metrics.database,
                duration=metrics.duration_seconds,
                rows_processed=metrics.rows_returned
            )
            
            return True
        
        return False
    
    def get_slow_queries(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent slow queries."""
        return [q.to_dict() for q in list(self.slow_queries)[-limit:]]
    
    def get_slow_query_stats(self) -> Dict[str, Any]:
        """Get statistics about slow queries."""
        if not self.slow_queries:
            return {"count": 0}
        
        durations = [q.duration_seconds for q in self.slow_queries]
        
        return {
            "count": len(self.slow_queries),
            "avg_duration": statistics.mean(durations),
            "median_duration": statistics.median(durations),
            "max_duration": max(durations),
            "min_duration": min(durations),
        }


class QueryPerformanceTracker:
    """Main class for tracking query performance."""
    
    def __init__(self):
        self.config = get_config()
        self.metrics = get_metrics()
        self.logger = get_structured_logger().get_logger("query_tracker")
        self.perf_logger = get_performance_logger()
        
        # Query storage
        self.recent_queries: deque = deque(maxlen=10000)  # Keep last 10k queries
        self.query_patterns: Dict[str, QueryPattern] = {}
        
        # Performance analysis
        self.slow_detector = SlowQueryDetector(
            slow_threshold=getattr(self.config.monitoring, 'slow_query_threshold', 5.0)
        )
        
        # Client statistics
        self.client_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            'query_count': 0,
            'total_duration': 0.0,
            'avg_duration': 0.0,
            'error_count': 0,
            'slow_query_count': 0,
        })
        
        # Database statistics
        self.database_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            'query_count': 0,
            'total_duration': 0.0,
            'avg_duration': 0.0,
            'total_rows': 0,
            'avg_rows': 0.0,
        })
        
        # Time-based statistics (last 24 hours in hourly buckets)
        self.hourly_stats: deque = deque(maxlen=24)
        self._init_hourly_stats()
    
    def _init_hourly_stats(self):
        """Initialize hourly statistics buckets."""
        now = datetime.now()
        for i in range(24):
            hour = now - timedelta(hours=23-i)
            self.hourly_stats.append({
                'hour': hour.replace(minute=0, second=0, microsecond=0),
                'query_count': 0,
                'total_duration': 0.0,
                'error_count': 0,
                'slow_count': 0,
            })
    
    def track_query(self, 
                   query_id: str,
                   client_id: str,
                   database: str,
                   schema: str,
                   query_text: str,
                   start_time: float,
                   end_time: float,
                   rows_returned: int = 0,
                   rows_examined: int = 0,
                   bytes_processed: int = 0,
                   connection_time: float = 0.0,
                   status: str = "success",
                   error_message: Optional[str] = None) -> QueryMetrics:
        """Track a completed query execution."""
        
        duration = end_time - start_time
        execution_time = duration - connection_time
        
        # Extract query type
        query_type = QueryNormalizer.extract_query_type(query_text)
        
        # Create metrics object
        metrics = QueryMetrics(
            query_id=query_id,
            client_id=client_id,
            database=database,
            schema=schema,
            query_type=query_type,
            query_text=query_text,
            start_time=start_time,
            end_time=end_time,
            duration_seconds=duration,
            rows_returned=rows_returned,
            rows_examined=rows_examined,
            bytes_processed=bytes_processed,
            connection_time=connection_time,
            execution_time=execution_time,
            status=status,
            error_message=error_message,
        )
        
        # Store query
        self.recent_queries.append(metrics)
        
        # Update pattern analysis
        self._update_query_patterns(metrics)
        
        # Update statistics
        self._update_client_stats(metrics)
        self._update_database_stats(metrics)
        self._update_hourly_stats(metrics)
        
        # Check for slow queries
        is_slow = self.slow_detector.check_query(metrics)
        
        # Update Prometheus metrics
        self.metrics.record_query(
            database=database,
            query_type=query_type,
            duration=duration,
            rows_returned=rows_returned,
            status=status
        )
        
        # Log performance data
        self.perf_logger.log_database_performance(
            query_type=query_type,
            database=database,
            duration=duration,
            rows_processed=rows_returned
        )
        
        # Log query details for analysis
        self.logger.info(
            "Query executed",
            query_id=query_id,
            client_id=client_id,
            database=database,
            query_type=query_type,
            duration_seconds=round(duration, 3),
            rows_returned=rows_returned,
            status=status,
            is_slow=is_slow,
            event_type="query_performance"
        )
        
        return metrics
    
    def _update_query_patterns(self, metrics: QueryMetrics):
        """Update query pattern analysis."""
        try:
            normalized = QueryNormalizer.normalize_query(metrics.query_text)
            pattern_id = QueryNormalizer.generate_pattern_id(normalized)
            
            if pattern_id not in self.query_patterns:
                self.query_patterns[pattern_id] = QueryPattern(
                    pattern_id=pattern_id,
                    normalized_query=normalized,
                    query_type=metrics.query_type
                )
            
            self.query_patterns[pattern_id].update_stats(metrics)
            
        except Exception as e:
            self.logger.error(f"Error updating query patterns: {e}")
    
    def _update_client_stats(self, metrics: QueryMetrics):
        """Update per-client statistics."""
        stats = self.client_stats[metrics.client_id]
        
        stats['query_count'] += 1
        stats['total_duration'] += metrics.duration_seconds
        stats['avg_duration'] = stats['total_duration'] / stats['query_count']
        
        if metrics.status != "success":
            stats['error_count'] += 1
        
        if metrics.duration_seconds >= self.slow_detector.slow_threshold:
            stats['slow_query_count'] += 1
    
    def _update_database_stats(self, metrics: QueryMetrics):
        """Update per-database statistics."""
        stats = self.database_stats[metrics.database]
        
        stats['query_count'] += 1
        stats['total_duration'] += metrics.duration_seconds
        stats['avg_duration'] = stats['total_duration'] / stats['query_count']
        stats['total_rows'] += metrics.rows_returned
        stats['avg_rows'] = stats['total_rows'] / stats['query_count']
    
    def _update_hourly_stats(self, metrics: QueryMetrics):
        """Update hourly time-series statistics."""
        current_hour = datetime.now().replace(minute=0, second=0, microsecond=0)
        
        # Make sure we have current hour bucket
        if not self.hourly_stats or self.hourly_stats[-1]['hour'] < current_hour:
            self.hourly_stats.append({
                'hour': current_hour,
                'query_count': 0,
                'total_duration': 0.0,
                'error_count': 0,
                'slow_count': 0,
            })
        
        # Update current hour stats
        current_stats = self.hourly_stats[-1]
        current_stats['query_count'] += 1
        current_stats['total_duration'] += metrics.duration_seconds
        
        if metrics.status != "success":
            current_stats['error_count'] += 1
        
        if metrics.duration_seconds >= self.slow_detector.slow_threshold:
            current_stats['slow_count'] += 1
    
    def get_query_statistics(self) -> Dict[str, Any]:
        """Get overall query statistics."""
        if not self.recent_queries:
            return {"message": "No queries tracked yet"}
        
        durations = [q.duration_seconds for q in self.recent_queries]
        
        return {
            "total_queries": len(self.recent_queries),
            "avg_duration": statistics.mean(durations),
            "median_duration": statistics.median(durations),
            "min_duration": min(durations),
            "max_duration": max(durations),
            "slow_query_count": len(self.slow_detector.slow_queries),
            "query_types": self._get_query_type_breakdown(),
            "status_breakdown": self._get_status_breakdown(),
        }
    
    def _get_query_type_breakdown(self) -> Dict[str, int]:
        """Get breakdown of queries by type."""
        breakdown = defaultdict(int)
        for query in self.recent_queries:
            breakdown[query.query_type] += 1
        return dict(breakdown)
    
    def _get_status_breakdown(self) -> Dict[str, int]:
        """Get breakdown of queries by status."""
        breakdown = defaultdict(int)
        for query in self.recent_queries:
            breakdown[query.status] += 1
        return dict(breakdown)
    
    def get_client_performance(self, client_id: str = None) -> Dict[str, Any]:
        """Get performance statistics for clients."""
        if client_id:
            return dict(self.client_stats.get(client_id, {}))
        
        return {cid: dict(stats) for cid, stats in self.client_stats.items()}
    
    def get_database_performance(self, database: str = None) -> Dict[str, Any]:
        """Get performance statistics for databases."""
        if database:
            return dict(self.database_stats.get(database, {}))
        
        return {db: dict(stats) for db, stats in self.database_stats.items()}
    
    def get_query_patterns(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get most common query patterns."""
        patterns = sorted(
            self.query_patterns.values(),
            key=lambda p: p.execution_count,
            reverse=True
        )
        
        return [p.to_dict() for p in patterns[:limit]]
    
    def get_slow_queries(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent slow queries."""
        return self.slow_detector.get_slow_queries(limit)
    
    def get_hourly_trends(self) -> List[Dict[str, Any]]:
        """Get hourly query trends."""
        return [
            {
                "hour": stat["hour"].isoformat(),
                "query_count": stat["query_count"],
                "avg_duration": stat["total_duration"] / stat["query_count"] if stat["query_count"] > 0 else 0,
                "error_count": stat["error_count"],
                "slow_count": stat["slow_count"],
                "error_rate": stat["error_count"] / stat["query_count"] if stat["query_count"] > 0 else 0,
            }
            for stat in self.hourly_stats
        ]
    
    def get_performance_insights(self) -> Dict[str, Any]:
        """Get performance insights and recommendations."""
        insights = {
            "recommendations": [],
            "warnings": [],
            "statistics": self.get_query_statistics(),
        }
        
        # Analyze patterns for insights
        patterns = sorted(
            self.query_patterns.values(),
            key=lambda p: p.avg_duration,
            reverse=True
        )
        
        # Check for slow patterns
        for pattern in patterns[:5]:
            if pattern.avg_duration > 10.0:
                insights["warnings"].append(
                    f"Query pattern {pattern.pattern_id} has high average duration: {pattern.avg_duration:.2f}s"
                )
        
        # Check client performance
        for client_id, stats in self.client_stats.items():
            if stats['query_count'] > 0:
                error_rate = stats['error_count'] / stats['query_count']
                if error_rate > 0.1:  # 10% error rate
                    insights["warnings"].append(
                        f"Client {client_id} has high error rate: {error_rate:.1%}"
                    )
        
        # General recommendations
        if len(self.slow_detector.slow_queries) > 10:
            insights["recommendations"].append(
                "Consider optimizing frequently executed slow queries"
            )
        
        if len(self.query_patterns) > 1000:
            insights["recommendations"].append(
                "High query pattern diversity detected - consider query optimization"
            )
        
        return insights


# Global query tracker instance
_query_tracker: Optional[QueryPerformanceTracker] = None


def get_query_tracker() -> QueryPerformanceTracker:
    """Get the global query tracker instance."""
    global _query_tracker
    if _query_tracker is None:
        _query_tracker = QueryPerformanceTracker()
    return _query_tracker


def track_query_execution(func):
    """Decorator to automatically track query execution."""
    def decorator(*args, **kwargs):
        from functools import wraps
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            tracker = get_query_tracker()
            start_time = time.time()
            query_id = f"query_{int(start_time * 1000)}"
            
            try:
                result = await func(*args, **kwargs)
                end_time = time.time()
                
                # Extract query details from result or arguments
                # This would need to be customized based on your function signatures
                tracker.track_query(
                    query_id=query_id,
                    client_id=kwargs.get('client_id', 'unknown'),
                    database=kwargs.get('database', 'unknown'),
                    schema=kwargs.get('schema', 'unknown'),
                    query_text=kwargs.get('query', 'unknown'),
                    start_time=start_time,
                    end_time=end_time,
                    status="success"
                )
                
                return result
                
            except Exception as e:
                end_time = time.time()
                
                tracker.track_query(
                    query_id=query_id,
                    client_id=kwargs.get('client_id', 'unknown'),
                    database=kwargs.get('database', 'unknown'),
                    schema=kwargs.get('schema', 'unknown'),
                    query_text=kwargs.get('query', str(e)),
                    start_time=start_time,
                    end_time=end_time,
                    status="error",
                    error_message=str(e)
                )
                
                raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # Similar logic for sync functions
            return func(*args, **kwargs)
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


# FastAPI endpoints for query performance API
async def get_query_stats_endpoint() -> Dict[str, Any]:
    """API endpoint to get query statistics."""
    tracker = get_query_tracker()
    return {
        "query_statistics": tracker.get_query_statistics(),
        "slow_query_stats": tracker.slow_detector.get_slow_query_stats(),
        "timestamp": datetime.now().isoformat(),
    }


async def get_query_patterns_endpoint(limit: int = 50) -> Dict[str, Any]:
    """API endpoint to get query patterns."""
    tracker = get_query_tracker()
    return {
        "query_patterns": tracker.get_query_patterns(limit),
        "pattern_count": len(tracker.query_patterns),
        "timestamp": datetime.now().isoformat(),
    }


async def get_slow_queries_endpoint(limit: int = 100) -> Dict[str, Any]:
    """API endpoint to get slow queries."""
    tracker = get_query_tracker()
    return {
        "slow_queries": tracker.get_slow_queries(limit),
        "timestamp": datetime.now().isoformat(),
    }


async def get_performance_insights_endpoint() -> Dict[str, Any]:
    """API endpoint to get performance insights."""
    tracker = get_query_tracker()
    return {
        "insights": tracker.get_performance_insights(),
        "timestamp": datetime.now().isoformat(),
    }


async def get_hourly_trends_endpoint() -> Dict[str, Any]:
    """API endpoint to get hourly trends."""
    tracker = get_query_tracker()
    return {
        "hourly_trends": tracker.get_hourly_trends(),
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    # Test query tracking
    tracker = QueryPerformanceTracker()
    
    # Simulate some queries
    import uuid
    
    for i in range(10):
        query_id = str(uuid.uuid4())
        start = time.time()
        time.sleep(0.1)  # Simulate query execution
        end = time.time()
        
        tracker.track_query(
            query_id=query_id,
            client_id=f"client_{i % 3}",
            database="TEST_DB",
            schema="PUBLIC",
            query_text=f"SELECT * FROM table_{i} WHERE id = {i}",
            start_time=start,
            end_time=end,
            rows_returned=100 * (i + 1),
            status="success"
        )
    
    # Print statistics
    print("Query Statistics:")
    print(json.dumps(tracker.get_query_statistics(), indent=2))
    
    print("\nQuery Patterns:")
    print(json.dumps(tracker.get_query_patterns(), indent=2))
    
    print("\nClient Performance:")
    print(json.dumps(tracker.get_client_performance(), indent=2))