"""Client session management for multi-client MCP server."""

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class ClientSession:
    """Represents a client session with associated metadata."""
    
    session_id: str
    client_id: str
    client_type: str  # 'http', 'websocket', 'stdio'
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    request_count: int = 0
    active_requests: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)
    connection_info: Dict[str, Any] = field(default_factory=dict)
    
    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = time.time()
    
    def add_request(self, request_id: str) -> None:
        """Add an active request to this session."""
        self.active_requests.add(request_id)
        self.request_count += 1
        self.update_activity()
    
    def remove_request(self, request_id: str) -> None:
        """Remove a completed request from this session."""
        self.active_requests.discard(request_id)
        self.update_activity()
    
    def get_uptime(self) -> float:
        """Get session uptime in seconds."""
        return time.time() - self.created_at
    
    def get_idle_time(self) -> float:
        """Get idle time since last activity in seconds."""
        return time.time() - self.last_activity
    
    def is_active(self) -> bool:
        """Check if session has active requests."""
        return len(self.active_requests) > 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert session to dictionary representation."""
        return {
            'session_id': self.session_id,
            'client_id': self.client_id,
            'client_type': self.client_type,
            'created_at': self.created_at,
            'last_activity': self.last_activity,
            'uptime_seconds': self.get_uptime(),
            'idle_seconds': self.get_idle_time(),
            'request_count': self.request_count,
            'active_requests': len(self.active_requests),
            'is_active': self.is_active(),
            'metadata': self.metadata,
            'connection_info': self.connection_info
        }


class SessionManager:
    """Manages client sessions across multiple connection types."""
    
    def __init__(self, 
                 session_timeout: float = 3600.0,  # 1 hour
                 cleanup_interval: float = 300.0,  # 5 minutes
                 max_sessions_per_client: int = 10):
        self.sessions: Dict[str, ClientSession] = {}
        self.client_sessions: Dict[str, Set[str]] = defaultdict(set)
        self.session_timeout = session_timeout
        self.cleanup_interval = cleanup_interval
        self.max_sessions_per_client = max_sessions_per_client
        self._cleanup_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        
        # Statistics
        self.total_sessions_created = 0
        self.total_sessions_expired = 0
        self.total_requests_processed = 0
    
    async def start(self) -> None:
        """Start the session manager and cleanup task."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("Session manager started")
    
    async def stop(self) -> None:
        """Stop the session manager and cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("Session manager stopped")
    
    async def create_session(self,
                           client_id: str,
                           client_type: str,
                           metadata: Optional[Dict[str, Any]] = None,
                           connection_info: Optional[Dict[str, Any]] = None) -> ClientSession:
        """Create a new client session."""
        async with self._lock:
            # Check if client has too many sessions
            if len(self.client_sessions[client_id]) >= self.max_sessions_per_client:
                # Remove oldest session for this client
                oldest_session_id = min(
                    self.client_sessions[client_id],
                    key=lambda sid: self.sessions[sid].created_at
                )
                await self._remove_session(oldest_session_id)
                logger.warning(f"Removed oldest session for client {client_id} (max sessions exceeded)")
            
            # Create new session
            session_id = str(uuid.uuid4())
            session = ClientSession(
                session_id=session_id,
                client_id=client_id,
                client_type=client_type,
                metadata=metadata or {},
                connection_info=connection_info or {}
            )
            
            self.sessions[session_id] = session
            self.client_sessions[client_id].add(session_id)
            self.total_sessions_created += 1
            
            logger.info(f"Created session {session_id} for client {client_id} ({client_type})")
            return session
    
    async def get_session(self, session_id: str) -> Optional[ClientSession]:
        """Get a session by ID."""
        session = self.sessions.get(session_id)
        if session:
            session.update_activity()
        return session
    
    async def get_client_sessions(self, client_id: str) -> List[ClientSession]:
        """Get all sessions for a specific client."""
        session_ids = self.client_sessions.get(client_id, set())
        return [self.sessions[sid] for sid in session_ids if sid in self.sessions]
    
    async def remove_session(self, session_id: str) -> bool:
        """Remove a session."""
        async with self._lock:
            return await self._remove_session(session_id)
    
    async def _remove_session(self, session_id: str) -> bool:
        """Internal method to remove a session (must be called with lock)."""
        if session_id not in self.sessions:
            return False
        
        session = self.sessions[session_id]
        
        # Clean up client session tracking
        self.client_sessions[session.client_id].discard(session_id)
        if not self.client_sessions[session.client_id]:
            del self.client_sessions[session.client_id]
        
        # Remove session
        del self.sessions[session_id]
        
        logger.info(f"Removed session {session_id} for client {session.client_id}")
        return True
    
    async def add_request(self, session_id: str, request_id: str) -> bool:
        """Add a request to a session."""
        session = await self.get_session(session_id)
        if session:
            session.add_request(request_id)
            self.total_requests_processed += 1
            return True
        return False
    
    async def remove_request(self, session_id: str, request_id: str) -> bool:
        """Remove a request from a session."""
        session = await self.get_session(session_id)
        if session:
            session.remove_request(request_id)
            return True
        return False
    
    async def get_all_sessions(self) -> List[ClientSession]:
        """Get all active sessions."""
        return list(self.sessions.values())
    
    async def get_session_stats(self) -> Dict[str, Any]:
        """Get session statistics."""
        sessions = list(self.sessions.values())
        
        # Calculate statistics
        total_sessions = len(sessions)
        active_sessions = sum(1 for s in sessions if s.is_active())
        idle_sessions = total_sessions - active_sessions
        
        # Group by client type
        by_type = defaultdict(int)
        for session in sessions:
            by_type[session.client_type] += 1
        
        # Active requests across all sessions
        total_active_requests = sum(len(s.active_requests) for s in sessions)
        
        # Average session duration
        avg_uptime = sum(s.get_uptime() for s in sessions) / max(total_sessions, 1)
        
        return {
            'total_sessions': total_sessions,
            'active_sessions': active_sessions,
            'idle_sessions': idle_sessions,
            'sessions_by_type': dict(by_type),
            'total_active_requests': total_active_requests,
            'average_uptime_seconds': avg_uptime,
            'total_sessions_created': self.total_sessions_created,
            'total_sessions_expired': self.total_sessions_expired,
            'total_requests_processed': self.total_requests_processed,
            'unique_clients': len(self.client_sessions)
        }
    
    async def cleanup_expired_sessions(self) -> int:
        """Clean up expired sessions and return count of removed sessions."""
        async with self._lock:
            current_time = time.time()
            expired_sessions = []
            
            for session_id, session in self.sessions.items():
                # Check if session is expired
                if (current_time - session.last_activity) > self.session_timeout:
                    expired_sessions.append(session_id)
            
            # Remove expired sessions
            for session_id in expired_sessions:
                await self._remove_session(session_id)
                self.total_sessions_expired += 1
            
            if expired_sessions:
                logger.info(f"Cleaned up {len(expired_sessions)} expired sessions")
            
            return len(expired_sessions)
    
    async def _cleanup_loop(self) -> None:
        """Background task for cleaning up expired sessions."""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self.cleanup_expired_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in session cleanup loop: {e}")
    
    async def get_client_info(self, client_id: str) -> Dict[str, Any]:
        """Get detailed information about a specific client."""
        sessions = await self.get_client_sessions(client_id)
        
        if not sessions:
            return {
                'client_id': client_id,
                'active': False,
                'sessions': []
            }
        
        total_requests = sum(s.request_count for s in sessions)
        active_requests = sum(len(s.active_requests) for s in sessions)
        oldest_session = min(sessions, key=lambda s: s.created_at)
        newest_session = max(sessions, key=lambda s: s.created_at)
        
        return {
            'client_id': client_id,
            'active': any(s.is_active() for s in sessions),
            'session_count': len(sessions),
            'total_requests': total_requests,
            'active_requests': active_requests,
            'oldest_session_age': oldest_session.get_uptime(),
            'newest_session_age': newest_session.get_uptime(),
            'sessions': [s.to_dict() for s in sessions]
        }
    
    async def force_cleanup_client(self, client_id: str) -> int:
        """Force cleanup of all sessions for a specific client."""
        async with self._lock:
            session_ids = list(self.client_sessions.get(client_id, set()))
            
            for session_id in session_ids:
                await self._remove_session(session_id)
            
            if session_ids:
                logger.info(f"Force cleaned up {len(session_ids)} sessions for client {client_id}")
            
            return len(session_ids)


# Global session manager instance
_session_manager: Optional[SessionManager] = None


async def get_session_manager() -> SessionManager:
    """Get the global session manager instance."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
        await _session_manager.start()
    return _session_manager


