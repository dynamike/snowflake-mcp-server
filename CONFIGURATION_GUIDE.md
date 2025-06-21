# Configuration Guide

This guide covers all configuration options for the Snowflake MCP server, from basic setup to advanced production deployment.

## 📋 Quick Start Configuration

### Minimal Configuration (.env)

```bash
# Required: Snowflake connection
SNOWFLAKE_ACCOUNT=your_account.region
SNOWFLAKE_USER=your_username
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_WAREHOUSE=your_warehouse
SNOWFLAKE_DATABASE=your_database
SNOWFLAKE_SCHEMA=your_schema
```

This minimal configuration will use all default values and work with stdio mode.

## 🔐 Authentication Configuration

### Password Authentication

```bash
SNOWFLAKE_ACCOUNT=mycompany.us-east-1
SNOWFLAKE_USER=john_doe
SNOWFLAKE_PASSWORD=secure_password123
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=ANALYTICS_DB
SNOWFLAKE_SCHEMA=PUBLIC
```

### Private Key Authentication (Service Accounts)

```bash
SNOWFLAKE_ACCOUNT=mycompany.us-east-1
SNOWFLAKE_USER=service_account
SNOWFLAKE_PRIVATE_KEY=/path/to/private_key.pem
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=key_passphrase
SNOWFLAKE_WAREHOUSE=SERVICE_WH
SNOWFLAKE_DATABASE=PROD_DB
SNOWFLAKE_SCHEMA=REPORTING
```

### External Browser Authentication (SSO)

```bash
SNOWFLAKE_ACCOUNT=mycompany.us-east-1
SNOWFLAKE_USER=jane.smith@company.com
SNOWFLAKE_AUTHENTICATOR=externalbrowser
SNOWFLAKE_WAREHOUSE=USER_WH
SNOWFLAKE_DATABASE=DEV_DB
SNOWFLAKE_SCHEMA=PUBLIC
```

## ⚙️ Server Configuration

### Connection Pool Settings

```bash
# Connection pool configuration
CONNECTION_POOL_MIN_SIZE=3        # Minimum connections to maintain
CONNECTION_POOL_MAX_SIZE=10       # Maximum connections allowed
CONNECTION_POOL_MAX_INACTIVE_TIME_MINUTES=30  # Retire idle connections after 30 min
CONNECTION_POOL_HEALTH_CHECK_INTERVAL_MINUTES=5  # Health check every 5 minutes
CONNECTION_POOL_CONNECTION_TIMEOUT_SECONDS=30    # Connection timeout
CONNECTION_POOL_RETRY_ATTEMPTS=3  # Retry failed connections 3 times

# Connection refresh settings
SNOWFLAKE_CONN_REFRESH_HOURS=8    # Refresh connections every 8 hours
```

### HTTP Server Settings

```bash
# HTTP/WebSocket server configuration
MCP_SERVER_HOST=0.0.0.0          # Listen on all interfaces
MCP_SERVER_PORT=8000             # HTTP server port
MCP_SERVER_WORKERS=1             # Number of worker processes
MCP_SERVER_TIMEOUT=300           # Request timeout in seconds

# CORS settings for web clients
CORS_ALLOW_ORIGINS=*             # Allowed origins (* for all)
CORS_ALLOW_METHODS=GET,POST,OPTIONS  # Allowed HTTP methods
CORS_ALLOW_HEADERS=*             # Allowed headers
```

### Logging Configuration

```bash
# Logging settings
LOG_LEVEL=INFO                   # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FORMAT=structured            # structured, simple, detailed
LOG_FILE=/var/log/snowflake-mcp/server.log  # Log file path (optional)
LOG_ROTATION_SIZE=100MB          # Rotate logs when they reach this size
LOG_ROTATION_BACKUPS=5           # Keep 5 backup log files

# Request logging
ENABLE_REQUEST_LOGGING=true      # Log all requests
REQUEST_LOG_LEVEL=INFO           # Request-specific log level
LOG_QUERY_DETAILS=false          # Log full query text (security sensitive)
```

## 📊 Monitoring and Metrics

### Prometheus Metrics

```bash
# Monitoring configuration
ENABLE_MONITORING=true           # Enable Prometheus metrics endpoint
METRICS_PORT=8001               # Metrics endpoint port
METRICS_PATH=/metrics           # Metrics endpoint path

# Metric collection settings
METRICS_COLLECTION_INTERVAL=30   # Collect metrics every 30 seconds
ENABLE_DETAILED_METRICS=true     # Collect detailed performance metrics
TRACK_QUERY_METRICS=true         # Track per-query performance
```

### Health Checks

