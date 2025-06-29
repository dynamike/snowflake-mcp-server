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

### 1. FastAPI Server Setup {#fastapi-setup}

**Step 1: Create HTTP/WebSocket MCP Server**

Create `snowflake_mcp_server/transports/http_server.py`:

```python
"""HTTP and WebSocket transport implementation for MCP server."""

import asyncio
import json
import logging
import traceback
from typing import Any, Dict, List, Optional, Set
from datetime import datetime

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from ..main import create_server
from ..utils.request_context import RequestContextManager, request_context
from ..utils.async_pool import get_connection_pool
from ..utils.health_monitor import health_monitor

logger = logging.getLogger(__name__)


class MCPRequest(BaseModel):
    """MCP protocol request model."""
    jsonrpc: str = "2.0"
    id: Optional[str] = None
    method: str
    params: Optional[Dict[str, Any]] = None


class MCPResponse(BaseModel):
    """MCP protocol response model."""
    jsonrpc: str = "2.0"
    id: Optional[str] = None
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None


class MCPError(BaseModel):
    """MCP protocol error model."""
    code: int
    message: str
    data: Optional[Any] = None


class ConnectionManager:
    """Manage WebSocket connections for MCP clients."""
    
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.connection_metadata: Dict[WebSocket, Dict[str, Any]] = {}
        self._connection_counter = 0
    
    async def connect(self, websocket: WebSocket, client_info: Dict[str, Any] = None) -> str:
        """Accept new WebSocket connection."""
        await websocket.accept()
        
        self._connection_counter += 1
        connection_id = f"ws_client_{self._connection_counter}"
        
        self.active_connections.add(websocket)
        self.connection_metadata[websocket] = {
            "connection_id": connection_id,
            "connected_at": datetime.now(),
            "client_info": client_info or {},
            "message_count": 0
        }
        
        logger.info(f"New WebSocket connection: {connection_id}")
        return connection_id
    
    def disconnect(self, websocket: WebSocket) -> None:
        """Remove WebSocket connection."""
        if websocket in self.active_connections:
            metadata = self.connection_metadata.get(websocket, {})
            connection_id = metadata.get("connection_id", "unknown")
            
            self.active_connections.discard(websocket)
            self.connection_metadata.pop(websocket, None)
            
            logger.info(f"WebSocket disconnected: {connection_id}")
    
    async def send_message(self, websocket: WebSocket, message: Dict[str, Any]) -> None:
        """Send message to specific WebSocket connection."""
        try:
            await websocket.send_text(json.dumps(message))
            
            # Update message counter
            if websocket in self.connection_metadata:
                self.connection_metadata[websocket]["message_count"] += 1
                
        except Exception as e:
            logger.error(f"Error sending WebSocket message: {e}")
            self.disconnect(websocket)
    
    def get_connection_stats(self) -> Dict[str, Any]:
        """Get statistics about active connections."""
        return {
            "active_connections": len(self.active_connections),
            "connections": [
                {
                    "connection_id": metadata["connection_id"],
                    "connected_at": metadata["connected_at"].isoformat(),
                    "message_count": metadata["message_count"],
                    "client_info": metadata["client_info"]
                }
                for metadata in self.connection_metadata.values()
            ]
        }


class MCPHttpServer:
    """HTTP server implementing MCP protocol."""
    
    def __init__(self, host: str = "localhost", port: int = 8000):
        self.host = host
        self.port = port
        self.app = FastAPI(
            title="Snowflake MCP Server",
            description="HTTP/WebSocket transport for Snowflake MCP operations",
            version="0.2.0"
        )
        self.mcp_server = create_server()
        self.connection_manager = ConnectionManager()
        self.request_manager = RequestContextManager()
        
        self._setup_middleware()
        self._setup_routes()
    
    def _setup_middleware(self) -> None:
        """Configure FastAPI middleware."""
        # CORS middleware for web clients
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # Configure based on security requirements
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )
        
        # Request logging middleware
        @self.app.middleware("http")
        async def log_requests(request: Request, call_next):
            start_time = datetime.now()
            
            response = await call_next(request)
            
            duration = (datetime.now() - start_time).total_seconds() * 1000
            logger.info(f"HTTP {request.method} {request.url.path} - {response.status_code} - {duration:.2f}ms")
            
            return response
    
    def _setup_routes(self) -> None:
        """Setup FastAPI routes."""
        
        @self.app.get("/health")
        async def health_check():
            """Health check endpoint."""
            health_status = health_monitor.get_current_health()
            pool_stats = None
            
            try:
                pool = await get_connection_pool()
                pool_stats = pool.get_stats()
            except Exception as e:
                logger.warning(f"Could not get pool stats: {e}")
            
            return {
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "version": "0.2.0",
                "connection_pool": pool_stats,
                "database_health": health_status,
                "websocket_connections": self.connection_manager.get_connection_stats()
            }
        
        @self.app.get("/status")
        async def server_status():
            """Detailed server status."""
            active_requests = await self.request_manager.get_active_requests()
            
            return {
                "server": {
                    "status": "running",
                    "uptime_seconds": (datetime.now() - self._start_time).total_seconds(),
                    "version": "0.2.0"
                },
                "requests": {
                    "active_count": len(active_requests),
                    "active_requests": [
                        {
                            "request_id": ctx.request_id,
                            "tool_name": ctx.tool_name,
                            "client_id": ctx.client_id,
                            "duration_ms": (datetime.now() - ctx.start_time).total_seconds() * 1000
                        }
                        for ctx in active_requests.values()
                    ]
                },
                "websockets": self.connection_manager.get_connection_stats()
            }
        
        @self.app.post("/mcp/tools/call")
        async def call_tool_http(request_data: MCPRequest):
            """HTTP endpoint for MCP tool calls."""
            try:
                # Extract client information
                client_id = request_data.params.get("_client_id", "http_client") if request_data.params else "http_client"
                
                # Create request context
                async with request_context(request_data.method, request_data.params or {}, client_id) as ctx:
                    # Route to appropriate handler
                    result = await self._route_tool_call(request_data.method, request_data.params)
                    
                    return MCPResponse(
                        id=request_data.id,
                        result=result
                    )
            
            except Exception as e:
                logger.error(f"Error in HTTP tool call: {e}")
                return MCPResponse(
                    id=request_data.id,
                    error={
                        "code": -32603,
                        "message": "Internal error",
                        "data": str(e)
                    }
                )
        
        @self.app.get("/mcp/tools")
        async def list_tools_http():
            """HTTP endpoint to list available tools."""
            try:
                tools = await self.mcp_server.list_tools()
                return {"tools": [tool.dict() for tool in tools]}
            except Exception as e:
                logger.error(f"Error listing tools: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.websocket("/mcp")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for MCP protocol."""
            connection_id = await self.connection_manager.connect(websocket)
            
            try:
                while True:
                    # Receive message from client
                    data = await websocket.receive_text()
                    
                    try:
                        message = json.loads(data)
                        request_obj = MCPRequest(**message)
                        
                        # Process MCP request
                        response = await self._handle_websocket_request(request_obj, connection_id)
                        
                        # Send response
                        await self.connection_manager.send_message(websocket, response.dict())
                        
                    except ValidationError as e:
                        # Invalid MCP request format
                        error_response = MCPResponse(
                            error={
                                "code": -32600,
                                "message": "Invalid Request",
                                "data": str(e)
                            }
                        )
                        await self.connection_manager.send_message(websocket, error_response.dict())
                    
                    except Exception as e:
                        # Internal error
                        logger.error(f"WebSocket request error: {e}")
                        error_response = MCPResponse(
                            error={
                                "code": -32603,
                                "message": "Internal error",
                                "data": str(e)
                            }
                        )
                        await self.connection_manager.send_message(websocket, error_response.dict())
            
            except WebSocketDisconnect:
                pass
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            finally:
                self.connection_manager.disconnect(websocket)
    
    async def _handle_websocket_request(self, request: MCPRequest, connection_id: str) -> MCPResponse:
        """Handle WebSocket MCP request."""
        try:
            # Create request context with WebSocket connection ID
            client_id = f"ws_{connection_id}"
            
            async with request_context(request.method, request.params or {}, client_id) as ctx:
                # Route to appropriate handler
                if request.method == "tools/list":
                    tools = await self.mcp_server.list_tools()
                    result = {"tools": [tool.dict() for tool in tools]}
                elif request.method.startswith("tools/call"):
                    tool_name = request.params.get("name") if request.params else None
                    tool_args = request.params.get("arguments") if request.params else None
                    
                    result = await self._route_tool_call(tool_name, tool_args)
                else:
                    raise ValueError(f"Unknown method: {request.method}")
                
                return MCPResponse(
                    id=request.id,
                    result=result
                )
        
        except Exception as e:
            logger.error(f"WebSocket request error: {e}")
            return MCPResponse(
                id=request.id,
                error={
                    "code": -32603,
                    "message": "Internal error",
                    "data": str(e)
                }
            )
    
    async def _route_tool_call(self, tool_name: str, arguments: Optional[Dict[str, Any]]) -> Any:
        """Route tool call to appropriate handler."""
        # Import handlers dynamically to avoid circular imports
        from ..main import (
            handle_list_databases,
            handle_list_views,
            handle_describe_view,
            handle_query_view,
            handle_execute_query
        )
        
        # Tool routing map
        tool_handlers = {
            "list_databases": handle_list_databases,
            "list_views": handle_list_views,
            "describe_view": handle_describe_view,
            "query_view": handle_query_view,
            "execute_query": handle_execute_query,
        }
        
        if tool_name not in tool_handlers:
            raise ValueError(f"Unknown tool: {tool_name}")
        
        handler = tool_handlers[tool_name]
        result = await handler(tool_name, arguments)
        
        # Convert MCP content to serializable format
        return [content.dict() for content in result]
    
    async def start(self) -> None:
        """Start the HTTP server."""
        self._start_time = datetime.now()
        
        # Initialize async infrastructure
        from ..main import initialize_async_infrastructure
        await initialize_async_infrastructure()
        
        logger.info(f"Starting Snowflake MCP HTTP server on {self.host}:{self.port}")
        
        # Configure uvicorn
        config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=True,
            loop="asyncio"
        )
        
        server = uvicorn.Server(config)
        await server.serve()
    
    async def shutdown(self) -> None:
        """Graceful shutdown of the server."""
        logger.info("Shutting down MCP HTTP server...")
        
        # Close all WebSocket connections
        for websocket in list(self.connection_manager.active_connections):
            try:
                await websocket.close()
            except Exception as e:
                logger.warning(f"Error closing WebSocket: {e}")
        
        # Cleanup async infrastructure
        from ..utils.async_pool import close_connection_pool
        from ..utils.health_monitor import health_monitor
        
        await close_connection_pool()
        await health_monitor.stop_monitoring()
        
        logger.info("Server shutdown complete")


# CLI entry point for HTTP server
async def run_http_server(host: str = "localhost", port: int = 8000):
    """Run the MCP HTTP server."""
    server = MCPHttpServer(host, port)
    
    try:
        await server.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        await server.shutdown()


def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Snowflake MCP HTTP Server")
    parser.add_argument("--host", default="localhost", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--log-level", default="INFO", help="Log level")
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(level=getattr(logging, args.log_level.upper()))
    
    # Run server
    asyncio.run(run_http_server(args.host, args.port))


if __name__ == "__main__":
    main()
```

