# Phase 4: Migration Documentation Implementation Details

## Context & Overview

Migration from the current v0.2.0 architecture to the new multi-client, async, daemon-capable architecture represents a significant upgrade that requires careful planning and execution. Users need comprehensive guidance to migrate existing deployments without service disruption.

**Migration Challenges:**
- Breaking changes in connection management and configuration
- New dependencies and infrastructure requirements
- Different deployment patterns (stdio → HTTP/WebSocket + daemon)
- Database connection pooling replacing singleton pattern
- New authentication and security requirements

**Target Documentation:**
- Step-by-step migration guide with rollback procedures
- Configuration transformation tools and examples
- Deployment pattern migration with minimal downtime
- Comprehensive troubleshooting for common migration issues
- Performance validation and benchmarking guidance

## Implementation Plan

### 2. Configuration Changes Documentation {#config-changes}

Create `docs/migration/configuration_changes.md`:

```markdown
# Configuration Changes in v1.0.0

## Overview

The new version introduces significant configuration changes to support daemon mode, connection pooling, and multi-client scenarios.

## Configuration File Locations

### Old Locations
- `.env` (development)
- Environment variables only

### New Locations
- `.env.development` (development)
- `.env.staging` (staging)  
- `.env.production` (production)
- `/etc/snowflake-mcp/production.env` (system-wide)

## Environment Variables

### Unchanged Variables
```bash
# These remain the same
SNOWFLAKE_ACCOUNT=your-account
SNOWFLAKE_USER=your-user  
SNOWFLAKE_AUTH_TYPE=private_key
SNOWFLAKE_PRIVATE_KEY_PATH=/path/to/key.pem
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=YOUR_DB
SNOWFLAKE_SCHEMA=PUBLIC
SNOWFLAKE_ROLE=YOUR_ROLE
```

### New Required Variables
```bash
# Environment identifier
ENVIRONMENT=production  # development, staging, production

# Server configuration
SERVER_HOST=0.0.0.0    # Bind address
SERVER_PORT=8000       # HTTP server port
LOG_LEVEL=INFO         # DEBUG, INFO, WARNING, ERROR
```

### New Optional Variables
```bash
# Connection Pool
SNOWFLAKE_POOL_MIN_SIZE=5
SNOWFLAKE_POOL_MAX_SIZE=20
SNOWFLAKE_POOL_MAX_INACTIVE_MINUTES=15
SNOWFLAKE_POOL_HEALTH_CHECK_MINUTES=2
SNOWFLAKE_POOL_CONNECTION_TIMEOUT=30.0
SNOWFLAKE_POOL_RETRY_ATTEMPTS=3

# Security
API_KEY=your-secure-api-key
JWT_SECRET=your-jwt-secret-key
CORS_ORIGINS=https://app.example.com,https://admin.example.com

# Rate Limiting
RATE_LIMIT_REQUESTS_PER_SECOND=10
RATE_LIMIT_BURST_SIZE=100
RATE_LIMIT_VIOLATIONS_THRESHOLD=5

# Process Management
PID_FILE=/var/run/snowflake-mcp.pid
LOG_FILE=/var/log/snowflake-mcp/application.log
WORKING_DIR=/var/lib/snowflake-mcp

# Monitoring
PROMETHEUS_ENABLED=true
PROMETHEUS_PORT=9090
HEALTH_CHECK_INTERVAL=60
```

## Configuration Validation

### Validation Tool
```bash
# Validate configuration
python scripts/validate_config.py --env-file .env.production

# Check for missing variables
python scripts/config_checker.py --environment production

# Test configuration
python scripts/test_config.py --config .env.production
```

### Common Validation Errors

**Missing Required Variables**
```bash
Error: SNOWFLAKE_ACCOUNT is required
Solution: Add SNOWFLAKE_ACCOUNT=your-account to .env
```

**Invalid Pool Configuration**  
```bash
Error: SNOWFLAKE_POOL_MAX_SIZE must be >= SNOWFLAKE_POOL_MIN_SIZE
Solution: Adjust pool size values
```

**Invalid Authentication**
```bash
Error: SNOWFLAKE_PRIVATE_KEY_PATH file not found
Solution: Verify key file path and permissions
```

## Configuration Migration Scripts

### Automatic Conversion
```bash
# Convert old .env to new format
python scripts/migration/convert_config.py \
    --input .env.old \
    --output .env.production \
    --environment production
