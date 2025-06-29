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

### 1. Migration Guide Creation {#migration-guide}

**Step 1: Pre-Migration Assessment Tool**

Create `scripts/migration/assess_current_deployment.py`:

```python
#!/usr/bin/env python3
"""Assessment tool for current deployment before migration."""

import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Any
from dataclasses import dataclass, asdict

@dataclass
class DeploymentAssessment:
    """Current deployment assessment results."""
    version: str
    installation_method: str  # pip, uv, git, etc.
    config_location: str
    env_variables: Dict[str, str]
    dependencies: Dict[str, str]
    usage_patterns: Dict[str, Any]
    recommendations: List[str]
    migration_complexity: str  # low, medium, high

class MigrationAssessment:
    """Assess current deployment for migration planning."""
    
    def __init__(self):
        self.current_dir = Path.cwd()
        self.assessment = DeploymentAssessment(
            version="unknown",
            installation_method="unknown", 
            config_location="unknown",
            env_variables={},
            dependencies={},
            usage_patterns={},
            recommendations=[],
            migration_complexity="medium"
        )
    
    def run_assessment(self) -> DeploymentAssessment:
        """Run complete deployment assessment."""
        print("🔍 Assessing current Snowflake MCP deployment...")
        
        self._detect_version()
        self._detect_installation_method()
        self._analyze_configuration()
        self._check_dependencies()
        self._analyze_usage_patterns()
        self._generate_recommendations()
        
        return self.assessment
    
    def _detect_version(self) -> None:
        """Detect current version."""
        try:
            # Try to import and get version
            import snowflake_mcp_server
            if hasattr(snowflake_mcp_server, '__version__'):
                self.assessment.version = snowflake_mcp_server.__version__
            else:
                # Try pyproject.toml
                pyproject_path = self.current_dir / "pyproject.toml"
                if pyproject_path.exists():
                    import toml
                    data = toml.load(pyproject_path)
                    self.assessment.version = data.get("project", {}).get("version", "unknown")
        except ImportError:
            self.assessment.version = "not_installed"
        
        print(f"   Current version: {self.assessment.version}")
    
    def _detect_installation_method(self) -> None:
        """Detect how the package was installed."""
        
        # Check for uv.lock
        if (self.current_dir / "uv.lock").exists():
            self.assessment.installation_method = "uv"
        
        # Check for pip installation
        elif self._check_pip_install():
            self.assessment.installation_method = "pip"
        
        # Check for git installation
        elif (self.current_dir / ".git").exists():
            self.assessment.installation_method = "git"
        
        # Check for local development
        elif (self.current_dir / "snowflake_mcp_server").exists():
            self.assessment.installation_method = "local_dev"
        
        print(f"   Installation method: {self.assessment.installation_method}")
    
    def _check_pip_install(self) -> bool:
        """Check if installed via pip."""
        try:
            result = subprocess.run(
                ["pip", "show", "snowflake-mcp-server"],
                capture_output=True, text=True
            )
            return result.returncode == 0
        except:
            return False
    
    def _analyze_configuration(self) -> None:
        """Analyze current configuration."""
        
        # Check for .env files
        env_files = [".env", ".env.local", ".env.production"]
        config_found = False
        
        for env_file in env_files:
            env_path = self.current_dir / env_file
            if env_path.exists():
                config_found = True
                self.assessment.config_location = str(env_path)
                
                # Parse environment variables
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            self.assessment.env_variables[key] = value
                break
        
        # Check system environment variables
        snowflake_env_vars = {
            k: v for k, v in os.environ.items() 
            if k.startswith('SNOWFLAKE_')
        }
        self.assessment.env_variables.update(snowflake_env_vars)
        
        if not config_found and not snowflake_env_vars:
            self.assessment.config_location = "not_found"
            self.assessment.recommendations.append(
                "⚠️  No configuration found. You'll need to set up configuration for the new version."
            )
        
        print(f"   Configuration: {self.assessment.config_location}")
        print(f"   Environment variables: {len(self.assessment.env_variables)} found")
    
    def _check_dependencies(self) -> None:
        """Check current dependencies."""
        
        # Check pyproject.toml
        pyproject_path = self.current_dir / "pyproject.toml"
        if pyproject_path.exists():
            try:
                import toml
                data = toml.load(pyproject_path)
                deps = data.get("project", {}).get("dependencies", [])
                
                for dep in deps:
                    if ">=" in dep:
                        name, version = dep.split(">=")
                        self.assessment.dependencies[name] = version
                    else:
                        self.assessment.dependencies[dep] = "unknown"
            except:
                pass
        
        print(f"   Dependencies: {len(self.assessment.dependencies)} found")
    
    def _analyze_usage_patterns(self) -> None:
        """Analyze current usage patterns."""
        
        usage = {}
        
        # Check if running as daemon
        usage["daemon_mode"] = self._check_daemon_mode()
        
        # Check for PM2 usage
        usage["pm2_managed"] = self._check_pm2_usage()
        
        # Check for systemd service
        usage["systemd_service"] = self._check_systemd_service()
        
        # Check for multiple clients
        usage["multi_client_setup"] = self._check_multi_client_setup()
        
        self.assessment.usage_patterns = usage
        
        print(f"   Usage patterns analyzed: {len(usage)} patterns checked")
    
    def _check_daemon_mode(self) -> bool:
        """Check if currently running in daemon mode."""
        try:
            # Check for running processes
            result = subprocess.run(
                ["ps", "aux"], capture_output=True, text=True
            )
            return "snowflake-mcp" in result.stdout
        except:
            return False
    
    def _check_pm2_usage(self) -> bool:
        """Check if PM2 is being used."""
        try:
            result = subprocess.run(
                ["pm2", "list"], capture_output=True, text=True
            )
            return "snowflake" in result.stdout.lower()
        except:
            return False
    
    def _check_systemd_service(self) -> bool:
        """Check for systemd service."""
        systemd_files = [
            "/etc/systemd/system/snowflake-mcp.service",
            "/usr/lib/systemd/system/snowflake-mcp.service"
        ]
        return any(Path(f).exists() for f in systemd_files)
    
    def _check_multi_client_setup(self) -> bool:
        """Check for multi-client configuration."""
        # Look for multiple client configurations
        config_indicators = [
            "CLAUDE_DESKTOP", "CLAUDE_CODE", "ROO_CODE",
            "CLIENT_ID", "client_id", "multiple"
        ]
        
        config_text = ""
        if self.assessment.config_location != "not_found":
            try:
                with open(self.assessment.config_location) as f:
                    config_text = f.read().upper()
            except:
                pass
        
        return any(indicator.upper() in config_text for indicator in config_indicators)
    
    def _generate_recommendations(self) -> None:
        """Generate migration recommendations."""
        
        complexity_factors = 0
        
        # Version-based complexity
        if self.assessment.version in ["unknown", "not_installed"]:
            complexity_factors += 2
            self.assessment.recommendations.append(
                "🔄 Clean installation recommended due to unknown current version"
            )
        
        # Installation method complexity
        if self.assessment.installation_method == "git":
            complexity_factors += 1
            self.assessment.recommendations.append(
                "📝 Git installation will require manual migration steps"
            )
        
        # Configuration complexity
        if not self.assessment.env_variables:
            complexity_factors += 2
            self.assessment.recommendations.append(
                "⚙️  Configuration setup required - no existing config found"
            )
        
        # Usage pattern complexity
        if self.assessment.usage_patterns.get("systemd_service"):
            complexity_factors += 1
            self.assessment.recommendations.append(
                "🔧 Systemd service configuration will need updates"
            )
        
        if self.assessment.usage_patterns.get("pm2_managed"):
            self.assessment.recommendations.append(
                "✅ PM2 configuration can be updated with new ecosystem file"
            )
        
        if self.assessment.usage_patterns.get("multi_client_setup"):
            complexity_factors += 1
            self.assessment.recommendations.append(
                "🔀 Multi-client setup detected - plan for session management migration"
            )
        
        # Determine complexity
        if complexity_factors <= 1:
            self.assessment.migration_complexity = "low"
        elif complexity_factors <= 3:
            self.assessment.migration_complexity = "medium"
        else:
            self.assessment.migration_complexity = "high"
        
        # Add general recommendations
        self.assessment.recommendations.extend([
            "📋 Review new configuration options in migration guide",
            "🧪 Test migration in non-production environment first",
            "💾 Backup current configuration before migration",
            "📊 Plan for performance testing after migration"
        ])
    
    def generate_report(self) -> str:
        """Generate assessment report."""
        
        report = []
        report.append("# Snowflake MCP Server Migration Assessment Report")
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        report.append("## Current Deployment")
        report.append(f"- **Version**: {self.assessment.version}")
        report.append(f"- **Installation Method**: {self.assessment.installation_method}")
        report.append(f"- **Configuration Location**: {self.assessment.config_location}")
        report.append(f"- **Migration Complexity**: {self.assessment.migration_complexity.upper()}")
        report.append("")
        
        report.append("## Configuration Analysis")
        if self.assessment.env_variables:
            report.append("### Environment Variables Found:")
            for key, value in self.assessment.env_variables.items():
                # Mask sensitive values
                display_value = value if not any(
                    sensitive in key.lower() 
                    for sensitive in ['password', 'key', 'secret', 'token']
                ) else "***"
                report.append(f"- `{key}`: {display_value}")
        else:
            report.append("- No configuration found")
        report.append("")
        
        report.append("## Usage Patterns")
        for pattern, detected in self.assessment.usage_patterns.items():
            status = "✅ Detected" if detected else "❌ Not detected"
            report.append(f"- **{pattern.replace('_', ' ').title()}**: {status}")
        report.append("")
        
        report.append("## Migration Recommendations")
        for rec in self.assessment.recommendations:
            report.append(f"- {rec}")
        report.append("")
        
        report.append("## Next Steps")
        report.append("1. Review the full migration guide")
        report.append("2. Backup your current configuration")
        report.append("3. Set up a test environment")
        report.append("4. Follow the step-by-step migration process")
        
        return "\n".join(report)

def main():
    """Main assessment function."""
    assessor = MigrationAssessment()
    assessment = assessor.run_assessment()
    
    # Generate and save report
    report = assessor.generate_report()
    
    report_file = Path("migration_assessment_report.md")
    with open(report_file, 'w') as f:
        f.write(report)
    
    print(f"\n📄 Assessment complete! Report saved to: {report_file}")
    print(f"🎯 Migration complexity: {assessment.migration_complexity.upper()}")
    
    # Save assessment data
    assessment_file = Path("migration_assessment.json")
    with open(assessment_file, 'w') as f:
        json.dump(asdict(assessment), f, indent=2, default=str)
    
    print(f"📊 Assessment data saved to: {assessment_file}")

if __name__ == "__main__":
    main()
```