async def cleanup_session_manager() -> None:
    """Clean up the global session manager."""
    global _session_manager
    if _session_manager:
        await _session_manager.stop()
        _session_manager = None


if __name__ == "__main__":
    # Test session manager
    async def test_session_manager():
        # Create session manager
        manager = SessionManager(session_timeout=5.0, cleanup_interval=2.0)
        await manager.start()
        
        try:
            # Create some test sessions
            session1 = await manager.create_session("client1", "websocket", {"user": "alice"})
            session2 = await manager.create_session("client2", "http", {"user": "bob"})
            session3 = await manager.create_session("client1", "stdio", {"user": "alice"})
            
            print(f"Created sessions: {session1.session_id}, {session2.session_id}, {session3.session_id}")
            
            # Add some requests
            await manager.add_request(session1.session_id, "req1")
            await manager.add_request(session1.session_id, "req2")
            await manager.add_request(session2.session_id, "req3")
            
            # Get stats
            stats = await manager.get_session_stats()
            print(f"Session stats: {stats}")
            
            # Get client info
            client_info = await manager.get_client_info("client1")
            print(f"Client1 info: {client_info}")
            
            # Wait for expiration
            print("Waiting for session expiration...")
            await asyncio.sleep(6)
            
            # Check expired sessions
            expired_count = await manager.cleanup_expired_sessions()
            print(f"Expired sessions: {expired_count}")
            
            # Final stats
            final_stats = await manager.get_session_stats()
            print(f"Final stats: {final_stats}")
            
        finally:
            await manager.stop()
    
    asyncio.run(test_session_manager())