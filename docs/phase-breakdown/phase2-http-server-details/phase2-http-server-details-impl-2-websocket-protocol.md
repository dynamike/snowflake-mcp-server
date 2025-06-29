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

### 2. WebSocket Protocol Implementation {#websocket-protocol}

**Step 2: MCP WebSocket Protocol Handler**

Create `snowflake_mcp_server/transports/websocket_handler.py`:

```python
"""WebSocket protocol handler for MCP compliance."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

import websockets
from websockets.server import WebSocketServerProtocol
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger(__name__)


class MCPWebSocketHandler:
    """Handle MCP protocol over WebSocket connections."""
    
    def __init__(self, mcp_server, request_manager):
        self.mcp_server = mcp_server
        self.request_manager = request_manager
        self.active_connections: Dict[str, WebSocketServerProtocol] = {}
        self.connection_metadata: Dict[str, Dict[str, Any]] = {}
    
    async def handle_connection(self, websocket: WebSocketServerProtocol, path: str) -> None:
        """Handle new WebSocket connection."""
        connection_id = f"ws_{id(websocket)}"
        
        self.active_connections[connection_id] = websocket
        self.connection_metadata[connection_id] = {
            "connected_at": datetime.now(),
            "path": path,
            "message_count": 0,
            "last_activity": datetime.now()
        }
        
        logger.info(f"New WebSocket connection: {connection_id} on {path}")
        
        try:
            # Send initial capabilities
            await self._send_capabilities(websocket)
            
            # Handle messages
            async for message in websocket:
                await self._handle_message(websocket, connection_id, message)
                
        except ConnectionClosed:
            logger.info(f"WebSocket connection closed: {connection_id}")
        except WebSocketException as e:
            logger.error(f"WebSocket error for {connection_id}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error for {connection_id}: {e}")
        finally:
            await self._cleanup_connection(connection_id)
    
    async def _send_capabilities(self, websocket: WebSocketServerProtocol) -> None:
        """Send server capabilities to client."""
        capabilities = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "capabilities": {
                    "tools": True,
                    "resources": False,
                    "prompts": False,
                    "logging": True
                },
                "serverInfo": {
                    "name": "snowflake-mcp-server",
                    "version": "0.2.0"
                }
            }
        }
        
        await websocket.send(json.dumps(capabilities))
    
    async def _handle_message(self, websocket: WebSocketServerProtocol, connection_id: str, message: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            # Update activity timestamp
            self.connection_metadata[connection_id]["last_activity"] = datetime.now()
            self.connection_metadata[connection_id]["message_count"] += 1
            
            # Parse JSON-RPC message
            data = json.loads(message)
            
            # Handle different message types
            if "method" in data:
                await self._handle_request(websocket, connection_id, data)
            elif "result" in data or "error" in data:
                await self._handle_response(websocket, connection_id, data)
            else:
                logger.warning(f"Unknown message format from {connection_id}: {data}")
        
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from {connection_id}: {e}")
            await self._send_error(websocket, None, -32700, "Parse error")
        except Exception as e:
            logger.error(f"Error handling message from {connection_id}: {e}")
            await self._send_error(websocket, data.get("id"), -32603, "Internal error")
    
    async def _handle_request(self, websocket: WebSocketServerProtocol, connection_id: str, data: Dict[str, Any]) -> None:
        """Handle JSON-RPC request."""
        method = data.get("method")
        params = data.get("params", {})
        request_id = data.get("id")
        
        try:
            # Route request based on method
            if method == "initialize":
                result = await self._handle_initialize(params)
            elif method == "tools/list":
                result = await self._handle_list_tools()
            elif method == "tools/call":
                result = await self._handle_tool_call(connection_id, params)
            elif method == "ping":
                result = {"pong": True, "timestamp": datetime.now().isoformat()}
            else:
                raise ValueError(f"Unknown method: {method}")
            
            # Send successful response
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result
            }
            await websocket.send(json.dumps(response))
        
        except Exception as e:
            logger.error(f"Error handling request {method}: {e}")
            await self._send_error(websocket, request_id, -32603, str(e))
    
    async def _handle_response(self, websocket: WebSocketServerProtocol, connection_id: str, data: Dict[str, Any]) -> None:
        """Handle JSON-RPC response (from client)."""
        # For now, just log responses from clients
        response_id = data.get("id")
        logger.debug(f"Received response from {connection_id}: {response_id}")
    
    async def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle client initialization."""
        return {
            "capabilities": {
                "tools": True,
                "resources": False,
                "prompts": False
            },
            "serverInfo": {
                "name": "snowflake-mcp-server",
                "version": "0.2.0"
            }
        }
    
    async def _handle_list_tools(self) -> Dict[str, Any]:
        """Handle tools/list request."""
        tools = await self.mcp_server.list_tools()
        return {
            "tools": [tool.dict() for tool in tools]
        }
    
    async def _handle_tool_call(self, connection_id: str, params: Dict[str, Any]) -> Any:
        """Handle tools/call request."""
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        if not tool_name:
            raise ValueError("Tool name is required")
        
        # Import handlers
        from ..main import (
            handle_list_databases,
            handle_list_views,
            handle_describe_view,
            handle_query_view,
            handle_execute_query
        )
        
        # Tool routing
        handlers = {
            "list_databases": handle_list_databases,
            "list_views": handle_list_views,
            "describe_view": handle_describe_view,
            "query_view": handle_query_view,
            "execute_query": handle_execute_query,
        }
        
        if tool_name not in handlers:
            raise ValueError(f"Unknown tool: {tool_name}")
        
        # Execute tool with request context
        from ..utils.request_context import request_context
        
        async with request_context(tool_name, arguments, connection_id) as ctx:
            handler = handlers[tool_name]
            result = await handler(tool_name, arguments)
            
            # Convert to serializable format
            return [content.dict() for content in result]
    
    async def _send_error(self, websocket: WebSocketServerProtocol, request_id: Optional[str], code: int, message: str) -> None:
        """Send JSON-RPC error response."""
        error_response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message
            }
        }
        
        try:
            await websocket.send(json.dumps(error_response))
        except Exception as e:
            logger.error(f"Failed to send error response: {e}")
    
    async def _cleanup_connection(self, connection_id: str) -> None:
        """Clean up connection resources."""
        self.active_connections.pop(connection_id, None)
        self.connection_metadata.pop(connection_id, None)
        
        logger.info(f"Cleaned up connection: {connection_id}")
    
    def get_connection_stats(self) -> Dict[str, Any]:
        """Get statistics about active connections."""
        return {
            "active_connections": len(self.active_connections),
            "connections": [
                {
                    "connection_id": conn_id,
                    "connected_at": metadata["connected_at"].isoformat(),
                    "message_count": metadata["message_count"],
                    "last_activity": metadata["last_activity"].isoformat()
                }
                for conn_id, metadata in self.connection_metadata.items()
            ]
        }


# Standalone WebSocket server
async def run_websocket_server(host: str = "localhost", port: int = 8001):
    """Run standalone WebSocket server."""
    from ..main import create_server, initialize_async_infrastructure
    from ..utils.request_context import RequestContextManager
    
    # Initialize infrastructure
    await initialize_async_infrastructure()
    
    # Create MCP server and handler
    mcp_server = create_server()
    request_manager = RequestContextManager()
    ws_handler = MCPWebSocketHandler(mcp_server, request_manager)
    
    logger.info(f"Starting MCP WebSocket server on ws://{host}:{port}")
    
    # Start WebSocket server
    async with websockets.serve(ws_handler.handle_connection, host, port):
        logger.info("WebSocket server ready for connections")
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    asyncio.run(run_websocket_server())
```

