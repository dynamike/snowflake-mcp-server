#!/usr/bin/env python3
"""Validation script for request ID tracking and logging."""

import asyncio
import logging
from io import StringIO


# Capture logging output for validation
class LogCapture:
    def __init__(self):
        self.stream = StringIO()
        self.handler = logging.StreamHandler(self.stream)
        
    def get_logs(self):
        return self.stream.getvalue()
    
    def clear(self):
        self.stream.seek(0)
        self.stream.truncate(0)


async def validate_request_tracking():
    """Validate that request tracking and logging are working correctly."""
    
    print("🔧 Request ID Tracking & Logging Validation")
    print("=" * 50)
    
    # Import after setup
    from snowflake_mcp_server.main import initialize_async_infrastructure
    from snowflake_mcp_server.utils.async_database import get_isolated_database_ops
    from snowflake_mcp_server.utils.contextual_logging import setup_contextual_logging
    from snowflake_mcp_server.utils.request_context import (
        request_context,
    )
    
    try:
        print("1. Setting up contextual logging...")
        # Set up logging and capture output
        log_capture = LogCapture()
        
        # Set up contextual logging
        setup_contextual_logging()
        
        # Add our capture handler to the request logger
        request_logger = logging.getLogger("snowflake_mcp.requests")
        request_logger.addHandler(log_capture.handler)
        request_logger.setLevel(logging.DEBUG)
        
        print("   ✅ Contextual logging initialized")
        
        print("2. Initializing async infrastructure...")
        await initialize_async_infrastructure()
        print("   ✅ Async infrastructure initialized")
        
        print("3. Testing request context with logging...")
        
        # Test request context logging
        async with request_context("test_tool", {"test_param": "test_value"}, "test_client") as ctx:
            print(f"   ✅ Request context created: {ctx.request_id}")
            
            # Test database operations with logging
            async with get_isolated_database_ops(ctx) as db_ops:
                # Test query execution with logging (simple query that should work)
                results, columns = await db_ops.execute_query_isolated("SELECT 1 as test_column")
                print("   ✅ Query executed with logging")
                
                # Check that metrics were updated
                assert ctx.metrics.queries_executed == 1
                print(f"   ✅ Query count tracked: {ctx.metrics.queries_executed}")
        
        print("4. Validating log output...")
        log_output = log_capture.get_logs()
        
        # Check for required log entries
        required_patterns = [
            "Starting tool call: test_tool",
            "test_param",
            "Connection acquired",
            "EXECUTE_QUERY",
            "Connection released",
            "Request completed"
        ]
        
        found_patterns = []
        for pattern in required_patterns:
            if pattern in log_output:
                found_patterns.append(pattern)
                print(f"   ✅ Found log pattern: {pattern}")
            else:
                print(f"   ❌ Missing log pattern: {pattern}")
        
        print(f"   📊 Found {len(found_patterns)}/{len(required_patterns)} expected log patterns")
        
        print("5. Testing request ID isolation...")
        
        # Clear previous logs
        log_capture.clear()
        
        # Test multiple concurrent requests with different IDs
        async def test_request(request_num):
            async with request_context(f"test_tool_{request_num}", {"request_num": request_num}, f"client_{request_num}") as ctx:
                async with get_isolated_database_ops(ctx) as db_ops:
                    await db_ops.execute_query_isolated(f"SELECT {request_num} as request_number")
                return ctx.request_id
        
        # Run 3 concurrent requests
        request_ids = await asyncio.gather(
            test_request(1),
            test_request(2), 
            test_request(3)
        )
        
        print(f"   ✅ Concurrent requests completed with IDs: {request_ids}")
        
        # Validate that all request IDs appear in logs
        final_logs = log_capture.get_logs()
        for req_id in request_ids:
            if req_id in final_logs:
                print(f"   ✅ Request ID {req_id[:8]}... found in logs")
            else:
                print(f"   ❌ Request ID {req_id[:8]}... missing from logs")
        
        print("6. Testing error logging...")
        log_capture.clear()
        
        try:
            async with request_context("error_test_tool", {"will_fail": True}, "error_client") as ctx:
                async with get_isolated_database_ops(ctx) as db_ops:
                    # This should cause an error
                    await db_ops.execute_query_isolated("SELECT * FROM definitely_non_existent_table_12345")
        except Exception as e:
            print(f"   ✅ Expected error caught: {type(e).__name__}")
        
        error_logs = log_capture.get_logs()
        if "Request error" in error_logs:
            print("   ✅ Error logging working correctly")
        else:
            print("   ❌ Error logging not found")
        
        print("\n🎉 Request ID tracking and logging validation completed!")
        
        # Print a sample of the logs for inspection
        print("\n📋 Sample log output:")
        print("-" * 50)
        sample_logs = error_logs.split('\n')[:10]  # First 10 lines
        for line in sample_logs:
            if line.strip():
                print(line)
        print("-" * 50)
        
        return True
        
    except Exception as e:
        print(f"   ❌ Validation failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    try:
        success = asyncio.run(validate_request_tracking())
        exit_code = 0 if success else 1
        exit(exit_code)
    except KeyboardInterrupt:
        print("\n⚠️  Validation interrupted")
        exit(1)
    except Exception as e:
        print(f"❌ Validation error: {e}")
        exit(1)