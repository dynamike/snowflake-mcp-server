"""Snowflake connection utilities.

This module provides utility functions and classes for establishing secure connections
to Snowflake using either service account authentication with private key or external
browser authentication. It handles the configuration parsing and security aspects of
connecting to Snowflake databases.

The primary components are:
- SnowflakeConfig: A Pydantic model that validates connection parameters
- SnowflakeConnectionManager: A singleton class that manages persistent connections
- load_private_key: A function to securely load RSA private keys
- get_snowflake_connection: A function that establishes connections using key pair
  auth or browser auth
"""

import asyncio
import contextlib
import os
import threading
import time
import warnings
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Tuple

import snowflake.connector
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import BaseModel, ValidationInfo, field_validator
from snowflake.connector import SnowflakeConnection
from snowflake.connector.errors import DatabaseError, OperationalError

if TYPE_CHECKING:
    from .async_pool import AsyncConnectionPool


class AuthType(str, Enum):
    """Authentication types for Snowflake."""

    PRIVATE_KEY = "private_key"
    EXTERNAL_BROWSER = "external_browser"


class SnowflakeConfig(BaseModel):
    """Snowflake connection configuration."""

    account: str
    user: str
    auth_type: AuthType
    private_key_path: Optional[str] = None
    warehouse: Optional[str] = None
    database: Optional[str] = None
    schema_name: Optional[str] = (
        None  # Renamed from schema to avoid conflict with BaseModel
    )
    role: Optional[str] = None

    @field_validator("private_key_path")
    @classmethod
    def validate_private_key_path(
        cls, v: Optional[str], info: ValidationInfo
    ) -> Optional[str]:
        """Validate that private_key_path is provided when auth_type is PRIVATE_KEY."""
        values = info.data
        if values.get("auth_type") == AuthType.PRIVATE_KEY and not v:
            raise ValueError(
                "private_key_path is required when auth_type is PRIVATE_KEY"
            )
        return v


def load_private_key(private_key_path: str) -> rsa.RSAPrivateKey:
    """Load private key from file."""
    with open(private_key_path, "rb") as key_file:
        p_key = load_pem_private_key(
            key_file.read(),
            password=None,
            backend=default_backend(),
        )
        if not isinstance(p_key, rsa.RSAPrivateKey):
            raise TypeError("Private key is not an RSA private key")
        return p_key


