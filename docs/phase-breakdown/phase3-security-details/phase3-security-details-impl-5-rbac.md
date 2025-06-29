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

### 5. Role-Based Access Controls {#rbac}

**Step 1: RBAC Implementation**

Create `snowflake_mcp_server/security/rbac.py`:

```python
"""Role-based access control system."""

import asyncio
import logging
from typing import Dict, Any, List, Set, Optional
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta

from .authentication import Permission

logger = logging.getLogger(__name__)


class Role(Enum):
    """Predefined roles."""
    ADMIN = "admin"
    POWER_USER = "power_user"
    ANALYST = "analyst"
    READ_ONLY = "read_only"
    GUEST = "guest"


@dataclass
class RoleDefinition:
    """Role definition with permissions."""
    role: Role
    permissions: Set[Permission]
    description: str
    max_queries_per_hour: int = 100
    max_data_bytes_per_day: int = 1073741824  # 1GB
    allowed_databases: Optional[List[str]] = None
    allowed_schemas: Optional[List[str]] = None
    
    def has_permission(self, permission: Permission) -> bool:
        """Check if role has specific permission."""
        return permission in self.permissions
    
    def can_access_database(self, database: str) -> bool:
        """Check if role can access database."""
        if self.allowed_databases is None:
            return True  # No restrictions
        return database.upper() in [db.upper() for db in self.allowed_databases]
    
    def can_access_schema(self, schema: str) -> bool:
        """Check if role can access schema."""
        if self.allowed_schemas is None:
            return True  # No restrictions
        return schema.upper() in [s.upper() for s in self.allowed_schemas]


class RBACManager:
    """Role-based access control manager."""
    
    def __init__(self):
        self._role_definitions = self._create_default_roles()
        self._client_roles: Dict[str, Set[Role]] = {}
        self._custom_permissions: Dict[str, Set[Permission]] = {}
        self._lock = asyncio.Lock()
    
    def _create_default_roles(self) -> Dict[Role, RoleDefinition]:
        """Create default role definitions."""
        
        return {
            Role.ADMIN: RoleDefinition(
                role=Role.ADMIN,
                permissions={
                    Permission.READ_DATABASES,
                    Permission.READ_TABLES,
                    Permission.READ_VIEWS,
                    Permission.EXECUTE_QUERIES,
                    Permission.ADMIN_OPERATIONS,
                    Permission.HEALTH_CHECK
                },
                description="Full administrative access",
                max_queries_per_hour=1000,
                max_data_bytes_per_day=10737418240  # 10GB
            ),
            
            Role.POWER_USER: RoleDefinition(
                role=Role.POWER_USER,
                permissions={
                    Permission.READ_DATABASES,
                    Permission.READ_TABLES,
                    Permission.READ_VIEWS,
                    Permission.EXECUTE_QUERIES,
                    Permission.HEALTH_CHECK
                },
                description="Advanced user with query access",
                max_queries_per_hour=500,
                max_data_bytes_per_day=5368709120  # 5GB
            ),
            
            Role.ANALYST: RoleDefinition(
                role=Role.ANALYST,
                permissions={
                    Permission.READ_DATABASES,
                    Permission.READ_TABLES,
                    Permission.READ_VIEWS,
                    Permission.EXECUTE_QUERIES
                },
                description="Data analyst with limited query access",
                max_queries_per_hour=200,
                max_data_bytes_per_day=2147483648  # 2GB
            ),
            
            Role.READ_ONLY: RoleDefinition(
                role=Role.READ_ONLY,
                permissions={
                    Permission.READ_DATABASES,
                    Permission.READ_TABLES,
                    Permission.READ_VIEWS
                },
                description="Read-only access to metadata",
                max_queries_per_hour=50,
                max_data_bytes_per_day=536870912  # 512MB
            ),
            
            Role.GUEST: RoleDefinition(
                role=Role.GUEST,
                permissions={
                    Permission.HEALTH_CHECK
                },
                description="Limited guest access",
                max_queries_per_hour=10,
                max_data_bytes_per_day=104857600  # 100MB
            )
        }
    
    async def assign_role(self, client_id: str, role: Role) -> None:
        """Assign role to client."""
        async with self._lock:
            if client_id not in self._client_roles:
                self._client_roles[client_id] = set()
            
            self._client_roles[client_id].add(role)
            logger.info(f"Assigned role {role.value} to client {client_id}")
    
    async def revoke_role(self, client_id: str, role: Role) -> None:
        """Revoke role from client."""
        async with self._lock:
            if client_id in self._client_roles:
                self._client_roles[client_id].discard(role)
                logger.info(f"Revoked role {role.value} from client {client_id}")
    
    async def get_client_roles(self, client_id: str) -> Set[Role]:
        """Get roles assigned to client."""
        async with self._lock:
            return self._client_roles.get(client_id, set())
    
    async def get_client_permissions(self, client_id: str) -> Set[Permission]:
        """Get effective permissions for client."""
        async with self._lock:
            permissions = set()
            
            # Get permissions from roles
            client_roles = self._client_roles.get(client_id, set())
            for role in client_roles:
                if role in self._role_definitions:
                    permissions.update(self._role_definitions[role].permissions)
            
            # Add custom permissions
            custom_perms = self._custom_permissions.get(client_id, set())
            permissions.update(custom_perms)
            
            return permissions
    
    async def check_permission(self, client_id: str, permission: Permission) -> bool:
        """Check if client has specific permission."""
        client_permissions = await self.get_client_permissions(client_id)
        return permission in client_permissions
    
    async def check_database_access(self, client_id: str, database: str) -> bool:
        """Check if client can access database."""
        async with self._lock:
            client_roles = self._client_roles.get(client_id, set())
            
            # Check all client roles
            for role in client_roles:
                if role in self._role_definitions:
                    role_def = self._role_definitions[role]
                    if role_def.can_access_database(database):
                        return True
            
            return False
    
    async def check_schema_access(self, client_id: str, schema: str) -> bool:
        """Check if client can access schema."""
        async with self._lock:
            client_roles = self._client_roles.get(client_id, set())
            
            # Check all client roles
            for role in client_roles:
                if role in self._role_definitions:
                    role_def = self._role_definitions[role]
                    if role_def.can_access_schema(schema):
                        return True
            
            return False
    
    async def get_client_limits(self, client_id: str) -> Dict[str, int]:
        """Get resource limits