```

### Manual Conversion Template

**conversion_template.py:**
```python
#!/usr/bin/env python3
"""Convert old configuration to new format."""

# Old variables mapping to new variables
VARIABLE_MAPPING = {
    # Direct mappings (no change)
    "SNOWFLAKE_ACCOUNT": "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER": "SNOWFLAKE_USER",
    "SNOWFLAKE_AUTH_TYPE": "SNOWFLAKE_AUTH_TYPE",
    "SNOWFLAKE_PRIVATE_KEY_PATH": "SNOWFLAKE_PRIVATE_KEY_PATH",
    "SNOWFLAKE_WAREHOUSE": "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_DATABASE": "SNOWFLAKE_DATABASE", 
    "SNOWFLAKE_SCHEMA": "SNOWFLAKE_SCHEMA",
    "SNOWFLAKE_ROLE": "SNOWFLAKE_ROLE",
}

# New variables with defaults
NEW_VARIABLES = {
    "ENVIRONMENT": "production",
    "SERVER_HOST": "0.0.0.0",
    "SERVER_PORT": "8000",
    "LOG_LEVEL": "INFO",
    "SNOWFLAKE_POOL_MIN_SIZE": "5",
    "SNOWFLAKE_POOL_MAX_SIZE": "20",
    "SNOWFLAKE_POOL_MAX_INACTIVE_MINUTES": "15",
    "SNOWFLAKE_POOL_HEALTH_CHECK_MINUTES": "2",
}

def convert_config(old_file: str, new_file: str):
    """Convert configuration file."""
    old_vars = {}
    
    # Read old configuration
    with open(old_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                old_vars[key] = value
    
    # Write new configuration
    with open(new_file, 'w') as f:
        f.write("# Snowflake MCP Server Configuration v1.0.0\n")
        f.write(f"# Converted from {old_file}\n\n")
        
        f.write("# Core Snowflake Configuration\n")
        for old_key, new_key in VARIABLE_MAPPING.items():
            if old_key in old_vars:
                f.write(f"{new_key}={old_vars[old_key]}\n")
        
        f.write("\n# New Configuration Options\n")
        for key, default_value in NEW_VARIABLES.items():
            f.write(f"{key}={default_value}\n")
        
        f.write("\n# Optional Security Configuration\n")
        f.write("# API_KEY=your-secure-api-key\n")
        f.write("# JWT_SECRET=your-jwt-secret\n")

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Usage: python convert_config.py <old_file> <new_file>")
        sys.exit(1)
    
    convert_config(sys.argv[1], sys.argv[2])
    print(f"Configuration converted: {sys.argv[1]} -> {sys.argv[2]}")
```

## Breaking Changes

### ⚠️ Critical Breaking Changes

1. **Transport Protocol**: stdio → HTTP/WebSocket
2. **Connection Management**: Singleton → Pool
3. **Authentication**: None → API Key/JWT
4. **Configuration**: Single .env → Environment-specific

### Migration Required For:

- **All MCP Client Configurations**
- **Process Management Scripts** 
- **Monitoring and Logging Setup**
- **Deployment Automation**

### Backwards Compatibility

- **Configuration**: Automatic conversion tool provided
- **Environment Variables**: Most unchanged
- **MCP Protocol**: Tool interfaces unchanged
- **Snowflake Authentication**: No changes required

## Environment-Specific Configurations

### Development
```bash
ENVIRONMENT=development
SERVER_HOST=localhost
SERVER_PORT=8000
LOG_LEVEL=DEBUG
SNOWFLAKE_POOL_MIN_SIZE=1
SNOWFLAKE_POOL_MAX_SIZE=5
```

### Staging  
```bash
ENVIRONMENT=staging
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
LOG_LEVEL=INFO
SNOWFLAKE_POOL_MIN_SIZE=3
SNOWFLAKE_POOL_MAX_SIZE=10
API_KEY=staging-api-key
```

### Production
```bash
ENVIRONMENT=production
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
LOG_LEVEL=WARNING
SNOWFLAKE_POOL_MIN_SIZE=10
SNOWFLAKE_POOL_MAX_SIZE=50
API_KEY=${PROD_API_KEY}
JWT_SECRET=${PROD_JWT_SECRET}
CORS_ORIGINS=https://app.company.com
```
```

