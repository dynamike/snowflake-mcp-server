# Phase 2: Multi-Client Architecture Implementation Details

## Context & Overview

The current Snowflake MCP server architecture creates bottlenecks when multiple MCP clients (Claude Desktop, Claude Code, Roo Code) attempt to connect simultaneously. The shared connection state and lack of client isolation cause performance degradation and potential data inconsistency issues.

**Current Issues:**
- Single connection shared across all clients
- Client requests can interfere with each other's database context
- No client identification or session management
- Resource contention leads to blocking operations
- No fair resource allocation between clients

**Target Architecture:**
- Client session management with unique identification
- Connection multiplexing with per-client isolation
- Fair resource allocation and queuing
- Client-specific rate limiting and quotas
- Session persistence across reconnections

## Current State Analysis

### Client Connection Problems in `main.py`

The stdio server only supports one client connection:
```python
def run_stdio_server() -> None:
    """Run the MCP server using stdin/stdout for communication."""
    # Only supports single client via stdio
```

Connection manager singleton shared across all requests:
```python
# In utils/snowflake_conn.py line 311
connection_manager = SnowflakeConnectionManager()  # Global singleton
```

## Implementation Plan

### 5. Multi-Client Testing {#client-testing}

**Step 5: Comprehensive Multi-Client Testing**

Create `tests/test_multi_client.py`:

```python
import pytest
import asyncio
import aiohttp
import websockets
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from snowflake_mcp_server.client.session_manager import ClientType
from snowflake_mcp_server.transports.http_server import MCPHttpServer

@pytest.mark.asyncio
async def test_multiple_simultaneous_http_clients():
    """Test multiple HTTP clients accessing server simultaneously."""
    
    # Start server
    server = MCPHttpServer(host="localhost", port=8901)
    server_task = asyncio.create_task(server.start())
    
    # Wait for server startup
    await asyncio.sleep(2)
    
    try:
        async def make_request(client_id: str, session: aiohttp.ClientSession):
            """Make request as specific client."""
            request_data = {
                "jsonrpc": "2.0",
                "id": f"test_{client_id}",
                "method": "list_databases",
                "params": {"_client_id": client_id}
            }
            
            async with session.post(
                "http://localhost:8901/mcp/tools/call",
                json=request_data
            ) as response:
                return await response.json()
        
        # Create multiple client sessions
        async with aiohttp.ClientSession() as session:
            # Simulate Claude Desktop, Claude Code, and Roo Code
            tasks = [
                make_request("claude_desktop_1", session),
                make_request("claude_code_1", session),
                make_request("roo_code_1", session),
                make_request("claude_desktop_2", session),
                make_request("claude_code_2", session),
            ]
            
            # Execute all requests concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Verify all requests succeeded
            assert len(results) == 5
            for i, result in enumerate(results):
                assert not isinstance(result, Exception), f"Request {i} failed: {result}"
                assert "result" in result
    
    finally:
        server_task.cancel()


@pytest.mark.asyncio
async def test_websocket_multi_client():
    """Test multiple WebSocket clients."""
    
    # Start server
    server = MCPHttpServer(host="localhost", port=8902)
    server_task = asyncio.create_task(server.start())
    
    await asyncio.sleep(2)
    
    try:
        async def websocket_client(client_id: str):
            """Single WebSocket client session."""
            uri = "ws://localhost:8902/mcp"
            
            async with websockets.connect(uri) as websocket:
                # Send list databases request
                request = {
                    "jsonrpc": "2.0",
                    "id": f"ws_{client_id}",
                    "method": "tools/call",
                    "params": {
                        "name": "list_databases",
                        "arguments": {"_client_id": client_id}
                    }
                }
                
                await websocket.send(json.dumps(request))
                response = await websocket.recv()
                
                data = json.loads(response)
                return data
        
        # Run multiple WebSocket clients concurrently
        tasks = [
            websocket_client("ws_client_1"),
            websocket_client("ws_client_2"),
            websocket_client("ws_client_3"),
        ]
        
        results = await asyncio.gather(*tasks)
        
        # Verify all succeeded
        assert len(results) == 3
        for result in results:
            assert "result" in result
    
    finally:
        server_task.cancel()


@pytest.mark.asyncio
async def test_client_isolation():
    """Test that clients don't interfere with each other's state."""
    
    server = MCPHttpServer(host="localhost", port=8903)
    server_task = asyncio.create_task(server.start())
    
    await asyncio.sleep(2)
    
    try:
        async def client_with_database_context(client_id: str, database: str):
            """Client that changes database context."""
            
            async with aiohttp.ClientSession() as session:
                # Change database context
                request = {
                    "jsonrpc": "2.0",
                    "id": f"ctx_{client_id}",
                    "method": "execute_query",
                    "params": {
                        "_client_id": client_id,
                        "database": database,
                        "query": "SELECT CURRENT_DATABASE()"
                    }
                }
                
                async with session.post(
                    "http://localhost:8903/mcp/tools/call",
                    json=request
                ) as response:
                    result = await response.json()
                    return result
        
        # Run clients with different database contexts simultaneously
        results = await asyncio.gather(
            client_with_database_context("client_a", "DATABASE_A"),
            client_with_database_context("client_b", "DATABASE_B"),
            client_with_database_context("client_c", "DATABASE_C"),
        )
        
        # Each client should see its own database context
        assert len(results) == 3
        for result in results:
            assert "result" in result
            # Would need actual database setup to verify different contexts
    
    finally:
        server_task.cancel()


@pytest.mark.asyncio
async def test_rate_limiting_per_client():
    """Test that rate limiting works per client."""
    
    from snowflake_mcp_server.client.session_manager import session_manager
    
    await session_manager.start()
    
    try:
        # Create sessions for different clients
        session_a = await session_manager.create_session("client_a", ClientType.CLAUDE_DESKTOP)
        session_b = await session_manager.create_session("client_b", ClientType.CLAUDE_CODE)
        
        # Exhaust rate limit for client A
        for _ in range(100):  # Default rate limit
            consumed = session_a.consume_rate_limit_token()
            if not consumed:
                break
        
        # Client A should be rate limited
        assert not session_a.consume_rate_limit_token()
        
        # Client B should still have tokens
        assert session_b.consume_rate_limit_token()
    
    finally:
        await session_manager.stop()


@pytest.mark.asyncio
async def test_connection_pool_per_client():
    """Test that each client gets fair access to connection pool."""
    
    from snowflake_mcp_server.client.connection_multiplexer import connection_multiplexer
    from snowflake_mcp_server.client.session_manager import session_manager
    from snowflake_mcp_server.utils.request_context import RequestContext
    
    await session_manager.start()
    
    try:
        # Create multiple client sessions
        sessions = []
        for i in range(3):
            session = await session_manager.create_session(
                f"pool_test_client_{i}",
                ClientType.HTTP_CLIENT
            )
            sessions.append(session)
        
        # Test concurrent connection acquisition
        async def test_connection_access(session, request_num):
            """Test connection access for a session."""
            request_ctx = RequestContext(
                request_id=f"test_req_{session.client_id}_{request_num}",
                client_id=session.client_id,
                tool_name="test_tool",
                arguments={},
                start_time=datetime.now()
            )
            
            async with connection_multiplexer.acquire_for_request(session, request_ctx) as conn:
                # Simulate some work
                await asyncio.sleep(0.1)
                return f"success_{session.client_id}"
        
        # Run concurrent connection tests
        tasks = []
        for i, session in enumerate(sessions):
            for j in range(2):  # 2 requests per client
                tasks.append(test_connection_access(session, j))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # All should succeed (no connection pool exhaustion)
        successful_results = [r for r in results if isinstance(r, str) and r.startswith("success_")]
        assert len(successful_results) == 6  # 3 clients * 2 requests each
    
    finally:
        await session_manager.stop()


def test_session_manager_scaling():
    """Test session manager handles many clients."""
    
    async def create_many_sessions():
        from snowflake_mcp_server.client.session_manager import session_manager
        
        await session_manager.start()
        
        try:
            sessions = []
            
            # Create 50 client sessions
            for i in range(50):
                session = await session_manager.create_session(
                    f"scale_client_{i}",
                    ClientType.HTTP_CLIENT
                )
                sessions.append(session)
            
            # Verify all sessions created
            assert len(sessions) == 50
            
            # Get stats
            stats = await session_manager.get_session_stats()
            assert stats["total_sessions"] == 50
            assert stats["unique_clients"] == 50
        
        finally:
            await session_manager.stop()
    
    asyncio.run(create_many_sessions())
```

