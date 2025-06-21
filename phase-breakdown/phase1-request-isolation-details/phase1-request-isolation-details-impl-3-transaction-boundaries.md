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

### 3. Transaction Boundary Management {#transaction-boundaries}

**Add Transaction Isolation Support**

Create `snowflake_mcp_server/utils/transaction_manager.py`:

```python
"""Transaction boundary management for isolated requests."""

import asyncio
import logging
from typing import Optional
from contextlib import asynccontextmanager
from snowflake.connector import SnowflakeConnection

logger = logging.getLogger(__name__)


class TransactionManager:
    """Manage transaction boundaries for isolated requests."""
    
    def __init__(self, connection: SnowflakeConnection, request_id: str):
        self.connection = connection
        self.request_id = request_id
        self._in_transaction = False
        self._autocommit_original = None
    
    async def begin_transaction(self) -> None:
        """Begin an explicit transaction."""
        if self._in_transaction:
            logger.warning(f"Request {self.request_id}: Already in transaction")
            return
        
        loop = asyncio.get_event_loop()
        
        # Save original autocommit setting
        self._autocommit_original = self.connection.autocommit
        
        # Disable autocommit and begin transaction
        await loop.run_in_executor(None, setattr, self.connection, 'autocommit', False)
        await loop.run_in_executor(None, self.connection.execute_string, "BEGIN")
        
        self._in_transaction = True
        logger.debug(f"Request {self.request_id}: Transaction started")
    
    async def commit_transaction(self) -> None:
        """Commit the current transaction."""
        if not self._in_transaction:
            return
        
        loop = asyncio.get_event_loop()
        
        try:
            await loop.run_in_executor(None, self.connection.execute_string, "COMMIT")
            logger.debug(f"Request {self.request_id}: Transaction committed")
        finally:
            await self._cleanup_transaction()
    
    async def rollback_transaction(self) -> None:
        """Rollback the current transaction."""
        if not self._in_transaction:
            return
        
        loop = asyncio.get_event_loop()
        
        try:
            await loop.run_in_executor(None, self.connection.execute_string, "ROLLBACK")
            logger.debug(f"Request {self.request_id}: Transaction rolled back")
        finally:
            await self._cleanup_transaction()
    
    async def _cleanup_transaction(self) -> None:
        """Clean up transaction state."""
        if self._autocommit_original is not None:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, setattr, self.connection, 'autocommit', self._autocommit_original
            )
        
        self._in_transaction = False
        self._autocommit_original = None


@asynccontextmanager
async def transaction_scope(connection: SnowflakeConnection, request_id: str, auto_commit: bool = True):
    """Context manager for transaction scope."""
    tx_manager = TransactionManager(connection, request_id)
    
    if not auto_commit:
        await tx_manager.begin_transaction()
    
    try:
        yield tx_manager
        
        if not auto_commit:
            await tx_manager.commit_transaction()
            
    except Exception as e:
        if not auto_commit:
            logger.error(f"Request {request_id}: Error in transaction, rolling back: {e}")
            await tx_manager.rollback_transaction()
        raise


# Update IsolatedDatabaseOperations to use transactions
class TransactionalDatabaseOperations(IsolatedDatabaseOperations):
    """Database operations with transaction management."""
    
    def __init__(self, connection: SnowflakeConnection, request_context: RequestContext):
        super().__init__(connection, request_context)
        self.transaction_manager = None
    
    async def __aenter__(self):
        """Enhanced entry with transaction support."""
        await super().__aenter__()
        
        # Initialize transaction manager
        self.transaction_manager = TransactionManager(
            self.connection, 
            self.request_context.request_id
        )
        
        return self
    
    async def execute_with_transaction(self, query: str, auto_commit: bool = True) -> Tuple[List[Tuple], List[str]]:
        """Execute query within transaction scope."""
        async with transaction_scope(self.connection, self.request_context.request_id, auto_commit):
            return await self.execute_query_isolated(query)
```

