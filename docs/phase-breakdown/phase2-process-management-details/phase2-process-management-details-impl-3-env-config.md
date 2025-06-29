# Phase 2: Process Management & Deployment Details

## Context & Overview

The current Snowflake MCP server requires a terminal window to remain open and cannot run as a background daemon service. To enable production deployment with automatic restart, process monitoring, and log management, we need to implement proper process management using PM2 and systemd.

**Current Limitations:**
- Requires terminal window to stay open
- No process monitoring or automatic restart
- No log rotation or centralized logging
- Cannot survive system reboots
- No cluster mode for high availability

**Target Architecture:**
- PM2 ecosystem for process management
- Daemon mode operation without terminal dependency
- Automatic restart on failures
- Log rotation and management
- Environment-based configuration
- Systemd integration for system boot

## Dependencies Required

Add to `pyproject.toml`:
```toml
[project.optional-dependencies]
production = [
    "gunicorn>=21.2.0",  # WSGI server for production
    "uvloop>=0.19.0",    # High-performance event loop (Unix only)
    "setproctitle>=1.3.0",  # Process title setting
]

[project.scripts]
snowflake-mcp = "snowflake_mcp_server.main:run_stdio_server"
snowflake-mcp-http = "snowflake_mcp_server.transports.http_server:main"
snowflake-mcp-daemon = "snowflake_mcp_server.daemon:main"
```

## Implementation Plan

### 3. Environment-Based Configuration {#env-config}

**Step 1: Configuration Management System**

Create `snowflake_mcp_server/config/manager.py`:

```python
"""Configuration management for different environments."""

import os
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from ..utils.async_pool import ConnectionPoolConfig
from ..utils.snowflake_conn import SnowflakeConfig, AuthType


logger = logging.getLogger(__name__)


class ServerConfig(BaseModel):
    """Server configuration model."""
    host: str = Field(default="localhost", description="Host to bind to")
    port: int = Field(default=8000, description="Port to bind to", ge=1, le=65535)
    workers: int = Field(default=1, description="Number of worker processes")
    log_level: str = Field(default="INFO", description="Logging level")
    reload: bool = Field(default=False, description="Auto-reload on file changes")
    
    # Security
    api_key: Optional[str] = Field(default=None, description="API key for authentication")
    cors_origins: list = Field(default=["*"], description="CORS allowed origins")
    
    # Performance
    keepalive_timeout: int = Field(default=5, description="Keep-alive timeout")
    max_connections: int = Field(default=1000, description="Maximum connections")
    
    # Paths
    pid_file: str = Field(default="/var/run/snowflake-mcp.pid", description="PID file path")
    log_file: str = Field(default="/var/log/snowflake-mcp/server.log", description="Log file path")
    working_dir: str = Field(default="/var/lib/snowflake-mcp", description="Working directory")


class EnvironmentConfig:
    """Environment-based configuration manager."""
    
    ENVIRONMENTS = {
        "development": {
            "log_level": "DEBUG",
            "reload": True,
            "workers": 1,
            "pool_min_size": 1,
            "pool_max_size": 5,
            "cors_origins": ["*"]
        },
        "staging": {
            "log_level": "INFO",
            "reload": False,
            "workers": 2,
            "pool_min_size": 2,
            "pool_max_size": 10,
            "cors_origins": ["https://staging.example.com"]
        },
        "production": {
            "log_level": "WARNING",
            "reload": False,
            "workers": 4,
            "pool_min_size": 5,
            "pool_max_size": 20,
            "cors_origins": ["https://app.example.com"]
        }
    }
    
    def __init__(self, environment: str = None):
        self.environment = environment or os.getenv("ENVIRONMENT", "development")
        self._config_cache = {}
        
    def get_snowflake_config(self) -> SnowflakeConfig:
        """Get Snowflake configuration for current environment."""
        env_prefix = f"{self.environment.upper()}_" if self.environment != "development" else ""
        
        auth_type_str = os.getenv(f"{env_prefix}SNOWFLAKE_AUTH_TYPE", "private_key").lower()
        auth_type = (
            AuthType.PRIVATE_KEY
            if auth_type_str == "private_key"
            else AuthType.EXTERNAL_BROWSER
        )
        
        config = SnowflakeConfig(
            account=os.getenv(f"{env_prefix}SNOWFLAKE_ACCOUNT", ""),
            user=os.getenv(f"{env_prefix}SNOWFLAKE_USER", ""),
            auth_type=auth_type,
            warehouse=os.getenv(f"{env_prefix}SNOWFLAKE_WAREHOUSE"),
            database=os.getenv(f"{env_prefix}SNOWFLAKE_DATABASE"),
            schema_name=os.getenv(f"{env_prefix}SNOWFLAKE_SCHEMA"),
            role=os.getenv(f"{env_prefix}SNOWFLAKE_ROLE"),
        )
        
        if auth_type == AuthType.PRIVATE_KEY:
            config.private_key_path = os.getenv(f"{env_prefix}SNOWFLAKE_PRIVATE_KEY_PATH", "")
        
        return config
    
    def get_pool_config(self) -> ConnectionPoolConfig:
        """Get connection pool configuration for current environment."""
        env_defaults = self.ENVIRONMENTS.get(self.environment, {})
        
        return ConnectionPoolConfig(
            min_size=int(os.getenv("SNOWFLAKE_POOL_MIN_SIZE", env_defaults.get("pool_min_size", 2))),
            max_size=int(os.getenv("SNOWFLAKE_POOL_MAX_SIZE", env_defaults.get("pool_max_size", 10))),
            max_inactive_time=timedelta(minutes=int(os.getenv("SNOWFLAKE_POOL_MAX_INACTIVE_MINUTES", "30"))),
            health_check_interval=timedelta(minutes=int(os.getenv("SNOWFLAKE_POOL_HEALTH_CHECK_MINUTES", "5"))),
            connection_timeout=float(os.getenv("SNOWFLAKE_POOL_CONNECTION_TIMEOUT", "30.0")),
            retry_attempts=int(os.getenv("SNOWFLAKE_POOL_RETRY_ATTEMPTS", "3")),
        )
    
    def get_server_config(self) -> ServerConfig:
        """Get server configuration for current environment."""
        env_defaults = self.ENVIRONMENTS.get(self.environment, {})
        
        return ServerConfig(
            host=os.getenv("SERVER_HOST", "localhost"),
            port=int(os.getenv("SERVER_PORT", "8000")),
            workers=int(os.getenv("SERVER_WORKERS", env_defaults.get("workers", 1))),
            log_level=os.getenv("LOG_LEVEL", env_defaults.get("log_level", "INFO")),
            reload=os.getenv("SERVER_RELOAD", "false").lower() == "true" or env_defaults.get("reload", False),
            api_key=os.getenv("API_KEY"),
            cors_origins=os.getenv("CORS_ORIGINS", ",".join(env_defaults.get("cors_origins", ["*"]))).split(","),
            keepalive_timeout=int(os.getenv("KEEPALIVE_TIMEOUT", "5")),
            max_connections=int(os.getenv("MAX_CONNECTIONS", "1000")),
            pid_file=os.getenv("PID_FILE", f"/var/run/snowflake-mcp-{self.environment}.pid"),
            log_file=os.getenv("LOG_FILE", f"/var/log/snowflake-mcp/{self.environment}.log"),
            working_dir=os.getenv("WORKING_DIR", f"/var/lib/snowflake-mcp/{self.environment}")
        )
    
    def load_from_file(self, config_file: Union[str, Path]) -> Dict[str, Any]:
        """Load configuration from file."""
        config_file = Path(config_file)
        
        if not config_file.exists():
            logger.warning(f"Configuration file not found: {config_file}")
            return {}
        
        if config_file.suffix == '.json':
            import json
            with open(config_file) as f:
                return json.load(f)
        elif config_file.suffix in ['.yml', '.yaml']:
            try:
                import yaml
                with open(config_file) as f:
                    return yaml.safe_load(f)
            except ImportError:
                logger.error("PyYAML not installed, cannot load YAML config")
                return {}
        else:
            logger.error(f"Unsupported config file format: {config_file.suffix}")
            return {}
    
    def validate_configuration(self) -> bool:
        """Validate current configuration."""
        try:
            snowflake_config = self.get_snowflake_config()
            pool_config = self.get_pool_config()
            server_config = self.get_server_config()
            
            # Validate required fields
            if not snowflake_config.account:
                logger.error("SNOWFLAKE_ACCOUNT is required")
                return False
            
            if not snowflake_config.user:
                logger.error("SNOWFLAKE_USER is required")
                return False
            
            if snowflake_config.auth_type == AuthType.PRIVATE_KEY and not snowflake_config.private_key_path:
                logger.error("SNOWFLAKE_PRIVATE_KEY_PATH is required for private key auth")
                return False
            
            # Validate pool configuration
            if pool_config.min_size <= 0:
                logger.error("Pool minimum size must be greater than 0")
                return False
            
            if pool_config.max_size < pool_config.min_size:
                logger.error("Pool maximum size must be >= minimum size")
                return False
            
            logger.info(f"Configuration validation passed for environment: {self.environment}")
            return True
            
        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            return False


# Global configuration manager
config_manager = EnvironmentConfig()
```

