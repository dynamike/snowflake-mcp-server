#!/usr/bin/env python3
"""Test performance impact of request isolation."""

import asyncio
import logging
import statistics
import time
from typing import List

from snowflake_mcp_server.main import initialize_async_infrastructure
from snowflake_mcp_server.utils.async_database import get_isolated_database_ops
from snowflake_mcp_server.utils.request_context import request_context


async def test_isolation_overhead():
    """Test performance overhead of request isolation."""
    
    print("🚀 Testing Request Isolation Performance Overhead")
    print("=" * 50)
    
    # Initialize infrastructure
    await initialize_async_infrastructure()
    
    # Test without isolation (direct operation)
    print("1. Testing operations without isolation...")
    start_time = time.time()
    for _ in range(100):
        # Simulate simple operation
        await asyncio.sleep(0.001)
    no_isolation_time = time.time() - start_time
    
    # Test with isolation
    print("2. Testing operations with isolation...")
    start_time = time.time()
    for i in range(100):
        async with request_context(f"test_tool_{i}", {"test": True}, "test_client"):
            await asyncio.sleep(0.001)
    with_isolation_time = time.time() - start_time
    
    overhead_percent = ((with_isolation_time - no_isolation_time) / no_isolation_time) * 100
    
    print(f"   📊 Without isolation: {no_isolation_time:.3f}s")
    print(f"   📊 With isolation: {with_isolation_time:.3f}s")
    print(f"   📊 Overhead: {overhead_percent:.1f}%")
    
    # Overhead should be minimal (<20%)
    if overhead_percent < 20:
        print("   ✅ Overhead within acceptable range (<20%)")
    else:
        print(f"   ⚠️  Overhead higher than expected (>{overhead_percent:.1f}%)")
    
    return overhead_percent


async def test_concurrent_isolation_performance():
    """Test performance under concurrent load."""
    
    print("\n🔄 Testing Concurrent Isolation Performance")
    print("=" * 50)
    
    async def isolated_operation(client_id: str, operation_id: int):
        """Single isolated operation."""
        async with request_context(f"operation_{operation_id}", {"op_id": operation_id}, client_id):
            # Simulate database work
            await asyncio.sleep(0.01)
            return f"result_{operation_id}"
    
    # Test concurrent operations
    print("1. Running 100 concurrent isolated operations...")
    start_time = time.time()
    tasks = [
        isolated_operation(f"client_{i % 5}", i)  # 5 different clients
        for i in range(100)
    ]
    results = await asyncio.gather(*tasks)
    total_time = time.time() - start_time
    
    print(f"   📊 100 concurrent isolated operations: {total_time:.3f}s")
    print(f"   📊 Average time per operation: {total_time/100*1000:.1f}ms")
    print(f"   📊 Operations per second: {100/total_time:.1f}")
    
    # Verify all operations completed
    assert len(results) == 100
    assert all(r.startswith("result_") for r in results)
    print(f"   ✅ All {len(results)} operations completed successfully")
    
    return total_time


async def test_database_isolation_performance():
    """Test performance of database operations with isolation."""
    
    print("\n💾 Testing Database Operations with Isolation")
    print("=" * 50)
    
    # Initialize infrastructure
    await initialize_async_infrastructure()
    
    async def database_operation(client_id: str, operation_id: int):
        """Database operation with full isolation."""
        async with request_context(f"db_operation_{operation_id}", {"db_op": True}, client_id) as ctx:
            async with get_isolated_database_ops(ctx) as db_ops:
                # Execute actual database query
                results, columns = await db_ops.execute_query_isolated(f"SELECT {operation_id} as op_id")
                return results[0][0]
    
    # Test database operations
    operation_counts = [10, 25, 50]
    
    for count in operation_counts:
        print(f"\n   Testing {count} concurrent database operations...")
        
        start_time = time.time()
        tasks = [
            database_operation(f"db_client_{i % 3}", i)  # 3 different clients
            for i in range(count)
        ]
        results = await asyncio.gather(*tasks)
        total_time = time.time() - start_time
        
        print(f"   📊 {count} database operations: {total_time:.3f}s")
        print(f"   📊 Average time per operation: {total_time/count*1000:.1f}ms")
        print(f"   📊 Database ops per second: {count/total_time:.1f}")
        
        # Verify results
        expected_results = list(range(count))
        assert sorted(results) == expected_results
        print(f"   ✅ All {len(results)} database operations completed correctly")