**Step 2: Complete Migration Guide**

Create `docs/migration/migration_guide.md`:

```markdown
# Snowflake MCP Server Migration Guide v0.2.0 → v1.0.0

## Overview

This guide provides step-by-step instructions for migrating from the current stdio-based architecture to the new multi-client, async, daemon-capable architecture.

## Pre-Migration Requirements

### 1. Run Migration Assessment

```bash
cd /path/to/your/snowflake-mcp-server
python scripts/migration/assess_current_deployment.py
```

Review the generated `migration_assessment_report.md` for your specific migration requirements.

### 2. Backup Current Setup

```bash
# Backup configuration
cp .env .env.backup
cp -r snowflake_mcp_server/ snowflake_mcp_server_backup/

# Backup any custom scripts or configurations
tar -czf mcp_backup_$(date +%Y%m%d_%H%M%S).tar.gz \
    .env* \
    *.config.js \
    scripts/ \
    logs/ \
    snowflake_mcp_server/
```

### 3. Prepare Test Environment

```bash
# Create test directory
mkdir mcp_migration_test
cd mcp_migration_test

# Clone the new version
git clone https://github.com/your-org/snowflake-mcp-server.git
cd snowflake-mcp-server
git checkout v1.0.0
```

## Migration Steps

### Phase 1: Environment Setup

#### 1.1 Install New Dependencies

```bash
# Install with uv (recommended)
uv install

