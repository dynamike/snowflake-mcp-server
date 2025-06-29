# Phase 2: Multi-Client Architecture Implementation Details

## Context & Overview

The current Snowflake MCP server architecture creates bottlenecks when multiple MCP clients (Claude Desktop, Claude Code, Roo Code) attempt to connect simultaneously. The shared connection state and lack of client isolation cause performance degradation and potential data inconsistency issues.

**Current Issues:**
- Single connection shared across all clients
- Client requests can interfere with each other's database context
- No client identification or session management
- Resource contention leads to blocking operations
- No fair resource allocation between clients

**Target Architecture:**
- Client session management with unique identification
- Connection multiplexing with per-client isolation
- Fair resource allocation and queuing
- Client-specific rate limiting and quotas
- Session persistence across reconnections

## Current State Analysis

### Client Connection Problems in `main.py`

The stdio server only supports one client connection:
```python
def run_stdio_server() -> None:
    """Run the MCP server using stdin/stdout for communication."""
    # Only supports single client via stdio
```

Connection manager singleton shared across all requests:
```python
# In utils/snowflake_conn.py line 311
connection_manager = SnowflakeConnectionManager()  # Global singleton
```

## Implementation Plan

### 1. Client Session Management {#session-management}

**Step 1: Client Session Framework**

Create `snowflake_mcp_server/client/session_manager.py`:

