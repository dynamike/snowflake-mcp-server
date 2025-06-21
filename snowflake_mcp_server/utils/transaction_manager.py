"""Transaction boundary management for isolated requests."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from snowflake.connector import SnowflakeConnection

logger = logging.getLogger(__name__)


class TransactionManager:
    """Manage transaction boundaries for isolated requests."""
    
    def __init__(self, connection: SnowflakeConnection, request_id: str):
        self.connection = connection
        self.request_id = request_id
        self._in_transaction = False
        self._autocommit_original: Optional[bool] = None
    
    async def begin_transaction(self) -> None:
        """Begin an explicit transaction."""
        if self._in_transaction:
            logger.warning(f"Request {self.request_id}: Already in transaction")
            return
        
        loop = asyncio.get_event_loop()
        
        # Save original autocommit setting
        self._autocommit_original = bool(self.connection.autocommit)
        
        # Disable autocommit and begin transaction
        await loop.run_in_executor(None, lambda: setattr(self.connection, 'autocommit', False))
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
                None, lambda: setattr(self.connection, 'autocommit', self._autocommit_original)
            )
        
        self._in_transaction = False
        self._autocommit_original = None
    
    @property
    def in_transaction(self) -> bool:
        """Check if currently in a transaction."""
        return self._in_transaction


@asynccontextmanager
async def transaction_scope(connection: SnowflakeConnection, request_id: str, auto_commit: bool = True) -> Any:
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