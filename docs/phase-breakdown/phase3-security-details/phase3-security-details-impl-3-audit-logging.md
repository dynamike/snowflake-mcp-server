# Phase 3: Security Enhancements Implementation Details

## Context & Overview

The current Snowflake MCP server lacks comprehensive security controls beyond basic Snowflake authentication. Production deployments require multiple layers of security including API authentication, SQL injection prevention, audit logging, and role-based access controls.

**Current Security Gaps:**
- No API authentication for HTTP/WebSocket endpoints
- Limited SQL injection prevention (only basic sqlglot parsing)
- No audit trail for queries and administrative actions
- Missing encryption validation for connections
- No role-based access controls for different client types
- Insufficient input validation and sanitization

**Target Architecture:**
- Multi-factor API authentication with API keys and JWT tokens
- Comprehensive SQL injection prevention with prepared statements
- Complete audit logging for security compliance
- Connection encryption validation and certificate management
- Role-based access controls with fine-grained permissions
- Input validation and sanitization at all entry points

## Dependencies Required

Add to `pyproject.toml`:
```toml
dependencies = [
    # Existing dependencies...
    "pyjwt>=2.8.0",              # JWT token handling
    "cryptography>=41.0.0",       # Already present, enhanced usage
    "bcrypt>=4.1.0",             # Password hashing
    "python-jose>=3.3.0",        # JWT utilities
    "passlib>=1.7.4",            # Password utilities
]

[project.optional-dependencies]
security = [
    "python-ldap>=3.4.0",       # LDAP integration
    "pyotp>=2.9.0",             # TOTP/MFA support
    "authlib>=1.2.1",           # OAuth2/OIDC support
]
```

## Implementation Plan

### 3. Audit Logging System {#audit-logging}

**Step 1: Comprehensive Audit Trail**

Create `snowflake_mcp_server/security/audit_logger.py`:

