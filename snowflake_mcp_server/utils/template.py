"""Template functions for creating new Snowflake query tools.

This module provides template functions and utilities to help developers
add new Snowflake query tools to the MCP server. Use these templates as
a starting point when adding new functionality to query Snowflake.
"""

from typing import Any, Dict, List

import mcp.types as mcp_types
from mcp_server_snowflake.main import get_snowflake_config
from mcp_server_snowflake.utils.snowflake_conn import (
    get_snowflake_connection,
)


async def template_simple_query(
    name: str, arguments: Dict[str, Any] | None
) -> List[mcp_types.TextContent]:
    """Template for a simple SQL query that returns text results.

    Use this template for tools that execute a single SQL query and return
    formatted text results.

    Args:
        name: The name of the tool being called
        arguments: The arguments provided to the tool

    Returns:
        A list containing a single TextContent with the results
    """
    try:
        # Get Snowflake connection
        config = get_snowflake_config()
        conn = get_snowflake_connection(config)

        # Extract and validate arguments
        param1 = arguments.get("param1") if arguments else None
        param2 = arguments.get("param2") if arguments else None

        if not param1:
            return [
                mcp_types.TextContent(type="text", text="Error: param1 is required")
            ]

        # Execute your SQL query
        cursor = conn.cursor()
        cursor.execute(f"YOUR SQL QUERY HERE WITH {param1} AND {param2}")

        # Process results
        results = []
        for row in cursor:
            # Process each row as needed
            results.append(str(row))

        cursor.close()
        conn.close()

        # Return formatted content
        return [
            mcp_types.TextContent(type="text", text="Results:\n" + "\n".join(results))
        ]

    except Exception as e:
        return [
            mcp_types.TextContent(type="text", text=f"Error executing query: {str(e)}")
        ]


async def template_table_query(
    name: str, arguments: Dict[str, Any] | None
) -> List[mcp_types.TextContent]:
    """Template for a SQL query that returns formatted table results.

    Use this template for tools that execute a SQL query and return
    results formatted as a markdown table.

    Args:
        name: The name of the tool being called
        arguments: The arguments provided to the tool

    Returns:
        A list containing a single TextContent with the results formatted as a table
    """
    try:
        # Get Snowflake connection
        config = get_snowflake_config()
        conn = get_snowflake_connection(config)

        # Extract and validate arguments
        param1 = arguments.get("param1") if arguments else None
        limit = arguments.get("limit", 10) if arguments else 10

        if not param1:
            return [
                mcp_types.TextContent(type="text", text="Error: param1 is required")
            ]

        # Execute your SQL query
        cursor = conn.cursor()
        cursor.execute(f"YOUR SQL QUERY HERE WITH {param1} LIMIT {limit}")

        # Get column names
        column_names = [col[0] for col in cursor.description]

        # Process results
        rows = cursor.fetchall()

        cursor.close()
        conn.close()

        if rows:
            # Format the results as a markdown table
            result = "## Query Results\n\n"

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
                        # Format the value as string and escape any pipe characters
                        formatted_values.append(str(val).replace("|", "\\|"))
                result += "| " + " | ".join(formatted_values) + " |\n"

            return [mcp_types.TextContent(type="text", text=result)]
        else:
            return [
                mcp_types.TextContent(
                    type="text", text="No results found for the query."
                )
            ]

    except Exception as e:
        return [
            mcp_types.TextContent(type="text", text=f"Error executing query: {str(e)}")
        ]


def create_snowflake_tool_definition(
    name: str, description: str, parameters: Dict[str, Any]
) -> mcp_types.Tool:
    """Create a tool definition for a Snowflake query tool.

    Args:
        name: The name of the tool
        description: A description of what the tool does
        parameters: A dictionary of parameters with their types and descriptions

    Returns:
        A Tool object that can be added to the list_tools function
    """
    # Convert parameters to the schema format
    properties = {}
    required = []

    for param_name, param_info in parameters.items():
        properties[param_name] = {
            "type": param_info.get("type", "string"),
            "description": param_info.get("description", ""),
        }

        if param_info.get("required", False):
            required.append(param_name)

    return mcp_types.Tool(
        name=name,
        description=description,
        inputSchema={"type": "object", "properties": properties, "required": required},
    )
