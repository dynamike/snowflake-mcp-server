module.exports = {
  apps: [
    {
      // Snowflake MCP Server - HTTP/WebSocket mode
      name: 'snowflake-mcp-http',
      script: 'uv',
      args: 'run snowflake-mcp-http --host 0.0.0.0 --port 8000',
      cwd: '/Users/robsherman/Servers/snowflake-mcp-server-origin-dev',
      instances: 1,
      exec_mode: 'fork',
      watch: false,
      env: {
        NODE_ENV: 'production',
        PYTHONPATH: '/Users/robsherman/Servers/snowflake-mcp-server-origin-dev',
        SNOWFLAKE_CONN_REFRESH_HOURS: '8',
        UVICORN_LOG_LEVEL: 'info'
      },
      env_development: {
        NODE_ENV: 'development',
        UVICORN_LOG_LEVEL: 'debug'
      },
      // Logging configuration
      log_file: './logs/snowflake-mcp-http.log',
      error_file: './logs/snowflake-mcp-http-error.log',
      out_file: './logs/snowflake-mcp-http-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,
      
      // Auto-restart configuration
      autorestart: true,
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s',
      
      // Memory and CPU limits
      max_memory_restart: '500M',
      
      // Health monitoring
      health_check_url: 'http://localhost:8000/health',
      health_check_grace_period: 10000,
      
      // Process management
      kill_timeout: 5000,
      listen_timeout: 8000,
      
      // Error handling
      exp_backoff_restart_delay: 100
    },
    
    {
      // Snowflake MCP Server - stdio mode (for Claude Desktop)
      name: 'snowflake-mcp-stdio',
      script: 'uv',
      args: 'run snowflake-mcp-stdio',
      cwd: '/Users/robsherman/Servers/snowflake-mcp-server-origin-dev',
      instances: 1,
      exec_mode: 'fork',
      watch: false,
      
      // Disabled by default since stdio is typically run on-demand
      autorestart: false,
      
      env: {
        NODE_ENV: 'production',
        PYTHONPATH: '/Users/robsherman/Servers/snowflake-mcp-server-origin-dev',
        SNOWFLAKE_CONN_REFRESH_HOURS: '8'
      },
      
      // Logging configuration
      log_file: './logs/snowflake-mcp-stdio.log',
      error_file: './logs/snowflake-mcp-stdio-error.log',
      out_file: './logs/snowflake-mcp-stdio-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,
      
      // Process management
      kill_timeout: 5000,
      listen_timeout: 3000
    }
  ],

  deploy: {
    production: {
      user: 'deploy',
      host: ['your-production-server.com'],
      ref: 'origin/main',
      repo: 'git@github.com:your-org/snowflake-mcp-server.git',
      path: '/var/www/snowflake-mcp-server',
      'post-deploy': 'uv pip install -e . && pm2 reload ecosystem.config.js --env production',
      env: {
        NODE_ENV: 'production'
      }
    },
    
    staging: {
      user: 'deploy',
      host: ['your-staging-server.com'],
      ref: 'origin/develop',
      repo: 'git@github.com:your-org/snowflake-mcp-server.git',
      path: '/var/www/snowflake-mcp-server-staging',
      'post-deploy': 'uv pip install -e . && pm2 reload ecosystem.config.js --env staging',
      env: {
        NODE_ENV: 'staging'
      }
    }
  }
};