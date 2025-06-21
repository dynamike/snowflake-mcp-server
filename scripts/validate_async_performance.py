#!/usr/bin/env python3
"""Basic validation script for async performance improvements."""

import asyncio
import logging
import time


# Basic performance validation
async def validate_async_operations():
    """Validate that async operations are working correctly."""
    
    print("🔧 Async Operations Validation")
    print("=" * 40)
    
    # Import after setup
    from snowflake_mcp_server.main import initialize_async_infrastructure
    from snowflake_mcp_server.utils.async_database import get_async_database_ops
    
    try:
        print("1. Initializing async infrastructure...")
        await initialize_async_infrastructure()
        print("   ✅ Async infrastructure initialized")
        
        print("2. Testing async database operations...")
        async with get_async_database_ops() as db_ops:
            # Test basic query
            start_time = time.time()
            results, columns = await db_ops.execute_query("SELECT 1 as test_column")
            duration = time.time() - start_time
            print(f"   ✅ Basic query completed in {duration:.3f}s")
            
            # Test context acquisition
            current_db, current_schema = await db_ops.get_current_context()
            print(f"   ✅ Context: {current_db}.{current_schema}")
        
        print("3. Testing concurrent operations...")
        start_time = time.time()
        
        async def test_operation():
            async with get_async_database_ops() as db_ops:
                await db_ops.execute_query("SELECT 1")
                return True
        
        # Run 5 concurrent operations
        tasks = [test_operation() for _ in range(5)]
        results = await asyncio.gather(*tasks)
        duration = time.time() - start_time
        
        print(f"   ✅ {len(results)} concurrent operations completed in {duration:.3f}s")
        
        print("\n🎉 Async validation completed successfully!")
        return True
        
    except Exception as e:
        print(f"   ❌ Validation failed: {e}")
        return False

if __name__ == "__main__":
    # Suppress info logs for cleaner output
    logging.getLogger().setLevel(logging.WARNING)
    
    try:
        success = asyncio.run(validate_async_operations())
        exit_code = 0 if success else 1
        exit(exit_code)
    except KeyboardInterrupt:
        print("\n⚠️  Validation interrupted")
        exit(1)
    except Exception as e:
        print(f"❌ Validation error: {e}")
        exit(1)