# Or with pip
pip install -e ".[production,monitoring,security]"
```

#### 1.2 Create New Configuration

The new version uses enhanced configuration. Convert your existing `.env`:

**Old Configuration (.env):**
```bash
SNOWFLAKE_ACCOUNT=your-account
SNOWFLAKE_USER=your-user
SNOWFLAKE_AUTH_TYPE=private_key
SNOWFLAKE_PRIVATE_KEY_PATH=/path/to/key.pem
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=YOUR_DB
SNOWFLAKE_SCHEMA=PUBLIC
```

**New Configuration (.env):**
```bash
# Core Snowflake settings (unchanged)
SNOWFLAKE_ACCOUNT=your-account
SNOWFLAKE_USER=your-user
SNOWFLAKE_AUTH_TYPE=private_key
SNOWFLAKE_PRIVATE_KEY_PATH=/path/to/key.pem
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=YOUR_DB
SNOWFLAKE_SCHEMA=PUBLIC

# New: Connection Pool Settings
SNOWFLAKE_POOL_MIN_SIZE=5
SNOWFLAKE_POOL_MAX_SIZE=20
SNOWFLAKE_POOL_MAX_INACTIVE_MINUTES=15
SNOWFLAKE_POOL_HEALTH_CHECK_MINUTES=2

