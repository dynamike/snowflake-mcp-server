# Phase 1: Async Operations Implementation Details

## Context & Overview

The current Snowflake MCP server in `snowflake_mcp_server/main.py` uses blocking synchronous database operations within async handler functions. This creates a performance bottleneck where each database call blocks the entire event loop, preventing true concurrent request processing.

**Current Issues:**
- `conn.cursor().execute()` calls are synchronous and block the event loop
- Multiple concurrent MCP requests queue up waiting for database operations
- Async/await keywords are used but don't provide actual concurrency benefits
- Thread pool executor not utilized for blocking I/O operations

**Target Architecture:**
- True async database operations using thread pool executors
- Non-blocking cursor management with proper resource cleanup
- Async context managers for connection acquisition/release
- Error handling optimized for async contexts

## Current State Analysis

### Problematic Patterns in `main.py`

Lines 91-120 in `handle_list_databases`:
```python
# BLOCKING: This blocks the event loop
conn = connection_manager.get_connection()
cursor = conn.cursor()
cursor.execute("SHOW DATABASES")  # Blocks until complete

# BLOCKING: Synchronous result processing
for row in cursor:
    databases.append(row[1])
```

Lines 164-174 in `handle_list_views`:
```python
# BLOCKING: Multiple synchronous execute calls
cursor.execute(f"SHOW VIEWS IN {database}.{schema}")
for row in cursor:
    view_name = row[1]
    # ... more blocking processing
```

## Implementation Plan

### 5. Performance Validation {#performance-validation}

**Benchmark Async vs Sync Performance**

Create `scripts/benchmark_async_performance.py`:

```python
#!/usr/bin/env python3

import asyncio
import time
import statistics
from concurrent.futures import ThreadPoolExecutor

async def benchmark_async_vs_sync():
    """Compare async vs sync performance."""
    
    # Initialize async infrastructure
    await initialize_async_infrastructure()
    
    # Test 1: Sequential operations
    print("=== Sequential Operations ===")
    
    # Async sequential
    start = time.time()
    for _ in range(10):
        async with get_async_database_ops() as db_ops:
            await db_ops.execute_query("SELECT 1")
    async_sequential_time = time.time() - start
    
    # Sync sequential (legacy)
    start = time.time()
    for _ in range(10):
        conn = connection_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
    sync_sequential_time = time.time() - start
    
    print(f"Async sequential: {async_sequential_time:.3f}s")
    print(f"Sync sequential: {sync_sequential_time:.3f}s")
    print(f"Improvement: {sync_sequential_time/async_sequential_time:.1f}x")
    
    # Test 2: Concurrent operations
    print("\n=== Concurrent Operations ===")
    
    async def async_operation():
        async with get_async_database_ops() as db_ops:
            return await db_ops.execute_query("SELECT COUNT(*) FROM INFORMATION_SCHEMA.DATABASES")
    
    # Async concurrent
    start = time.time()
    tasks = [async_operation() for _ in range(20)]
    await asyncio.gather(*tasks)
    async_concurrent_time = time.time() - start
    
    # Sync concurrent (simulated)
    def sync_operation():
        conn = connection_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.DATABASES")
        result = cursor.fetchone()
        cursor.close()
        return result
    
    start = time.time()
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(sync_operation) for _ in range(20)]
        results = [f.result() for f in futures]
    sync_concurrent_time = time.time() - start
    
    print(f"Async concurrent: {async_concurrent_time:.3f}s")
    print(f"Sync concurrent: {sync_concurrent_time:.3f}s")
    print(f"Improvement: {sync_concurrent_time/async_concurrent_time:.1f}x")
    
    # Test 3: Memory usage comparison
    print("\n=== Memory Usage Test ===")
    import psutil
    import os
    
    process = psutil.Process(os.getpid())
    initial_memory = process.memory_info().rss / 1024 / 1024  # MB
    
    # Create many concurrent operations
    tasks = [async_operation() for _ in range(100)]
    await asyncio.gather(*tasks)
    
    peak_memory = process.memory_info().rss / 1024 / 1024  # MB
    print(f"Memory usage: {initial_memory:.1f} MB -> {peak_memory:.1f} MB")
    print(f"Memory increase: {peak_memory - initial_memory:.1f} MB")


if __name__ == "__main__":
    asyncio.run(benchmark_async_vs_sync())
```

