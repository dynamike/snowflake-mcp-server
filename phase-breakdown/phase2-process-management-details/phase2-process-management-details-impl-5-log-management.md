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

### 5. Log Management Implementation {#log-management}

**Step 1: Structured Logging Setup**

Create `snowflake_mcp_server/utils/logging_config.py`:

```python
"""Advanced logging configuration for production deployment."""

import os
import sys
import logging
import logging.handlers
from pathlib import Path
from typing import Dict, Any, Optional
import json
from datetime import datetime


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in ['name', 'msg', 'args', 'levelname', 'levelno', 'pathname',
                          'filename', 'module', 'lineno', 'funcName', 'created', 'msecs',
                          'relativeCreated', 'thread', 'threadName', 'processName',
                          'process', 'getMessage', 'exc_info', 'exc_text', 'stack_info']:
                log_entry[key] = value
        
        return json.dumps(log_entry)


class LoggingConfig:
    """Production logging configuration."""
    
    def __init__(
        self,
        log_level: str = "INFO",
        log_dir: str = "/var/log/snowflake-mcp",
        max_bytes: int = 50 * 1024 * 1024,  # 50MB
        backup_count: int = 10,
        json_format: bool = True
    ):
        self.log_level = getattr(logging, log_level.upper())
        self.log_dir = Path(log_dir)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.json_format = json_format
        
        # Ensure log directory exists
        self.log_dir.mkdir(parents=True, exist_ok=True)
    
    def setup_logging(self) -> None:
        """Setup production logging configuration."""
        
        # Create root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(self.log_level)
        
        # Remove existing handlers
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        
        # Setup formatters
        if self.json_format:
            formatter = JSONFormatter()
        else:
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] - %(message)s'
            )
        
        # Main application log
        main_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "application.log",
            maxBytes=self.max_bytes,
            backupCount=self.backup_count
        )
        main_handler.setLevel(self.log_level)
        main_handler.setFormatter(formatter)
        
        # Error log (ERROR and above only)
        error_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "error.log",
            maxBytes=self.max_bytes,
            backupCount=self.backup_count
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        
        # Access log for HTTP requests
        access_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "access.log",
            maxBytes=self.max_bytes,
            backupCount=self.backup_count
        )
        access_handler.setLevel(logging.INFO)
        access_handler.setFormatter(formatter)
        
        # Console handler for development
        if os.getenv("ENVIRONMENT") == "development":
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.DEBUG)
            console_handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            ))
            root_logger.addHandler(console_handler)
        
        # Add handlers to root logger
        root_logger.addHandler(main_handler)
        root_logger.addHandler(error_handler)
        
        # Setup access logger
        access_logger = logging.getLogger("access")
        access_logger.addHandler(access_handler)
        access_logger.propagate = False
        
        # Setup specific loggers
        self._setup_component_loggers()
    
    def _setup_component_loggers(self) -> None:
        """Setup component-specific loggers."""
        
        # Database operations logger
        db_logger = logging.getLogger("snowflake_mcp.database")
        db_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "database.log",
            maxBytes=self.max_bytes,
            backupCount=self.backup_count
        )
        db_handler.setFormatter(JSONFormatter() if self.json_format else logging.Formatter(
            '%(asctime)s - DATABASE - %(levelname)s - %(message)s'
        ))
        db_logger.addHandler(db_handler)
        db_logger.propagate = False
        
        # Performance logger
        perf_logger = logging.getLogger("snowflake_mcp.performance")
        perf_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "performance.log",
            maxBytes=self.max_bytes,
            backupCount=self.backup_count
        )
        perf_handler.setFormatter(JSONFormatter() if self.json_format else logging.Formatter(
            '%(asctime)s - PERF - %(message)s'
        ))
        perf_logger.addHandler(perf_handler)
        perf_logger.propagate = False
```

## Testing Strategy

Create `tests/test_daemon_mode.py`:

```python
import pytest
import asyncio
import time
import signal
import subprocess
from pathlib import Path

@pytest.mark.integration
def test_daemon_startup():
    """Test daemon starts and stops properly."""
    
    # Start daemon
    result = subprocess.run([
        "uv", "run", "snowflake-mcp-daemon", "start",
        "--host", "localhost",
        "--port", "8899",
        "--pid-file", "./tmp/test.pid",
        "--no-daemon"  # Run in foreground for testing
    ], timeout=10, capture_output=True, text=True)
    
    # Should start successfully
    assert result.returncode == 0


@pytest.mark.integration
def test_pm2_integration():
    """Test PM2 process management."""
    
    # Start with PM2
    subprocess.run(["pm2", "start", "ecosystem.config.js", "--env", "development"])
    
    # Wait for startup
    time.sleep(5)
    
    try:
        # Check process is running
        result = subprocess.run(["pm2", "list"], capture_output=True, text=True)
        assert "snowflake-mcp-http" in result.stdout
        
        # Test health check
        import requests
        response = requests.get("http://localhost:8000/health")
        assert response.status_code == 200
        
    finally:
        # Cleanup
        subprocess.run(["pm2", "delete", "all"])
```

## Verification Steps

1. **Daemon Mode**: Verify server runs in background without terminal
2. **PM2 Integration**: Test automatic restart and process monitoring
3. **Environment Config**: Confirm different environments load correct settings
4. **Systemd Service**: Test service start/stop/restart functionality
5. **Log Management**: Verify log rotation and structured logging
6. **Health Monitoring**: Test health checks work in daemon mode

## Completion Criteria

- [ ] PM2 ecosystem configuration manages server lifecycle
- [ ] Daemon mode runs server in background without terminal dependency
- [ ] Environment-based configuration loads appropriate settings
- [ ] Systemd service enables automatic startup on boot
- [ ] Log rotation and management prevents disk space issues
- [ ] Health monitoring works in daemon mode
- [ ] Graceful shutdown handles active connections properly
- [ ] Process monitoring detects and restarts failed instances
- [ ] Installation scripts automate deployment setup