class SnowflakeConnectionManager:
    """Singleton manager for Snowflake connection pooling and refresh.

    This class maintains a persistent connection to Snowflake and refreshes it
    periodically in the background based on the configured refresh interval.
    """

    _instance = None
    _lock = threading.Lock()

    # Class instance variable type annotations
    _initialized: bool
    _connection: Optional[SnowflakeConnection]
    _connection_lock: threading.Lock
    _config: Optional[SnowflakeConfig]
    _last_refresh_time: Optional[datetime]
    _refresh_thread: Optional[threading.Thread]
    _stop_event: threading.Event
    _refresh_interval: timedelta
    _connection_healthy: bool
    _last_error: Optional[Exception]
    _retry_count: int
    _max_retry_count: int
    _retry_backoff_seconds: List[int]

    def __new__(cls) -> "SnowflakeConnectionManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SnowflakeConnectionManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        """Initialize the connection manager if not already initialized."""
        if self._initialized:
            return

        self._connection = None
        self._connection_lock = threading.Lock()
        self._config = None
        self._last_refresh_time = None
        self._refresh_thread = None
        self._stop_event = threading.Event()
        self._connection_healthy = False
        self._last_error = None
        self._retry_count = 0
        self._max_retry_count = 3
        # Exponential backoff retry intervals in seconds: 10s, 30s, 60s
        self._retry_backoff_seconds = [10, 30, 60]

        # Get refresh interval from environment variable (in hours, default 8)
        refresh_interval_hours = float(os.getenv("SNOWFLAKE_CONN_REFRESH_HOURS", "8"))
        self._refresh_interval = timedelta(hours=refresh_interval_hours)

        self._initialized = True

    def initialize(self, config: SnowflakeConfig) -> None:
        """Initialize the connection manager with the given configuration."""
        with self._connection_lock:
            self._config = config
            self._connect()

            # Start background refresh thread if not already running
            if self._refresh_thread is None or not self._refresh_thread.is_alive():
                self._stop_event.clear()
                self._refresh_thread = threading.Thread(
                    target=self._refresh_connection_periodically, daemon=True
                )
                self._refresh_thread.start()

    def get_connection(self) -> SnowflakeConnection:
        """Get the current Snowflake connection, creating it if necessary."""
        with self._connection_lock:
            if self._connection is None or not self._connection_healthy:
                if self._config is None:
                    raise ValueError(
                        "Connection manager not initialized with a configuration"
                    )
                self._connect()

            # At this point, self._connection should never be None because
            # _connect() would have either set it or raised an exception
            assert (
                self._connection is not None
            ), "Connection is None after connect attempt"
            return self._connection

    def is_healthy(self) -> Tuple[bool, Optional[str]]:
        """Check if the connection is healthy.

        Returns:
            Tuple[bool, Optional[str]]: A tuple containing:
                - Boolean indicating if the connection is healthy
                - Optional error message if not healthy, None otherwise
        """
        with self._connection_lock:
            error_msg = None
            if self._last_error:
                error_msg = (
                    f"{type(self._last_error).__name__}: {str(self._last_error)}"
                )
            return (self._connection_healthy, error_msg)

    def close(self) -> None:
        """Close the current connection and stop the refresh thread."""
        self._stop_event.set()

        with self._connection_lock:
            if self._connection is not None:
                try:
                    self._connection.close()
                except Exception:
                    pass  # Ignore errors during close
                self._connection = None
                self._connection_healthy = False

    def _connect(self) -> None:
        """Establish a new connection to Snowflake."""
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass  # Ignore errors during close

        if self._config is None:
            raise ValueError("Connection configuration is not initialized")

        try:
            self._connection = get_snowflake_connection(self._config)
            self._last_refresh_time = datetime.now()
            self._connection_healthy = True
            self._last_error = None
            self._retry_count = 0
        except Exception as e:
            self._connection_healthy = False
            self._last_error = e
            raise

    @contextlib.contextmanager
    def _temporarily_release_lock(self) -> Iterator[None]:
        """Context manager to safely release and reacquire the connection lock.

        This allows other threads to access the connection while this thread sleeps,
        without risking inconsistent lock states.

        Yields:
            None: This context manager doesn't yield any value.
        """
        self._connection_lock.release()
        try:
            yield
        finally:
            self._connection_lock.acquire()

    def _refresh_connection_periodically(self) -> None:
        """Background thread that refreshes the connection periodically."""
        while not self._stop_event.is_set():
            # Sleep for a short interval and check if it's time to refresh
            if self._stop_event.wait(60):  # Check every minute
                break

            with self._connection_lock:
                if (
                    self._last_refresh_time is not None
                    and datetime.now() - self._last_refresh_time
                    >= self._refresh_interval
                ):
                    try:
                        self._connect()
                    except (OperationalError, DatabaseError) as e:
                        # Potentially recoverable errors
                        self._connection_healthy = False
                        self._last_error = e
                        self._retry_count += 1

                        if self._retry_count <= self._max_retry_count:
                            # Schedule a retry
                            retry_delay = self._retry_backoff_seconds[
                                min(
                                    self._retry_count - 1,
                                    len(self._retry_backoff_seconds) - 1,
                                )
                            ]

                            # Safely release the lock during sleep without risk of inconsistent lock state
                            with self._temporarily_release_lock():
                                time.sleep(retry_delay)

                            # Try again immediately after the backoff
                            continue
                    except Exception as e:
                        # All other errors
                        self._connection_healthy = False
                        self._last_error = e


def get_snowflake_connection(config: SnowflakeConfig) -> SnowflakeConnection:
    """Create a connection to Snowflake using key pair or external browser auth."""
    conn_params: Dict[str, Any] = {
        "account": config.account,
        "user": config.user,
    }

    # Set authentication parameters based on auth_type
    if config.auth_type == AuthType.PRIVATE_KEY:
        if not config.private_key_path:
            raise ValueError(
                "Private key path is required for private key authentication"
            )
        private_key = load_private_key(config.private_key_path)
        conn_params["private_key"] = private_key
    elif config.auth_type == AuthType.EXTERNAL_BROWSER:
        conn_params["authenticator"] = "externalbrowser"

    # Add optional connection parameters
    if config.warehouse:
        conn_params["warehouse"] = config.warehouse
    if config.database:
        conn_params["database"] = config.database
    if config.schema_name:
        conn_params["schema"] = config.schema_name
    if config.role:
        conn_params["role"] = config.role

    # Explicitly cast the return value to SnowflakeConnection to satisfy mypy
    connection: SnowflakeConnection = snowflake.connector.connect(**conn_params)
    return connection


async def create_async_connection(config: SnowflakeConfig) -> SnowflakeConnection:
    """Create a Snowflake connection asynchronously."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_snowflake_connection, config)


async def test_connection_health(connection: SnowflakeConnection) -> bool:
    """Test if a connection is healthy asynchronously."""
    try:
        loop = asyncio.get_event_loop()
        
        def _test_connection() -> bool:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            cursor.close()
            return result is not None
        
        return await loop.run_in_executor(None, _test_connection)
    except Exception:
        return False


class LegacyConnectionManager:
    """Legacy connection manager for backwards compatibility."""
    
    def __init__(self) -> None:
        self._pool: Optional["AsyncConnectionPool"] = None
        self._config: Optional[SnowflakeConfig] = None
    
    def initialize(self, config: SnowflakeConfig) -> None:
        """Initialize with async pool."""
        self._config = config
        # Pool initialization happens asynchronously
    
    async def get_async_connection(self) -> Any:
        """Get connection from async pool."""
        if self._pool is None:
            from .async_pool import get_connection_pool
            self._pool = await get_connection_pool()
        
        return self._pool.acquire()
    
    def get_connection(self) -> SnowflakeConnection:
        """Legacy sync method - deprecated."""
        warnings.warn(
            "Synchronous get_connection is deprecated. Use get_async_connection().",
            DeprecationWarning,
            stacklevel=2
        )
        # Fallback implementation for compatibility
        if self._config is None:
            raise ValueError("Connection manager not initialized")
        return get_snowflake_connection(self._config)


# Create a singleton instance for convenience
connection_manager = SnowflakeConnectionManager()
legacy_connection_manager = LegacyConnectionManager()
