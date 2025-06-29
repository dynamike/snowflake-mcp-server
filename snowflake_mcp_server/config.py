"""Environment-based configuration management for Snowflake MCP Server."""

import logging
import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, validator

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)


class SnowflakeConnectionConfig(BaseModel):
    """Snowflake connection configuration."""
    
    account: str = Field(..., description="Snowflake account identifier")
    user: str = Field(..., description="Snowflake username")
    auth_type: str = Field("private_key", description="Authentication type")
    private_key_path: Optional[str] = Field(None, description="Path to private key file")
    private_key_passphrase: Optional[str] = Field(None, description="Private key passphrase")
    private_key: Optional[str] = Field(None, description="Private key content (base64)")
    
    @validator('account')
    def validate_account(cls, v):
        if not v:
            raise ValueError("SNOWFLAKE_ACCOUNT is required")
        return v
    
    @validator('user')
    def validate_user(cls, v):
        if not v:
            raise ValueError("SNOWFLAKE_USER is required")
        return v


class ConnectionPoolConfig(BaseModel):
    """Connection pool configuration."""
    
    min_size: int = Field(2, description="Minimum pool size")
    max_size: int = Field(10, description="Maximum pool size")
    connection_timeout: float = Field(30.0, description="Connection timeout in seconds")
    health_check_interval: int = Field(5, description="Health check interval in minutes")
    max_inactive_time: int = Field(30, description="Max inactive time in minutes")
    refresh_hours: int = Field(8, description="Connection refresh interval in hours")
    
    @validator('min_size')
    def validate_min_size(cls, v):
        if v < 1:
            raise ValueError("min_size must be at least 1")
        return v
    
    @validator('max_size')
    def validate_max_size(cls, v, values):
        if 'min_size' in values and v < values['min_size']:
            raise ValueError("max_size must be >= min_size")
        return v


class HttpServerConfig(BaseModel):
    """HTTP server configuration."""
    
    host: str = Field("0.0.0.0", description="HTTP server host")
    port: int = Field(8000, description="HTTP server port")
    cors_origins: List[str] = Field(["*"], description="CORS allowed origins")
    max_request_size: int = Field(10, description="Maximum request size in MB")
    request_timeout: int = Field(300, description="Request timeout in seconds")
    
    @validator('port')
    def validate_port(cls, v):
        if not (1 <= v <= 65535):
            raise ValueError("port must be between 1 and 65535")
        return v
    
    @validator('cors_origins', pre=True)
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(',') if origin.strip()]
        return v


class LoggingConfig(BaseModel):
    """Logging configuration."""
    
    level: str = Field("INFO", description="Log level")
    format: str = Field("text", description="Log format (text or json)")
    structured: bool = Field(True, description="Enable structured logging")
    file_max_size: int = Field(100, description="Log file max size in MB")
    file_backup_count: int = Field(5, description="Number of log files to keep")
    
    @validator('level')
    def validate_level(cls, v):
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"level must be one of {valid_levels}")
        return v.upper()
    
    @validator('format')
    def validate_format(cls, v):
        if v.lower() not in ["text", "json"]:
            raise ValueError("format must be 'text' or 'json'")
        return v.lower()


class PerformanceConfig(BaseModel):
    """Performance and resource configuration."""
    
    max_concurrent_requests: int = Field(10, description="Max concurrent requests per client")
    default_query_limit: int = Field(100, description="Default query row limit")
    max_query_limit: int = Field(10000, description="Maximum query row limit")
    enable_query_cache: bool = Field(False, description="Enable query result caching")
    query_cache_ttl: int = Field(5, description="Query cache TTL in minutes")
    
    @validator('default_query_limit')
    def validate_default_limit(cls, v):
        if v < 1:
            raise ValueError("default_query_limit must be at least 1")
        return v
    
    @validator('max_query_limit')
    def validate_max_limit(cls, v, values):
        if 'default_query_limit' in values and v < values['default_query_limit']:
            raise ValueError("max_query_limit must be >= default_query_limit")
        return v


