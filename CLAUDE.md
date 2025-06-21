# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview
This is a Model Context Protocol (MCP) server for Snowflake that enables Claude to perform read-only operations against Snowflake databases. The server is built with Python 3.12+ and uses stdio-based communication for integration with Claude Desktop.

## Build & Run Commands
- Setup: `uv pip install -e .`
- Start server: `uv run snowflake-mcp` or `uv run snowflake-mcp-stdio`
- Alternative: `python -m snowflake_mcp_server.main`

## Test Commands
- Run all tests: `pytest`
- Run single test: `pytest tests/test_file.py::test_function`
- Test coverage: `pytest --cov=snowflake_mcp_server`

## Lint & Format
- Lint: `ruff check .`
- Format code: `ruff format .`
- Type check: `mypy snowflake_mcp_server/`

## Architecture Overview

### Core Components
- **snowflake_mcp_server/main.py**: Main MCP server implementation with tool handlers for database operations (list_databases, list_views, describe_view, query_view, execute_query)
- **snowflake_mcp_server/utils/snowflake_conn.py**: Connection management with singleton pattern, authentication handling, and background connection refresh
- **snowflake_mcp_server/utils/template.py**: Template utilities for SQL query formatting

### Authentication System
The server supports two authentication methods:
- **Private Key Auth**: Service account with RSA private key (non-interactive)
- **External Browser Auth**: Interactive browser-based authentication

Configuration is managed through environment variables loaded from `.env` files with examples provided for both auth types.

### Connection Management
Uses `SnowflakeConnectionManager` singleton for:
- Persistent connection pooling with configurable refresh intervals (default 8 hours)
- Background connection health monitoring and automatic reconnection
- Thread-safe connection access with proper locking
- Exponential backoff retry logic for connection failures

### Security Features
- Read-only operations enforced via SQL parsing with sqlglot
- Automatic LIMIT clause injection to prevent large result sets
- SQL injection prevention through parameterized queries
- Input validation using Pydantic models

### MCP Tools Available
- `list_databases`: List accessible Snowflake databases
- `list_views`: List views in specified database/schema
- `describe_view`: Get view structure and DDL definition
- `query_view`: Query view data with optional row limits
- `execute_query`: Execute custom read-only SQL with result formatting

## Configuration
- Environment variables in `.env` file for Snowflake credentials
- Connection refresh interval configurable via `SNOWFLAKE_CONN_REFRESH_HOURS`
- Default query limits: 10 rows for view queries, 100 rows for custom queries

## Code Style Guidelines
- Python 3.12+ with full type annotations
- Ruff formatting with 88-character line length
- Async/await pattern for all database operations
- Pydantic models for configuration and data validation
- Google-style docstrings for public functions
- Exception handling with client-safe error messages