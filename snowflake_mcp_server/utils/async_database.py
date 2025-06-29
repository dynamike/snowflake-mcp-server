"""Async utilities for database operations."""

import asyncio
import functools
import logging
import traceback
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Set, Tuple

from snowflake.connector import SnowflakeConnection
from snowflake.connector.cursor import SnowflakeCursor
from snowflake.connector.errors import DatabaseError, OperationalError

if TYPE_CHECKING:
    from .request_context import RequestContext

logger = logging.getLogger(__name__)


class AsyncErrorHandler:
    """Handle errors in async database operations."""
    
    @staticmethod
    async def handle_database_error(
        operation: Callable,
        error_context: str,
        *args: Any,
        **kwargs: Any
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


def run_in_executor(func: Callable) -> Callable:
    """Decorator to run database operations in thread pool executor."""
    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))
    return wrapper


class AsyncCursorManager:
    """Manage cursor lifecycle asynchronously."""
    
    def __init__(self, connection: SnowflakeConnection):
        self.connection = connection
        self._active_cursors: Set[SnowflakeCursor] = set()
        self._cursor_lock = asyncio.Lock()
    
    @asynccontextmanager
    async def cursor(self) -> Any:
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


class AsyncDatabaseOperations:
    """Async wrapper for Snowflake database operations."""
    
    def __init__(self, connection: SnowflakeConnection):
        self.connection = connection
        self.cursor_manager = AsyncCursorManager(connection)
        self._executor_pool = None
    
    async def execute_query(self, query: str) -> Tuple[List[Any], List[str]]:
        """Execute query with managed cursor."""
        try:
            async with self.cursor_manager.cursor() as cursor:
                loop = asyncio.get_event_loop()
                
                def _execute() -> Tuple[List[Any], List[str]]:
                    cursor.execute(query)
                    results = list(cursor.fetchall())
                    column_names = [desc[0] for desc in cursor.description or []]
                    return results, column_names
                
                return await loop.run_in_executor(None, _execute)
        except Exception as e:
            logger.error(f"Query execution failed: {query[:100]}... Error: {e}")
            raise
    
    async def execute_query_one(self, query: str) -> Optional[Any]:
        """Execute a query and return first result."""
        try:
            async with self.cursor_manager.cursor() as cursor:
                loop = asyncio.get_event_loop()
                
                def _execute() -> Optional[Any]:
                    cursor.execute(query)
                    result = cursor.fetchone()
                    return result
                
                return await loop.run_in_executor(None, _execute)
        except Exception as e:
            logger.error(f"Query execution failed: {query[:100]}... Error: {e}")
            raise
    
    async def execute_query_limited(self, query: str, limit: int) -> Tuple[List[Any], List[str]]:
        """Execute a query with result limit."""
        try:
            async with self.cursor_manager.cursor() as cursor:
                loop = asyncio.get_event_loop()
                
                def _execute() -> Tuple[List[Any], List[str]]:
                    cursor.execute(query)
                    results = list(cursor.fetchmany(limit))
                    column_names = [desc[0] for desc in cursor.description or []]
                    return results, column_names
                
                return await loop.run_in_executor(None, _execute)
        except Exception as e:
            logger.error(f"Limited query execution failed: {query[:100]}... Error: {e}")
            raise
    
    async def get_current_context(self) -> Tuple[str, str]:
        """Get current database and schema context."""
        result = await self.execute_query_one("SELECT CURRENT_DATABASE(), CURRENT_SCHEMA()")
        if result:
            return result[0] or "Unknown", result[1] or "Unknown"
        return "Unknown", "Unknown"
    
    async def use_database(self, database: str) -> None:
        """Switch to specified database."""
        await self.execute_query_one(f"USE DATABASE {database}")
    
    async def use_schema(self, schema: str) -> None:
        """Switch to specified schema."""
        await self.execute_query_one(f"USE SCHEMA {schema}")
    
    async def cleanup(self) -> None:
        """Cleanup all resources."""
        await self.cursor_manager.close_all_cursors()