class SecurityConfig(BaseModel):
    """Security configuration."""
    
    enable_api_auth: bool = Field(False, description="Enable API key authentication")
    api_keys: List[str] = Field([], description="API keys")
    enable_sql_protection: bool = Field(True, description="Enable SQL injection protection")
    enable_rate_limiting: bool = Field(False, description="Enable request rate limiting")
    rate_limit_per_minute: int = Field(60, description="Rate limit per minute per client")
    
    @validator('api_keys', pre=True)
    def parse_api_keys(cls, v):
        if isinstance(v, str):
            return [key.strip() for key in v.split(',') if key.strip()]
        return v


class MonitoringConfig(BaseModel):
    """Monitoring and health check configuration."""
    
    enable_metrics: bool = Field(False, description="Enable Prometheus metrics")
    metrics_endpoint: str = Field("/metrics", description="Metrics endpoint path")
    health_check_timeout: int = Field(10, description="Health check timeout in seconds")
    detailed_health_checks: bool = Field(True, description="Enable detailed health checks")


class DevelopmentConfig(BaseModel):
    """Development and debug configuration."""
    
    debug: bool = Field(False, description="Enable debug mode")
    log_sql_queries: bool = Field(False, description="Enable SQL query logging")
    enable_profiling: bool = Field(False, description="Enable performance profiling")
    mock_snowflake: bool = Field(False, description="Mock Snowflake responses for testing")


class ServerConfig(BaseModel):
    """Complete server configuration."""
    
    environment: str = Field("production", description="Environment name")
    app_version: str = Field("0.2.0", description="Application version")
    
    # Sub-configurations
    snowflake: SnowflakeConnectionConfig
    pool: ConnectionPoolConfig
    http: HttpServerConfig
    logging: LoggingConfig
    performance: PerformanceConfig
    security: SecurityConfig
    monitoring: MonitoringConfig
    development: DevelopmentConfig
    
    @validator('environment')
    def validate_environment(cls, v):
        valid_envs = ["development", "staging", "production"]
        if v.lower() not in valid_envs:
            raise ValueError(f"environment must be one of {valid_envs}")
        return v.lower()