async def test_connection_pool_performance():
    """Test connection pool performance under load."""
    
    print("\n🔗 Testing Connection Pool Performance")
    print("=" * 50)
    
    # Initialize infrastructure
    await initialize_async_infrastructure()
    
    connection_times: List[float] = []
    
    async def pool_operation(operation_id: int):
        """Operation that measures connection acquisition time."""
        async with request_context(f"pool_op_{operation_id}", {"pool_test": True}, f"pool_client_{operation_id % 10}") as ctx:
            start_time = time.time()
            async with get_isolated_database_ops(ctx) as db_ops:
                connection_time = time.time() - start_time
                connection_times.append(connection_time)
                
                # Quick query
                await db_ops.execute_query_isolated("SELECT 1")
                return operation_id
    
    # Test pool performance with different loads
    for batch_size in [20, 50]:
        print(f"\n   Testing connection pool with {batch_size} concurrent requests...")
        connection_times.clear()
        
        start_time = time.time()
        tasks = [pool_operation(i) for i in range(batch_size)]
        results = await asyncio.gather(*tasks)
        total_time = time.time() - start_time
        
        # Calculate connection statistics
        avg_connection_time = statistics.mean(connection_times)
        max_connection_time = max(connection_times)
        
        print(f"   📊 {batch_size} operations completed in: {total_time:.3f}s")
        print(f"   📊 Average connection acquisition: {avg_connection_time*1000:.1f}ms")
        print(f"   📊 Max connection acquisition: {max_connection_time*1000:.1f}ms")
        print(f"   📊 Pool efficiency: {batch_size/total_time:.1f} ops/sec")
        
        assert len(results) == batch_size
        print(f"   ✅ All {len(results)} pool operations completed successfully")


async def test_memory_usage_stability():
    """Test memory usage remains stable under concurrent load."""
    
    print("\n🧠 Testing Memory Usage Stability")
    print("=" * 50)
    
    # Initialize infrastructure
    await initialize_async_infrastructure()
    
    import os

    import psutil
    
    process = psutil.Process(os.getpid())
    
    async def memory_test_operation(operation_id: int):
        """Operation for memory testing."""
        async with request_context(f"mem_test_{operation_id}", {"memory_test": True}, f"mem_client_{operation_id % 5}") as ctx:
            async with get_isolated_database_ops(ctx) as db_ops:
                # Execute multiple queries to stress memory
                for i in range(3):
                    await db_ops.execute_query_isolated(f"SELECT {operation_id + i} as mem_test")
                return operation_id
    
    # Measure initial memory
    initial_memory = process.memory_info().rss / 1024 / 1024  # MB
    print(f"   📊 Initial memory usage: {initial_memory:.1f} MB")
    
    # Run memory stress test
    print("   Running memory stress test with 200 operations...")
    start_time = time.time()
    
    # Run in batches to avoid overwhelming the system
    for batch in range(4):  # 4 batches of 50 = 200 total
        tasks = [memory_test_operation(i + batch * 50) for i in range(50)]
        batch_results = await asyncio.gather(*tasks)
        
        # Check memory between batches
        current_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = current_memory - initial_memory
        
        print(f"   📊 Batch {batch + 1} completed - Memory: {current_memory:.1f} MB (+{memory_increase:.1f} MB)")
    
    total_time = time.time() - start_time
    final_memory = process.memory_info().rss / 1024 / 1024  # MB
    total_memory_increase = final_memory - initial_memory
    
    print(f"   📊 Total time for 200 operations: {total_time:.3f}s")
    print(f"   📊 Final memory usage: {final_memory:.1f} MB")
    print(f"   📊 Total memory increase: {total_memory_increase:.1f} MB")
    
    # Memory increase should be reasonable (less than 100MB for this test)
    if total_memory_increase < 100:
        print(f"   ✅ Memory usage stable (increase: {total_memory_increase:.1f} MB)")
    else:
        print(f"   ⚠️  High memory usage increase: {total_memory_increase:.1f} MB")
    
    return total_memory_increase


async def main():
    """Run all performance tests."""
    
    print("🧪 Request Isolation Performance Test Suite")
    print("=" * 60)
    
    try:
        # Suppress verbose logging for cleaner output
        logging.getLogger().setLevel(logging.WARNING)
        
        # Run all performance tests
        overhead = await test_isolation_overhead()
        await test_concurrent_isolation_performance()
        await test_database_isolation_performance() 
        await test_connection_pool_performance()
        memory_increase = await test_memory_usage_stability()
        
        # Summary
        print("\n🎯 Performance Test Summary")
        print("=" * 40)
        print(f"✅ Isolation overhead: {overhead:.1f}% (target: <20%)")
        print(f"✅ Memory stability: +{memory_increase:.1f} MB (target: <100MB)")
        print("✅ Concurrent operations: Working correctly")
        print("✅ Database isolation: Working correctly")
        print("✅ Connection pooling: Working correctly")
        
        # Overall assessment
        if overhead < 20 and memory_increase < 100:
            print("\n🎉 All performance tests PASSED!")
            print("   Request isolation has acceptable performance characteristics.")
            return True
        else:
            print("\n⚠️  Some performance concerns detected.")
            return False
        
    except Exception as e:
        print(f"\n❌ Performance test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        exit_code = 0 if success else 1
        exit(exit_code)
    except KeyboardInterrupt:
        print("\n⚠️  Performance test interrupted")
        exit(1)
    except Exception as e:
        print(f"❌ Test error: {e}")
        exit(1)