# Phase 1: Request Isolation Implementation Details

## Context & Overview

The current Snowflake MCP server shares connection state across all MCP tool calls, creating potential race conditions and data consistency issues when multiple clients or concurrent requests modify database/schema context or transaction state.

**Current Issues:**
- Global connection state shared between all tool calls
- `USE DATABASE` and `USE SCHEMA` commands affect all subsequent operations
- No request boundaries or isolation between MCP tool calls
- Transaction state shared across concurrent operations
- Session parameters can be modified by one request affecting others

**Target Architecture:**
- Per-request connection isolation from connection pool
- Request context tracking with unique IDs
- Isolated database/schema context per tool call
- Transaction boundary management per operation
- Request-level logging and error tracking

## Current State Analysis

### Problematic State Sharing in `main.py`

Lines 145-148 in `handle_list_views`:
```python
# GLOBAL STATE CHANGE: Affects all future requests
if database:
    conn.cursor().execute(f"USE DATABASE {database}")
if schema:
    conn.cursor().execute(f"USE SCHEMA {schema}")
```

Lines 433-436 in `handle_execute_query`:
```python
# GLOBAL STATE CHANGE: Persists beyond current request
if database:
    conn.cursor().execute(f"USE DATABASE {database}")
if schema:
    conn.cursor().execute(f"USE SCHEMA {schema}")
```

## Implementation Plan

### 5. Concurrency Testing {#concurrency-testing}

**Create Concurrent Request Test Suite**

Create `tests/test_request_isolation.py`:

```python
import pytest
import asyncio
from datetime import datetime
from snowflake_mcp_server.utils.request_context import RequestContextManager, request_context

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
    
    async def request_with_db_change(database: str):
        """Request that changes database context."""
        async with request_context("execute_query", {"database": database}, "test_client") as ctx:
            async with get_isolated_database_ops(ctx) as db_ops:
                # Change database context in isolation
                await db_ops.use_database_isolated(database)
                
                # Verify context is set
                current_db, _ = await db_ops.get_current_context()
                return current_db
    
    # Run concurrent requests with different database contexts
    results = await asyncio.gather(
        request_with_db_change("DATABASE_A"),
        request_with_db_change("DATABASE_B"),
        request_with_db_change("DATABASE_C"),
    )
    
    # Each request should see its own database context
    # (Note: This test requires actual Snowflake connection)
    assert len(set(results)) == 3  # All different results


@pytest.mark.asyncio
async def test_request_context_cleanup():
    """Test that request contexts are properly cleaned up."""
    
    manager = RequestContextManager()
    
    # Create some requests
    contexts = []
    for i in range(5):
        async with request_context(f"tool_{i}", {}, f"client_{i}") as ctx:
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
            async with request_context("failing_tool", {}, "test_client") as ctx:
                raise Exception("Simulated error")
        except Exception:
            results["error"] += 1
    
    async def successful_request():
        """Request that succeeds."""
        async with request_context("success_tool", {}, "test_client") as ctx:
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
```

## Performance Testing

Create `scripts/test_isolation_performance.py`:

```python
#!/usr/bin/env python3

import asyncio
import time
import statistics
from snowflake_mcp_server.utils.request_context import request_context

async def test_isolation_overhead():
    """Test performance overhead of request isolation."""
    
    # Test without isolation (direct operation)
    start_time = time.time()
    for _ in range(100):
        # Simulate simple operation
        await asyncio.sleep(0.001)
    no_isolation_time = time.time() - start_time
    
    # Test with isolation
    start_time = time.time()
    for i in range(100):
        async with request_context(f"test_tool_{i}", {}, "test_client"):
            await asyncio.sleep(0.001)
    with_isolation_time = time.time() - start_time
    
    overhead_percent = ((with_isolation_time - no_isolation_time) / no_isolation_time) * 100
    
    print(f"Without isolation: {no_isolation_time:.3f}s")
    print(f"With isolation: {with_isolation_time:.3f}s")
    print(f"Overhead: {overhead_percent:.1f}%")
    
    # Overhead should be minimal (<20%)
    assert overhead_percent < 20


async def test_concurrent_isolation_performance():
    """Test performance under concurrent load."""
    
    async def isolated_operation(client_id: str, operation_id: int):
        """Single isolated operation."""
        async with request_context(f"operation_{operation_id}", {}, client_id):
            # Simulate database work
            await asyncio.sleep(0.01)
            return f"result_{operation_id}"
    
    # Test concurrent operations
    start_time = time.time()
    tasks = [
        isolated_operation(f"client_{i % 5}", i)  # 5 different clients
        for i in range(100)
    ]
    results = await asyncio.gather(*tasks)
    total_time = time.time() - start_time
    
    print(f"100 concurrent isolated operations: {total_time:.3f}s")
    print(f"Average time per operation: {total_time/100*1000:.1f}ms")
    
    # Verify all operations completed
    assert len(results) == 100
    assert all(r.startswith("result_") for r in results)


if __name__ == "__main__":
    asyncio.run(test_isolation_overhead())
    asyncio.run(test_concurrent_isolation_performance())
```

## Verification Steps

1. **Context Isolation**: Verify each request gets unique context with proper tracking
2. **Database State**: Confirm database/schema changes don't leak between requests  
3. **Connection Isolation**: Test that each request gets its own connection from pool
4. **Transaction Boundaries**: Verify transactions are isolated per request
5. **Error Isolation**: Confirm errors in one request don't affect others
6. **Performance**: Measure isolation overhead (<20% performance impact)
7. **Cleanup**: Verify request contexts are properly cleaned up after completion

## Completion Criteria

- [ ] Request context manager tracks all tool calls with unique IDs
- [ ] Database context changes isolated per request with automatic restoration
- [ ] Connection pool provides isolated connections per request
- [ ] Transaction boundaries properly managed per request
- [ ] Request logging includes context information for debugging
- [ ] Concurrent requests don't interfere with each other's state
- [ ] Performance overhead of isolation is under 20%
- [ ] Error handling preserves isolation and doesn't affect other requests
- [ ] Memory usage remains stable with proper context cleanup
- [ ] Integration tests demonstrate 10+ concurrent clients operating independently