```python
"""Client session management for multi-client support."""

import asyncio
import uuid
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Any
from dataclasses import dataclass, field
from enum import Enum
import weakref

logger = logging.getLogger(__name__)


class ClientType(str, Enum):
    """Types of MCP clients."""
    CLAUDE_DESKTOP = "claude_desktop"
    CLAUDE_CODE = "claude_code"
    ROO_CODE = "roo_code"
    HTTP_CLIENT = "http_client"
    WEBSOCKET_CLIENT = "websocket_client"
    UNKNOWN = "unknown"


class ConnectionState(str, Enum):
    """Client connection states."""
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ACTIVE = "active"
    IDLE = "idle"
    DISCONNECTING = "disconnecting"
    DISCONNECTED = "disconnected"


@dataclass
class ClientMetrics:
    """Metrics for client session tracking."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    avg_response_time_ms: float = 0.0
    last_activity: datetime = field(default_factory=datetime.now)
    connection_count: int = 0
    
    def update_response_time(self, response_time_ms: float) -> None:
        """Update average response time with new measurement."""
        if self.total_requests == 0:
            self.avg_response_time_ms = response_time_ms
        else:
            # Rolling average
            self.avg_response_time_ms = (
                (self.avg_response_time_ms * (self.total_requests - 1) + response_time_ms)
                / self.total_requests
            )


@dataclass
class ClientSession:
    """Client session information and state."""
    session_id: str
    client_id: str
    client_type: ClientType
    client_info: Dict[str, Any]
    created_at: datetime
    last_seen: datetime
    connection_state: ConnectionState
    metrics: ClientMetrics = field(default_factory=ClientMetrics)
    active_requests: Set[str] = field(default_factory=set)
    preferences: Dict[str, Any] = field(default_factory=dict)
    rate_limit_tokens: int = 100  # Token bucket for rate limiting
    quota_remaining: int = 1000   # Daily quota remaining
    
    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_seen = datetime.now()
        self.metrics.last_activity = self.last_seen
    
    def add_active_request(self, request_id: str) -> None:
        """Add active request to session."""
        self.active_requests.add(request_id)
        self.metrics.total_requests += 1
        self.update_activity()
    
    def remove_active_request(self, request_id: str, success: bool = True) -> None:
        """Remove active request from session."""
        self.active_requests.discard(request_id)
        if success:
            self.metrics.successful_requests += 1
        else:
            self.metrics.failed_requests += 1
        self.update_activity()
    
    def is_idle(self, idle_threshold: timedelta = timedelta(minutes=5)) -> bool:
        """Check if session is idle."""
        return (
            len(self.active_requests) == 0 and
            datetime.now() - self.last_seen > idle_threshold
        )
    
    def is_expired(self, expiry_threshold: timedelta = timedelta(hours=24)) -> bool:
        """Check if session has expired."""
        return datetime.now() - self.created_at > expiry_threshold
    
    def consume_quota(self, amount: int = 1) -> bool:
        """Consume quota tokens, return False if insufficient."""
        if self.quota_remaining >= amount:
            self.quota_remaining -= amount
            return True
        return False
    
    def consume_rate_limit_token(self) -> bool:
        """Consume rate limit token, return False if insufficient."""
        if self.rate_limit_tokens > 0:
            self.rate_limit_tokens -= 1
            return True
        return False


class ClientSessionManager:
    """Manage client sessions for multi-client support."""
    
    def __init__(
        self,
        max_sessions: int = 100,
        session_timeout: timedelta = timedelta(hours=2),
        cleanup_interval: timedelta = timedelta(minutes=10)
    ):
        self.max_sessions = max_sessions
        self.session_timeout = session_timeout
        self.cleanup_interval = cleanup_interval
        
        self._sessions: Dict[str, ClientSession] = {}
        self._client_sessions: Dict[str, Set[str]] = {}  # client_id -> session_ids
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._token_refill_task: Optional[asyncio.Task] = None
        
    async def start(self) -> None:
        """Start session manager background tasks."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self._token_refill_task = asyncio.create_task(self._token_refill_loop())
        logger.info("Client session manager started")
    
    async def stop(self) -> None:
        """Stop session manager and cleanup."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
        if self._token_refill_task:
            self._token_refill_task.cancel()
        
        async with self._lock:
            self._sessions.clear()
            self._client_sessions.clear()
        
        logger.info("Client session manager stopped")
    
    async def create_session(
        self,
        client_id: str,
        client_type: ClientType = ClientType.UNKNOWN,
        client_info: Dict[str, Any] = None
    ) -> ClientSession:
        """Create a new client session."""
        
        async with self._lock:
            # Check session limits
            if len(self._sessions) >= self.max_sessions:
                # Clean up expired sessions first
                await self._cleanup_expired_sessions()
                
                if len(self._sessions) >= self.max_sessions:
                    raise RuntimeError("Maximum number of client sessions reached")
            
            # Generate unique session ID
            session_id = f"{client_type.value}_{client_id}_{uuid.uuid4().hex[:8]}"
            
            # Create session
            session = ClientSession(
                session_id=session_id,
                client_id=client_id,
                client_type=client_type,
                client_info=client_info or {},
                created_at=datetime.now(),
                last_seen=datetime.now(),
                connection_state=ConnectionState.CONNECTING
            )
            
            # Store session
            self._sessions[session_id] = session
            
            # Index by client ID
            if client_id not in self._client_sessions:
                self._client_sessions[client_id] = set()
            self._client_sessions[client_id].add(session_id)
            
            logger.info(f"Created session {session_id} for client {client_id}")
            return session
    
    async def get_session(self, session_id: str) -> Optional[ClientSession]:
        """Get session by ID."""
        async with self._lock:
            return self._sessions.get(session_id)
    
    async def get_client_sessions(self, client_id: str) -> List[ClientSession]:
        """Get all sessions for a client."""
        async with self._lock:
            session_ids = self._client_sessions.get(client_id, set())
            return [
                self._sessions[sid] for sid in session_ids 
                if sid in self._sessions
            ]
    
    async def update_session_state(
        self,
        session_id: str,
        state: ConnectionState
    ) -> bool:
        """Update session connection state."""
        async with self._lock:
            if session_id in self._sessions:
                session = self._sessions[session_id]
                session.connection_state = state
                session.update_activity()
                
                if state == ConnectionState.CONNECTED:
                    session.metrics.connection_count += 1
                
                logger.debug(f"Session {session_id} state updated to {state}")
                return True
            return False
    
    async def remove_session(self, session_id: str) -> bool:
        """Remove a session."""
        async with self._lock:
            if session_id not in self._sessions:
                return False
            
            session = self._sessions.pop(session_id)
            
            # Remove from client index
            client_sessions = self._client_sessions.get(session.client_id, set())
            client_sessions.discard(session_id)
            
            if not client_sessions:
                self._client_sessions.pop(session.client_id, None)
            
            logger.info(f"Removed session {session_id}")
            return True
    
    async def get_session_stats(self) -> Dict[str, Any]:
        """Get session statistics."""
        async with self._lock:
            total_sessions = len(self._sessions)
            active_sessions = sum(
                1 for s in self._sessions.values()
                if s.connection_state in [ConnectionState.CONNECTED, ConnectionState.ACTIVE]
            )
            
            client_types = {}
            for session in self._sessions.values():
                client_type = session.client_type.value
                client_types[client_type] = client_types.get(client_type, 0) + 1
            
            total_requests = sum(s.metrics.total_requests for s in self._sessions.values())
            avg_response_time = (
                sum(s.metrics.avg_response_time_ms for s in self._sessions.values()) /
                total_sessions if total_sessions > 0 else 0
            )
            
            return {
                "total_sessions": total_sessions,
                "active_sessions": active_sessions,
                "client_types": client_types,
                "total_requests": total_requests,
                "avg_response_time_ms": avg_response_time,
                "unique_clients": len(self._client_sessions)
            }
    
    async def _cleanup_loop(self) -> None:
        """Background task to cleanup expired sessions."""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval.total_seconds())
                await self._cleanup_expired_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in session cleanup: {e}")
    
    async def _cleanup_expired_sessions(self) -> None:
        """Clean up expired and idle sessions."""
        async with self._lock:
            expired_sessions = []
            
            for session_id, session in list(self._sessions.items()):
                if (session.is_expired(self.session_timeout) or 
                    (session.connection_state == ConnectionState.DISCONNECTED and 
                     session.is_idle(timedelta(minutes=1)))):
                    expired_sessions.append(session_id)
            
            for session_id in expired_sessions:
                await self.remove_session(session_id)
            
            if expired_sessions:
                logger.info(f"Cleaned up {len(expired_sessions)} expired sessions")
    
    async def _token_refill_loop(self) -> None:
        """Background task to refill rate limit tokens."""
        while True:
            try:
                await asyncio.sleep(60)  # Refill every minute
                await self._refill_tokens()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in token refill: {e}")
    
    async def _refill_tokens(self) -> None:
        """Refill rate limit tokens for all sessions."""
        async with self._lock:
            for session in self._sessions.values():
                # Refill tokens (max 100, refill 10 per minute)
                session.rate_limit_tokens = min(100, session.rate_limit_tokens + 10)


# Global session manager
session_manager = ClientSessionManager()
```

