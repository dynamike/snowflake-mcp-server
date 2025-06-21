"""Log rotation and management utilities for Snowflake MCP Server."""

import logging
import logging.handlers
import time
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import get_config


class RotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Enhanced rotating file handler with better error handling."""
    
    def __init__(self, filename: str, mode: str = 'a', maxBytes: int = 0, 
                 backupCount: int = 0, encoding: Optional[str] = None, delay: bool = False):
        # Ensure log directory exists
        log_dir = Path(filename).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        
        super().__init__(filename, mode, maxBytes, backupCount, encoding, delay)
    
    def doRollover(self):
        """Enhanced rollover with better error handling."""
        try:
            super().doRollover()
        except (OSError, IOError):
            # If rollover fails, try to continue logging to the main file
            self.handleError(None)


class TimedRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """Enhanced timed rotating file handler with better error handling."""
    
    def __init__(self, filename: str, when: str = 'h', interval: int = 1, 
                 backupCount: int = 0, encoding: Optional[str] = None, 
                 delay: bool = False, utc: bool = False, atTime=None):
        # Ensure log directory exists
        log_dir = Path(filename).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        
        super().__init__(filename, when, interval, backupCount, encoding, delay, utc, atTime)
    
    def doRollover(self):
        """Enhanced rollover with better error handling."""
        try:
            super().doRollover()
        except (OSError, IOError):
            # If rollover fails, try to continue logging to the main file
            self.handleError(None)


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging."""
    
    def format(self, record):
        """Format log record as JSON."""
        import json
        
        log_entry = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(record.created)),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
            'thread': record.thread,
            'thread_name': record.threadName,
        }
        
        # Add exception information if present
        if record.exc_info:
            log_entry['exception'] = self.formatException(record.exc_info)
        
        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in ['name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 
                          'filename', 'module', 'lineno', 'funcName', 'created', 
                          'msecs', 'relativeCreated', 'thread', 'threadName', 
                          'processName', 'process', 'stack_info', 'exc_info', 'exc_text']:
                log_entry[key] = value
        
        return json.dumps(log_entry, default=str)


