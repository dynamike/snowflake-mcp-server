"""FastAPI-based HTTP/WebSocket MCP server implementation."""

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Set

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from snowflake_mcp_server.main import (
    get_available_tools,
    handle_describe_view,
    handle_execute_query,
    handle_list_databases,
    handle_list_views,
    handle_query_view,
    initialize_async_infrastructure,
)
from snowflake_mcp_server.utils.contextual_logging import setup_server_logging

logger = logging.getLogger(__name__)


# Pydantic models for MCP protocol
class MCPCall(BaseModel):
    """MCP tool call request."""
    method: str = Field(..., description="MCP method name")
    params: Dict[str, Any] = Field(default_factory=dict, description="Method parameters")


class MCPResult(BaseModel):
    """MCP tool call result."""
    content: List[Dict[str, Any]] = Field(default_factory=list, description="Result content")


class MCPError(BaseModel):
    """MCP error response."""
    code: int = Field(..., description="Error code")
    message: str = Field(..., description="Error message")
    data: Optional[Dict[str, Any]] = Field(None, description="Additional error data")


class MCPResponse(BaseModel):
    """MCP response wrapper."""
    id: Optional[str] = Field(None, description="Request ID")
    result: Optional[MCPResult] = Field(None, description="Success result")
    error: Optional[MCPError] = Field(None, description="Error details")


class HealthStatus(BaseModel):
    """Health check status."""
    status: str = Field(..., description="Overall status")
    timestamp: str = Field(..., description="Status timestamp")
    version: str = Field(..., description="Server version")
    uptime_seconds: float = Field(..., description="Server uptime in seconds")


class ServerStatus(BaseModel):
    """Detailed server status."""
    status: str = Field(..., description="Overall status")
    timestamp: str = Field(..., description="Status timestamp")
    version: str = Field(..., description="Server version")
    uptime_seconds: float = Field(..., description="Server uptime in seconds")
    connection_pool: Dict[str, Any] = Field(..., description="Connection pool status")
    active_connections: int = Field(..., description="Active WebSocket connections")
    total_requests: int = Field(..., description="Total requests processed")
    available_tools: List[str] = Field(..., description="Available MCP tools")


class ClientConnection:
    """Represents a connected MCP client."""
    
    def __init__(self, client_id: str, websocket: WebSocket):
        self.client_id = client_id
        self.websocket = websocket
        self.connected_at = time.time()
        self.last_activity = time.time()
        self.request_count = 0
        
    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = time.time()
        self.request_count += 1
    
    def get_uptime(self) -> float:
        """Get connection uptime in seconds."""
        return time.time() - self.connected_at