```python
"""Security audit logging system."""

import asyncio
import json
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum
import hashlib
from pathlib import Path

logger = logging.getLogger(__name__)


class AuditEventType(Enum):
    """Types of audit events."""
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    QUERY_EXECUTION = "query_execution"
    DATA_ACCESS = "data_access"
    ADMIN_ACTION = "admin_action"
    SECURITY_VIOLATION = "security_violation"
    CONNECTION_EVENT = "connection_event"
    ERROR_EVENT = "error_event"


class AuditResult(Enum):
    """Audit event results."""
    SUCCESS = "success"
    FAILURE = "failure"
    BLOCKED = "blocked"
    WARNING = "warning"


@dataclass
class AuditEvent:
    """Audit event record."""
    event_id: str
    timestamp: datetime
    event_type: AuditEventType
    result: AuditResult
    client_id: str
    user_id: Optional[str] = None
    source_ip: Optional[str] = None
    user_agent: Optional[str] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    
    # Event-specific data
    action: Optional[str] = None
    resource: Optional[str] = None
    query: Optional[str] = None
    query_hash: Optional[str] = None
    database: Optional[str] = None
    schema: Optional[str] = None
    table: Optional[str] = None
    
    # Results and metrics
    duration_ms: Optional[float] = None
    rows_affected: Optional[int] = None
    bytes_processed: Optional[int] = None
    
    # Security context
    permissions_used: Optional[List[str]] = None
    security_violations: Optional[List[str]] = None
    risk_score: Optional[float] = None
    
    # Additional metadata
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        
        # Convert datetime to ISO format
        data['timestamp'] = self.timestamp.isoformat()
        
        # Convert enums to values
        data['event_type'] = self.event_type.value
        data['result'] = self.result.value
        
        return data


class AuditLogger:
    """Security audit logging system."""
    
    def __init__(
        self,
        log_file: Optional[str] = None,
        max_file_size: int = 100 * 1024 * 1024,  # 100MB
        backup_count: int = 10,
        enable_real_time_alerts: bool = True
    ):
        self.log_file = Path(log_file) if log_file else None
        self.max_file_size = max_file_size
        self.backup_count = backup_count
        self.enable_real_time_alerts = enable_real_time_alerts
        
        # In-memory event storage for analysis
        self._recent_events: List[AuditEvent] = []
        self._max_recent_events = 1000
        self._lock = asyncio.Lock()
        
        # Event counters for monitoring
        self._event_counters: Dict[str, int] = {}
        
        # Setup file logging if specified
        if self.log_file:
            self._setup_file_logging()
    
    def _setup_file_logging(self) -> None:
        """Setup file-based audit logging."""
        # Ensure log directory exists
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Create audit-specific logger
        self.audit_file_logger = logging.getLogger("audit")
        self.audit_file_logger.setLevel(logging.INFO)
        
        # Create file handler with rotation
        from logging.handlers import RotatingFileHandler
        
        handler = RotatingFileHandler(
            self.log_file,
            maxBytes=self.max_file_size,
            backupCount=self.backup_count
        )
        
        # JSON formatter for structured audit logs
        formatter = logging.Formatter('%(message)s')
        handler.setFormatter(formatter)
        
        self.audit_file_logger.addHandler(handler)
        self.audit_file_logger.propagate = False
    
    async def log_event(self, event: AuditEvent) -> None:
        """Log audit event."""
        
        async with self._lock:
            # Add to recent events
            self._recent_events.append(event)
            
            # Trim recent events list
            if len(self._recent_events) > self._max_recent_events:
                self._recent_events = self._recent_events[-self._max_recent_events:]
            
            # Update counters
            counter_key = f"{event.event_type.value}_{event.result.value}"
            self._event_counters[counter_key] = self._event_counters.get(counter_key, 0) + 1
        
        # Log to file if configured
        if hasattr(self, 'audit_file_logger'):
            self.audit_file_logger.info(json.dumps(event.to_dict()))
        
        # Log to standard logger for debugging
        logger.info(f"Audit: {event.event_type.value} - {event.result.value} - Client: {event.client_id}")
        
        # Check for real-time alerts
        if self.enable_real_time_alerts:
            await self._check_alert_conditions(event)
    
    async def log_authentication(
        self,
        client_id: str,
        result: AuditResult,
        auth_method: str,
        source_ip: str = None,
        user_agent: str = None,
        metadata: Dict[str, Any] = None
    ) -> None:
        """Log authentication event."""
        
        event = AuditEvent(
            event_id=self._generate_event_id(),
            timestamp=datetime.now(),
            event_type=AuditEventType.AUTHENTICATION,
            result=result,
            client_id=client_id,
            source_ip=source_ip,
            user_agent=user_agent,
            action=auth_method,
            metadata=metadata
        )
        
        await self.log_event(event)
    
    async def log_query_execution(
        self,
        client_id: str,
        query: str,
        result: AuditResult,
        database: str = None,
        schema: str = None,
        duration_ms: float = None,
        rows_affected: int = None,
        request_id: str = None,
        security_violations: List[str] = None
    ) -> None:
        """Log query execution event."""
        
        # Hash query for privacy
        query_hash = hashlib.sha256(query.encode()).hexdigest()[:16]
        
        # Truncate query for logging (remove sensitive data)
        logged_query = query[:200] + "..." if len(query) > 200 else query
        
        event = AuditEvent(
            event_id=self._generate_event_id(),
            timestamp=datetime.now(),
            event_type=AuditEventType.QUERY_EXECUTION,
            result=result,
            client_id=client_id,
            request_id=request_id,
            action="execute_query",
            query=logged_query,
            query_hash=query_hash,
            database=database,
            schema=schema,
            duration_ms=duration_ms,
            rows_affected=rows_affected,
            security_violations=security_violations
        )
        
        await self.log_event(event)
    
    async def log_data_access(
        self,
        client_id: str,
        resource: str,
        action: str,
        result: AuditResult,
        bytes_processed: int = None,
        request_id: str = None
    ) -> None:
        """Log data access event."""
        
        event = AuditEvent(
            event_id=self._generate_event_id(),
            timestamp=datetime.now(),
            event_type=AuditEventType.DATA_ACCESS,
            result=result,
            client_id=client_id,
            request_id=request_id,
            action=action,
            resource=resource,
            bytes_processed=bytes_processed
        )
        
        await self.log_event(event)
    
    async def log_security_violation(
        self,
        client_id: str,
        violation_type: str,
        details: str,
        source_ip: str = None,
        request_id: str = None,
        risk_score: float = None
    ) -> None:
        """Log security violation event."""
        
        event = AuditEvent(
            event_id=self._generate_event_id(),
            timestamp=datetime.now(),
            event_type=AuditEventType.SECURITY_VIOLATION,
            result=AuditResult.BLOCKED,
            client_id=client_id,
            source_ip=source_ip,
            request_id=request_id,
            action=violation_type,
            security_violations=[details],
            risk_score=risk_score
        )
        
        await self.log_event(event)
    
    async def get_recent_events(
        self,
        limit: int = 100,
        event_type: AuditEventType = None,
        client_id: str = None
    ) -> List[AuditEvent]:
        """Get recent audit events with optional filtering."""
        
        async with self._lock:
            events = self._recent_events.copy()
        
        # Apply filters
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        
        if client_id:
            events = [e for e in events if e.client_id == client_id]
        
        # Return most recent first
        return sorted(events, key=lambda x: x.timestamp, reverse=True)[:limit]
    
    async def get_event_statistics(self) -> Dict[str, Any]:
        """Get audit event statistics."""
        
        async with self._lock:
            stats = {
                "total_events": len(self._recent_events),
                "event_counts": self._event_counters.copy()
            }
            
            # Calculate rates by event type
            if self._recent_events:
                latest_time = max(e.timestamp for e in self._recent_events)
                earliest_time = min(e.timestamp for e in self._recent_events)
                duration = (latest_time - earliest_time).total_seconds()
                
                if duration > 0:
                    stats["events_per_second"] = len(self._recent_events) / duration
            
            return stats
    
    async def _check_alert_conditions(self, event: AuditEvent) -> None:
        """Check if event should trigger real-time alerts."""
        
        # Alert on security violations
        if event.event_type == AuditEventType.SECURITY_VIOLATION:
            logger.critical(f"Security violation detected: {event.action} from {event.client_id}")
        
        # Alert on repeated authentication failures
        if (event.event_type == AuditEventType.AUTHENTICATION and 
            event.result == AuditResult.FAILURE):
            
            # Check for repeated failures from same client
            recent_failures = [
                e for e in self._recent_events[-10:]  # Last 10 events
                if (e.client_id == event.client_id and 
                    e.event_type == AuditEventType.AUTHENTICATION and
                    e.result == AuditResult.FAILURE)
            ]
            
            if len(recent_failures) >= 3:
                logger.warning(f"Multiple authentication failures from client {event.client_id}")
    
    def _generate_event_id(self) -> str:
        """Generate unique event ID."""
        import uuid
        return str(uuid.uuid4())


# Global audit logger
audit_logger = AuditLogger()


def initialize_audit_logger(
    log_file: str = "/var/log/snowflake-mcp/audit.log",
    enable_real_time_alerts: bool = True
) -> None:
    """Initialize global audit logger."""
    global audit_logger
    audit_logger = AuditLogger(log_file, enable_real_time_alerts=enable_real_time_alerts)


# Decorator for audit logging
def audit_operation(event_type: AuditEventType, action: str):
    """Decorator to audit function calls."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            from ..utils.request_context import current_client_id, current_request_id
            
            client_id = current_client_id.get() or "unknown"
            request_id = current_request_id.get()
            
            start_time = datetime.now()
            result = AuditResult.SUCCESS
            
            try:
                return_value = await func(*args, **kwargs)
                return return_value
                
            except Exception as e:
                result = AuditResult.FAILURE
                raise
            
            finally:
                duration_ms = (datetime.now() - start_time).total_seconds() * 1000
                
                # Create audit event based on type
                if event_type == AuditEventType.QUERY_EXECUTION:
                    query = kwargs.get('query', args[0] if args else "unknown")
                    await audit_logger.log_query_execution(
                        client_id=client_id,
                        query=str(query),
                        result=result,
                        duration_ms=duration_ms,
                        request_id=request_id
                    )
                else:
                    # Generic audit event
                    event = AuditEvent(
                        event_id=audit_logger._generate_event_id(),
                        timestamp=start_time,
                        event_type=event_type,
                        result=result,
                        client_id=client_id,
                        request_id=request_id,
                        action=action,
                        duration_ms=duration_ms
                    )
                    await audit_logger.log_event(event)
        
        return wrapper
    return decorator
```

