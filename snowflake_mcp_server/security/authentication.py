"""API key authentication and user management."""

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ..config import get_config
from ..monitoring import get_audit_logger, get_metrics, get_structured_logger

logger = logging.getLogger(__name__)


class AuthenticationMethod(Enum):
    """Authentication methods supported."""
    API_KEY = "api_key"
    BEARER_TOKEN = "bearer_token"
    BASIC_AUTH = "basic_auth"
    OAUTH2 = "oauth2"
    MUTUAL_TLS = "mutual_tls"


class UserStatus(Enum):
    """User account status."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    EXPIRED = "expired"


@dataclass
class APIKey:
    """Represents an API key."""
    
    key_id: str
    user_id: str
    key_hash: str  # Hashed version of the actual key
    name: str
    scopes: List[str]
    created_at: datetime
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    usage_count: int = 0
    is_active: bool = True
    rate_limit_override: Optional[Dict[str, int]] = None
    ip_whitelist: List[str] = field(default_factory=list)
    
    def is_expired(self) -> bool:
        """Check if the API key is expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at
    
    def is_valid(self) -> bool:
        """Check if the API key is valid for use."""
        return self.is_active and not self.is_expired()
    
    def has_scope(self, scope: str) -> bool:
        """Check if the API key has a specific scope."""
        return scope in self.scopes or "*" in self.scopes
    
    def is_ip_allowed(self, ip_address: str) -> bool:
        """Check if the IP address is allowed."""
        if not self.ip_whitelist:
            return True  # No restrictions
        return ip_address in self.ip_whitelist
    
    def to_dict(self, include_sensitive: bool = False) -> Dict[str, Any]:
        """Convert to dictionary format."""
        data = {
            "key_id": self.key_id,
            "user_id": self.user_id,
            "name": self.name,
            "scopes": self.scopes,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "usage_count": self.usage_count,
            "is_active": self.is_active,
            "is_expired": self.is_expired(),
            "ip_whitelist": self.ip_whitelist,
        }
        
        if include_sensitive:
            data["key_hash"] = self.key_hash
            data["rate_limit_override"] = self.rate_limit_override
        
        return data


@dataclass
class User:
    """Represents a user account."""
    
    user_id: str
    username: str
    email: str
    status: UserStatus
    roles: List[str]
    created_at: datetime
    last_login_at: Optional[datetime] = None
    login_count: int = 0
    failed_login_attempts: int = 0
    last_failed_login_at: Optional[datetime] = None
    password_changed_at: Optional[datetime] = None
    two_factor_enabled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def is_active(self) -> bool:
        """Check if the user account is active."""
        return self.status == UserStatus.ACTIVE
    
    def is_locked_out(self, max_attempts: int = 5, lockout_duration: int = 900) -> bool:
        """Check if the user is locked out due to failed login attempts."""
        if self.failed_login_attempts < max_attempts:
            return False
        
        if self.last_failed_login_at is None:
            return False
        
        lockout_expires = self.last_failed_login_at + timedelta(seconds=lockout_duration)
        return datetime.now(timezone.utc) < lockout_expires
    
    def has_role(self, role: str) -> bool:
        """Check if the user has a specific role."""
        return role in self.roles
    
    def to_dict(self, include_sensitive: bool = False) -> Dict[str, Any]:
        """Convert to dictionary format."""
        data = {
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email,
            "status": self.status.value,
            "roles": self.roles,
            "created_at": self.created_at.isoformat(),
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "login_count": self.login_count,
            "two_factor_enabled": self.two_factor_enabled,
            "metadata": self.metadata,
        }
        
        if include_sensitive:
            data.update({
                "failed_login_attempts": self.failed_login_attempts,
                "last_failed_login_at": self.last_failed_login_at.isoformat() if self.last_failed_login_at else None,
                "password_changed_at": self.password_changed_at.isoformat() if self.password_changed_at else None,
            })
        
        return data


class AuthenticationError(Exception):
    """Raised when authentication fails."""
    
    def __init__(self, message: str, error_code: str = "AUTH_FAILED", 
                 retry_after: Optional[int] = None):
        super().__init__(message)
        self.error_code = error_code
        self.retry_after = retry_after


