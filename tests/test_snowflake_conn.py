"""Tests for Snowflake connection utilities."""

from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp_server_snowflake.utils.snowflake_conn import (
    AuthType,
    SnowflakeConfig,
    get_snowflake_connection,
)


@pytest.fixture
def mock_private_key() -> rsa.RSAPrivateKey:
    """Mock a private key."""
    return MagicMock(spec=rsa.RSAPrivateKey)


@pytest.fixture
def snowflake_config_private_key() -> SnowflakeConfig:
    """Create a sample Snowflake configuration with private key auth."""
    return SnowflakeConfig(
        account="testaccount",
        user="testuser",
        auth_type=AuthType.PRIVATE_KEY,
        private_key_path="/path/to/key.p8",
        warehouse="test_warehouse",
        database="test_database",
        schema_name="test_schema",
        role="test_role",
    )


@pytest.fixture
def snowflake_config_browser() -> SnowflakeConfig:
    """Create a sample Snowflake configuration with external browser auth."""
    return SnowflakeConfig(
        account="testaccount",
        user="testuser",
        auth_type=AuthType.EXTERNAL_BROWSER,
        warehouse="test_warehouse",
        database="test_database",
        schema_name="test_schema",
        role="test_role",
    )


@patch("mcp_server_snowflake.utils.snowflake_conn.load_private_key")
@patch("snowflake.connector.connect")
def test_get_snowflake_connection_private_key(
    mock_connect: MagicMock,
    mock_load_key: MagicMock,
    snowflake_config_private_key: SnowflakeConfig,
    mock_private_key: rsa.RSAPrivateKey,
) -> None:
    """Test creating a Snowflake connection with private key auth."""
    # Setup mocks
    mock_load_key.return_value = mock_private_key
    mock_connection = MagicMock()
    mock_connect.return_value = mock_connection

    # Call function
    conn = get_snowflake_connection(snowflake_config_private_key)

    # Assertions
    mock_load_key.assert_called_once_with(snowflake_config_private_key.private_key_path)
    mock_connect.assert_called_once_with(
        account=snowflake_config_private_key.account,
        user=snowflake_config_private_key.user,
        private_key=mock_private_key,
        warehouse=snowflake_config_private_key.warehouse,
        database=snowflake_config_private_key.database,
        schema=snowflake_config_private_key.schema_name,
        role=snowflake_config_private_key.role,
    )
    assert conn == mock_connection


@patch("snowflake.connector.connect")
def test_get_snowflake_connection_browser_auth(
    mock_connect: MagicMock,
    snowflake_config_browser: SnowflakeConfig,
) -> None:
    """Test creating a Snowflake connection with external browser auth."""
    # Setup mocks
    mock_connection = MagicMock()
    mock_connect.return_value = mock_connection

    # Call function
    conn = get_snowflake_connection(snowflake_config_browser)

    # Assertions
    mock_connect.assert_called_once_with(
        account=snowflake_config_browser.account,
        user=snowflake_config_browser.user,
        authenticator="externalbrowser",
        warehouse=snowflake_config_browser.warehouse,
        database=snowflake_config_browser.database,
        schema=snowflake_config_browser.schema_name,
        role=snowflake_config_browser.role,
    )
    assert conn == mock_connection
