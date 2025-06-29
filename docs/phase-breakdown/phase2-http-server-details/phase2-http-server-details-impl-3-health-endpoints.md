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

### 3. Health Check Endpoints {#health-endpoints}

**Step 3: Comprehensive Health Monitoring**

Add to `http_server.py` (additional health endpoints):

```python
# Additional health check routes
@self.app.get("/health/detailed")
async def detailed_health_check():
    """Detailed health check with component status."""
    health_details = {}
    
    # Connection pool health
    try:
        pool = await get_connection_pool()
        pool_stats = pool.get_stats()
        health_details["connection_pool"] = {
            "status": "healthy" if pool_stats["healthy_connections"] > 0 else "unhealthy",
            "stats": pool_stats
        }
    except Exception as e:
        health_details["connection_pool"] = {
            "status": "error",
            "error": str(e)
        }
    
    # Database connectivity
    try:
        async with get_isolated_database_ops(None) as db_ops:
            await db_ops.execute_query("SELECT 1")
        health_details["database"] = {"status": "healthy"}
    except Exception as e:
        health_details["database"] = {
            "status": "unhealthy",
            "error": str(e)
        }
    
    # Request manager health
    active_requests = await request_manager.get_active_requests()
    health_details["request_manager"] = {
        "status": "healthy",
        "active_requests": len(active_requests)
    }
    
    # WebSocket connections
    ws_stats = connection_manager.get_connection_stats()
    health_details["websockets"] = {
        "status": "healthy",
        "active_connections": ws_stats["active_connections"]
    }
    
    # Overall health determination
    overall_status = "healthy"
    for component, details in health_details.items():
        if details["status"] != "healthy":
            overall_status = "unhealthy"
            break
    
    return {
        "overall_status": overall_status,
        "timestamp": datetime.now().isoformat(),
        "components": health_details
    }

@self.app.get("/metrics")
async def prometheus_metrics():
    """Prometheus-style metrics endpoint."""
    pool_stats = {}
    try:
        pool = await get_connection_pool()
        pool_stats = pool.get_stats()
    except Exception:
        pass
    
    active_requests = await request_manager.get_active_requests()
    ws_stats = connection_manager.get_connection_stats()
    
    # Generate Prometheus format metrics
    metrics = []
    metrics.append(f"# HELP snowflake_mcp_connections_total Total database connections")
    metrics.append(f"# TYPE snowflake_mcp_connections_total gauge")
    metrics.append(f"snowflake_mcp_connections_total {pool_stats.get('total_connections', 0)}")
    
    metrics.append(f"# HELP snowflake_mcp_connections_active Active database connections")
    metrics.append(f"# TYPE snowflake_mcp_connections_active gauge")
    metrics.append(f"snowflake_mcp_connections_active {pool_stats.get('active_connections', 0)}")
    
    metrics.append(f"# HELP snowflake_mcp_requests_active Active MCP requests")
    metrics.append(f"# TYPE snowflake_mcp_requests_active gauge")
    metrics.append(f"snowflake_mcp_requests_active {len(active_requests)}")
    
    metrics.append(f"# HELP snowflake_mcp_websockets_active Active WebSocket connections")
    metrics.append(f"# TYPE snowflake_mcp_websockets_active gauge")
    metrics.append(f"snowflake_mcp_websockets_active {ws_stats['active_connections']}")
    
    return Response(content="\n".join(metrics), media_type="text/plain")
```

