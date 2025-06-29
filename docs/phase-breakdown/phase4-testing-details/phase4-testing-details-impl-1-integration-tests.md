# Phase 4: Comprehensive Testing Suite Implementation Details

## Context & Overview

The architectural improvements introduce significant complexity that requires comprehensive testing to ensure reliability, performance, and correctness. The current testing is minimal and doesn't cover the new async operations, multi-client scenarios, or failure conditions.

**Current Testing Gaps:**
- Limited unit test coverage (basic connection tests only)
- No integration tests for async operations
- Missing load testing for concurrent scenarios
- No chaos engineering or failure simulation
- Insufficient performance regression testing
- No end-to-end testing with real MCP clients

**Target Architecture:**
- Comprehensive unit tests with >95% coverage
- Integration tests for all async operations and workflows
- Load testing with realistic concurrent scenarios
- Chaos engineering tests for resilience validation
- Automated regression testing with performance baselines
- End-to-end testing with multiple MCP client types

## Dependencies Required

Add to `pyproject.toml`:
```toml
[project.optional-dependencies]
testing = [
    "pytest>=7.4.0",            # Already present
    "pytest-asyncio>=0.21.0",   # Async test support
    "pytest-cov>=4.1.0",        # Coverage reporting
    "pytest-xdist>=3.3.0",      # Parallel test execution
    "pytest-benchmark>=4.0.0",   # Performance benchmarking
    "httpx>=0.25.0",            # HTTP client for testing
    "websockets>=12.0",         # WebSocket testing
    "locust>=2.17.0",           # Load testing framework
    "factory-boy>=3.3.0",       # Test data factories
    "freezegun>=1.2.0",         # Time manipulation for tests
    "responses>=0.23.0",        # HTTP request mocking
    "pytest-mock>=3.11.0",      # Enhanced mocking
]

chaos_testing = [
    "chaos-toolkit>=1.15.0",    # Chaos engineering
    "toxiproxy-python>=0.1.0",  # Network failure simulation
]
```

## Implementation Plan

### 1. Integration Testing Framework {#integration-tests}

**Step 1: Async Operations Integration Tests**

Create `tests/integration/test_async_operations.py`:

```python
"""Integration tests for async database operations."""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from snowflake_mcp_server.utils.async_pool import initialize_connection_pool, close_connection_pool
from snowflake_mcp_server.utils.request_context import RequestContext, request_context
from snowflake_mcp_server.utils.async_database import get_isolated_database_ops
from snowflake_mcp_server.main import (
    handle_list_databases, handle_list_views, handle_describe_view,
    handle_query_view, handle_execute_query
)


@pytest.fixture
async def async_infrastructure():
    """Setup async infrastructure for testing."""
    # Mock Snowflake config
    from snowflake_mcp_server.utils.snowflake_conn import SnowflakeConfig, AuthType
    from snowflake_mcp_server.utils.async_pool import ConnectionPoolConfig
    
    config = SnowflakeConfig(
        account="test_account",
        user="test_user",
        auth_type=AuthType.EXTERNAL_BROWSER
    )
    
    pool_config = ConnectionPoolConfig(min_size=1, max_size=3)
    
    # Initialize with mocked connections
    await initialize_connection_pool(config, pool_config)
    
    yield
    
    # Cleanup
    await close_connection_pool()


@pytest.mark.asyncio
async def test_concurrent_database_operations(async_infrastructure):
    """Test multiple concurrent database operations."""
    
    async def database_operation(operation_id: int):
        """Single database operation."""
        async with request_context(f"test_op_{operation_id}", {}, f"client_{operation_id}") as ctx:
            async with get_isolated_database_ops(ctx) as db_ops:
                # Mock query execution
                return await db_ops.execute_query_isolated("SELECT 1")
    
    # Run 10 concurrent operations
    tasks = [database_operation(i) for i in range(10)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Verify all operations completed successfully
    assert len(results) == 10
    successful_results = [r for r in results if not isinstance(r, Exception)]
    assert len(successful_results) == 10


@pytest.mark.asyncio
async def test_request_isolation_integrity(async_infrastructure):
    """Test that request isolation maintains data integrity."""
    
    isolation_results = []
    
    async def isolated_operation(client_id: str, database: str):
        """Operation that changes database context."""
        async with request_context("test_context", {"database": database}, client_id) as ctx:
            async with get_isolated_database_ops(ctx) as db_ops:
                # Simulate database context change
                await db_ops.use_database_isolated(database)
                
                # Get current context
                current_db, current_schema = await db_ops.get_current_context()
                isolation_results.append({
                    "client_id": client_id,
                    "expected_db": database,
                    "actual_db": current_db,
                    "request_id": ctx.request_id
                })
    
    # Run operations with different database contexts
    await asyncio.gather(
        isolated_operation("client_a", "DATABASE_A"),
        isolated_operation("client_b", "DATABASE_B"),
        isolated_operation("client_c", "DATABASE_C"),
    )
    
    # Verify isolation worked
    assert len(isolation_results) == 3
    for result in isolation_results:
        assert result["expected_db"] == result["actual_db"]


@pytest.mark.asyncio
async def test_connection_pool_behavior(async_infrastructure):
    """Test connection pool behavior under load."""
    
    from snowflake_mcp_server.utils.async_pool import get_connection_pool
    
    pool = await get_connection_pool()
    initial_stats = pool.get_stats()
    
    # Use all available connections
    active_connections = []
    
    async def acquire_connection():
        async with pool.acquire() as conn:
            active_connections.append(conn)
            await asyncio.sleep(0.1)  # Hold connection briefly
    
    # Acquire connections up to pool limit
    tasks = [acquire_connection() for _ in range(initial_stats["total_connections"])]
    await asyncio.gather(*tasks)
    
    # Verify pool stats
    final_stats = pool.get_stats()
    assert final_stats["active_connections"] == 0  # All released
    assert final_stats["total_connections"] >= initial_stats["total_connections"]


@pytest.mark.asyncio
async def test_error_handling_and_recovery(async_infrastructure):
    """Test error handling and recovery in async operations."""
    
    error_count = 0
    success_count = 0
    
    async def operation_with_potential_failure(should_fail: bool):
        """Operation that may fail."""
        nonlocal error_count, success_count
        
        try:
            async with request_context("test_error", {}, "test_client") as ctx:
                async with get_isolated_database_ops(ctx) as db_ops:
                    if should_fail:
                        raise Exception("Simulated database error")
                    
                    # Normal operation
                    await db_ops.execute_query_isolated("SELECT 1")
                    success_count += 1
        
        except Exception:
            error_count += 1
    
    # Mix of successful and failing operations
    tasks = [
        operation_with_potential_failure(i % 3 == 0)  # Every 3rd operation fails
        for i in range(10)
    ]
    
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # Verify error handling
    assert error_count > 0  # Some operations failed
    assert success_count > 0  # Some operations succeeded
    assert error_count + success_count == 10


@pytest.mark.asyncio
async def test_mcp_handler_integration():
    """Test MCP handlers with full async infrastructure."""
    
    # Test list databases
    result = await handle_list_databases("list_databases")
    assert len(result) == 1
    assert "Available Snowflake databases:" in result[0].text
    
    # Test with arguments
    result = await handle_list_views("list_views", {"database": "TEST_DB"})
    assert len(result) == 1
    # Should handle the database parameter
    
    # Test query execution with isolation
    result = await handle_execute_query("execute_query", {
        "query": "SELECT 1 as test_column",
        "database": "TEST_DB"
    })
    assert len(result) == 1
```

**Step 2: Multi-Client Integration Tests**

Create `tests/integration/test_multi_client_scenarios.py`:

```python
"""Integration tests for multi-client scenarios."""

import pytest
import asyncio
import aiohttp
import json
from datetime import datetime

from snowflake_mcp_server.client.session_manager import session_manager, ClientType
from snowflake_mcp_server.client.connection_multiplexer import connection_multiplexer
from snowflake_mcp_server.transports.http_server import MCPHttpServer


@pytest.fixture
async def test_server():
    """Start test HTTP server."""
    server = MCPHttpServer(host="localhost", port=0)  # Random port
    
    # Start server in background
    server_task = asyncio.create_task(server.start())
    
    # Wait for server to start
    await asyncio.sleep(1)
    
    yield server
    
    # Cleanup
    server_task.cancel()
    await server.shutdown()


@pytest.mark.asyncio
async def test_multiple_client_sessions():
    """Test multiple client sessions simultaneously."""
    
    await session_manager.start()
    
    try:
        # Create multiple client sessions
        sessions = []
        for i in range(5):
            session = await session_manager.create_session(
                f"client_{i}",
                ClientType.HTTP_CLIENT,
                {"test": True}
            )
            sessions.append(session)
        
        # Verify sessions are isolated
        assert len(sessions) == 5
        
        session_ids = [s.session_id for s in sessions]
        assert len(set(session_ids)) == 5  # All unique
        
        # Test session activity
        for session in sessions:
            session.add_active_request(f"req_{session.client_id}")
            await asyncio.sleep(0.1)
            session.remove_active_request(f"req_{session.client_id}", True)
        
        # Verify metrics
        stats = await session_manager.get_session_stats()
        assert stats["total_sessions"] == 5
        assert stats["unique_clients"] == 5
    
    finally:
        await session_manager.stop()


@pytest.mark.asyncio 
async def test_client_resource_isolation():
    """Test resource isolation between clients."""
    
    await session_manager.start()
    
    try:
        # Create clients with different resource usage
        heavy_client = await session_manager.create_session("heavy_client", ClientType.CLAUDE_DESKTOP)
        light_client = await session_manager.create_session("light_client", ClientType.HTTP_CLIENT)
        
        # Simulate heavy usage on one client
        for i in range(50):
            heavy_client.add_active_request(f"heavy_req_{i}")
        
        # Light client should still be responsive
        light_client.add_active_request("light_req")
        
        # Verify isolation
        assert len(heavy_client.active_requests) == 50
        assert len(light_client.active_requests) == 1
        
        # Check that light client isn't affected by heavy client
        assert light_client.metrics.total_requests == 1
        assert heavy_client.metrics.total_requests == 50
    
    finally:
        await session_manager.stop()


@pytest.mark.asyncio
async def test_connection_multiplexing():
    """Test connection multiplexing for multiple clients."""
    
    from snowflake_mcp_server.utils.request_context import RequestContext
    
    await session_manager.start()
    
    try:
        # Create multiple clients
        clients = []
        for i in range(3):
            client = await session_manager.create_session(f"multiplex_client_{i}", ClientType.CLAUDE_CODE)
            clients.append(client)
        
        # Test concurrent connection usage
        async def use_connection(client, operation_id):
            request_ctx = RequestContext(
                request_id=f"req_{client.client_id}_{operation_id}",
                client_id=client.client_id,
                tool_name="test_multiplex",
                arguments={},
                start_time=datetime.now()
            )
            
            async with connection_multiplexer.acquire_for_request(client, request_ctx) as conn:
                # Simulate database work
                await asyncio.sleep(0.1)
                return f"result_{client.client_id}_{operation_id}"
        
        # Run concurrent operations across clients
        tasks = []
        for client in clients:
            for op_id in range(2):
                tasks.append(use_connection(client, op_id))
        
        results = await asyncio.gather(*tasks)
        
        # Verify all operations completed
        assert len(results) == 6  # 3 clients * 2 operations
        assert all(r.startswith("result_") for r in results)
        
        # Check multiplexer stats
        stats = connection_multiplexer.get_global_stats()
        assert stats["global_stats"]["total_clients"] >= 3
    
    finally:
        await session_manager.stop()
```