**Step 2: Environment Files**

Create `.env.development`:

```bash
# Development Environment Configuration
ENVIRONMENT=development

# Snowflake Configuration
SNOWFLAKE_ACCOUNT=your-account
SNOWFLAKE_USER=your-user
SNOWFLAKE_AUTH_TYPE=private_key
SNOWFLAKE_PRIVATE_KEY_PATH=./keys/dev-private-key.pem
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=DEV_DATABASE
SNOWFLAKE_SCHEMA=PUBLIC
SNOWFLAKE_ROLE=DEV_ROLE

# Server Configuration
SERVER_HOST=localhost
SERVER_PORT=8000
SERVER_WORKERS=1
SERVER_RELOAD=true
LOG_LEVEL=DEBUG

# Connection Pool
SNOWFLAKE_POOL_MIN_SIZE=1
SNOWFLAKE_POOL_MAX_SIZE=5
SNOWFLAKE_POOL_MAX_INACTIVE_MINUTES=30
SNOWFLAKE_POOL_HEALTH_CHECK_MINUTES=5

# Paths
PID_FILE=./tmp/dev.pid
LOG_FILE=./logs/dev.log
WORKING_DIR=./tmp
```

Create `.env.production`:

```bash
# Production Environment Configuration
ENVIRONMENT=production

# Snowflake Configuration
SNOWFLAKE_ACCOUNT=${PROD_SNOWFLAKE_ACCOUNT}
SNOWFLAKE_USER=${PROD_SNOWFLAKE_USER}
SNOWFLAKE_AUTH_TYPE=private_key
SNOWFLAKE_PRIVATE_KEY_PATH=/etc/snowflake-mcp/prod-private-key.pem
SNOWFLAKE_WAREHOUSE=PROD_WH
SNOWFLAKE_DATABASE=PROD_DATABASE
SNOWFLAKE_SCHEMA=PUBLIC
SNOWFLAKE_ROLE=PROD_ROLE

# Server Configuration
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
SERVER_WORKERS=4
SERVER_RELOAD=false
LOG_LEVEL=INFO

# Security
API_KEY=${PROD_API_KEY}
CORS_ORIGINS=https://app.example.com,https://admin.example.com

# Connection Pool
SNOWFLAKE_POOL_MIN_SIZE=5
SNOWFLAKE_POOL_MAX_SIZE=20
SNOWFLAKE_POOL_MAX_INACTIVE_MINUTES=15
SNOWFLAKE_POOL_HEALTH_CHECK_MINUTES=2

# Paths
PID_FILE=/var/run/snowflake-mcp.pid
LOG_FILE=/var/log/snowflake-mcp/production.log
WORKING_DIR=/var/lib/snowflake-mcp
```

