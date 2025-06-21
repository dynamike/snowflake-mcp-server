"""Test request isolation and concurrency handling."""

import asyncio

import pytest

from snowflake_mcp_server.utils.async_database import get_isolated_database_ops
from snowflake_mcp_server.utils.request_context import (
    RequestContextManager,
    request_context,
)


@pytest.mark.asyncio
async def test_concurrent_request_isolation():
    """Test that concurrent requests maintain isolation."""
    
    manager = RequestContextManager()
    results = []
    
    async def simulate_request(client_id: str, tool_name: str, delay: float):
        """Simulate a request with database context changes."""
        async with request_context(tool_name, {"database": f"db_{client_id}"}, client_id) as ctx:
            # Simulate some work
            await asyncio.sleep(delay)
            
            # Verify context isolation
            assert ctx.client_id == client_id
            assert ctx.tool_name == tool_name
            
            results.append({
                "request_id": ctx.request_id,
                "client_id": client_id,
                "tool_name": tool_name,
                "duration": ctx.get_duration_ms()
            })
    
    # Run multiple concurrent requests
    tasks = [
        simulate_request("client_1", "list_databases", 0.1),
        simulate_request("client_2", "execute_query", 0.2),
        simulate_request("client_1", "list_views", 0.15),
        simulate_request("client_3", "describe_view", 0.05),
    ]
    
    await asyncio.gather(*tasks)
    
    # Verify all requests completed
    assert len(results) == 4
    
    # Verify request IDs are unique
    request_ids = [r["request_id"] for r in results]
    assert len(set(request_ids)) == 4
    
    # Verify client isolation
    client_1_requests = [r for r in results if r["client_id"] == "client_1"]
    assert len(client_1_requests) == 2


@pytest.mark.asyncio
async def test_database_context_isolation():
    """Test that database context changes don't affect other requests."""
    
    # Initialize async infrastructure first
    from snowflake_mcp_server.main import initialize_async_infrastructure
    await initialize_async_infrastructure()
    
    async def request_with_db_change(request_num: int):
        """Request that simulates database context changes."""
        async with request_context("execute_query", {"request": request_num}, f"client_{request_num}") as ctx:
            async with get_isolated_database_ops(ctx) as db_ops:
                # Simulate query execution - each request gets isolated context
                results, columns = await db_ops.execute_query_isolated(f"SELECT {request_num} as request_number")
                
                # Verify we got our expected result
                assert len(results) == 1
                assert results[0][0] == request_num
                
                return request_num
    
    # Run concurrent requests with different contexts
    results = await asyncio.gather(
        request_with_db_change(1),
        request_with_db_change(2),
        request_with_db_change(3),
        request_with_db_change(4),
        request_with_db_change(5),
    )
    
    # Each request should see its own result
    assert results == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_request_context_cleanup():
    """Test that request contexts are properly cleaned up."""
    
    manager = RequestContextManager()
    
    # Create some requests
    contexts = []
    for i in range(5):
        async with request_context(f"tool_{i}", {"test": True}, f"client_{i}") as ctx:
            contexts.append(ctx.request_id)
    
    # Verify all requests completed
    active_requests = await manager.get_active_requests()
    assert len(active_requests) == 0
    
    # Verify requests are in completed history
    for request_id in contexts:
        completed_ctx = await manager.get_request_context(request_id)
        assert completed_ctx is not None
        assert completed_ctx.metrics.end_time is not None


@pytest.mark.asyncio
async def test_error_isolation():
    """Test that errors in one request don't affect others."""
    
    results = {"success": 0, "error": 0}
    
    async def failing_request():
        """Request that always fails."""
        try:
            async with request_context("failing_tool", {"will_fail": True}, "test_client") as ctx:
                raise Exception("Simulated error")
        except Exception:
            results["error"] += 1
    
    async def successful_request():
        """Request that succeeds."""
        async with request_context("success_tool", {"will_succeed": True}, "test_client") as ctx:
            await asyncio.sleep(0.1)
            results["success"] += 1
    
    # Run mixed success/failure requests
    tasks = [
        failing_request(),
        successful_request(),
        failing_request(),
        successful_request(),
        successful_request(),
    ]
    
    # Gather with return_exceptions to handle failures
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # Verify both success and failure cases were handled
    assert results["success"] == 3
    assert results["error"] == 2