class ConnectionManager:
    """Manages WebSocket connections for MCP clients."""
    
    def __init__(self):
        self.connections: Dict[str, ClientConnection] = {}
        self.active_connections: Set[WebSocket] = set()
        self.total_requests = 0
        
    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        """Accept a new WebSocket connection."""
        await websocket.accept()
        
        connection = ClientConnection(client_id, websocket)
        self.connections[client_id] = connection
        self.active_connections.add(websocket)
        
        logger.info(f"Client {client_id} connected via WebSocket")
    
    def disconnect(self, client_id: str) -> None:
        """Disconnect a client."""
        if client_id in self.connections:
            connection = self.connections.pop(client_id)
            self.active_connections.discard(connection.websocket)
            logger.info(f"Client {client_id} disconnected")
    
    async def send_to_client(self, client_id: str, message: Dict[str, Any]) -> None:
        """Send message to specific client."""
        if client_id in self.connections:
            connection = self.connections[client_id]
            try:
                await connection.websocket.send_text(json.dumps(message))
                connection.update_activity()
            except Exception as e:
                logger.error(f"Failed to send message to client {client_id}: {e}")
                self.disconnect(client_id)
    
    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Broadcast message to all connected clients."""
        if not self.active_connections:
            return
            
        # Send to all connections concurrently
        tasks = []
        for websocket in self.active_connections.copy():
            tasks.append(websocket.send_text(json.dumps(message)))
        
        # Wait for all sends to complete, handling failures gracefully
        await asyncio.gather(*tasks, return_exceptions=True)
    
    def get_connection_count(self) -> int:
        """Get number of active connections."""
        return len(self.active_connections)
    
    def get_client_stats(self) -> List[Dict[str, Any]]:
        """Get statistics for all connected clients."""
        stats = []
        for client_id, connection in self.connections.items():
            stats.append({
                "client_id": client_id,
                "connected_at": connection.connected_at,
                "uptime_seconds": connection.get_uptime(),
                "request_count": connection.request_count,
                "last_activity": connection.last_activity
            })
        return stats


class MCPHttpServer:
    """HTTP/WebSocket MCP server implementation."""
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8000):
        self.host = host
        self.port = port
        self.start_time = time.time()
        self.connection_manager = ConnectionManager()
        self.app = self._create_app()
        
    def _create_app(self) -> FastAPI:
        """Create FastAPI application with all routes and middleware."""
        
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            """FastAPI lifespan events."""
            # Startup
            logger.info("Starting Snowflake MCP HTTP server...")
            setup_server_logging()
            await initialize_async_infrastructure()
            logger.info(f"HTTP server ready on {self.host}:{self.port}")
            
            yield
            
            # Shutdown
            logger.info("Shutting down HTTP server...")
            await self._cleanup_connections()
            logger.info("HTTP server shutdown complete")
        
        app = FastAPI(
            title="Snowflake MCP Server",
            description="Model Context Protocol server for Snowflake database operations",
            version="0.2.0",
            lifespan=lifespan
        )
        
        # Add CORS middleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # Configure appropriately for production
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        # Health check endpoint
        @app.get("/health", response_model=HealthStatus)
        async def health_check():
            """Simple health check endpoint."""
            return HealthStatus(
                status="healthy",
                timestamp=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                version="0.2.0",
                uptime_seconds=time.time() - self.start_time
            )
        
        # Detailed status endpoint
        @app.get("/status", response_model=ServerStatus)
        async def server_status():
            """Detailed server status endpoint."""
            from snowflake_mcp_server.utils.async_pool import get_pool_status
            
            pool_status = await get_pool_status()
            available_tools = await get_available_tools()
            
            return ServerStatus(
                status="healthy",
                timestamp=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                version="0.2.0",
                uptime_seconds=time.time() - self.start_time,
                connection_pool=pool_status,
                active_connections=self.connection_manager.get_connection_count(),
                total_requests=self.connection_manager.total_requests,
                available_tools=[tool["name"] for tool in available_tools]
            )
        
        # MCP tools listing
        @app.get("/mcp/tools")
        async def list_tools():
            """List available MCP tools."""
            tools = await get_available_tools()
            return {"tools": tools}
        
        # HTTP MCP tool call endpoint
        @app.post("/mcp/tools/call", response_model=MCPResponse)
        async def call_tool(call_request: MCPCall):
            """Execute MCP tool call via HTTP."""
            request_id = str(uuid.uuid4())
            
            try:
                self.connection_manager.total_requests += 1
                
                # Add client tracking to parameters
                params = call_request.params.copy()
                params["_client_id"] = params.get("_client_id", "http_client")
                params["_request_id"] = request_id
                
                # Route to appropriate handler
                result = await self._execute_tool_call(call_request.method, params)
                
                return MCPResponse(
                    id=request_id,
                    result=MCPResult(content=result)
                )
                
            except Exception as e:
                logger.error(f"Tool call error: {e}")
                return MCPResponse(
                    id=request_id,
                    error=MCPError(
                        code=-1,
                        message=str(e),
                        data={"method": call_request.method, "params": call_request.params}
                    )
                )
        
        # WebSocket endpoint for real-time MCP communication
        @app.websocket("/mcp")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for MCP protocol."""
            client_id = str(uuid.uuid4())
            
            try:
                await self.connection_manager.connect(websocket, client_id)
                
                # Send initial connection confirmation
                await self.connection_manager.send_to_client(client_id, {
                    "type": "connection",
                    "status": "connected",
                    "client_id": client_id,
                    "server_version": "0.2.0"
                })
                
                # Handle incoming messages
                while True:
                    try:
                        # Receive message from client
                        data = await websocket.receive_text()
                        message = json.loads(data)
                        
                        # Process MCP message
                        response = await self._handle_websocket_message(client_id, message)
                        
                        # Send response back to client
                        if response:
                            await self.connection_manager.send_to_client(client_id, response)
                            
                    except WebSocketDisconnect:
                        break
                    except Exception as e:
                        logger.error(f"WebSocket message error for client {client_id}: {e}")
                        error_response = {
                            "type": "error",
                            "error": {
                                "code": -1,
                                "message": str(e)
                            }
                        }
                        await self.connection_manager.send_to_client(client_id, error_response)
                        
            except Exception as e:
                logger.error(f"WebSocket connection error: {e}")
            finally:
                self.connection_manager.disconnect(client_id)
        
        return app
    
    async def _execute_tool_call(self, method: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Execute MCP tool call and return results."""
        
        # Map method names to handlers
        handlers = {
            "list_databases": handle_list_databases,
            "list_views": handle_list_views,
            "describe_view": handle_describe_view,
            "query_view": handle_query_view,
            "execute_query": handle_execute_query,
        }
        
        if method not in handlers:
            raise HTTPException(status_code=400, detail=f"Unknown method: {method}")
        
        handler = handlers[method]
        
        # Execute handler with proper context
        result = await handler(method, params)
        
        # Ensure result is in proper format
        if isinstance(result, list):
            return result
        elif isinstance(result, dict):
            return [result]
        else:
            return [{"content": str(result)}]
    
    async def _handle_websocket_message(self, client_id: str, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle incoming WebSocket message."""
        
        try:
            message_type = message.get("type", "call")
            
            if message_type == "call":
                # Handle tool call
                method = message.get("method")
                params = message.get("params", {})
                request_id = message.get("id", str(uuid.uuid4()))
                
                # Add client context
                params["_client_id"] = client_id
                params["_request_id"] = request_id
                
                self.connection_manager.total_requests += 1
                
                # Execute tool call
                result = await self._execute_tool_call(method, params)
                
                return {
                    "type": "result",
                    "id": request_id,
                    "result": {"content": result}
                }
                
            elif message_type == "ping":
                # Handle ping/pong
                return {
                    "type": "pong",
                    "timestamp": time.time()
                }
                
            else:
                return {
                    "type": "error",
                    "error": {
                        "code": -1,
                        "message": f"Unknown message type: {message_type}"
                    }
                }
                
        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}")
            return {
                "type": "error",
                "error": {
                    "code": -1,
                    "message": str(e)
                }
            }
    
    async def _cleanup_connections(self) -> None:
        """Clean up all connections during shutdown."""
        if self.connection_manager.active_connections:
            logger.info(f"Closing {len(self.connection_manager.active_connections)} WebSocket connections...")
            
            # Send shutdown notice to all clients
            shutdown_message = {
                "type": "shutdown",
                "message": "Server is shutting down",
                "timestamp": time.time()
            }
            
            await self.connection_manager.broadcast(shutdown_message)
            
            # Close all connections
            for websocket in self.connection_manager.active_connections.copy():
                try:
                    await websocket.close()
                except Exception as e:
                    logger.error(f"Error closing WebSocket: {e}")
            
            self.connection_manager.connections.clear()
            self.connection_manager.active_connections.clear()
    
    async def start(self) -> None:
        """Start the HTTP server."""
        config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=True
        )
        
        server = uvicorn.Server(config)
        await server.serve()
    
    def run(self) -> None:
        """Run the HTTP server (blocking)."""
        uvicorn.run(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=True
        )


# Global server instance
_http_server: Optional[MCPHttpServer] = None


def get_http_server() -> MCPHttpServer:
    """Get or create the global HTTP server instance."""
    global _http_server
    if _http_server is None:
        _http_server = MCPHttpServer()
    return _http_server


async def start_http_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start the HTTP/WebSocket MCP server."""
    server = MCPHttpServer(host, port)
    await server.start()


def run_http_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the HTTP/WebSocket MCP server (blocking)."""
    server = MCPHttpServer(host, port)
    server.run()


if __name__ == "__main__":
    # Run server directly
    run_http_server()