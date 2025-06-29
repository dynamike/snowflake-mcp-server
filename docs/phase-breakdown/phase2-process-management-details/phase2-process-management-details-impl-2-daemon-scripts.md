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

### 2. Daemon Mode Implementation {#daemon-scripts}

**Step 1: Create Daemon Mode Entry Point**

Create `snowflake_mcp_server/daemon.py`:

```python
"""Daemon mode implementation for Snowflake MCP server."""

import os
import sys
import signal
import logging
import asyncio
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional

try:
    import setproctitle
except ImportError:
    setproctitle = None

from .transports.http_server import run_http_server_with_shutdown
from .utils.contextual_logging import setup_contextual_logging


class DaemonManager:
    """Manage daemon process lifecycle."""
    
    def __init__(
        self,
        pid_file: str = "/var/run/snowflake-mcp.pid",
        log_file: str = "/var/log/snowflake-mcp/daemon.log",
        working_dir: str = "/var/lib/snowflake-mcp"
    ):
        self.pid_file = Path(pid_file)
        self.log_file = Path(log_file)
        self.working_dir = Path(working_dir)
        self.logger = None
        
    def setup_logging(self) -> None:
        """Setup daemon logging."""
        # Ensure log directory exists
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.log_file),
                logging.StreamHandler(sys.stdout)  # Remove in true daemon mode
            ]
        )
        
        self.logger = logging.getLogger(__name__)
        
    def daemonize(self) -> None:
        """Daemonize the current process."""
        try:
            # First fork
            pid = os.fork()
            if pid > 0:
                sys.exit(0)  # Exit parent
        except OSError as e:
            self.logger.error(f"First fork failed: {e}")
            sys.exit(1)
        
        # Decouple from parent environment
        os.chdir(str(self.working_dir))
        os.setsid()
        os.umask(0)
        
        try:
            # Second fork
            pid = os.fork()
            if pid > 0:
                sys.exit(0)  # Exit second parent
        except OSError as e:
            self.logger.error(f"Second fork failed: {e}")
            sys.exit(1)
        
        # Redirect standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()
        
        si = open(os.devnull, 'r')
        so = open(str(self.log_file), 'a+')
        se = open(str(self.log_file), 'a+')
        
        os.dup2(si.fileno(), sys.stdin.fileno())
        os.dup2(so.fileno(), sys.stdout.fileno())
        os.dup2(se.fileno(), sys.stderr.fileno())
        
        # Write PID file
        self.write_pid_file()
        
        # Register cleanup handler
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        
    def write_pid_file(self) -> None:
        """Write PID to file."""
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.pid_file, 'w') as f:
            f.write(str(os.getpid()))
        
        self.logger.info(f"PID file written: {self.pid_file}")
        
    def remove_pid_file(self) -> None:
        """Remove PID file."""
        try:
            self.pid_file.unlink()
            self.logger.info(f"PID file removed: {self.pid_file}")
        except FileNotFoundError:
            pass
        except Exception as e:
            self.logger.error(f"Error removing PID file: {e}")
    
    def _signal_handler(self, signum: int, frame) -> None:
        """Handle termination signals."""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.remove_pid_file()
        sys.exit(0)
    
    def is_running(self) -> bool:
        """Check if daemon is already running."""
        if not self.pid_file.exists():
            return False
        
        try:
            with open(self.pid_file, 'r') as f:
                pid = int(f.read().strip())
            
            # Check if process exists
            os.kill(pid, 0)
            return True
            
        except (ValueError, ProcessLookupError, PermissionError):
            # PID file exists but process doesn't
            self.remove_pid_file()
            return False
    
    def stop_daemon(self) -> bool:
        """Stop running daemon."""
        if not self.is_running():
            self.logger.info("Daemon is not running")
            return True
        
        try:
            with open(self.pid_file, 'r') as f:
                pid = int(f.read().strip())
            
            # Send SIGTERM
            os.kill(pid, signal.SIGTERM)
            
            # Wait for process to exit
            import time
            for _ in range(30):  # Wait up to 30 seconds
                try:
                    os.kill(pid, 0)
                    time.sleep(1)
                except ProcessLookupError:
                    break
            else:
                # Force kill if still running
                self.logger.warning("Process didn't exit gracefully, force killing...")
                os.kill(pid, signal.SIGKILL)
            
            self.remove_pid_file()
            self.logger.info("Daemon stopped successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping daemon: {e}")
            return False
    
    async def run_server(self, host: str = "localhost", port: int = 8000) -> None:
        """Run the HTTP server in daemon mode."""
        self.logger.info(f"Starting Snowflake MCP server daemon on {host}:{port}")
        
        # Set process title if available
        if setproctitle:
            setproctitle.setproctitle(f"snowflake-mcp-daemon:{port}")
        
        try:
            await run_http_server_with_shutdown(host, port)
        except Exception as e:
            self.logger.error(f"Server error: {e}")
            raise
        finally:
            self.logger.info("Server stopped")


def start_daemon(
    host: str = "localhost",
    port: int = 8000,
    daemon: bool = True,
    pid_file: str = "/var/run/snowflake-mcp.pid",
    log_file: str = "/var/log/snowflake-mcp/daemon.log",
    working_dir: str = "/var/lib/snowflake-mcp"
) -> None:
    """Start the daemon."""
    
    manager = DaemonManager(pid_file, log_file, working_dir)
    manager.setup_logging()
    
    # Check if already running
    if manager.is_running():
        print("Daemon is already running")
        sys.exit(1)
    
    # Daemonize if requested
    if daemon:
        print(f"Starting daemon mode, logs: {log_file}")
        manager.daemonize()
    
    # Setup contextual logging
    setup_contextual_logging()
    
    # Run server
    try:
        asyncio.run(manager.run_server(host, port))
    except KeyboardInterrupt:
        manager.logger.info("Received interrupt, shutting down...")
    except Exception as e:
        manager.logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        manager.remove_pid_file()


def stop_daemon(pid_file: str = "/var/run/snowflake-mcp.pid") -> None:
    """Stop the daemon."""
    manager = DaemonManager(pid_file=pid_file)
    manager.setup_logging()
    
    if manager.stop_daemon():
        print("Daemon stopped successfully")
    else:
        print("Failed to stop daemon")
        sys.exit(1)


def status_daemon(pid_file: str = "/var/run/snowflake-mcp.pid") -> None:
    """Check daemon status."""
    manager = DaemonManager(pid_file=pid_file)
    
    if manager.is_running():
        with open(pid_file, 'r') as f:
            pid = f.read().strip()
        print(f"Daemon is running (PID: {pid})")
    else:
        print("Daemon is not running")


def main():
    """CLI entry point for daemon management."""
    parser = argparse.ArgumentParser(description="Snowflake MCP Server Daemon")
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Start command
    start_parser = subparsers.add_parser('start', help='Start the daemon')
    start_parser.add_argument('--host', default='localhost', help='Host to bind to')
    start_parser.add_argument('--port', type=int, default=8000, help='Port to bind to')
    start_parser.add_argument('--no-daemon', action='store_true', help='Run in foreground')
    start_parser.add_argument('--pid-file', default='/var/run/snowflake-mcp.pid', help='PID file path')
    start_parser.add_argument('--log-file', default='/var/log/snowflake-mcp/daemon.log', help='Log file path')
    start_parser.add_argument('--working-dir', default='/var/lib/snowflake-mcp', help='Working directory')
    
    # Stop command
    stop_parser = subparsers.add_parser('stop', help='Stop the daemon')
    stop_parser.add_argument('--pid-file', default='/var/run/snowflake-mcp.pid', help='PID file path')
    
    # Status command
    status_parser = subparsers.add_parser('status', help='Check daemon status')
    status_parser.add_argument('--pid-file', default='/var/run/snowflake-mcp.pid', help='PID file path')
    
    # Restart command
    restart_parser = subparsers.add_parser('restart', help='Restart the daemon')
    restart_parser.add_argument('--host', default='localhost', help='Host to bind to')
    restart_parser.add_argument('--port', type=int, default=8000, help='Port to bind to')
    restart_parser.add_argument('--pid-file', default='/var/run/snowflake-mcp.pid', help='PID file path')
    restart_parser.add_argument('--log-file', default='/var/log/snowflake-mcp/daemon.log', help='Log file path')
    restart_parser.add_argument('--working-dir', default='/var/lib/snowflake-mcp', help='Working directory')
    
    args = parser.parse_args()
    
    if args.command == 'start':
        start_daemon(
            host=args.host,
            port=args.port,
            daemon=not args.no_daemon,
            pid_file=args.pid_file,
            log_file=args.log_file,
            working_dir=args.working_dir
        )
    elif args.command == 'stop':
        stop_daemon(pid_file=args.pid_file)
    elif args.command == 'status':
        status_daemon(pid_file=args.pid_file)
    elif args.command == 'restart':
        # Stop then start
        stop_daemon(pid_file=args.pid_file)
        import time
        time.sleep(2)  # Brief pause
        start_daemon(
            host=args.host,
            port=args.port,
            daemon=True,
            pid_file=args.pid_file,
            log_file=args.log_file,
            working_dir=args.working_dir
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