@asynccontextmanager
async def get_async_database_ops() -> Any:
    """Context manager for async database operations."""
    from .async_pool import get_connection_pool
    
    pool = await get_connection_pool()
    async with pool.acquire() as connection:
        db_ops = AsyncDatabaseOperations(connection)
        try:
            yield db_ops
        finally:
            await db_ops.cleanup()


class IsolatedDatabaseOperations(AsyncDatabaseOperations):
    """Database operations with request isolation."""
    
    def __init__(self, connection: SnowflakeConnection, request_context: "RequestContext"):
        super().__init__(connection)
        self.request_context = request_context
        self._original_database: Optional[str] = None
        self._original_schema: Optional[str] = None
        self._context_changed = False
    
    async def __aenter__(self) -> "IsolatedDatabaseOperations":
        """Async context entry - capture original context."""
        # Capture current database/schema context
        try:
            current_db, current_schema = await self.get_current_context()
            self._original_database = current_db
            self._original_schema = current_schema
            
            logger.debug(f"Request {self.request_context.request_id}: "
                        f"Original context: {current_db}.{current_schema}")
        except Exception as e:
            logger.warning(f"Could not capture original context: {e}")
        
        return self
    
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context exit - restore original context."""
        try:
            # Restore original context if it was changed
            if self._context_changed and self._original_database:
                await self._restore_original_context()
        except Exception as e:
            logger.warning(f"Error restoring context: {e}")
        finally:
            await self.cleanup()
    
    async def use_database_isolated(self, database: str) -> None:
        """Switch database with isolation tracking."""
        from .contextual_logging import log_database_operation
        
        await self.use_database(database)
        self.request_context.set_database_context(database)
        self._context_changed = True
        
        log_database_operation("USE DATABASE", database=database)
        logger.debug(f"Request {self.request_context.request_id}: "
                    f"Changed to database {database}")
    
    async def use_schema_isolated(self, schema: str) -> None:
        """Switch schema with isolation tracking."""
        from .contextual_logging import log_database_operation
        
        await self.use_schema(schema)
        if self.request_context.database_context:
            self.request_context.set_database_context(
                self.request_context.database_context, 
                schema
            )
        self._context_changed = True
        
        log_database_operation("USE SCHEMA", database=self.request_context.database_context, schema=schema)
        logger.debug(f"Request {self.request_context.request_id}: "
                    f"Changed to schema {schema}")
    
    async def execute_query_isolated(self, query: str) -> Tuple[List[Any], List[str]]:
        """Execute query with request tracking."""
        from .contextual_logging import log_database_operation
        
        try:
            self.request_context.increment_query_count()
            
            # Log the database operation
            query_preview = query[:100] + ("..." if len(query) > 100 else "")
            log_database_operation(
                "EXECUTE_QUERY", 
                database=self.request_context.database_context,
                schema=self.request_context.schema_context,
                query_preview=query_preview
            )
            
            logger.debug(f"Request {self.request_context.request_id}: "
                        f"Executing query: {query[:100]}...")
            
            start_time = datetime.now()
            result = await self.execute_query(query)
            duration = (datetime.now() - start_time).total_seconds() * 1000
            
            logger.debug(f"Request {self.request_context.request_id}: "
                        f"Query completed in {duration:.2f}ms")
            
            return result
            
        except Exception as e:
            self.request_context.add_error(e, f"query_execution: {query[:100]}")
            logger.error(f"Request {self.request_context.request_id}: "
                        f"Query failed: {e}")
            raise
    
    async def _restore_original_context(self) -> None:
        """Restore original database/schema context."""
        if self._original_database and self._original_database != "Unknown":
            await self.use_database(self._original_database)
            
            if self._original_schema and self._original_schema != "Unknown":
                await self.use_schema(self._original_schema)
            
            logger.debug(f"Request {self.request_context.request_id}: "
                        f"Restored context to {self._original_database}.{self._original_schema}")


class TransactionalDatabaseOperations(IsolatedDatabaseOperations):
    """Database operations with transaction management."""
    
    def __init__(self, connection: SnowflakeConnection, request_context: "RequestContext"):
        super().__init__(connection, request_context)
        self.transaction_manager: Optional[Any] = None
    
    async def __aenter__(self) -> "TransactionalDatabaseOperations":
        """Enhanced entry with transaction support."""
        await super().__aenter__()
        
        # Initialize transaction manager
        from .transaction_manager import TransactionManager
        self.transaction_manager = TransactionManager(
            self.connection, 
            self.request_context.request_id
        )
        
        return self
    
    async def execute_with_transaction(self, query: str, auto_commit: bool = True) -> Tuple[List[Any], List[str]]:
        """Execute query within transaction scope."""
        from .contextual_logging import log_transaction_event
        from .transaction_manager import transaction_scope
        
        self.request_context.increment_transaction_operation()
        log_transaction_event("begin", auto_commit=auto_commit)
        
        try:
            async with transaction_scope(self.connection, self.request_context.request_id, auto_commit) as tx_manager:
                result = await self.execute_query_isolated(query)
                
                # Track commits/rollbacks based on transaction manager state
                if not auto_commit and tx_manager.in_transaction:
                    self.request_context.increment_transaction_commit()
                    log_transaction_event("commit")
                
                return result
        except Exception:
            # Track rollback on exception
            if not auto_commit:
                self.request_context.increment_transaction_rollback()
                log_transaction_event("rollback")
            raise
    
    async def execute_multi_statement_transaction(self, queries: List[str]) -> List[Tuple[List[Any], List[str]]]:
        """Execute multiple queries in a single transaction."""
        from .transaction_manager import transaction_scope
        
        results = []
        async with transaction_scope(self.connection, self.request_context.request_id, auto_commit=False):
            for query in queries:
                result = await self.execute_query_isolated(query)
                results.append(result)
        
        return results
    
    async def begin_explicit_transaction(self) -> None:
        """Begin an explicit transaction that persists until committed/rolled back."""
        if self.transaction_manager:
            self.request_context.increment_transaction_operation()
            await self.transaction_manager.begin_transaction()
    
    async def commit_transaction(self) -> None:
        """Commit the current explicit transaction."""
        if self.transaction_manager:
            await self.transaction_manager.commit_transaction()
            self.request_context.increment_transaction_commit()
    
    async def rollback_transaction(self) -> None:
        """Rollback the current explicit transaction."""
        if self.transaction_manager:
            await self.transaction_manager.rollback_transaction()
            self.request_context.increment_transaction_rollback()


@asynccontextmanager
async def get_isolated_database_ops(request_context: "RequestContext") -> Any:
    """Get isolated database operations for a request."""
    from .async_pool import get_connection_pool
    from .contextual_logging import log_connection_event
    
    pool = await get_connection_pool()
    async with pool.acquire() as connection:
        # Set connection ID in metrics and log acquisition
        connection_id = str(id(connection))
        request_context.metrics.connection_id = connection_id
        log_connection_event("acquired", connection_id=connection_id)
        
        try:
            db_ops = IsolatedDatabaseOperations(connection, request_context)
            async with db_ops:
                yield db_ops
        finally:
            log_connection_event("released", connection_id=connection_id)


@asynccontextmanager
async def get_transactional_database_ops(request_context: "RequestContext") -> Any:
    """Get transactional database operations for a request."""
    from .async_pool import get_connection_pool
    from .contextual_logging import log_connection_event
    
    pool = await get_connection_pool()
    async with pool.acquire() as connection:
        # Set connection ID in metrics and log acquisition
        connection_id = str(id(connection))
        request_context.metrics.connection_id = connection_id
        log_connection_event("acquired", connection_id=connection_id)
        
        try:
            db_ops = TransactionalDatabaseOperations(connection, request_context)
            async with db_ops:
                yield db_ops
        finally:
            log_connection_event("released", connection_id=connection_id)