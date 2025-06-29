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

### 4. Error Handling for Async Contexts {#error-handling}

**Create Async Error Handler**

Add to `utils/async_database.py`:

```python
import traceback
from typing import Callable, Any

class AsyncErrorHandler:
    """Handle errors in async database operations."""
    
    @staticmethod
    async def handle_database_error(
        operation: Callable,
        error_context: str,
        *args,
        **kwargs
    ) -> Any:
        """Wrapper for database operations with error handling."""
        try:
            return await operation(*args, **kwargs)
        except OperationalError as e:
            logger.error(f"Database operational error in {error_context}: {e}")
            # Could implement retry logic here
            raise
        except DatabaseError as e:
            logger.error(f"Database error in {error_context}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in {error_context}: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise


# Usage in handlers:
async def handle_list_databases_with_error_handling(
    name: str, arguments: Optional[Dict[str, Any]] = None
) -> Sequence[Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]]:
    """Enhanced error handling version."""
    
    async def _database_operation():
        async with get_async_database_ops() as db_ops:
            results, _ = await db_ops.execute_query("SHOW DATABASES")
            return [row[1] for row in results]
    
    try:
        databases = await AsyncErrorHandler.handle_database_error(
            _database_operation,
            "list_databases"
        )
        
        return [
            mcp_types.TextContent(
                type="text",
                text="Available Snowflake databases:\n" + "\n".join(databases),
            )
        ]
    except Exception as e:
        return [
            mcp_types.TextContent(
                type="text", 
                text=f"Error querying databases: {str(e)}"
            )
        ]
```

