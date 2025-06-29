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

### 1. Handler Conversion to Async Pattern {#handler-conversion}

**Step 1: Create Async Database Utilities**

Create `snowflake_mcp_server/utils/async_database.py`:

```python
"""Async utilities for database operations."""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple, Union
from contextlib import asynccontextmanager
import functools

from snowflake.connector import SnowflakeConnection
from snowflake.connector.cursor import SnowflakeCursor

logger = logging.getLogger(__name__)


def run_in_executor(func):
    """Decorator to run database operations in thread pool executor."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))
    return wrapper


class AsyncDatabaseOperations:
    """Async wrapper for Snowflake database operations."""
    
    def __init__(self, connection: SnowflakeConnection):
        self.connection = connection
        self._executor_pool = None
    
    async def execute_query(self, query: str) -> List[Tuple]:
        """Execute a query asynchronously and return all results."""
        
        @run_in_executor
        def _execute():
            cursor = self.connection.cursor()
            try:
                cursor.execute(query)
                results = cursor.fetchall()
                return results, [desc[0] for desc in cursor.description or []]
            finally:
                cursor.close()
        
        try:
            results, column_names = await _execute()
            return results, column_names
        except Exception as e:
            logger.error(f"Query execution failed: {query[:100]}... Error: {e}")
            raise
    
    async def execute_query_one(self, query: str) -> Optional[Tuple]:
        """Execute a query and return first result."""
        
        @run_in_executor
        def _execute():
            cursor = self.connection.cursor()
            try:
                cursor.execute(query)
                result = cursor.fetchone()
                return result
            finally:
                cursor.close()
        
        try:
            return await _execute()
        except Exception as e:
            logger.error(f"Query execution failed: {query[:100]}... Error: {e}")
            raise
    
    async def execute_query_limited(self, query: str, limit: int) -> Tuple[List[Tuple], List[str]]:
        """Execute a query with result limit."""
        
        @run_in_executor
        def _execute():
            cursor = self.connection.cursor()
            try:
                cursor.execute(query)
                results = cursor.fetchmany(limit)
                column_names = [desc[0] for desc in cursor.description or []]
                return results, column_names
            finally:
                cursor.close()
        
        try:
            return await _execute()
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


@asynccontextmanager
async def get_async_database_ops():
    """Context manager for async database operations."""
    from .async_pool import get_connection_pool
    
    pool = await get_connection_pool()
    async with pool.acquire() as connection:
        yield AsyncDatabaseOperations(connection)
```

**Step 2: Convert Database Handlers**

Update `snowflake_mcp_server/main.py` handlers:

```python
# Replace handle_list_databases
async def handle_list_databases(
    name: str, arguments: Optional[Dict[str, Any]] = None
) -> Sequence[Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]]:
    """Tool handler to list all accessible Snowflake databases."""
    try:
        async with get_async_database_ops() as db_ops:
            results, _ = await db_ops.execute_query("SHOW DATABASES")
            
            # Process results asynchronously
            databases = [row[1] for row in results]  # Database name is in second column
            
            return [
                mcp_types.TextContent(
                    type="text",
                    text="Available Snowflake databases:\n" + "\n".join(databases),
                )
            ]

    except Exception as e:
        logger.error(f"Error querying databases: {e}")
        return [
            mcp_types.TextContent(
                type="text", text=f"Error querying databases: {str(e)}"
            )
        ]


# Replace handle_list_views  
async def handle_list_views(
    name: str, arguments: Optional[Dict[str, Any]] = None
) -> Sequence[Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]]:
    """Tool handler to list views in a specified database and schema."""
    try:
        database = arguments.get("database") if arguments else None
        schema = arguments.get("schema") if arguments else None

        if not database:
            return [
                mcp_types.TextContent(
                    type="text", text="Error: database parameter is required"
                )
            ]

        async with get_async_database_ops() as db_ops:
            # Set database context
            await db_ops.use_database(database)
            
            # Handle schema context
            if schema:
                await db_ops.use_schema(schema)
            else:
                # Get current schema
                _, current_schema = await db_ops.get_current_context()
                schema = current_schema

            # Execute views query
            results, _ = await db_ops.execute_query(f"SHOW VIEWS IN {database}.{schema}")

            # Process results
            views = []
            for row in results:
                view_name = row[1]  # View name is in second column
                created_on = row[5]  # Creation date
                views.append(f"{view_name} (created: {created_on})")

            if views:
                return [
                    mcp_types.TextContent(
                        type="text",
                        text=f"Views in {database}.{schema}:\n" + "\n".join(views),
                    )
                ]
            else:
                return [
                    mcp_types.TextContent(
                        type="text", text=f"No views found in {database}.{schema}"
                    )
                ]

    except Exception as e:
        logger.error(f"Error listing views: {e}")
        return [
            mcp_types.TextContent(type="text", text=f"Error listing views: {str(e)}")
        ]


# Replace handle_describe_view
async def handle_describe_view(
    name: str, arguments: Optional[Dict[str, Any]] = None
) -> Sequence[Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]]:
    """Tool handler to describe the structure of a view."""
    try:
        database = arguments.get("database") if arguments else None
        schema = arguments.get("schema") if arguments else None
        view_name = arguments.get("view_name") if arguments else None

        if not database or not view_name:
            return [
                mcp_types.TextContent(
                    type="text",
                    text="Error: database and view_name parameters are required",
                )
            ]

        async with get_async_database_ops() as db_ops:
            # Handle schema context
            if schema:
                full_view_name = f"{database}.{schema}.{view_name}"
            else:
                current_db, current_schema = await db_ops.get_current_context()
                if current_schema == "Unknown":
                    return [
                        mcp_types.TextContent(
                            type="text", text="Error: Could not determine current schema"
                        )
                    ]
                schema = current_schema
                full_view_name = f"{database}.{schema}.{view_name}"

            # Execute describe query asynchronously
            describe_results, _ = await db_ops.execute_query(f"DESCRIBE VIEW {full_view_name}")
            
            # Get DDL asynchronously
            ddl_result = await db_ops.execute_query_one(f"SELECT GET_DDL('VIEW', '{full_view_name}')")
            view_ddl = ddl_result[0] if ddl_result else "Definition not available"

            # Process column information
            columns = []
            for row in describe_results:
                col_name = row[0]
                col_type = row[1]
                col_null = "NULL" if row[3] == "Y" else "NOT NULL"
                columns.append(f"{col_name} : {col_type} {col_null}")

            if columns:
                result = f"## View: {full_view_name}\n\n"
                result += "### Columns:\n"
                for col in columns:
                    result += f"- {col}\n"

                result += "\n### View Definition:\n```sql\n"
                result += view_ddl
                result += "\n```"

                return [mcp_types.TextContent(type="text", text=result)]
            else:
                return [
                    mcp_types.TextContent(
                        type="text",
                        text=f"View {full_view_name} not found or you don't have permission to access it.",
                    )
                ]

    except Exception as e:
        logger.error(f"Error describing view: {e}")
        return [
            mcp_types.TextContent(type="text", text=f"Error describing view: {str(e)}")
        ]


# Replace handle_query_view
async def handle_query_view(
    name: str, arguments: Optional[Dict[str, Any]] = None
) -> Sequence[Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]]:
    """Tool handler to query data from a view with optional limit."""
    try:
        database = arguments.get("database") if arguments else None
        schema = arguments.get("schema") if arguments else None
        view_name = arguments.get("view_name") if arguments else None
        limit = int(arguments.get("limit", 10)) if arguments and arguments.get("limit") is not None else 10

        if not database or not view_name:
            return [
                mcp_types.TextContent(
                    type="text",
                    text="Error: database and view_name parameters are required",
                )
            ]

        async with get_async_database_ops() as db_ops:
            # Handle schema context
            if schema:
                full_view_name = f"{database}.{schema}.{view_name}"
            else:
                current_db, current_schema = await db_ops.get_current_context()
                if current_schema == "Unknown":
                    return [
                        mcp_types.TextContent(
                            type="text", text="Error: Could not determine current schema"
                        )
                    ]
                schema = current_schema
                full_view_name = f"{database}.{schema}.{view_name}"

            # Execute query with limit
            rows, column_names = await db_ops.execute_query_limited(
                f"SELECT * FROM {full_view_name}",
                limit
            )

            if rows:
                # Format results as markdown table
                result = f"## Data from {full_view_name} (Showing {len(rows)} rows)\n\n"

                # Create header row
                result += "| " + " | ".join(column_names) + " |\n"
                result += "| " + " | ".join(["---" for _ in column_names]) + " |\n"

                # Add data rows
                for row in rows:
                    formatted_values = []
                    for val in row:
                        if val is None:
                            formatted_values.append("NULL")
                        else:
                            formatted_values.append(str(val).replace("|", "\\|"))
                    result += "| " + " | ".join(formatted_values) + " |\n"

                return [mcp_types.TextContent(type="text", text=result)]
            else:
                return [
                    mcp_types.TextContent(
                        type="text",
                        text=f"No data found in view {full_view_name} or the view is empty.",
                    )
                ]

    except Exception as e:
        logger.error(f"Error querying view: {e}")
        return [
            mcp_types.TextContent(type="text", text=f"Error querying view: {str(e)}")
        ]