```bash
# Health check configuration
HEALTH_CHECK_ENABLED=true        # Enable health check endpoint
HEALTH_CHECK_PATH=/health        # Health check endpoint path
HEALTH_CHECK_INTERVAL=60         # Internal health checks every 60 seconds
SNOWFLAKE_HEALTH_CHECK_TIMEOUT=10  # Snowflake connectivity check timeout
```

## 🚦 Rate Limiting and Security

### Rate Limiting

```bash
# Rate limiting configuration
ENABLE_RATE_LIMITING=true        # Enable rate limiting
RATE_LIMIT_REQUESTS_PER_MINUTE=60   # Global rate limit
RATE_LIMIT_BURST_SIZE=10         # Burst allowance
RATE_LIMIT_STORAGE=memory        # memory, redis, database

# Per-client rate limiting
CLIENT_RATE_LIMIT_ENABLED=true   # Enable per-client limits
CLIENT_RATE_LIMIT_REQUESTS_PER_MINUTE=30  # Per client limit
CLIENT_RATE_LIMIT_WINDOW_MINUTES=1       # Rate limit window
```

### Security Settings

```bash
# Security configuration
ENABLE_API_KEY_AUTH=false        # Require API key authentication
API_KEY_HEADER=X-API-Key         # API key header name
VALID_API_KEYS=key1,key2,key3    # Comma-separated valid API keys

# SQL security
ENABLE_SQL_INJECTION_PROTECTION=true  # Enable SQL injection prevention
ALLOWED_SQL_COMMANDS=SELECT,SHOW,DESCRIBE,EXPLAIN  # Allowed SQL commands
MAX_QUERY_RESULT_ROWS=10000      # Maximum rows returned per query
QUERY_TIMEOUT_SECONDS=300        # Maximum query execution time

# Connection security
REQUIRE_SSL=true                 # Require SSL connections to Snowflake
VERIFY_SSL_CERTS=true           # Verify SSL certificates
```

## 🏗️ Deployment Mode Configuration

### stdio Mode (Default)

```bash
# stdio mode settings
MCP_MODE=stdio                   # Set mode to stdio
STDIO_BUFFER_SIZE=8192          # Buffer size for stdio communication
STDIO_TIMEOUT=30                # stdio operation timeout
```

### HTTP Mode

```bash
# HTTP mode settings
MCP_MODE=http                    # Set mode to HTTP
HTTP_KEEP_ALIVE=true            # Enable HTTP keep-alive
HTTP_MAX_CONNECTIONS=100        # Maximum concurrent HTTP connections
HTTP_REQUEST_TIMEOUT=30         # HTTP request timeout
```

### WebSocket Mode

```bash
# WebSocket mode settings
MCP_MODE=websocket              # Set mode to WebSocket
WS_PING_INTERVAL=30             # WebSocket ping interval
WS_PING_TIMEOUT=10              # WebSocket ping timeout
WS_MAX_MESSAGE_SIZE=1048576     # Maximum WebSocket message size (1MB)
```

### Daemon Mode

```bash
# Daemon mode settings (PM2 ecosystem.config.js)
MCP_MODE=daemon                 # Set mode to daemon
DAEMON_PID_FILE=/var/run/snowflake-mcp.pid  # PID file location
DAEMON_USER=snowflake-mcp       # User to run daemon as
DAEMON_GROUP=snowflake-mcp      # Group to run daemon as
```

## 🎛️ Advanced Configuration

### Session Management

```bash
# Session management
SESSION_TIMEOUT_MINUTES=60       # Session timeout
MAX_CONCURRENT_SESSIONS=100      # Maximum concurrent sessions
SESSION_CLEANUP_INTERVAL=300     # Cleanup expired sessions every 5 minutes
ENABLE_SESSION_PERSISTENCE=false # Persist sessions across restarts
```

### Resource Management

```bash
# Resource allocation
MAX_MEMORY_MB=1024              # Maximum memory usage
MAX_CPU_PERCENT=80              # Maximum CPU usage
MAX_CONCURRENT_REQUESTS=50       # Maximum concurrent requests
REQUEST_QUEUE_SIZE=200          # Request queue size when at max concurrency

# Garbage collection
GC_INTERVAL_SECONDS=300         # Run garbage collection every 5 minutes
MEMORY_CLEANUP_THRESHOLD=0.8    # Cleanup when memory usage > 80%
```

### Transaction Management

```bash
# Transaction settings
DEFAULT_TRANSACTION_TIMEOUT=300  # Default transaction timeout (5 minutes)
MAX_TRANSACTION_DURATION=1800   # Maximum transaction duration (30 minutes)
AUTO_COMMIT_QUERIES=true        # Auto-commit single queries
ENABLE_TRANSACTION_ISOLATION=true  # Enable transaction isolation per request
```

## 📁 Configuration File Examples