def load_config() -> ServerConfig:
    """Load configuration from environment variables."""
    
    # Helper function to get environment variable with default
    def get_env(key: str, default=None, type_func=str):
        value = os.getenv(key, default)
        if value is None:
            return default
        if type_func is bool:
            if isinstance(value, bool):
                return value
            return str(value).lower() in ('true', '1', 'yes', 'on')
        return type_func(value)
    
    try:
        config = ServerConfig(
            environment=get_env("ENVIRONMENT", "production"),
            app_version=get_env("APP_VERSION", "0.2.0"),
            
            snowflake=SnowflakeConnectionConfig(
                account=get_env("SNOWFLAKE_ACCOUNT"),
                user=get_env("SNOWFLAKE_USER"),
                auth_type=get_env("SNOWFLAKE_AUTH_TYPE", "private_key"),
                private_key_path=get_env("SNOWFLAKE_PRIVATE_KEY_PATH"),
                private_key_passphrase=get_env("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
                private_key=get_env("SNOWFLAKE_PRIVATE_KEY"),
            ),
            
            pool=ConnectionPoolConfig(
                min_size=get_env("SNOWFLAKE_POOL_MIN_SIZE", 2, int),
                max_size=get_env("SNOWFLAKE_POOL_MAX_SIZE", 10, int),
                connection_timeout=get_env("SNOWFLAKE_CONN_TIMEOUT", 30.0, float),
                health_check_interval=get_env("SNOWFLAKE_HEALTH_CHECK_INTERVAL", 5, int),
                max_inactive_time=get_env("SNOWFLAKE_MAX_INACTIVE_TIME", 30, int),
                refresh_hours=get_env("SNOWFLAKE_CONN_REFRESH_HOURS", 8, int),
            ),
            
            http=HttpServerConfig(
                host=get_env("MCP_HTTP_HOST", "0.0.0.0"),
                port=get_env("MCP_HTTP_PORT", 8000, int),
                cors_origins=get_env("MCP_CORS_ORIGINS", "*"),
                max_request_size=get_env("MCP_MAX_REQUEST_SIZE", 10, int),
                request_timeout=get_env("MCP_REQUEST_TIMEOUT", 300, int),
            ),
            
            logging=LoggingConfig(
                level=get_env("LOG_LEVEL", "INFO"),
                format=get_env("LOG_FORMAT", "text"),
                structured=get_env("STRUCTURED_LOGGING", True, bool),
                file_max_size=get_env("LOG_FILE_MAX_SIZE", 100, int),
                file_backup_count=get_env("LOG_FILE_BACKUP_COUNT", 5, int),
            ),
            
            performance=PerformanceConfig(
                max_concurrent_requests=get_env("MAX_CONCURRENT_REQUESTS", 10, int),
                default_query_limit=get_env("DEFAULT_QUERY_LIMIT", 100, int),
                max_query_limit=get_env("MAX_QUERY_LIMIT", 10000, int),
                enable_query_cache=get_env("ENABLE_QUERY_CACHE", False, bool),
                query_cache_ttl=get_env("QUERY_CACHE_TTL", 5, int),
            ),
            
            security=SecurityConfig(
                enable_api_auth=get_env("ENABLE_API_AUTH", False, bool),
                api_keys=get_env("API_KEYS", ""),
                enable_sql_protection=get_env("ENABLE_SQL_PROTECTION", True, bool),
                enable_rate_limiting=get_env("ENABLE_RATE_LIMITING", False, bool),
                rate_limit_per_minute=get_env("RATE_LIMIT_PER_MINUTE", 60, int),
            ),
            
            monitoring=MonitoringConfig(
                enable_metrics=get_env("ENABLE_METRICS", False, bool),
                metrics_endpoint=get_env("METRICS_ENDPOINT", "/metrics"),
                health_check_timeout=get_env("HEALTH_CHECK_TIMEOUT", 10, int),
                detailed_health_checks=get_env("DETAILED_HEALTH_CHECKS", True, bool),
            ),
            
            development=DevelopmentConfig(
                debug=get_env("DEBUG", False, bool),
                log_sql_queries=get_env("LOG_SQL_QUERIES", False, bool),
                enable_profiling=get_env("ENABLE_PROFILING", False, bool),
                mock_snowflake=get_env("MOCK_SNOWFLAKE", False, bool),
            ),
        )
        
        logger.info(f"Configuration loaded successfully for environment: {config.environment}")
        return config
        
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        raise


# Global configuration instance
_config: Optional[ServerConfig] = None


def get_config() -> ServerConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> ServerConfig:
    """Reload configuration from environment variables."""
    global _config
    _config = load_config()
    return _config


def validate_config_file(config_path: str) -> bool:
    """Validate a configuration file."""
    try:
        if Path(config_path).exists():
            # Load the config file temporarily
            from dotenv import dotenv_values
            config_values = dotenv_values(config_path)
            
            # Temporarily set environment variables
            original_env = {}
            for key, value in config_values.items():
                original_env[key] = os.getenv(key)
                os.environ[key] = value
            
            try:
                # Try to load configuration
                test_config = load_config()
                logger.info(f"Configuration file {config_path} is valid")
                return True
            finally:
                # Restore original environment
                for key, value in original_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        else:
            logger.error(f"Configuration file {config_path} does not exist")
            return False
            
    except Exception as e:
        logger.error(f"Configuration file {config_path} is invalid: {e}")
        return False


if __name__ == "__main__":
    # Test configuration loading
    try:
        config = load_config()
        print("Configuration loaded successfully!")
        print(f"Environment: {config.environment}")
        print(f"Snowflake Account: {config.snowflake.account}")
        print(f"HTTP Port: {config.http.port}")
    except Exception as e:
        print(f"Configuration error: {e}")