class AuthenticationManager:
    """Manages authentication for the MCP server."""
    
    def __init__(self):
        self.config = get_config()
        self.logger = get_structured_logger().get_logger("auth_manager")
        self.audit_logger = get_audit_logger()
        self.metrics = get_metrics()
        
        # In-memory storage (in production, this would be a database)
        self.users: Dict[str, User] = {}
        self.api_keys: Dict[str, APIKey] = {}
        self.api_keys_by_user: Dict[str, List[str]] = {}
        
        # Authentication attempts tracking
        self.auth_attempts: Dict[str, List[datetime]] = {}
        
        # Rate limiting for authentication
        self.auth_rate_limits = {
            "max_attempts_per_minute": getattr(self.config.security, 'max_auth_attempts_per_minute', 10),
            "max_attempts_per_hour": getattr(self.config.security, 'max_auth_attempts_per_hour', 100),
            "lockout_duration": getattr(self.config.security, 'auth_lockout_duration', 900),  # 15 minutes
        }
        
        # Initialize with default admin user if configured
        self._init_default_users()
    
    def _init_default_users(self):
        """Initialize default users from configuration."""
        # Create default admin user if configured
        admin_key = getattr(self.config.security, 'default_admin_api_key', None)
        if admin_key:
            admin_user = User(
                user_id="admin",
                username="admin",
                email="admin@localhost",
                status=UserStatus.ACTIVE,
                roles=["admin", "user"],
                created_at=datetime.now(timezone.utc)
            )
            
            self.users["admin"] = admin_user
            
            # Create API key for admin
            api_key = APIKey(
                key_id="admin_key",
                user_id="admin",
                key_hash=self._hash_api_key(admin_key),
                name="Default Admin Key",
                scopes=["*"],
                created_at=datetime.now(timezone.utc)
            )
            
            self.api_keys[admin_key] = api_key
            self.api_keys_by_user["admin"] = [admin_key]
            
            self.logger.info("Created default admin user and API key")
    
    def _hash_api_key(self, api_key: str) -> str:
        """Hash an API key for secure storage."""
        salt = getattr(self.config.security, 'api_key_salt', 'default_salt').encode()
        return hashlib.pbkdf2_hex(api_key.encode(), salt, 100000)
    
    def _verify_api_key_hash(self, api_key: str, key_hash: str) -> bool:
        """Verify an API key against its hash."""
        return hmac.compare_digest(self._hash_api_key(api_key), key_hash)
    
    def generate_api_key(self) -> str:
        """Generate a new API key."""
        # Format: mcp_<16_random_chars>_<timestamp>
        random_part = secrets.token_urlsafe(16)
        timestamp = int(time.time())
        return f"mcp_{random_part}_{timestamp}"
    
    async def create_user(self, username: str, email: str, roles: List[str], 
                         metadata: Optional[Dict[str, Any]] = None) -> User:
        """Create a new user."""
        user_id = f"user_{secrets.token_urlsafe(8)}"
        
        user = User(
            user_id=user_id,
            username=username,
            email=email,
            status=UserStatus.ACTIVE,
            roles=roles,
            created_at=datetime.now(timezone.utc),
            metadata=metadata or {}
        )
        
        self.users[user_id] = user
        self.api_keys_by_user[user_id] = []
        
        self.logger.info(
            f"Created user {username}",
            user_id=user_id,
            username=username,
            email=email,
            roles=roles
        )
        
        self.audit_logger.log_authentication(
            user_id=user_id,
            client_id="system",
            success=True,
            method="user_creation"
        )
        
        return user
    
    async def create_api_key(self, user_id: str, name: str, scopes: List[str],
                           expires_in_days: Optional[int] = None,
                           ip_whitelist: Optional[List[str]] = None,
                           rate_limit_override: Optional[Dict[str, int]] = None) -> Tuple[str, APIKey]:
        """Create a new API key for a user."""
        if user_id not in self.users:
            raise AuthenticationError(f"User {user_id} not found", "USER_NOT_FOUND")
        
        api_key = self.generate_api_key()
        key_id = f"key_{secrets.token_urlsafe(8)}"
        
        expires_at = None
        if expires_in_days:
            expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        
        api_key_obj = APIKey(
            key_id=key_id,
            user_id=user_id,
            key_hash=self._hash_api_key(api_key),
            name=name,
            scopes=scopes,
            created_at=datetime.now(timezone.utc),
            expires_at=expires_at,
            ip_whitelist=ip_whitelist or [],
            rate_limit_override=rate_limit_override
        )
        
        self.api_keys[api_key] = api_key_obj
        self.api_keys_by_user[user_id].append(api_key)
        
        self.logger.info(
            f"Created API key for user {user_id}",
            user_id=user_id,
            key_id=key_id,
            name=name,
            scopes=scopes,
            expires_at=expires_at.isoformat() if expires_at else None
        )
        
        return api_key, api_key_obj
    
    async def authenticate_api_key(self, api_key: str, client_ip: Optional[str] = None,
                                 required_scope: Optional[str] = None) -> Tuple[User, APIKey]:
        """Authenticate using an API key."""
        start_time = time.time()
        
        try:
            # Check rate limiting first
            await self._check_auth_rate_limit(api_key, client_ip)
            
            # Find API key
            api_key_obj = None
            for stored_key, key_obj in self.api_keys.items():
                if self._verify_api_key_hash(api_key, key_obj.key_hash):
                    api_key_obj = key_obj
                    break
            
            if not api_key_obj:
                await self._record_failed_auth(api_key, "invalid_key", client_ip)
                raise AuthenticationError("Invalid API key", "INVALID_API_KEY")
            
            # Check if API key is valid
            if not api_key_obj.is_valid():
                await self._record_failed_auth(api_key, "expired_key", client_ip)
                if api_key_obj.is_expired():
                    raise AuthenticationError("API key expired", "API_KEY_EXPIRED")
                else:
                    raise AuthenticationError("API key inactive", "API_KEY_INACTIVE")
            
            # Check IP whitelist
            if client_ip and not api_key_obj.is_ip_allowed(client_ip):
                await self._record_failed_auth(api_key, "ip_not_allowed", client_ip)
                raise AuthenticationError("IP address not allowed", "IP_NOT_ALLOWED")
            
            # Check required scope
            if required_scope and not api_key_obj.has_scope(required_scope):
                await self._record_failed_auth(api_key, "insufficient_scope", client_ip)
                raise AuthenticationError(f"Insufficient scope: {required_scope}", "INSUFFICIENT_SCOPE")
            
            # Get user
            user = self.users.get(api_key_obj.user_id)
            if not user:
                await self._record_failed_auth(api_key, "user_not_found", client_ip)
                raise AuthenticationError("User not found", "USER_NOT_FOUND")
            
            # Check if user is active
            if not user.is_active():
                await self._record_failed_auth(api_key, "user_inactive", client_ip)
                raise AuthenticationError("User account inactive", "USER_INACTIVE")
            
            # Check user lockout
            if user.is_locked_out():
                await self._record_failed_auth(api_key, "user_locked", client_ip)
                raise AuthenticationError("User account locked", "USER_LOCKED", 
                                        retry_after=self.auth_rate_limits["lockout_duration"])
            
            # Update API key usage
            api_key_obj.last_used_at = datetime.now(timezone.utc)
            api_key_obj.usage_count += 1
            
            # Update user login info
            user.last_login_at = datetime.now(timezone.utc)
            user.login_count += 1
            user.failed_login_attempts = 0  # Reset failed attempts on successful login
            
            # Record successful authentication
            duration = time.time() - start_time
            
            self.metrics.record_request(
                client_id=user.user_id,
                tool_name="authenticate",
                duration=duration,
                status="success"
            )
            
            self.audit_logger.log_authentication(
                user_id=user.user_id,
                client_id=user.user_id,
                success=True,
                method=AuthenticationMethod.API_KEY.value,
                ip_address=client_ip
            )
            
            self.logger.info(
                f"Successful authentication for user {user.username}",
                user_id=user.user_id,
                username=user.username,
                key_id=api_key_obj.key_id,
                client_ip=client_ip,
                scopes=api_key_obj.scopes,
                event_type="authentication_success"
            )
            
            return user, api_key_obj
            
        except AuthenticationError:
            # Record failed authentication metrics
            duration = time.time() - start_time
            self.metrics.record_request(
                client_id="unknown",
                tool_name="authenticate",
                duration=duration,
                status="error"
            )
            raise
    
    async def _check_auth_rate_limit(self, identifier: str, client_ip: Optional[str]):
        """Check authentication rate limits."""
        now = datetime.now(timezone.utc)
        
        # Clean old attempts
        for key in list(self.auth_attempts.keys()):
            self.auth_attempts[key] = [
                attempt for attempt in self.auth_attempts[key]
                if now - attempt < timedelta(hours=1)
            ]
            if not self.auth_attempts[key]:
                del self.auth_attempts[key]
        
        # Check rate limits
        for limit_key in [identifier, client_ip]:
            if not limit_key:
                continue
                
            attempts = self.auth_attempts.get(limit_key, [])
            
            # Check per-minute limit
            recent_attempts = [
                attempt for attempt in attempts
                if now - attempt < timedelta(minutes=1)
            ]
            
            if len(recent_attempts) >= self.auth_rate_limits["max_attempts_per_minute"]:
                raise AuthenticationError(
                    "Too many authentication attempts", 
                    "RATE_LIMITED", 
                    retry_after=60
                )
            
            # Check per-hour limit
            if len(attempts) >= self.auth_rate_limits["max_attempts_per_hour"]:
                raise AuthenticationError(
                    "Too many authentication attempts", 
                    "RATE_LIMITED", 
                    retry_after=3600
                )
    
    async def _record_failed_auth(self, identifier: str, reason: str, client_ip: Optional[str]):
        """Record failed authentication attempt."""
        now = datetime.now(timezone.utc)
        
        # Record in rate limiting tracker
        for key in [identifier, client_ip]:
            if key:
                if key not in self.auth_attempts:
                    self.auth_attempts[key] = []
                self.auth_attempts[key].append(now)
        
        # Update user failed attempts if we can identify the user
        api_key_obj = None
        for stored_key, key_obj in self.api_keys.items():
            if self._verify_api_key_hash(identifier, key_obj.key_hash):
                api_key_obj = key_obj
                break
        
        if api_key_obj:
            user = self.users.get(api_key_obj.user_id)
            if user:
                user.failed_login_attempts += 1
                user.last_failed_login_at = now
        
        # Log failed authentication
        self.audit_logger.log_authentication(
            user_id=api_key_obj.user_id if api_key_obj else "unknown",
            client_id="unknown",
            success=False,
            method=AuthenticationMethod.API_KEY.value,
            ip_address=client_ip
        )
        
        self.logger.warning(
            "Failed authentication attempt",
            reason=reason,
            client_ip=client_ip,
            user_id=api_key_obj.user_id if api_key_obj else "unknown",
            event_type="authentication_failed"
        )
    
    async def revoke_api_key(self, api_key: str, user_id: Optional[str] = None) -> bool:
        """Revoke an API key."""
        api_key_obj = self.api_keys.get(api_key)
        if not api_key_obj:
            return False
        
        # Check user permission if specified
        if user_id and api_key_obj.user_id != user_id:
            raise AuthenticationError("Not authorized to revoke this key", "UNAUTHORIZED")
        
        # Remove from storage
        del self.api_keys[api_key]
        if api_key_obj.user_id in self.api_keys_by_user:
            self.api_keys_by_user[api_key_obj.user_id].remove(api_key)
        
        self.logger.info(
            "Revoked API key",
            user_id=api_key_obj.user_id,
            key_id=api_key_obj.key_id,
            name=api_key_obj.name
        )
        
        return True
    
    async def list_api_keys(self, user_id: str) -> List[Dict[str, Any]]:
        """List API keys for a user."""
        if user_id not in self.users:
            raise AuthenticationError(f"User {user_id} not found", "USER_NOT_FOUND")
        
        user_keys = self.api_keys_by_user.get(user_id, [])
        return [
            self.api_keys[key].to_dict() for key in user_keys 
            if key in self.api_keys
        ]
    
    async def get_user(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        return self.users.get(user_id)
    
    async def update_user_status(self, user_id: str, status: UserStatus) -> bool:
        """Update user status."""
        user = self.users.get(user_id)
        if not user:
            return False
        
        old_status = user.status
        user.status = status
        
        self.logger.info(
            "Updated user status",
            user_id=user_id,
            username=user.username,
            old_status=old_status.value,
            new_status=status.value
        )
        
        return True
    
    async def get_authentication_stats(self) -> Dict[str, Any]:
        """Get authentication statistics."""
        now = datetime.now(timezone.utc)
        
        # Count users by status
        user_stats = {"total": len(self.users)}
        for status in UserStatus:
            user_stats[status.value] = sum(
                1 for user in self.users.values() 
                if user.status == status
            )
        
        # Count API keys
        total_keys = len(self.api_keys)
        active_keys = sum(1 for key in self.api_keys.values() if key.is_valid())
        expired_keys = sum(1 for key in self.api_keys.values() if key.is_expired())
        
        # Recent authentication attempts
        recent_attempts = []
        for attempts in self.auth_attempts.values():
            recent_attempts.extend([
                attempt for attempt in attempts
                if now - attempt < timedelta(hours=24)
            ])
        
        return {
            "users": user_stats,
            "api_keys": {
                "total": total_keys,
                "active": active_keys,
                "expired": expired_keys,
                "inactive": total_keys - active_keys - expired_keys,
            },
            "authentication_attempts": {
                "last_24_hours": len(recent_attempts),
                "rate_limited_identifiers": len(self.auth_attempts),
            },
            "timestamp": now.isoformat(),
        }


# Global authentication manager instance
_auth_manager: Optional[AuthenticationManager] = None


def get_auth_manager() -> AuthenticationManager:
    """Get the global authentication manager instance."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthenticationManager()
    return _auth_manager


def require_authentication(required_scope: Optional[str] = None):
    """Decorator to require authentication for API endpoints."""
    def decorator(func):
        from functools import wraps
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract authentication info from request
            # This would typically come from HTTP headers
            api_key = kwargs.get('api_key') or kwargs.get('authorization')
            client_ip = kwargs.get('client_ip')
            
            if not api_key:
                raise AuthenticationError("No API key provided", "NO_API_KEY")
            
            # Remove "Bearer " prefix if present
            if api_key.startswith("Bearer "):
                api_key = api_key[7:]
            
            auth_manager = get_auth_manager()
            user, api_key_obj = await auth_manager.authenticate_api_key(
                api_key, client_ip, required_scope
            )
            
            # Add user info to kwargs
            kwargs['authenticated_user'] = user
            kwargs['api_key_obj'] = api_key_obj
            kwargs['user_id'] = user.user_id
            
            return await func(*args, **kwargs)
        
        return wrapper
    
    return decorator


# FastAPI endpoints for authentication management
async def create_api_key_endpoint(user_id: str, name: str, scopes: List[str],
                                expires_in_days: Optional[int] = None) -> Dict[str, Any]:
    """API endpoint to create an API key."""
    auth_manager = get_auth_manager()
    
    try:
        api_key, api_key_obj = await auth_manager.create_api_key(
            user_id, name, scopes, expires_in_days
        )
        
        return {
            "success": True,
            "api_key": api_key,  # Only return this once!
            "key_info": api_key_obj.to_dict(),
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


async def revoke_api_key_endpoint(api_key: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """API endpoint to revoke an API key."""
    auth_manager = get_auth_manager()
    
    try:
        success = await auth_manager.revoke_api_key(api_key, user_id)
        return {"success": success}
        
    except Exception as e:
        return {"success": False, "error": str(e)}


async def list_api_keys_endpoint(user_id: str) -> Dict[str, Any]:
    """API endpoint to list user's API keys."""
    auth_manager = get_auth_manager()
    
    try:
        keys = await auth_manager.list_api_keys(user_id)
        return {"api_keys": keys}
        
    except Exception as e:
        return {"success": False, "error": str(e)}


async def get_auth_stats_endpoint() -> Dict[str, Any]:
    """API endpoint to get authentication statistics."""
    auth_manager = get_auth_manager()
    return await auth_manager.get_authentication_stats()


if __name__ == "__main__":
    # Test authentication
    import asyncio
    
    async def test_auth():
        auth_manager = AuthenticationManager()
        
        # Create test user
        user = await auth_manager.create_user(
            username="testuser",
            email="test@example.com",
            roles=["user"]
        )
        
        # Create API key
        api_key, key_obj = await auth_manager.create_api_key(
            user.user_id,
            "Test Key",
            ["read", "write"],
            expires_in_days=30
        )
        
        print(f"Created API key: {api_key}")
        
        # Test authentication
        try:
            auth_user, auth_key = await auth_manager.authenticate_api_key(
                api_key, "127.0.0.1", "read"
            )
            print(f"Authentication successful for: {auth_user.username}")
        except AuthenticationError as e:
            print(f"Authentication failed: {e}")
        
        # Get stats
        stats = await auth_manager.get_authentication_stats()
        print(f"Auth stats: {stats}")
    
    asyncio.run(test_auth())