# Replace handle_execute_query
async def handle_execute_query(
    name: str, arguments: Optional[Dict[str, Any]] = None
) -> Sequence[Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]]:
    """Tool handler to execute read-only SQL queries against Snowflake."""
    try:
        query = arguments.get("query") if arguments else None
        database = arguments.get("database") if arguments else None
        schema = arguments.get("schema") if arguments else None
        limit_rows = int(arguments.get("limit", 100)) if arguments and arguments.get("limit") is not None else 100

        if not query:
            return [
                mcp_types.TextContent(
                    type="text", text="Error: query parameter is required"
                )
            ]

        # Validate read-only query (keep existing sqlglot validation)
        try:
            parsed_statements = sqlglot.parse(query, dialect="snowflake")
            read_only_types = {"select", "show", "describe", "explain", "with"}

            if not parsed_statements:
                raise ParseError("Error: Could not parse SQL query")

            for stmt in parsed_statements:
                if (
                    stmt is not None
                    and hasattr(stmt, "key")
                    and stmt.key
                    and stmt.key.lower() not in read_only_types
                ):
                    raise ParseError(
                        f"Error: Only read-only queries are allowed. Found statement type: {stmt.key}"
                    )

        except ParseError as e:
            return [
                mcp_types.TextContent(
                    type="text",
                    text=f"Error: Only SELECT/SHOW/DESCRIBE/EXPLAIN/WITH queries are allowed for security reasons. {str(e)}",
                )
            ]

        async with get_async_database_ops() as db_ops:
            # Set database and schema context if provided
            if database:
                await db_ops.use_database(database)
            if schema:
                await db_ops.use_schema(schema)

            # Get current context for display
            current_db, current_schema = await db_ops.get_current_context()

            # Add LIMIT clause if not present
            if "LIMIT " not in query.upper():
                query = query.rstrip().rstrip(";")
                query = f"{query} LIMIT {limit_rows};"

            # Execute query asynchronously
            rows, column_names = await db_ops.execute_query_limited(query, limit_rows)

            if rows:
                # Format results as markdown table
                result = f"## Query Results (Database: {current_db}, Schema: {current_schema})\n\n"
                result += f"Showing {len(rows)} row{'s' if len(rows) != 1 else ''}\n\n"
                result += f"```sql\n{query}\n```\n\n"

                # Create header row
                result += "| " + " | ".join(column_names) + " |\n"
                result += "| " + " | ".join(["---" for _ in column_names]) + " |\n"

                # Add data rows with truncation
                for row in rows:
                    formatted_values = []
                    for val in row:
                        if val is None:
                            formatted_values.append("NULL")
                        else:
                            val_str = str(val).replace("|", "\\|")
                            if len(val_str) > 200:  # Truncate long values
                                val_str = val_str[:197] + "..."
                            formatted_values.append(val_str)
                    result += "| " + " | ".join(formatted_values) + " |\n"

                return [mcp_types.TextContent(type="text", text=result)]
            else:
                return [
                    mcp_types.TextContent(
                        type="text",
                        text=f"Query completed successfully but returned no results.",
                    )
                ]

    except Exception as e:
        logger.error(f"Error executing query: {e}")
        return [
            mcp_types.TextContent(type="text", text=f"Error executing query: {str(e)}")
        ]
```

