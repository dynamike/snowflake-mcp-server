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

### 1. API Authentication System {#api-auth}

**Step 1: Multi-Layer Authentication Framework**

Create `snowflake_mcp_server/security/authentication.py`:

```python
"""Multi-layer authentication system for MCP server."""

import asyncio
import logging
import secrets
import time
from typing import Dict, Any, Optional, List, Union
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
import hashlib
import hmac

import jwt
import bcrypt
from fastapi import HTTPException, status, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext

logger = logging.getLogger(__name__)


class AuthMethod(Enum):
    """Authentication methods."""
    API_KEY = "api_key"
    JWT_TOKEN = "jwt_token"
    BASIC_AUTH = "basic_auth"
    MUTUAL_TLS = "mutual_tls"


class Permission(Enum):
    """Permission types."""
    READ_DATABASES = "read_databases"
    READ_TABLES = "read_tables"
    READ_VIEWS = "read_views"
    EXECUTE_QUERIES = "execute_queries"
    ADMIN_OPERATIONS = "admin_operations"
    HEALTH_CHECK = "health_check"


@dataclass
class AuthToken:
    """Authentication token information."""
    token_id: str
    client_id: str
    permissions: List[Permission]
    expires_at: datetime
    created_at: datetime
    last_used: Optional[datetime] = None
    usage_count: int = 0
    
    def is_expired(self) -> bool:
        """Check if token is expired."""
        return datetime.now() >= self.expires_at
    
    def has_permission(self, permission: Permission) -> bool:
        """Check if token has specific permission."""
        return permission in self.permissions
    
    def use_token(self) -> None:
        """Record token usage."""
        self.last_used = datetime.now()
        self.usage_count += 1


@dataclass
class APIKey:
    """API key configuration."""
    key_id: str
    client_id: str
    key_hash: str  # Hashed API key
    permissions: List[Permission]
    expires_at: Optional[datetime] = None
    created_at: datetime = None
    last_used: Optional[datetime] = None
    usage_count: int = 0
    is_active: bool = True
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
    
    def is_expired(self) -> bool:
        """Check if API key is expired."""
        if self.expires_at is None:
            return False
        return datetime.now() >= self.expires_at
    
    def verify_key(self, provided_key: str) -> bool:
        """Verify provided key against stored hash."""
        return bcrypt.checkpw(provided_key.encode(), self.key_hash.encode())


class JWTManager:
    """JWT token management."""
    
    def __init__(self, secret_key: str, algorithm: str = "HS256"):
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    
    def create_access_token(
        self,
        client_id: str,
        permissions: List[Permission],
        expires_delta: timedelta = timedelta(hours=24)
    ) -> str:
        """Create JWT access token."""
        
        expires_at = datetime.utcnow() + expires_delta
        token_id = secrets.token_urlsafe(16)
        
        payload = {
            "sub": client_id,
            "jti": token_id,
            "permissions": [p.value for p in permissions],
            "exp": expires_at,
            "iat": datetime.utcnow(),
            "iss": "snowflake-mcp-server",
            "aud": "mcp-clients"
        }
        
        token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
        logger.info(f"Created JWT token for client {client_id} (expires: {expires_at})")
        
        return token
    
    def verify_token(self, token: str) -> AuthToken:
        """Verify and decode JWT token."""
        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                audience="mcp-clients",
                issuer="snowflake-mcp-server"
            )
            
            permissions = [Permission(p) for p in payload.get("permissions", [])]
            
            auth_token = AuthToken(
                token_id=payload["jti"],
                client_id=payload["sub"],
                permissions=permissions,
                expires_at=datetime.fromtimestamp(payload["exp"]),
                created_at=datetime.fromtimestamp(payload["iat"])
            )
            
            if auth_token.is_expired():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has expired"
                )
            
            return auth_token
            
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired"
            )
        except jwt.InvalidTokenError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {str(e)}"
            )


class APIKeyManager:
    """API key management."""
    
    def __init__(self):
        self._api_keys: Dict[str, APIKey] = {}
        self._client_keys: Dict[str, List[str]] = {}  # client_id -> key_ids
        self._lock = asyncio.Lock()
    
    async def create_api_key(
        self,
        client_id: str,
        permissions: List[Permission],
        expires_in: Optional[timedelta] = None
    ) -> tuple[str, str]:
        """
        Create new API key.
        
        Returns:
            tuple: (key_id, raw_api_key)
        """
        
        async with self._lock:
            # Generate key components
            key_id = f"mcp_{secrets.token_urlsafe(8)}"
            raw_key = secrets.token_urlsafe(32)
            
            # Hash the key for storage
            key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()
            
            # Calculate expiration
            expires_at = None
            if expires_in:
                expires_at = datetime.now() + expires_in
            
            # Create API key record
            api_key = APIKey(
                key_id=key_id,
                client_id=client_id,
                key_hash=key_hash,
                permissions=permissions,
                expires_at=expires_at
            )
            
            # Store key
            self._api_keys[key_id] = api_key
            
            if client_id not in self._client_keys:
                self._client_keys[client_id] = []
            self._client_keys[client_id].append(key_id)
            
            logger.info(f"Created API key {key_id} for client {client_id}")
            
            # Return key_id and raw key (only time raw key is available)
            return key_id, f"{key_id}.{raw_key}"
    
    async def verify_api_key(self, provided_key: str) -> APIKey:
        """Verify API key and return key info."""
        
        # Parse key format: key_id.raw_key
        try:
            key_id, raw_key = provided_key.split(".", 1)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key format"
            )
        
        async with self._lock:
            if key_id not in self._api_keys:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API key not found"
                )
            
            api_key = self._api_keys[key_id]
            
            # Check if key is active
            if not api_key.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API key has been deactivated"
                )
            
            # Check expiration
            if api_key.is_expired():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API key has expired"
                )
            
            # Verify key
            if not api_key.verify_key(raw_key):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key"
                )
            
            # Update usage
            api_key.last_used = datetime.now()
            api_key.usage_count += 1
            
            return api_key
    
    async def revoke_api_key(self, key_id: str) -> bool:
        """Revoke API key."""
        async with self._lock:
            if key_id in self._api_keys:
                self._api_keys[key_id].is_active = False
                logger.info(f"Revoked API key {key_id}")
                return True
            return False
    
    async def list_client_keys(self, client_id: str) -> List[Dict[str, Any]]:
        """List API keys for client."""
        async with self._lock:
            key_ids = self._client_keys.get(client_id, [])
            keys_info = []
            
            for key_id in key_ids:
                if key_id in self._api_keys:
                    api_key = self._api_keys[key_id]
                    keys_info.append({
                        "key_id": key_id,
                        "permissions": [p.value for p in api_key.permissions],
                        "created_at": api_key.created_at.isoformat(),
                        "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
                        "last_used": api_key.last_used.isoformat() if api_key.last_used else None,
                        "usage_count": api_key.usage_count,
                        "is_active": api_key.is_active
                    })
            
            return keys_info


class AuthenticationManager:
    """Main authentication manager."""
    
    def __init__(self, jwt_secret: str):
        self.jwt_manager = JWTManager(jwt_secret)
        self.api_key_manager = APIKeyManager()
        self._security = HTTPBearer(auto_error=False)
    
    async def authenticate_request(
        self,
        request: Request,
        required_permissions: List[Permission] = None
    ) -> tuple[str, List[Permission]]:
        """
        Authenticate request and return client_id and permissions.
        
        Supports multiple authentication methods:
        1. Bearer token (JWT)
        2. API key in Authorization header
        3. API key in query parameter
        """
        
        # Try Bearer token first
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix
            
            try:
                # Try JWT token
                auth_token = self.jwt_manager.verify_token(token)
                auth_token.use_token()
                
                if required_permissions:
                    for perm in required_permissions:
                        if not auth_token.has_permission(perm):
                            raise HTTPException(
                                status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Missing required permission: {perm.value}"
                            )
                
                return auth_token.client_id, auth_token.permissions
                
            except HTTPException:
                # Try API key format
                try:
                    api_key = await self.api_key_manager.verify_api_key(token)
                    
                    if required_permissions:
                        for perm in required_permissions:
                            if perm not in api_key.permissions:
                                raise HTTPException(
                                    status_code=status.HTTP_403_FORBIDDEN,
                                    detail=f"Missing required permission: {perm.value}"
                                )
                    
                    return api_key.client_id, api_key.permissions
                    
                except HTTPException:
                    pass
        
        # Try API key in query parameter
        api_key_param = request.query_params.get("api_key")
        if api_key_param:
            try:
                api_key = await self.api_key_manager.verify_api_key(api_key_param)
                
                if required_permissions:
                    for perm in required_permissions:
                        if perm not in api_key.permissions:
                            raise HTTPException(
                                status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Missing required permission: {perm.value}"
                            )
                
                return api_key.client_id, api_key.permissions
                
            except HTTPException:
                pass
        
        # No valid authentication found
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid authentication required",
            headers={"WWW-Authenticate": "Bearer"}
        )


# Global authentication manager
auth_manager: Optional[AuthenticationManager] = None


def get_auth_manager() -> AuthenticationManager:
    """Get global authentication manager."""
    if auth_manager is None:
        raise RuntimeError("Authentication manager not initialized")
    return auth_manager


def initialize_auth_manager(jwt_secret: str) -> None:
    """Initialize global authentication manager."""
    global auth_manager
    auth_manager = AuthenticationManager(jwt_secret)


# Authentication dependency for FastAPI
async def require_auth(
    request: Request,
    permissions: List[Permission] = None
) -> tuple[str, List[Permission]]:
    """FastAPI dependency for authentication."""
    return await get_auth_manager().authenticate_request(request, permissions)


# Permission-specific dependencies
async def require_read_access(request: Request) -> tuple[str, List[Permission]]:
    """Require read permissions."""
    return await require_auth(request, [Permission.READ_DATABASES, Permission.READ_VIEWS])


async def require_query_access(request: Request) -> tuple[str, List[Permission]]:
    """Require query execution permissions."""
    return await require_auth(request, [Permission.EXECUTE_QUERIES])


async def require_admin_access(request: Request) -> tuple[str, List[Permission]]:
    """Require admin permissions."""
    return await require_auth(request, [Permission.ADMIN_OPERATIONS])
```

