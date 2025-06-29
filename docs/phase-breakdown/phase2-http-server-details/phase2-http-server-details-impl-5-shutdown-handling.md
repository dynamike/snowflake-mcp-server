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

### 5. Graceful Shutdown Handling {#shutdown-handling}

**Step 5: Proper Shutdown Sequence**

Add to `http_server.py`:

```python
import signal
import sys

class GracefulShutdownHandler:
    """Handle graceful shutdown of the server."""
    
    def __init__(self, mcp_server: MCPHttpServer):
        self.mcp_server = mcp_server
        self.shutdown_requested = False
    
    def setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum: int, frame) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.shutdown_requested = True
    
    async def shutdown_sequence(self) -> None:
        """Execute graceful shutdown sequence."""
        logger.info("Starting graceful shutdown sequence...")
        
        # 1. Stop accepting new connections
        logger.info("Stopping new connection acceptance...")
        
        # 2. Wait for active requests to complete (with timeout)
        logger.info("Waiting for active requests to complete...")
        timeout = 30  # seconds
        
        for i in range(timeout):
            active_requests = await self.mcp_server.request_manager.get_active_requests()
            if not active_requests:
                break
            
            logger.info(f"Waiting for {len(active_requests)} active requests... ({timeout - i}s remaining)")
            await asyncio.sleep(1)
        
        # 3. Close WebSocket connections
        logger.info("Closing WebSocket connections...")
        await self.mcp_server.shutdown()
        
        logger.info("Graceful shutdown complete")


# Update server startup to include shutdown handling
async def run_http_server_with_shutdown(host: str = "localhost", port: int = 8000):
    """Run HTTP server with graceful shutdown."""
    server = MCPHttpServer(host, port)
    shutdown_handler = GracefulShutdownHandler(server)
    
    # Setup signal handlers
    shutdown_handler.setup_signal_handlers()
    
    try:
        # Start server in background task
        server_task = asyncio.create_task(server.start())
        
        # Wait for shutdown signal or server completion
        while not shutdown_handler.shutdown_requested:
            if server_task.done():
                break
            await asyncio.sleep(0.1)
        
        if shutdown_handler.shutdown_requested:
            logger.info("Shutdown requested, cancelling server...")
            server_task.cancel()
            
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            
            await shutdown_handler.shutdown_sequence()
    
    except Exception as e:
        logger.error(f"Server error: {e}")
        raise
    finally:
        logger.info("Server stopped")
```

## Testing Strategy

### Unit Tests

Create `tests/test_http_server.py`:

```python
import pytest
import asyncio
from fastapi.testclient import TestClient
from snowflake_mcp_server.transports.http_server import MCPHttpServer

@pytest.fixture
def test_server():
    """Create test server instance."""
    return MCPHttpServer(host="localhost", port=8888)

@pytest.fixture
def test_client(test_server):
    """Create test client."""
    return TestClient(test_server.app)

def test_health_endpoint(test_client):
    """Test health check endpoint."""
    response = test_client.get("/health")
    assert response.status_code == 200
    
    data = response.json()
    assert "status" in data
    assert "timestamp" in data

def test_tools_list_endpoint(test_client):
    """Test tools listing endpoint."""
    response = test_client.get("/mcp/tools")
    assert response.status_code == 200
    
    data = response.json()
    assert "tools" in data
    assert isinstance(data["tools"], list)

@pytest.mark.asyncio
async def test_websocket_connection():
    """Test WebSocket connection."""
    import websockets
    
    # Start server in background
    server = MCPHttpServer(host="localhost", port=8889)
    server_task = asyncio.create_task(server.start())
    
    # Wait a moment for server to start
    await asyncio.sleep(1)
    
    try:
        # Test WebSocket connection
        async with websockets.connect("ws://localhost:8889/mcp") as websocket:
            # Send ping
            await websocket.send(json.dumps({
                "jsonrpc": "2.0",
                "id": "test_1",
                "method": "ping"
            }))
            
            # Receive response
            response = await websocket.recv()
            data = json.loads(response)
            
            assert data["id"] == "test_1"
            assert "result" in data
    
    finally:
        server_task.cancel()
```

## Verification Steps

1. **HTTP Server**: Verify server starts and responds to health checks
2. **WebSocket Support**: Test WebSocket connections and MCP protocol compliance
3. **Tool Integration**: Confirm all MCP tools work via HTTP and WebSocket
4. **Security Headers**: Validate security headers are present in responses
5. **Graceful Shutdown**: Test proper cleanup on server shutdown
6. **Multi-client**: Verify multiple simultaneous connections work correctly

## Completion Criteria

- [ ] FastAPI server runs on configurable host/port
- [ ] WebSocket endpoint supports MCP protocol
- [ ] Health check endpoints return accurate status
- [ ] All MCP tools accessible via HTTP and WebSocket
- [ ] Security headers and CORS properly configured
- [ ] Graceful shutdown handles active connections properly
- [ ] Multiple clients can connect simultaneously without interference
- [ ] Error handling provides meaningful responses to clients
- [ ] Performance meets requirements (handle 50+ concurrent connections)