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

### 2. Connection Isolation Per Tool Call {#connection-isolation}

**Update MCP Handlers with Request Isolation**

Modify `snowflake_mcp_server/main.py`:

```python
# Add request isolation wrapper
from functools import wraps
from .utils.request_context import request_context

def with_request_isolation(tool_name: str):
    """Decorator to add request isolation to MCP handlers."""
    def decorator(handler_func):
        @wraps(handler_func)
        async def wrapper(name: str, arguments: Optional[Dict[str, Any]] = None):
            # Extract client ID from arguments or headers if available
            client_id = arguments.get("_client_id", "unknown") if arguments else "unknown"
            
            async with request_context(tool_name, arguments or {}, client_id) as ctx:
                try:
                    # Call original handler with request context
                    return await handler_func(name, arguments, ctx)
                except Exception as e:
                    ctx.add_error(e, f"handler_{tool_name}")
                    # Re-raise to maintain original error handling
                    raise
        return wrapper
    return decorator


# Update handlers with isolation
@with_request_isolation("list_databases")
async def handle_list_databases(
    name: str, 
    arguments: Optional[Dict[str, Any]] = None,
    request_ctx: Optional[RequestContext] = None
) -> Sequence[Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]]:
    """Tool handler to list all accessible Snowflake databases with isolation."""
    try:
        async with get_isolated_database_ops(request_ctx) as db_ops:
            results, _ = await db_ops.execute_query_isolated("SHOW DATABASES")
            
            databases = [row[1] for row in results]
            
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


@with_request_isolation("list_views")
async def handle_list_views(
    name: str, 
    arguments: Optional[Dict[str, Any]] = None,
    request_ctx: Optional[RequestContext] = None
) -> Sequence[Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]]:
    """Tool handler to list views with request isolation."""
    try:
        database = arguments.get("database") if arguments else None
        schema = arguments.get("schema") if arguments else None

        if not database:
            return [
                mcp_types.TextContent(
                    type="text", text="Error: database parameter is required"
                )
            ]

        async with get_isolated_database_ops(request_ctx) as db_ops:
            # Set database context in isolation
            await db_ops.use_database_isolated(database)
            
            # Handle schema context
            if schema:
                await db_ops.use_schema_isolated(schema)
            else:
                # Get current schema in this isolated context
                _, current_schema = await db_ops.get_current_context()
                schema = current_schema

            # Execute views query in isolated context
            results, _ = await db_ops.execute_query_isolated(f"SHOW VIEWS IN {database}.{schema}")

            # Process results
            views = []
            for row in results:
                view_name = row[1]
                created_on = row[5]
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


@with_request_isolation("execute_query")
async def handle_execute_query(
    name: str, 
    arguments: Optional[Dict[str, Any]] = None,
    request_ctx: Optional[RequestContext] = None
) -> Sequence[Union[mcp_types.TextContent, mcp_types.ImageContent, mcp_types.EmbeddedResource]]:
    """Tool handler to execute queries with complete isolation."""
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

        # SQL validation (keep existing validation)
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

        async with get_isolated_database_ops(request_ctx) as db_ops:
            # Set database and schema context in isolation
            if database:
                await db_ops.use_database_isolated(database)
            if schema:
                await db_ops.use_schema_isolated(schema)

            # Get current context for display
            current_db, current_schema = await db_ops.get_current_context()

            # Add LIMIT clause if not present
            if "LIMIT " not in query.upper():
                query = query.rstrip().rstrip(";")
                query = f"{query} LIMIT {limit_rows};"

            # Execute query in isolated context
            rows, column_names = await db_ops.execute_query_isolated(query)

            if rows:
                # Format results
                result = f"## Query Results (Database: {current_db}, Schema: {current_schema})\n\n"
                result += f"Request ID: {request_ctx.request_id}\n"
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
                            if len(val_str) > 200:
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

