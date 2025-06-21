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

### 1. PM2 Ecosystem Configuration {#pm2-config}

**Step 1: Create PM2 Configuration Files**

Create `ecosystem.config.js`:

```javascript
module.exports = {
  apps: [
    {
      name: 'snowflake-mcp-http',
      script: 'uv',
      args: 'run snowflake-mcp-http --host 0.0.0.0 --port 8000',
      cwd: '/path/to/snowflake-mcp-server',
      instances: 1,
      exec_mode: 'fork',
      
      // Environment configuration
      env: {
        NODE_ENV: 'production',
        PYTHONPATH: '/path/to/snowflake-mcp-server',
        SNOWFLAKE_POOL_MIN_SIZE: '3',
        SNOWFLAKE_POOL_MAX_SIZE: '15',
        SNOWFLAKE_POOL_MAX_INACTIVE_MINUTES: '30',
        LOG_LEVEL: 'INFO'
      },
      
      // Development environment
      env_development: {
        NODE_ENV: 'development',
        LOG_LEVEL: 'DEBUG',
        SNOWFLAKE_POOL_MIN_SIZE: '1',
        SNOWFLAKE_POOL_MAX_SIZE: '5'
      },
      
      // Production environment
      env_production: {
        NODE_ENV: 'production',
        LOG_LEVEL: 'INFO',
        SNOWFLAKE_POOL_MIN_SIZE: '5',
        SNOWFLAKE_POOL_MAX_SIZE: '20'
      },
      
      // Process management
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s',
      max_memory_restart: '500M',
      
      // Logging
      log_file: './logs/combined.log',
      out_file: './logs/out.log',
      error_file: './logs/error.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      
      // Monitoring
      pmx: true,
      monitoring: true,
      
      // Health monitoring
      health_check_url: 'http://localhost:8000/health',
      health_check_grace_period: 3000,
      
      // Auto restart on file changes (development only)
      watch: false,  // Set to true for development
      ignore_watch: ['node_modules', 'logs', '*.log']
    },
    
    {
      name: 'snowflake-mcp-websocket',
      script: 'uv',
      args: 'run python -m snowflake_mcp_server.transports.websocket_handler',
      cwd: '/path/to/snowflake-mcp-server',
      instances: 1,
      exec_mode: 'fork',
      
      env: {
        NODE_ENV: 'production',
        WEBSOCKET_HOST: '0.0.0.0',
        WEBSOCKET_PORT: '8001',
        LOG_LEVEL: 'INFO'
      },
      
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s',
      max_memory_restart: '300M',
      
      log_file: './logs/websocket-combined.log',
      out_file: './logs/websocket-out.log',
      error_file: './logs/websocket-error.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      
      pmx: true,
      monitoring: true,
      health_check_url: 'ws://localhost:8001'
    }
  ],
  
  // Deployment configuration
  deploy: {
    production: {
      user: 'mcp-server',
      host: ['server1.example.com', 'server2.example.com'],
      ref: 'origin/main',
      repo: 'git@github.com:your-org/snowflake-mcp-server.git',
      path: '/var/www/snowflake-mcp-server',
      'pre-deploy-local': '',
      'post-deploy': 'uv install && pm2 reload ecosystem.config.js --env production',
      'pre-setup': ''
    },
    
    staging: {
      user: 'mcp-server',
      host: 'staging.example.com',
      ref: 'origin/develop',
      repo: 'git@github.com:your-org/snowflake-mcp-server.git',
      path: '/var/www/snowflake-mcp-server-staging',
      'post-deploy': 'uv install && pm2 reload ecosystem.config.js --env development'
    }
  }
};
```

**Step 2: Create Environment-Specific Configurations**

Create `config/production.ecosystem.config.js`:

```javascript
module.exports = {
  apps: [
    {
      name: 'snowflake-mcp-cluster',
      script: 'uv',
      args: 'run snowflake-mcp-http --host 0.0.0.0 --port 8000',
      
      // Cluster mode for high availability
      instances: 'max',  // Use all CPU cores
      exec_mode: 'cluster',
      
      // Environment
      env_production: {
        NODE_ENV: 'production',
        SNOWFLAKE_POOL_MIN_SIZE: '10',
        SNOWFLAKE_POOL_MAX_SIZE: '50',
        SNOWFLAKE_POOL_MAX_INACTIVE_MINUTES: '15',
        SNOWFLAKE_POOL_HEALTH_CHECK_MINUTES: '2',
        LOG_LEVEL: 'INFO',
        PYTHONPATH: '/var/www/snowflake-mcp-server'
      },
      
      // Process management
      restart_delay: 2000,
      max_restarts: 15,
      min_uptime: '30s',
      max_memory_restart: '1G',
      
      // Graceful shutdown
      kill_timeout: 5000,
      listen_timeout: 3000,
      
      // Logging with rotation
      log_file: '/var/log/snowflake-mcp/combined.log',
      out_file: '/var/log/snowflake-mcp/out.log',
      error_file: '/var/log/snowflake-mcp/error.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,
      
      // Advanced monitoring
      pmx: true,
      monitoring: true,
      
      // Load balancing
      instance_var: 'INSTANCE_ID',
      
      // Health checks
      health_check_url: 'http://localhost:8000/health/detailed',
      health_check_grace_period: 5000,
      health_check_fatal: true
    }
  ]
};
```

