#!/usr/bin/env python3
"""Validation script for transaction boundary management."""

import asyncio
import logging
import time


# Basic transaction validation
async def validate_transaction_boundaries():
    """Validate that transaction boundaries are working correctly."""
    
    print("🔧 Transaction Boundaries Validation")
    print("=" * 40)
    
    # Import after setup
    from datetime import datetime

    from snowflake_mcp_server.main import initialize_async_infrastructure
    from snowflake_mcp_server.utils.async_database import get_transactional_database_ops
    from snowflake_mcp_server.utils.request_context import (
        RequestContext,
        RequestMetrics,
    )
    
    try:
        print("1. Initializing async infrastructure...")
        await initialize_async_infrastructure()
        print("   ✅ Async infrastructure initialized")
        
        print("2. Testing basic transaction manager...")
        
        # Create a test request context
        test_context = RequestContext(
            request_id="test-tx-001",
            client_id="test-client",
            tool_name="test_transaction",
            arguments={"test": True},
            start_time=datetime.now(),
            metrics=RequestMetrics(start_time=datetime.now())
        )
        
        async with get_transactional_database_ops(test_context) as db_ops:
            # Test transaction manager initialization
            assert db_ops.transaction_manager is not None
            print("   ✅ Transaction manager initialized")
            
            # Test basic query with auto-commit (default behavior)
            start_time = time.time()
            results, columns = await db_ops.execute_with_transaction("SELECT 1 as test_column", auto_commit=True)
            duration = time.time() - start_time
            print(f"   ✅ Auto-commit query completed in {duration:.3f}s")
            
            # Test explicit transaction handling
            await db_ops.begin_explicit_transaction()
            print("   ✅ Explicit transaction started")
            
            # Execute query in transaction
            results, columns = await db_ops.execute_query_isolated("SELECT 2 as test_column")
            print("   ✅ Query executed in transaction")
            
            # Commit transaction
            await db_ops.commit_transaction()
            print("   ✅ Transaction committed")
        
        print("3. Testing transaction metrics...")
        # Check that metrics were tracked
        assert test_context.metrics.transaction_operations > 0
        print(f"   ✅ Transaction operations: {test_context.metrics.transaction_operations}")
        print(f"   ✅ Transaction commits: {test_context.metrics.transaction_commits}")
        print(f"   ✅ Transaction rollbacks: {test_context.metrics.transaction_rollbacks}")
        
        print("4. Testing error handling with rollback...")
        test_context2 = RequestContext(
            request_id="test-tx-002",
            client_id="test-client",
            tool_name="test_rollback",
            arguments={"test": True},
            start_time=datetime.now(),
            metrics=RequestMetrics(start_time=datetime.now())
        )
        
        try:
            async with get_transactional_database_ops(test_context2) as db_ops:
                # This should cause a rollback when exception occurs
                await db_ops.execute_with_transaction("SELECT 1", auto_commit=False)
                # Simulate an error
                raise Exception("Simulated error for rollback test")
        except Exception as e:
            if "Simulated error" in str(e):
                print("   ✅ Error handling test completed")
                print(f"   ✅ Rollbacks tracked: {test_context2.metrics.transaction_rollbacks}")
        
        print("\n🎉 Transaction boundaries validation completed successfully!")
        return True
        
    except Exception as e:
        print(f"   ❌ Validation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    # Suppress info logs for cleaner output
    logging.getLogger().setLevel(logging.WARNING)
    
    try:
        success = asyncio.run(validate_transaction_boundaries())
        exit_code = 0 if success else 1
        exit(exit_code)
    except KeyboardInterrupt:
        print("\n⚠️  Validation interrupted")
        exit(1)
    except Exception as e:
        print(f"❌ Validation error: {e}")
        exit(1)