# New: Server Settings
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
LOG_LEVEL=INFO

# New: Security Settings (optional)
API_KEY=your-secure-api-key
JWT_SECRET=your-jwt-secret-key

# New: Rate Limiting (optional)
RATE_LIMIT_REQUESTS_PER_SECOND=10
RATE_LIMIT_BURST_SIZE=100

# Environment
ENVIRONMENT=production
```

#### 1.3 Migration Configuration Tool

```bash
# Use the conversion tool
python scripts/migration/convert_config.py --input .env.backup --output .env
```

### Phase 2: Test New Architecture

#### 2.1 Test Async Operations

```bash
# Test basic functionality
python -m pytest tests/test_async_operations.py -v

# Test connection pooling
python -m pytest tests/test_connection_pool.py -v

# Test multi-client scenarios
python -m pytest tests/test_multi_client.py -v
```

#### 2.2 Test HTTP/WebSocket Server

```bash
# Start server in test mode
uv run snowflake-mcp-http --host localhost --port 8001

# In another terminal, test endpoints
curl http://localhost:8001/health
curl http://localhost:8001/status

# Test WebSocket connection
python scripts/test_websocket_client.py ws://localhost:8001/mcp
```

#### 2.3 Performance Comparison

```bash
# Run performance tests
python scripts/migration/performance_comparison.py \
    --old-version v0.2.0 \
    --new-version v1.0.0 \
    --clients 10 \
    --requests-per-client 100
```

### Phase 3: Production Migration

#### 3.1 Gradual Migration (Recommended)

**Option A: Blue-Green Deployment**

```bash
# 1. Set up new version on different port
SERVER_PORT=8001 uv run snowflake-mcp-daemon start

# 2. Test with one client
# Update one MCP client configuration to use HTTP transport

# 3. Validate functionality
python scripts/validate_migration.py --endpoint http://localhost:8001

# 4. Gradually migrate more clients
# 5. Switch traffic and shutdown old version
```

**Option B: Side-by-Side Migration**

```bash
# 1. Keep old stdio version running
# 2. Start new HTTP version on different port
# 3. Update client configurations one by one
# 4. Monitor both versions
# 5. Shutdown old version when all clients migrated
```

#### 3.2 Direct Migration (For Simple Setups)

```bash
# 1. Stop current service
sudo systemctl stop snowflake-mcp  # if using systemd
# or
pm2 stop snowflake-mcp  # if using PM2

# 2. Backup and update code
cp -r snowflake_mcp_server/ snowflake_mcp_server_v0.2.0_backup/
git pull origin main  # or install new version

# 3. Update configuration (see Phase 1.2)

# 4. Start new daemon
uv run snowflake-mcp-daemon start

# 5. Update client configurations
# 6. Test functionality
```

### Phase 4: Client Configuration Updates

#### 4.1 Claude Desktop Configuration

**Old (stdio):**
```json
{
  "mcpServers": {
    "snowflake": {
      "command": "uv",
      "args": ["run", "snowflake-mcp"],
      "env": {
        "SNOWFLAKE_ACCOUNT": "your-account"
      }
    }
  }
}
```

**New (HTTP):**
```json
{
  "mcpServers": {
    "snowflake": {
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer your-api-key"
      }
    }
  }
}
```

#### 4.2 Update Other MCP Clients

Similar updates needed for:
- Claude Code
- Roo Code  
- Custom integrations

### Phase 5: Validation and Monitoring

#### 5.1 Functional Validation

```bash
# Test all MCP tools
python scripts/validate_all_tools.py