class LogManager:
    """Centralized log management for the MCP server."""
    
    def __init__(self):
        self.config = get_config()
        self.handlers: Dict[str, logging.Handler] = {}
        self._setup_complete = False
    
    def setup_logging(self) -> None:
        """Setup logging configuration based on server config."""
        if self._setup_complete:
            return
        
        # Get root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, self.config.logging.level))
        
        # Clear existing handlers
        root_logger.handlers.clear()
        
        # Setup console handler
        self._setup_console_handler()
        
        # Setup file handlers
        self._setup_file_handlers()
        
        # Setup specific logger configurations
        self._setup_logger_configs()
        
        self._setup_complete = True
        
        logging.info(f"Logging initialized - Level: {self.config.logging.level}, Format: {self.config.logging.format}")
    
    def _setup_console_handler(self) -> None:
        """Setup console logging handler."""
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        
        if self.config.logging.format == 'json':
            formatter = JsonFormatter()
        else:
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
        
        console_handler.setFormatter(formatter)
        logging.getLogger().addHandler(console_handler)
        self.handlers['console'] = console_handler
    
    def _setup_file_handlers(self) -> None:
        """Setup file logging handlers with rotation."""
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        
        # Main application log
        main_log_file = log_dir / "snowflake-mcp-server.log"
        main_handler = RotatingFileHandler(
            filename=str(main_log_file),
            maxBytes=self.config.logging.file_max_size * 1024 * 1024,  # Convert MB to bytes
            backupCount=self.config.logging.file_backup_count,
            encoding='utf-8'
        )
        main_handler.setLevel(getattr(logging, self.config.logging.level))
        
        # Error log
        error_log_file = log_dir / "snowflake-mcp-error.log"
        error_handler = RotatingFileHandler(
            filename=str(error_log_file),
            maxBytes=self.config.logging.file_max_size * 1024 * 1024,
            backupCount=self.config.logging.file_backup_count,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        
        # Access log for HTTP requests
        access_log_file = log_dir / "snowflake-mcp-access.log"
        access_handler = TimedRotatingFileHandler(
            filename=str(access_log_file),
            when='midnight',
            interval=1,
            backupCount=30,  # Keep 30 days of access logs
            encoding='utf-8'
        )
        access_handler.setLevel(logging.INFO)
        
        # Setup formatters
        if self.config.logging.format == 'json':
            file_formatter = JsonFormatter()
        else:
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
            )
        
        main_handler.setFormatter(file_formatter)
        error_handler.setFormatter(file_formatter)
        access_handler.setFormatter(file_formatter)
        
        # Add handlers to root logger
        logging.getLogger().addHandler(main_handler)
        logging.getLogger().addHandler(error_handler)
        
        # Store handlers
        self.handlers['main_file'] = main_handler
        self.handlers['error_file'] = error_handler
        self.handlers['access_file'] = access_handler
    
    def _setup_logger_configs(self) -> None:
        """Setup specific logger configurations."""
        # Snowflake connector can be very verbose
        logging.getLogger('snowflake.connector').setLevel(logging.WARNING)
        logging.getLogger('snowflake.connector.network').setLevel(logging.ERROR)
        
        # FastAPI/Uvicorn access logs
        if 'access_file' in self.handlers:
            uvicorn_access = logging.getLogger('uvicorn.access')
            uvicorn_access.addHandler(self.handlers['access_file'])
            uvicorn_access.propagate = False
        
        # SQL query logging (if enabled)
        if self.config.development.log_sql_queries:
            sql_logger = logging.getLogger('snowflake_mcp.sql')
            sql_logger.setLevel(logging.DEBUG)
            
            # Create separate SQL log file
            sql_log_file = Path("logs") / "snowflake-mcp-sql.log"
            sql_handler = TimedRotatingFileHandler(
                filename=str(sql_log_file),
                when='midnight',
                interval=1,
                backupCount=7,  # Keep 7 days of SQL logs
                encoding='utf-8'
            )
            sql_handler.setLevel(logging.DEBUG)
            
            sql_formatter = logging.Formatter(
                '%(asctime)s - [SQL] - %(message)s'
            )
            sql_handler.setFormatter(sql_formatter)
            sql_logger.addHandler(sql_handler)
            sql_logger.propagate = False
            
            self.handlers['sql_file'] = sql_handler
    
    def get_log_files(self) -> Dict[str, Any]:
        """Get information about current log files."""
        log_dir = Path("logs")
        if not log_dir.exists():
            return {}
        
        log_files = {}
        for log_file in log_dir.glob("*.log"):
            try:
                stat = log_file.stat()
                log_files[log_file.name] = {
                    'path': str(log_file),
                    'size_bytes': stat.st_size,
                    'size_mb': round(stat.st_size / (1024 * 1024), 2),
                    'modified': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime)),
                    'created': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_ctime)),
                }
            except (OSError, IOError):
                # Skip files we can't access
                continue
        
        return log_files
    
    def rotate_logs(self) -> Dict[str, bool]:
        """Manually trigger log rotation for all handlers."""
        results = {}
        
        for name, handler in self.handlers.items():
            if isinstance(handler, (RotatingFileHandler, TimedRotatingFileHandler)):
                try:
                    handler.doRollover()
                    results[name] = True
                except Exception as e:
                    logging.error(f"Failed to rotate log for handler {name}: {e}")
                    results[name] = False
            else:
                results[name] = None  # Not a rotating handler
        
        return results
    
    def cleanup_old_logs(self, days_to_keep: int = 30) -> int:
        """Clean up old log files beyond retention period."""
        log_dir = Path("logs")
        if not log_dir.exists():
            return 0
        
        cutoff_time = time.time() - (days_to_keep * 24 * 60 * 60)
        cleaned_count = 0
        
        # Clean up old rotated log files
        for log_file in log_dir.glob("*.log.*"):
            try:
                if log_file.stat().st_mtime < cutoff_time:
                    log_file.unlink()
                    cleaned_count += 1
                    logging.info(f"Cleaned up old log file: {log_file}")
            except (OSError, IOError) as e:
                logging.warning(f"Failed to clean up log file {log_file}: {e}")
        
        return cleaned_count
    
    def get_log_stats(self) -> Dict[str, Any]:
        """Get logging statistics."""
        log_dir = Path("logs")
        
        stats = {
            'log_directory': str(log_dir),
            'log_files': self.get_log_files(),
            'total_log_size_mb': 0,
            'handlers': list(self.handlers.keys()),
            'config': {
                'level': self.config.logging.level,
                'format': self.config.logging.format,
                'structured': self.config.logging.structured,
                'file_max_size_mb': self.config.logging.file_max_size,
                'file_backup_count': self.config.logging.file_backup_count,
            }
        }
        
        # Calculate total log size
        for file_info in stats['log_files'].values():
            stats['total_log_size_mb'] += file_info['size_mb']
        
        stats['total_log_size_mb'] = round(stats['total_log_size_mb'], 2)
        
        return stats


# Global log manager instance
_log_manager: Optional[LogManager] = None


def get_log_manager() -> LogManager:
    """Get the global log manager instance."""
    global _log_manager
    if _log_manager is None:
        _log_manager = LogManager()
    return _log_manager


def setup_logging() -> None:
    """Setup logging using the global log manager."""
    log_manager = get_log_manager()
    log_manager.setup_logging()


def sql_logger(query: str, params: Optional[Dict[str, Any]] = None, 
               duration_ms: Optional[float] = None) -> None:
    """Log SQL queries if SQL logging is enabled."""
    config = get_config()
    if not config.development.log_sql_queries:
        return
    
    logger = logging.getLogger('snowflake_mcp.sql')
    
    log_message = f"QUERY: {query}"
    if params:
        log_message += f" | PARAMS: {params}"
    if duration_ms is not None:
        log_message += f" | DURATION: {duration_ms:.2f}ms"
    
    logger.debug(log_message)


if __name__ == "__main__":
    # Test log manager
    setup_logging()
    
    logger = logging.getLogger(__name__)
    logger.info("Log manager test started")
    logger.warning("This is a warning message")
    logger.error("This is an error message")
    
    # Test SQL logging
    sql_logger("SELECT * FROM test_table", {"param1": "value1"}, 45.2)
    
    # Get stats
    log_manager = get_log_manager()
    stats = log_manager.get_log_stats()
    print(f"Log stats: {stats}")
    
    logger.info("Log manager test completed")