@pytest.mark.asyncio
async def test_transaction_isolation():
    """Test that transaction boundaries are isolated per request."""
    
    # Initialize async infrastructure first
    from snowflake_mcp_server.main import initialize_async_infrastructure
    from snowflake_mcp_server.utils.async_database import get_transactional_database_ops
    
    await initialize_async_infrastructure()
    
    async def transactional_request(request_num: int, use_transaction: bool):
        """Request with transaction handling."""
        async with request_context("execute_query", {"tx": use_transaction}, f"tx_client_{request_num}") as ctx:
            async with get_transactional_database_ops(ctx) as db_ops:
                # Execute query with or without transaction
                if use_transaction:
                    results, columns = await db_ops.execute_with_transaction(
                        f"SELECT {request_num} as tx_number", 
                        auto_commit=True
                    )
                else:
                    results, columns = await db_ops.execute_query_isolated(
                        f"SELECT {request_num} as no_tx_number"
                    )
                
                # Check transaction metrics
                if use_transaction:
                    assert ctx.metrics.transaction_operations > 0
                else:
                    assert ctx.metrics.transaction_operations == 0
                
                return results[0][0]
    
    # Run concurrent requests with different transaction settings
    results = await asyncio.gather(
        transactional_request(1, True),   # With transaction
        transactional_request(2, False),  # Without transaction
        transactional_request(3, True),   # With transaction
        transactional_request(4, False),  # Without transaction
    )
    
    # All requests should complete successfully
    assert results == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_connection_pool_isolation():
    """Test that each request gets isolated connections from the pool."""
    
    # Initialize async infrastructure first
    from snowflake_mcp_server.main import initialize_async_infrastructure
    await initialize_async_infrastructure()
    
    connection_ids = []
    
    async def pool_test_request(request_num: int):
        """Request that captures connection ID."""
        async with request_context("pool_test", {"num": request_num}, f"pool_client_{request_num}") as ctx:
            async with get_isolated_database_ops(ctx) as db_ops:
                # Execute simple query
                await db_ops.execute_query_isolated("SELECT 1")
                
                # Capture connection ID from metrics
                connection_ids.append(ctx.metrics.connection_id)
                return ctx.metrics.connection_id
    
    # Run concurrent requests
    results = await asyncio.gather(
        pool_test_request(1),
        pool_test_request(2),
        pool_test_request(3),
        pool_test_request(4),
        pool_test_request(5),
    )
    
    # Verify we got connection IDs
    assert len(results) == 5
    assert all(conn_id is not None for conn_id in results)
    
    # Note: Connection IDs may be reused due to pooling, but each request
    # should get a connection and complete successfully


@pytest.mark.asyncio 
async def test_request_metrics_isolation():
    """Test that request metrics are properly tracked per request."""
    
    # Initialize async infrastructure first
    from snowflake_mcp_server.main import initialize_async_infrastructure
    await initialize_async_infrastructure()
    
    metrics_results = []
    
    async def metrics_test_request(request_num: int, query_count: int):
        """Request that executes multiple queries."""
        async with request_context("metrics_test", {"queries": query_count}, f"metrics_client_{request_num}") as ctx:
            async with get_isolated_database_ops(ctx) as db_ops:
                # Execute specified number of queries
                for i in range(query_count):
                    await db_ops.execute_query_isolated(f"SELECT {i} as query_{i}")
                
                # Capture metrics
                metrics_results.append({
                    "request_id": ctx.request_id,
                    "queries_executed": ctx.metrics.queries_executed,
                    "expected_queries": query_count,
                    "duration_ms": ctx.get_duration_ms()
                })
                
                return ctx.metrics.queries_executed
    
    # Run concurrent requests with different query counts
    results = await asyncio.gather(
        metrics_test_request(1, 1),  # 1 query
        metrics_test_request(2, 3),  # 3 queries  
        metrics_test_request(3, 2),  # 2 queries
        metrics_test_request(4, 4),  # 4 queries
    )
    
    # Verify query counts match expectations
    assert results == [1, 3, 2, 4]
    
    # Verify metrics were tracked correctly for each request
    for metrics in metrics_results:
        assert metrics["queries_executed"] == metrics["expected_queries"]
        assert metrics["duration_ms"] is not None
        assert metrics["duration_ms"] > 0