### Development Environment (.env.development)

```bash
# Development configuration
SNOWFLAKE_ACCOUNT=dev_account.us-west-2
SNOWFLAKE_USER=dev_user
SNOWFLAKE_PASSWORD=dev_password
SNOWFLAKE_WAREHOUSE=DEV_WH
SNOWFLAKE_DATABASE=DEV_DB
SNOWFLAKE_SCHEMA=PUBLIC

# Relaxed settings for development
CONNECTION_POOL_MIN_SIZE=2
CONNECTION_POOL_MAX_SIZE=5
LOG_LEVEL=DEBUG
ENABLE_REQUEST_LOGGING=true
LOG_QUERY_DETAILS=true

# Disable production features
ENABLE_RATE_LIMITING=false
ENABLE_API_KEY_AUTH=false
REQUIRE_SSL=false

# Quick development settings
MCP_MODE=stdio
HEALTH_CHECK_INTERVAL=300
```

### Production Environment (.env.production)

```bash
# Production configuration
SNOWFLAKE_ACCOUNT=prod_account.us-east-1
SNOWFLAKE_USER=prod_service_account
SNOWFLAKE_PRIVATE_KEY=/etc/snowflake-mcp/private_key.pem
SNOWFLAKE_WAREHOUSE=PROD_WH
SNOWFLAKE_DATABASE=PROD_DB
SNOWFLAKE_SCHEMA=ANALYTICS

# Optimized for production load
CONNECTION_POOL_MIN_SIZE=5
CONNECTION_POOL_MAX_SIZE=20
CONNECTION_POOL_MAX_INACTIVE_TIME_MINUTES=15

# Production server settings
MCP_MODE=daemon
MCP_SERVER_HOST=0.0.0.0
MCP_SERVER_PORT=8000
MCP_SERVER_WORKERS=4

# Security enabled
ENABLE_RATE_LIMITING=true
RATE_LIMIT_REQUESTS_PER_MINUTE=120
ENABLE_API_KEY_AUTH=true
API_KEY_HEADER=X-API-Key
REQUIRE_SSL=true

# Monitoring enabled
ENABLE_MONITORING=true
METRICS_PORT=8001
HEALTH_CHECK_ENABLED=true

# Production logging
LOG_LEVEL=INFO
LOG_FILE=/var/log/snowflake-mcp/server.log
LOG_ROTATION_SIZE=100MB
ENABLE_REQUEST_LOGGING=true
LOG_QUERY_DETAILS=false

# Resource limits
MAX_MEMORY_MB=2048
MAX_CONCURRENT_REQUESTS=100
REQUEST_QUEUE_SIZE=500
```

### Testing Environment (.env.testing)

```bash
# Testing configuration
SNOWFLAKE_ACCOUNT=test_account.us-west-1
SNOWFLAKE_USER=test_user
SNOWFLAKE_PASSWORD=test_password
SNOWFLAKE_WAREHOUSE=TEST_WH
SNOWFLAKE_DATABASE=TEST_DB
SNOWFLAKE_SCHEMA=PUBLIC

# Test-optimized settings
CONNECTION_POOL_MIN_SIZE=2
CONNECTION_POOL_MAX_SIZE=8
CONNECTION_POOL_HEALTH_CHECK_INTERVAL_MINUTES=1

# Detailed logging for debugging
LOG_LEVEL=DEBUG
ENABLE_REQUEST_LOGGING=true
LOG_QUERY_DETAILS=true

# Fast timeouts for quick test feedback
CONNECTION_POOL_CONNECTION_TIMEOUT_SECONDS=10
HTTP_REQUEST_TIMEOUT=15
QUERY_TIMEOUT_SECONDS=60

# Monitoring for test analysis
ENABLE_MONITORING=true
ENABLE_DETAILED_METRICS=true
TRACK_QUERY_METRICS=true
```

## 🔧 Configuration Validation

### Validation Script

Create a script to validate your configuration:

```bash
#!/bin/bash
# validate_config.sh

echo "🔍 Validating Snowflake MCP Server Configuration..."

# Check required variables
required_vars=(
    "SNOWFLAKE_ACCOUNT"
    "SNOWFLAKE_USER"
    "SNOWFLAKE_WAREHOUSE"
    "SNOWFLAKE_DATABASE"
    "SNOWFLAKE_SCHEMA"
)

for var in "${required_vars[@]}"; do
    if [[ -z "${!var}" ]]; then
        echo "❌ Missing required variable: $var"
        exit 1
    else
        echo "✅ $var is set"
    fi
done

# Check authentication method
if [[ -n "$SNOWFLAKE_PASSWORD" ]]; then
    echo "✅ Using password authentication"
elif [[ -n "$SNOWFLAKE_PRIVATE_KEY" ]]; then
    echo "✅ Using private key authentication"
    if [[ ! -f "$SNOWFLAKE_PRIVATE_KEY" ]]; then
        echo "❌ Private key file not found: $SNOWFLAKE_PRIVATE_KEY"
        exit 1
    fi
elif [[ "$SNOWFLAKE_AUTHENTICATOR" == "externalbrowser" ]]; then
    echo "✅ Using external browser authentication"
else
    echo "❌ No valid authentication method configured"
    exit 1
fi

# Validate numeric settings
if [[ -n "$CONNECTION_POOL_MIN_SIZE" && -n "$CONNECTION_POOL_MAX_SIZE" ]]; then
    if (( CONNECTION_POOL_MIN_SIZE > CONNECTION_POOL_MAX_SIZE )); then
        echo "❌ CONNECTION_POOL_MIN_SIZE cannot be greater than CONNECTION_POOL_MAX_SIZE"
        exit 1
    fi
    echo "✅ Connection pool sizes are valid"
fi

echo "🎉 Configuration validation passed!"
```

### Test Connection

```bash
# Test basic connectivity
python -c "
import asyncio
from snowflake_mcp_server.main import initialize_async_infrastructure, test_snowflake_connection

async def test():
    await initialize_async_infrastructure()
    result = await test_snowflake_connection()
    print('✅ Connection successful!' if result else '❌ Connection failed!')

asyncio.run(test())
"
```

## 🔄 Configuration Best Practices

### 1. Environment-Specific Configurations

Use separate configuration files for each environment:
- `.env.development`
- `.env.testing`
- `.env.staging`
- `.env.production`

### 2. Secrets Management

**Do not commit secrets to version control.**

Use environment-specific secret management:
```bash
# Development: local .env files (gitignored)
cp .env.example .env.development

# Production: secret management service
export SNOWFLAKE_PASSWORD="$(aws secretsmanager get-secret-value --secret-id prod/snowflake/password --query SecretString --output text)"
```

### 3. Configuration Layering

Order of precedence (highest to lowest):
1. Environment variables
2. `.env` file
3. Default values

### 4. Monitoring Configuration Changes

Log configuration changes:
```bash
# Add to startup logs
echo "Configuration loaded: $(date)" >> /var/log/snowflake-mcp/config.log
env | grep SNOWFLAKE_ | sed 's/PASSWORD=.*/PASSWORD=***/' >> /var/log/snowflake-mcp/config.log
```

### 5. Configuration Documentation

Document your configuration decisions:
```bash
# Create configuration README for your deployment
cat > CONFIG_README.md << 'EOF'
# Our Snowflake MCP Configuration

## Environment: Production
## Last Updated: $(date)
## Contact: data-team@company.com

### Key Settings:
- Connection Pool: 5-20 connections
- Rate Limiting: 120 req/min
- Monitoring: Enabled on port 8001
- Security: API key authentication required

### Changes Log:
- 2024-01-15: Increased pool size for holiday traffic
- 2024-01-10: Enabled detailed metrics for performance analysis
EOF
```

## 🚨 Common Configuration Issues

### Issue: Connection Pool Exhausted

**Symptoms:** "Connection pool exhausted" errors
**Solution:** Increase pool size or reduce connection hold time
```bash
CONNECTION_POOL_MAX_SIZE=25
CONNECTION_POOL_MAX_INACTIVE_TIME_MINUTES=15
```

### Issue: High Memory Usage

**Symptoms:** Server running out of memory
**Solution:** Reduce pool size and add memory limits
```bash
CONNECTION_POOL_MAX_SIZE=10
MAX_MEMORY_MB=1024
MEMORY_CLEANUP_THRESHOLD=0.7
```

### Issue: Rate Limit Errors

**Symptoms:** Clients receiving rate limit errors
**Solution:** Adjust rate limits or implement client-side batching
```bash
RATE_LIMIT_REQUESTS_PER_MINUTE=200
CLIENT_RATE_LIMIT_REQUESTS_PER_MINUTE=50
```

### Issue: SSL/TLS Errors

**Symptoms:** SSL verification failures
**Solution:** Configure SSL settings appropriately
```bash
REQUIRE_SSL=true
VERIFY_SSL_CERTS=true
# Or for development:
VERIFY_SSL_CERTS=false
```

---

## 📚 Related Documentation

- **[Migration Guide](MIGRATION_GUIDE.md):** Upgrading from v0.2.0
- **[Operations Runbook](OPERATIONS_RUNBOOK.md):** Day-to-day operations
- **[Deployment Examples](deploy/):** Complete deployment configurations
- **[Monitoring Setup](deploy/monitoring/):** Prometheus and Grafana setup