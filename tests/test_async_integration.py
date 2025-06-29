"""Comprehensive integration tests for async operations in Snowflake MCP server."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from snowflake_mcp_server.utils.async_database import (
    AsyncDatabaseOperations,
    IsolatedDatabaseOperations,
    TransactionalDatabaseOperations,
    get_async_database_ops,
    get_isolated_database_ops,
    get_transactional_database_ops,
)
from snowflake_mcp_server.utils.async_pool import (
    AsyncConnectionPool,
    ConnectionPoolConfig,
    PooledConnection,
    get_connection_pool,
    initialize_connection_pool,
    close_connection_pool,
)
from snowflake_mcp_server.utils.request_context import (
    RequestContext,
    request_context,
)
from snowflake_mcp_server.utils.snowflake_conn import SnowflakeConfig


@pytest.fixture
async def mock_snowflake_config():
    """Mock Snowflake configuration for testing."""
    return SnowflakeConfig(
        account="test_account",
        user="test_user",
        password="test_password",
        warehouse="test_warehouse",
        database="test_database",
        schema_name="test_schema",
    )


@pytest.fixture
async def mock_connection():
    """Mock Snowflake connection for testing."""
    mock_conn = MagicMock()
    mock_conn.is_closed.return_value = False
    mock_conn.close = MagicMock()
    
    # Mock cursor creation and execution
    mock_cursor = MagicMock()
    mock_cursor.execute = MagicMock()
    mock_cursor.fetchall = MagicMock(return_value=[("test_result",)])
    mock_cursor.fetchone = MagicMock(return_value=("single_result",))
    mock_cursor.fetchmany = MagicMock(return_value=[("limited_result",)])
    mock_cursor.description = [("column1",), ("column2",)]
    mock_cursor.close = MagicMock()
    
    mock_conn.cursor = MagicMock(return_value=mock_cursor)
    
    return mock_conn


@pytest.mark.asyncio
async def test_async_connection_pool_lifecycle(mock_snowflake_config):
    """Test complete lifecycle of async connection pool."""
    
    pool_config = ConnectionPoolConfig(
        min_size=2,
        max_size=5,
        connection_timeout=10.0,
        retry_attempts=2
    )
    
    # Mock connection creation
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create:
        mock_connections = []
        for i in range(5):
            mock_conn = MagicMock()
            mock_conn.is_closed.return_value = False
            mock_conn.close = MagicMock()
            mock_connections.append(mock_conn)
        
        mock_create.side_effect = mock_connections
        
        # Initialize pool
        pool = AsyncConnectionPool(mock_snowflake_config, pool_config)
        await pool.initialize()
        
        # Verify minimum connections created
        assert pool.total_connection_count >= pool_config.min_size
        assert pool.healthy_connection_count >= pool_config.min_size
        
        # Test connection acquisition
        connection_contexts = []
        for i in range(3):
            connection_contexts.append(pool.acquire())
        
        # Acquire connections concurrently
        async def acquire_and_use(pool_context):
            async with pool_context as conn:
                # Simulate database work
                await asyncio.sleep(0.1)
                return str(id(conn))
        
        # Test concurrent acquisition
        tasks = [acquire_and_use(ctx) for ctx in connection_contexts]
        results = await asyncio.gather(*tasks)
        
        # Verify all acquisitions succeeded
        assert len(results) == 3
        assert all(result for result in results)
        
        # Test pool statistics
        stats = pool.get_stats()
        assert stats["total_connections"] >= pool_config.min_size
        assert stats["healthy_connections"] >= pool_config.min_size
        
        # Test pool closure
        await pool.close()
        assert pool._closed


@pytest.mark.asyncio
async def test_pooled_connection_health_checks(mock_snowflake_config):
    """Test connection health monitoring and management."""
    
    pool_config = ConnectionPoolConfig(
        min_size=2,
        max_size=4,
        health_check_interval=timedelta(seconds=1)
    )
    
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create, \
         patch('snowflake_mcp_server.utils.async_pool.test_connection_health') as mock_health:
        
        # Create mock connections with different health states
        healthy_conn = MagicMock()
        healthy_conn.is_closed.return_value = False
        healthy_conn.close = MagicMock()
        
        unhealthy_conn = MagicMock()
        unhealthy_conn.is_closed.return_value = True
        unhealthy_conn.close = MagicMock()
        
        mock_create.side_effect = [healthy_conn, unhealthy_conn, healthy_conn, healthy_conn]
        mock_health.side_effect = [True, False, True, True]  # Health check results
        
        pool = AsyncConnectionPool(mock_snowflake_config, pool_config)
        await pool.initialize()
        
        # Wait for initial health checks
        await asyncio.sleep(0.1)
        
        # Manually trigger health checks
        await pool._perform_health_checks()
        
        # Verify unhealthy connections are removed
        healthy_count = pool.healthy_connection_count
        assert healthy_count >= pool_config.min_size
        
        await pool.close()


@pytest.mark.asyncio
async def test_async_database_operations_cursor_management(mock_connection):
    """Test async database operations with proper cursor management."""
    
    db_ops = AsyncDatabaseOperations(mock_connection)
    
    # Test query execution
    results, columns = await db_ops.execute_query("SELECT * FROM test_table")
    
    assert results == [("test_result",)]
    assert len(columns) == 2
    
    # Verify cursor was created and closed properly
    mock_connection.cursor.assert_called()
    cursor = mock_connection.cursor.return_value
    cursor.execute.assert_called_with("SELECT * FROM test_table")
    cursor.close.assert_called()
    
    # Test single result query
    result = await db_ops.execute_query_one("SELECT COUNT(*) FROM test_table")
    assert result == ("single_result",)
    
    # Test limited query
    results, columns = await db_ops.execute_query_limited("SELECT * FROM test_table LIMIT 5", 5)
    assert results == [("limited_result",)]
    
    # Test context operations
    db, schema = await db_ops.get_current_context()
    assert db == "test_result"
    assert schema == "test_result"
    
    # Test cleanup
    await db_ops.cleanup()


@pytest.mark.asyncio
async def test_isolated_database_operations(mock_connection):
    """Test isolated database operations with request context."""
    
    async with request_context("test_tool", {"test": True}, "test_client") as ctx:
        isolated_ops = IsolatedDatabaseOperations(mock_connection, ctx)
        
        async with isolated_ops as db_ops:
            # Test isolated query execution
            results, columns = await db_ops.execute_query_isolated("SELECT * FROM isolated_table")
            
            # Verify request metrics were updated
            assert ctx.metrics.queries_executed == 1
            assert ctx.get_duration_ms() > 0
            
            # Test database context switching
            await db_ops.use_database_isolated("test_database")
            assert ctx.database_context == "test_database"
            
            # Test schema context switching
            await db_ops.use_schema_isolated("test_schema")
            assert ctx.schema_context == "test_schema"


@pytest.mark.asyncio
async def test_transactional_database_operations(mock_connection):
    """Test transactional database operations."""
    
    async with request_context("test_tool", {"test": True}, "test_client") as ctx:
        tx_ops = TransactionalDatabaseOperations(mock_connection, ctx)
        
        async with tx_ops as db_ops:
            # Test single query with transaction
            results, columns = await db_ops.execute_with_transaction(
                "INSERT INTO test_table VALUES (1, 'test')",
                auto_commit=True
            )
            
            # Verify transaction metrics
            assert ctx.metrics.transaction_operations == 1
            
            # Test multi-statement transaction
            queries = [
                "INSERT INTO test_table VALUES (2, 'test2')",
                "INSERT INTO test_table VALUES (3, 'test3')",
                "UPDATE test_table SET value = 'updated' WHERE id = 1"
            ]
            
            results_list = await db_ops.execute_multi_statement_transaction(queries)
            assert len(results_list) == 3
            
            # Test explicit transaction control
            await db_ops.begin_explicit_transaction()
            await db_ops.execute_query_isolated("INSERT INTO test_table VALUES (4, 'test4')")
            await db_ops.commit_transaction()
            
            assert ctx.metrics.transaction_commits >= 1


@pytest.mark.asyncio
async def test_concurrent_async_operations():
    """Test concurrent async database operations."""
    
    # Mock multiple connections
    connections = []
    for i in range(5):
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        mock_conn.close = MagicMock()
        
        mock_cursor = MagicMock()
        mock_cursor.execute = MagicMock()
        mock_cursor.fetchall = MagicMock(return_value=[(f"result_{i}",)])
        mock_cursor.description = [("column1",)]
        mock_cursor.close = MagicMock()
        mock_conn.cursor = MagicMock(return_value=mock_cursor)
        
        connections.append(mock_conn)
    
    async def concurrent_operation(connection, operation_id: int):
        """Simulate concurrent database operation."""
        db_ops = AsyncDatabaseOperations(connection)
        
        try:
            # Execute multiple queries concurrently within the operation
            tasks = []
            for i in range(3):
                query = f"SELECT {operation_id}_{i} as result"
                tasks.append(db_ops.execute_query(query))
            
            results = await asyncio.gather(*tasks)
            
            # Verify all queries completed
            assert len(results) == 3
            for result, columns in results:
                assert len(result) == 1
                assert len(columns) == 1
            
            return operation_id
            
        finally:
            await db_ops.cleanup()
    
    # Run concurrent operations
    start_time = time.time()
    tasks = [concurrent_operation(conn, i) for i, conn in enumerate(connections)]
    results = await asyncio.gather(*tasks)
    end_time = time.time()
    
    # Verify all operations completed successfully
    assert results == list(range(5))
    assert end_time - start_time < 2.0  # Should complete quickly due to async


@pytest.mark.asyncio
async def test_async_error_handling():
    """Test async error handling and recovery."""
    
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    
    # Simulate connection errors
    mock_cursor.execute.side_effect = [
        Exception("Connection lost"),
        None,  # Recovery
        Exception("Query timeout"),
        None   # Recovery
    ]
    
    mock_cursor.fetchall.return_value = [("recovered_result",)]
    mock_cursor.description = [("column1",)]
    mock_cursor.close = MagicMock()
    mock_conn.cursor = MagicMock(return_value=mock_cursor)
    
    db_ops = AsyncDatabaseOperations(mock_conn)
    
    # Test error handling in query execution
    with pytest.raises(Exception, match="Connection lost"):
        await db_ops.execute_query("SELECT * FROM test_table")
    
    # Test successful execution after error
    with pytest.raises(Exception, match="Query timeout"):
        await db_ops.execute_query("SELECT * FROM test_table")
    
    # Verify cleanup still works after errors
    await db_ops.cleanup()


@pytest.mark.asyncio
async def test_async_context_managers():
    """Test async context managers for database operations."""
    
    # Mock pool and connection
    with patch('snowflake_mcp_server.utils.async_database.get_connection_pool') as mock_get_pool:
        mock_pool = AsyncMock()
        mock_connection = MagicMock()
        mock_connection.is_closed.return_value = False
        
        @asynccontextmanager
        async def mock_acquire():
            yield mock_connection
        
        mock_pool.acquire = mock_acquire
        mock_get_pool.return_value = mock_pool
        
        # Test basic async database ops context
        async with get_async_database_ops() as db_ops:
            assert isinstance(db_ops, AsyncDatabaseOperations)
            assert db_ops.connection == mock_connection
        
        # Test isolated database ops context
        async with request_context("test_tool", {}, "test_client") as ctx:
            async with get_isolated_database_ops(ctx) as isolated_ops:
                assert isinstance(isolated_ops, IsolatedDatabaseOperations)
                assert isolated_ops.connection == mock_connection
                assert isolated_ops.request_context == ctx
        
        # Test transactional database ops context
        async with request_context("test_tool", {}, "test_client") as ctx:
            async with get_transactional_database_ops(ctx) as tx_ops:
                assert isinstance(tx_ops, TransactionalDatabaseOperations)
                assert tx_ops.connection == mock_connection
                assert tx_ops.request_context == ctx


@pytest.mark.asyncio
async def test_connection_pool_under_load():
    """Test connection pool behavior under high load."""
    
    config = SnowflakeConfig(
        account="test", user="test", password="test",
        warehouse="test", database="test", schema_name="test"
    )
    
    pool_config = ConnectionPoolConfig(
        min_size=3,
        max_size=10,
        connection_timeout=5.0
    )
    
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create:
        # Create mock connections
        mock_connections = []
        for i in range(15):  # More than max pool size
            mock_conn = MagicMock()
            mock_conn.is_closed.return_value = False
            mock_conn.close = MagicMock()
            mock_connections.append(mock_conn)
        
        mock_create.side_effect = mock_connections
        
        pool = AsyncConnectionPool(config, pool_config)
        await pool.initialize()
        
        # Generate high load
        async def high_load_operation(operation_id: int):
            async with pool.acquire() as conn:
                # Simulate work
                await asyncio.sleep(0.2)
                return operation_id
        
        # Create more concurrent operations than max pool size
        tasks = [high_load_operation(i) for i in range(20)]
        
        start_time = time.time()
        results = await asyncio.gather(*tasks)
        end_time = time.time()
        
        # Verify all operations completed
        assert len(results) == 20
        assert results == list(range(20))
        
        # Pool should not exceed max size
        assert pool.total_connection_count <= pool_config.max_size
        
        # Operations should complete in reasonable time (connection reuse)
        assert end_time - start_time < 10.0
        
        await pool.close()


@pytest.mark.asyncio
async def test_async_performance_benchmarks():
    """Benchmark async operations vs theoretical sync performance."""
    
    # Create multiple mock connections for realistic pooling
    mock_connections = []
    for i in range(5):
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        mock_conn.close = MagicMock()
        
        mock_cursor = MagicMock()
        mock_cursor.execute = MagicMock()
        mock_cursor.fetchall = MagicMock(return_value=[(f"result_{i}",)] * 100)
        mock_cursor.description = [("column1",), ("column2",)]
        mock_cursor.close = MagicMock()
        mock_conn.cursor = MagicMock(return_value=mock_cursor)
        
        mock_connections.append(mock_conn)
    
    # Simulate async execution with concurrency
    async def async_query_batch(connections, query_count: int):
        """Execute batch of queries asynchronously."""
        async def single_query(conn, query_id):
            db_ops = AsyncDatabaseOperations(conn)
            try:
                # Simulate query execution time
                await asyncio.sleep(0.01)  # 10ms simulated query time
                results, columns = await db_ops.execute_query(f"SELECT * FROM table_{query_id}")
                return len(results)
            finally:
                await db_ops.cleanup()
        
        # Distribute queries across connections
        tasks = []
        for i in range(query_count):
            conn = mock_connections[i % len(mock_connections)]
            tasks.append(single_query(conn, i))
        
        start_time = time.time()
        results = await asyncio.gather(*tasks)
        end_time = time.time()
        
        return results, end_time - start_time
    
    # Benchmark different concurrency levels
    test_cases = [
        (10, "Low concurrency"),
        (50, "Medium concurrency"),
        (100, "High concurrency"),
    ]
    
    performance_results = []
    
    for query_count, description in test_cases:
        results, execution_time = await async_query_batch(mock_connections, query_count)
        
        # Verify all queries completed successfully
        assert len(results) == query_count
        assert all(result == 100 for result in results)  # 100 rows per query
        
        throughput = query_count / execution_time
        performance_results.append({
            "description": description,
            "query_count": query_count,
            "execution_time": execution_time,
            "throughput": throughput
        })
        
        print(f"{description}: {query_count} queries in {execution_time:.3f}s "
              f"({throughput:.1f} queries/sec)")
    
    # Verify performance improves with async concurrency
    # High concurrency should have better throughput than low concurrency
    high_concurrency_throughput = performance_results[2]["throughput"]
    low_concurrency_throughput = performance_results[0]["throughput"]
    
    # Async should provide significant throughput improvement
    improvement_ratio = high_concurrency_throughput / low_concurrency_throughput
    assert improvement_ratio > 2.0, f"Expected >2x improvement, got {improvement_ratio:.1f}x"
    
    print(f"\n🚀 Async Performance Summary:")
    print(f"   Low concurrency throughput: {low_concurrency_throughput:.1f} queries/sec")
    print(f"   High concurrency throughput: {high_concurrency_throughput:.1f} queries/sec")
    print(f"   Performance improvement: {improvement_ratio:.1f}x")


@pytest.mark.asyncio
async def test_async_resource_cleanup():
    """Test proper cleanup of async resources."""
    
    cleanup_tracker = {
        "cursors_closed": 0,
        "connections_closed": 0,
        "pools_closed": 0
    }
    
    # Mock connection with cleanup tracking
    mock_conn = MagicMock()
    mock_conn.is_closed.return_value = False
    
    def track_connection_close():
        cleanup_tracker["connections_closed"] += 1
    
    mock_conn.close = MagicMock(side_effect=track_connection_close)
    
    # Mock cursor with cleanup tracking
    mock_cursor = MagicMock()
    
    def track_cursor_close():
        cleanup_tracker["cursors_closed"] += 1
    
    mock_cursor.close = MagicMock(side_effect=track_cursor_close)
    mock_cursor.execute = MagicMock()
    mock_cursor.fetchall = MagicMock(return_value=[("test",)])
    mock_cursor.description = [("col1",)]
    
    mock_conn.cursor = MagicMock(return_value=mock_cursor)
    
    # Test cursor cleanup in AsyncDatabaseOperations
    db_ops = AsyncDatabaseOperations(mock_conn)
    
    # Execute multiple queries to create multiple cursors
    for i in range(3):
        await db_ops.execute_query(f"SELECT {i}")
    
    # Cleanup should close all cursors
    await db_ops.cleanup()
    assert cleanup_tracker["cursors_closed"] == 3
    
    # Test cleanup in context managers
    async with request_context("test_tool", {}, "test_client") as ctx:
        isolated_ops = IsolatedDatabaseOperations(mock_conn, ctx)
        
        async with isolated_ops as db_ops:
            await db_ops.execute_query_isolated("SELECT 'isolated'")
        
        # Context manager should trigger cleanup
        assert cleanup_tracker["cursors_closed"] == 4  # Previous 3 + 1 new
    
    # Test pool cleanup
    config = SnowflakeConfig(
        account="test", user="test", password="test",
        warehouse="test", database="test", schema_name="test"
    )
    
    pool_config = ConnectionPoolConfig(min_size=2, max_size=3)
    
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create:
        # Create connections that track cleanup
        test_connections = []
        for i in range(3):
            test_conn = MagicMock()
            test_conn.is_closed.return_value = False
            test_conn.close = MagicMock(side_effect=track_connection_close)
            test_connections.append(test_conn)
        
        mock_create.side_effect = test_connections
        
        pool = AsyncConnectionPool(config, pool_config)
        await pool.initialize()
        
        # Use some connections
        async with pool.acquire() as conn:
            pass
        
        # Close pool should cleanup all connections
        initial_closed = cleanup_tracker["connections_closed"]
        await pool.close()
        
        # Should have closed all pool connections
        assert cleanup_tracker["connections_closed"] >= initial_closed + 2  # At least min_size
    
    print(f"\n🧹 Resource Cleanup Summary:")
    print(f"   Cursors closed: {cleanup_tracker['cursors_closed']}")
    print(f"   Connections closed: {cleanup_tracker['connections_closed']}")


if __name__ == "__main__":
    # Run with pytest for detailed output
    pytest.main([__file__, "-v", "-s", "--tb=short"])