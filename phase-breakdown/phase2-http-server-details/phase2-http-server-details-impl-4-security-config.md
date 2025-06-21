# Phase 2: HTTP/WebSocket Server Implementation Details

## Context & Overview

The current Snowflake MCP server only supports stdio communication mode, requiring it to run in a terminal window and limiting it to single client connections. To enable daemon mode with multi-client support, we need to implement HTTP and WebSocket transport layers following the MCP protocol specification.

**Current Limitations:**
- Only stdio transport available (`stdio_server()` in `main.py`)
- Requires terminal window to remain open
- Cannot handle multiple simultaneous client connections
- No health check endpoints for monitoring
- No graceful shutdown capabilities

**Target Architecture:**
- FastAPI-based HTTP server with WebSocket support
- MCP protocol compliance over HTTP/WebSocket transports
- Health check and status endpoints
- Graceful shutdown with connection cleanup
- CORS and security headers for web clients

## Dependencies Required

Add to `pyproject.toml`:
```toml
dependencies = [
    # Existing dependencies...
    "fastapi>=0.104.0",  # Modern async web framework
    "uvicorn>=0.24.0",   # ASGI server implementation  
    "websockets>=12.0",  # WebSocket support
    "pydantic>=2.4.2",   # Data validation (already present)
    "python-multipart>=0.0.6",  # Form data parsing
    "httpx>=0.25.0",     # HTTP client for testing
]

[project.optional-dependencies]
server = [
    "gunicorn>=21.2.0",  # Production WSGI server
    "uvloop>=0.19.0",    # Fast event loop (Unix only)
]
```

## Implementation Plan

### 4. Security Configuration {#security-config}

**Step 4: Security Headers and CORS**

Create `snowflake_mcp_server/transports/security.py`:

```python
"""Security middleware and configuration."""

import logging
from typing import Callable, Optional
from fastapi import Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        
        return response


class APIKeyAuth(HTTPBearer):
    """API key authentication for HTTP endpoints."""
    
    def __init__(self, api_key: Optional[str] = None):
        super().__init__(auto_error=False)
        self.api_key = api_key
    
    async def __call__(self, request: Request) -> Optional[HTTPAuthorizationCredentials]:
        if not self.api_key:
            return None  # No authentication required
        
        credentials = await super().__call__(request)
        
        if not credentials or credentials.credentials != self.api_key:
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        return credentials


def configure_security(app: FastAPI, api_key: Optional[str] = None) -> None:
    """Configure security for FastAPI app."""
    
    # Add security headers middleware
    app.add_middleware(SecurityHeadersMiddleware)
    
    # Configure CORS based on environment
    from fastapi.middleware.cors import CORSMiddleware
    
    # In production, configure specific origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: Configure for production
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    
    # Add API key authentication if configured
    if api_key:
        auth = APIKeyAuth(api_key)
        
        # Protect MCP endpoints
        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            if request.url.path.startswith("/mcp/"):
                await auth(request)
            
            return await call_next(request)
```

