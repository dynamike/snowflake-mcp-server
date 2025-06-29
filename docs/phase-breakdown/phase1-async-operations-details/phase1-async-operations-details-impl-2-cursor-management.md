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

### 2. Async Cursor Management {#cursor-management}

**Create Proper Cursor Resource Management**

Add to `async_database.py`:

```python
class AsyncCursorManager:
    """Manage cursor lifecycle asynchronously."""
    
    def __init__(self, connection: SnowflakeConnection):
        self.connection = connection
        self._active_cursors: Set[SnowflakeCursor] = set()
        self._cursor_lock = asyncio.Lock()
    
    @asynccontextmanager
    async def cursor(self):
        """Async context manager for cursor lifecycle."""
        cursor = None
        try:
            # Create cursor in executor
            loop = asyncio.get_event_loop()
            cursor = await loop.run_in_executor(None, self.connection.cursor)
            
            async with self._cursor_lock:
                self._active_cursors.add(cursor)
            
            yield cursor
            
        finally:
            if cursor:
                # Close cursor in executor
                async with self._cursor_lock:
                    self._active_cursors.discard(cursor)
                
                try:
                    await loop.run_in_executor(None, cursor.close)
                except Exception as e:
                    logger.warning(f"Error closing cursor: {e}")
    
    async def close_all_cursors(self) -> None:
        """Close all active cursors."""
        async with self._cursor_lock:
            cursors_to_close = list(self._active_cursors)
            self._active_cursors.clear()
        
        loop = asyncio.get_event_loop()
        for cursor in cursors_to_close:
            try:
                await loop.run_in_executor(None, cursor.close)
            except Exception as e:
                logger.warning(f"Error closing cursor during cleanup: {e}")


# Update AsyncDatabaseOperations to use cursor manager
class AsyncDatabaseOperations:
    def __init__(self, connection: SnowflakeConnection):
        self.connection = connection
        self.cursor_manager = AsyncCursorManager(connection)
    
    async def execute_query(self, query: str) -> Tuple[List[Tuple], List[str]]:
        """Execute query with managed cursor."""
        async with self.cursor_manager.cursor() as cursor:
            loop = asyncio.get_event_loop()
            
            def _execute():
                cursor.execute(query)
                results = cursor.fetchall()
                column_names = [desc[0] for desc in cursor.description or []]
                return results, column_names
            
            return await loop.run_in_executor(None, _execute)
    
    async def cleanup(self) -> None:
        """Cleanup all resources."""
        await self.cursor_manager.close_all_cursors()
```