## Testing Strategy

### Unit Tests for Async Operations

Create `tests/test_async_operations.py`:

```python
import pytest
import asyncio
from unittest.mock import Mock, patch
from snowflake_mcp_server.utils.async_database import AsyncDatabaseOperations

@pytest.mark.asyncio
async def test_async_database_operations():
    """Test async database operation wrapper."""
    
    # Mock connection
    mock_connection = Mock()
    mock_cursor = Mock()
    mock_connection.cursor.return_value = mock_cursor
    
    # Mock query results
    mock_cursor.fetchall.return_value = [('database1',), ('database2',)]
    mock_cursor.description = [('name',)]
    
    db_ops = AsyncDatabaseOperations(mock_connection)
    
    # Test async query execution
    results, columns = await db_ops.execute_query("SHOW DATABASES")
    
    assert len(results) == 2
    assert results[0][0] == 'database1'
    assert columns == ['name']
    
    # Verify cursor was closed
    mock_cursor.close.assert_called_once()


@pytest.mark.asyncio
async def test_concurrent_async_operations():
    """Test multiple concurrent async operations."""
    
    mock_connection = Mock()
    mock_cursor = Mock()
    mock_connection.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [('test',)]
    mock_cursor.description = [('col1',)]
    
    db_ops = AsyncDatabaseOperations(mock_connection)
    
    # Run multiple concurrent operations
    tasks = [
        db_ops.execute_query("SELECT 1"),
        db_ops.execute_query("SELECT 2"),
        db_ops.execute_query("SELECT 3"),
    ]
    
    results = await asyncio.gather(*tasks)
    
    assert len(results) == 3
    # Verify all operations completed


@pytest.mark.asyncio
async def test_async_error_handling():
    """Test error handling in async operations."""
    
    mock_connection = Mock()
    mock_connection.cursor.side_effect = Exception("Connection failed")
    
    db_ops = AsyncDatabaseOperations(mock_connection)
    
    with pytest.raises(Exception) as exc_info:
        await db_ops.execute_query("SELECT 1")
    
    assert "Connection failed" in str(exc_info.value)
```

### Integration Tests

Create `tests/test_async_handlers.py`:

```python
@pytest.mark.asyncio
async def test_async_list_databases():
    """Test async database listing handler."""
    
    # Initialize async infrastructure
    await initialize_async_infrastructure()
    
    # Test handler
    result = await handle_list_databases("list_databases")
    
    assert len(result) == 1
    assert isinstance(result[0], mcp_types.TextContent)
    assert "Available Snowflake databases:" in result[0].text


@pytest.mark.asyncio
async def test_concurrent_handlers():
    """Test multiple handlers running concurrently."""
    
    await initialize_async_infrastructure()
    
    # Run multiple handlers concurrently
    tasks = [
        handle_list_databases("list_databases"),
        handle_list_databases("list_databases"),
        handle_list_databases("list_databases"),
    ]
    
    results = await asyncio.gather(*tasks)
    
    assert len(results) == 3
    assert all(len(result) == 1 for result in results)
```

## Verification Steps

1. **Async Conversion**: Verify all database operations use thread pool executor
2. **Cursor Management**: Confirm cursors are properly closed after operations
3. **Connection Pooling**: Test connection acquisition/release works correctly
4. **Error Handling**: Verify exceptions are properly caught and handled
5. **Performance**: Measure improvement in concurrent operation throughput
6. **Resource Cleanup**: Ensure no cursor or connection leaks

## Completion Criteria

- [ ] All handlers converted to use AsyncDatabaseOperations
- [ ] Thread pool executor used for all blocking database calls
- [ ] Cursor lifecycle properly managed with automatic cleanup
- [ ] Connection acquisition/release works with pool
- [ ] Error handling preserves async context and provides meaningful messages
- [ ] Performance tests show 3x improvement in concurrent throughput
- [ ] Resource leak tests pass after 1000 operations
- [ ] Integration tests demonstrate multiple clients can operate concurrently

## Performance Targets

- **Sequential Operations**: 20% improvement over sync version
- **Concurrent Operations**: 300%+ improvement with 10+ concurrent requests
- **Memory Usage**: No memory leaks after extended operation
- **Error Recovery**: Graceful handling of connection failures with automatic retry