## Performance Testing

Create `scripts/test_multi_client_performance.py`:

```python
#!/usr/bin/env python3

import asyncio
import aiohttp
import time
import statistics
from concurrent.futures import ThreadPoolExecutor

async def benchmark_multi_client_performance():
    """Benchmark multi-client performance."""
    
    print("Starting multi-client performance test...")
    
    async def client_workload(client_id: str, num_requests: int):
        """Workload for a single client."""
        
        async with aiohttp.ClientSession() as session:
            times = []
            
            for i in range(num_requests):
                start_time = time.time()
                
                request_data = {
                    "jsonrpc": "2.0",
                    "id": f"{client_id}_req_{i}",
                    "method": "list_databases",
                    "params": {"_client_id": client_id}
                }
                
                try:
                    async with session.post(
                        "http://localhost:8000/mcp/tools/call",
                        json=request_data
                    ) as response:
                        await response.json()
                    
                    duration = time.time() - start_time
                    times.append(duration)
                    
                except Exception as e:
                    print(f"Error in {client_id} request {i}: {e}")
            
            return {
                "client_id": client_id,
                "requests": len(times),
                "avg_time": statistics.mean(times) if times else 0,
                "median_time": statistics.median(times) if times else 0,
                "max_time": max(times) if times else 0,
                "total_time": sum(times)
            }
    
    # Test with increasing number of clients
    for num_clients in [1, 5, 10, 20]:
        print(f"\n--- Testing with {num_clients} clients ---")
        
        requests_per_client = 20
        
        # Create client tasks
        tasks = [
            client_workload(f"client_{i}", requests_per_client)
            for i in range(num_clients)
        ]
        
        # Measure total time
        start_time = time.time()
        results = await asyncio.gather(*tasks)
        total_time = time.time() - start_time
        
        # Calculate metrics
        total_requests = sum(r["requests"] for r in results)
        avg_response_time = statistics.mean([r["avg_time"] for r in results])
        throughput = total_requests / total_time
        
        print(f"Total requests: {total_requests}")
        print(f"Total time: {total_time:.2f}s")
        print(f"Throughput: {throughput:.2f} requests/second")
        print(f"Average response time: {avg_response_time:.3f}s")
        
        # Per-client breakdown
        for result in results:
            print(f"  {result['client_id']}: {result['avg_time']:.3f}s avg")


if __name__ == "__main__":
    asyncio.run(benchmark_multi_client_performance())
```

## Verification Steps

1. **Session Management**: Verify unique sessions created for each client type
2. **Connection Multiplexing**: Test connection pool isolation between clients
3. **Client Isolation**: Confirm database context changes don't affect other clients
4. **Resource Allocation**: Verify fair queuing and rate limiting work correctly
5. **Performance**: Measure throughput with 10+ concurrent clients
6. **Error Handling**: Test client disconnection and reconnection scenarios

## Completion Criteria

- [ ] Client session manager tracks unique client instances
- [ ] Connection multiplexing provides isolated connections per client
- [ ] Database context changes are isolated between clients
- [ ] Fair resource allocation prevents any single client from monopolizing resources
- [ ] Rate limiting and quotas work independently per client
- [ ] Multiple Claude Desktop, Claude Code, and Roo Code clients can operate simultaneously
- [ ] Performance tests demonstrate linear scalability up to 20 concurrent clients
- [ ] Error in one client doesn't affect other clients' operations
- [ ] Session persistence works across client reconnections
- [ ] Resource cleanup prevents memory leaks with long-running clients