# Test multi-client scenarios
python scripts/test_concurrent_clients.py

# Test performance under load
python scripts/load_test.py --clients 20 --duration 300
```

#### 5.2 Set Up Monitoring

```bash
# Configure Prometheus metrics
curl http://localhost:8000/metrics

# Set up Grafana dashboards
python scripts/setup_monitoring.py

# Configure alerting
python scripts/setup_alerts.py
```

## Rollback Procedures

### Emergency Rollback

```bash
# 1. Stop new service
uv run snowflake-mcp-daemon stop
# or
sudo systemctl stop snowflake-mcp

# 2. Restore old configuration
cp .env.backup .env
cp -r snowflake_mcp_server_backup/ snowflake_mcp_server/

# 3. Restart old service
# Use your previous startup method

# 4. Revert client configurations
# Update MCP client configs back to stdio

# 5. Validate functionality
python scripts/validate_rollback.py
```

### Planned Rollback

```bash
# If migration testing reveals issues:

# 1. Document issues found
echo "Migration issues found: [describe issues]" >> migration_log.txt

# 2. Graceful shutdown of new version
uv run snowflake-mcp-daemon stop

# 3. Keep old version running
# 4. Plan resolution of issues
# 5. Retry migration after fixes
```

## Verification Checklist

### ✅ Pre-Migration
- [ ] Migration assessment completed
- [ ] Current setup backed up
- [ ] Test environment prepared
- [ ] Dependencies verified

### ✅ Migration Process
- [ ] New configuration created and validated
- [ ] Async operations tested
- [ ] HTTP/WebSocket server tested
- [ ] Performance comparison completed
- [ ] Client configurations updated

### ✅ Post-Migration
- [ ] All MCP tools functioning
- [ ] Multi-client scenarios working
- [ ] Performance metrics acceptable
- [ ] Monitoring setup and alerting configured
- [ ] Documentation updated

## Common Issues and Solutions

### Issue: Connection Pool Errors

**Symptoms:** "Connection pool exhausted" errors
**Solution:** 
```bash
# Increase pool size
SNOWFLAKE_POOL_MAX_SIZE=30

# Or decrease client concurrency
# Implement rate limiting per client
```

### Issue: Authentication Failures

**Symptoms:** 401 Unauthorized errors
**Solution:**
```bash
# Verify API key configuration
# Check JWT token expiration
# Validate client authentication setup
```

### Issue: Performance Degradation

**Symptoms:** Slower response times than v0.2.0
**Solution:**
```bash
# Check connection pool utilization
curl http://localhost:8000/health/detailed

# Optimize pool configuration
# Review query performance logs
```

### Issue: Client Connection Failures

**Symptoms:** MCP clients cannot connect
**Solution:**
```bash
# Verify server is running
curl http://localhost:8000/health

# Check client configuration format
# Validate network connectivity
# Review firewall settings
```

## Performance Expectations

### Expected Improvements

- **Concurrent Clients**: 50+ vs 1 (5000% improvement)
- **Request Throughput**: 10x improvement under load
- **Memory Efficiency**: 50% reduction per client
- **Connection Reliability**: 99.9% uptime vs periodic stdio issues

### Monitoring Key Metrics

```bash
# Response times
curl http://localhost:8000/metrics | grep mcp_request_duration

# Connection pool health
curl http://localhost:8000/metrics | grep mcp_db_connections

# Error rates
curl http://localhost:8000/metrics | grep mcp_errors_total
```

## Support and Troubleshooting

### Getting Help

1. Check the troubleshooting guide: `docs/troubleshooting.md`
2. Review logs: `tail -f /var/log/snowflake-mcp/application.log`
3. Run diagnostics: `python scripts/diagnose_issues.py`
4. Open GitHub issue with migration assessment report

### Migration Support

- Migration complexity: **High** - Plan 2-4 hours
- Migration complexity: **Medium** - Plan 1-2 hours  
- Migration complexity: **Low** - Plan 30-60 minutes

Contact support before migration if complexity is **High**.
```

