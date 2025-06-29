# Deployment Guide

This directory contains comprehensive deployment examples for the Snowflake MCP Server across different environments and platforms.

## 📁 Directory Structure

```
deploy/
├── docker/                 # Docker deployment
│   ├── Dockerfile
│   └── docker-compose.yml
├── kubernetes/             # Kubernetes deployment
│   ├── namespace.yaml
│   ├── configmap.yaml
│   ├── secret.yaml
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── rbac.yaml
│   └── ingress.yaml
├── cloud/                  # Cloud provider deployments
│   └── aws/
│       └── cloudformation.yaml
├── systemd/                # Systemd service files
│   ├── snowflake-mcp-http.service
│   └── snowflake-mcp-stdio.service
├── monitoring/             # Monitoring configuration
│   └── prometheus.yml
├── install-systemd.sh      # Systemd installation script
└── DEPLOYMENT_README.md    # This file
```

## 🚀 Quick Start Deployments

### 1. Docker Compose (Fastest)

```bash
# Clone and setup
git clone <repository>
cd snowflake-mcp-server

# Configure environment
cp .env.example .env
# Edit .env with your Snowflake credentials

# Build and start
cd deploy/docker
docker-compose up -d

# Check status
docker-compose ps
curl http://localhost:8000/health
```

**Use case:** Development, testing, quick production deployment

### 2. Systemd Service (Production)

```bash
# Install as system service
sudo ./deploy/install-systemd.sh

# Configure credentials
sudo nano /opt/snowflake-mcp-server/.env

# Start service
sudo systemctl start snowflake-mcp-http
sudo systemctl status snowflake-mcp-http
```

**Use case:** Traditional Linux servers, VMs

### 3. Kubernetes (Enterprise)

```bash
# Create namespace and secrets
kubectl apply -f deploy/kubernetes/namespace.yaml
kubectl apply -f deploy/kubernetes/secret.yaml  # Edit first!
kubectl apply -f deploy/kubernetes/configmap.yaml

# Deploy application
kubectl apply -f deploy/kubernetes/rbac.yaml
kubectl apply -f deploy/kubernetes/deployment.yaml
kubectl apply -f deploy/kubernetes/service.yaml

# Optional: Ingress for external access
kubectl apply -f deploy/kubernetes/ingress.yaml

# Check deployment
kubectl get pods -n snowflake-mcp
kubectl logs -f deployment/snowflake-mcp-server -n snowflake-mcp
```

**Use case:** Container orchestration, auto-scaling, enterprise environments

## 🔧 Deployment Options

### Docker Deployment

#### Standard Docker

```bash
# Build image
docker build -f deploy/docker/Dockerfile -t snowflake-mcp-server .

# Run container
docker run -d \
  --name snowflake-mcp \
  -p 8000:8000 \
  -p 8001:8001 \
  --env-file .env \
  snowflake-mcp-server
```

#### Docker Compose with Monitoring

```bash
# Full stack with Prometheus and Grafana
cd deploy/docker
docker-compose up -d

# Access services
# - MCP Server: http://localhost:8000
# - Metrics: http://localhost:8001/metrics
# - Prometheus: http://localhost:9090
# - Grafana: http://localhost:3000 (admin/admin)
```

### Kubernetes Deployment

#### Minimal Deployment

```bash
# Quick deployment (customize first)
kubectl create namespace snowflake-mcp

# Create secret with your credentials
kubectl create secret generic snowflake-mcp-secrets \
  --from-literal=SNOWFLAKE_ACCOUNT=your_account \
  --from-literal=SNOWFLAKE_USER=your_user \
  --from-literal=SNOWFLAKE_PASSWORD=your_password \
  --from-literal=SNOWFLAKE_WAREHOUSE=your_warehouse \
  --from-literal=SNOWFLAKE_DATABASE=your_database \
  --from-literal=SNOWFLAKE_SCHEMA=your_schema \
  -n snowflake-mcp

# Deploy
kubectl apply -f deploy/kubernetes/
```

#### Production Deployment

```bash
# 1. Edit secrets and configuration
vim deploy/kubernetes/secret.yaml
vim deploy/kubernetes/configmap.yaml

# 2. Deploy infrastructure
kubectl apply -f deploy/kubernetes/namespace.yaml
kubectl apply -f deploy/kubernetes/rbac.yaml

# 3. Deploy configuration
kubectl apply -f deploy/kubernetes/secret.yaml
kubectl apply -f deploy/kubernetes/configmap.yaml

# 4. Deploy application
kubectl apply -f deploy/kubernetes/deployment.yaml
kubectl apply -f deploy/kubernetes/service.yaml

# 5. Configure external access
kubectl apply -f deploy/kubernetes/ingress.yaml  # Edit domain first
```

### Cloud Deployments

#### AWS ECS with CloudFormation

```bash
# Deploy infrastructure and application
aws cloudformation create-stack \
  --stack-name snowflake-mcp-server \
  --template-body file://deploy/cloud/aws/cloudformation.yaml \
  --parameters \
    ParameterKey=VpcId,ParameterValue=vpc-12345678 \
    ParameterKey=SubnetIds,ParameterValue="subnet-12345678,subnet-87654321" \
    ParameterKey=PublicSubnetIds,ParameterValue="subnet-11111111,subnet-22222222" \
    ParameterKey=SnowflakeAccount,ParameterValue=your_account \
    ParameterKey=SnowflakeUser,ParameterValue=your_user \
    ParameterKey=SnowflakePassword,ParameterValue=your_password \
    ParameterKey=SnowflakeWarehouse,ParameterValue=your_warehouse \
    ParameterKey=SnowflakeDatabase,ParameterValue=your_database \
  --capabilities CAPABILITY_IAM

# Check deployment
aws cloudformation describe-stacks --stack-name snowflake-mcp-server
```

## ⚙️ Configuration

### Environment Variables

All deployment methods support configuration via environment variables:

```bash
# Required: Snowflake connection
SNOWFLAKE_ACCOUNT=your_account.region
SNOWFLAKE_USER=your_username
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_WAREHOUSE=your_warehouse
SNOWFLAKE_DATABASE=your_database
SNOWFLAKE_SCHEMA=your_schema

# Optional: Server configuration
MCP_SERVER_HOST=0.0.0.0
MCP_SERVER_PORT=8000
LOG_LEVEL=INFO

# Optional: Performance tuning
CONNECTION_POOL_MIN_SIZE=3
CONNECTION_POOL_MAX_SIZE=10
ENABLE_MONITORING=true
ENABLE_RATE_LIMITING=true
```

### Secrets Management

#### Docker Compose

Use `.env` file or Docker secrets:

```bash
# Using .env file
cp .env.example .env
# Edit .env with your values

# Using Docker secrets (Swarm mode)
echo "your_password" | docker secret create snowflake_password -
```

#### Kubernetes

Use Kubernetes secrets:

```bash
# Create secret from command line
kubectl create secret generic snowflake-mcp-secrets \
  --from-literal=SNOWFLAKE_PASSWORD=your_password \
  -n snowflake-mcp

# Or from file
kubectl apply -f deploy/kubernetes/secret.yaml
```

#### AWS

Use AWS Secrets Manager or Systems Manager Parameter Store:

```bash
# Store in Secrets Manager
aws secretsmanager create-secret \
  --name "snowflake-mcp/credentials" \
  --secret-string '{
    "SNOWFLAKE_ACCOUNT": "your_account",
    "SNOWFLAKE_USER": "your_user",
    "SNOWFLAKE_PASSWORD": "your_password"
  }'
```

## 📊 Monitoring Setup

### Prometheus Metrics

All deployments expose metrics on port 8001:

```bash
# Check metrics
curl http://localhost:8001/metrics

# Key metrics:
# - mcp_requests_total
# - mcp_request_duration_seconds
# - pool_connections_active
# - pool_connections_total
```

### Health Checks

Health endpoint available on all deployments:

```bash
# Check health
curl http://localhost:8000/health

# Response:
{
  "status": "healthy",
  "timestamp": "2024-01-18T10:30:00Z",
  "version": "1.0.0",
  "snowflake_connection": "healthy",
  "connection_pool": {
    "active": 2,
    "total": 5,
    "healthy": 5
  }
}
```

### Log Management

#### Docker

```bash
# View logs
docker logs snowflake-mcp

# Follow logs
docker logs -f snowflake-mcp

# With compose
docker-compose logs -f snowflake-mcp
```

#### Kubernetes

```bash
# View logs
kubectl logs -f deployment/snowflake-mcp-server -n snowflake-mcp

# View all pods
kubectl logs -f -l app=snowflake-mcp-server -n snowflake-mcp
```

#### Systemd

```bash
# View logs
journalctl -u snowflake-mcp-http -f

# Recent logs
journalctl -u snowflake-mcp-http --since "1 hour ago"
```

## 🔒 Security Considerations

### Network Security

#### Docker

```bash
# Create isolated network
docker network create snowflake-mcp-network

# Run with custom network
docker run --network snowflake-mcp-network ...
```

#### Kubernetes

```yaml
# Network policies
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: snowflake-mcp-netpol
spec:
  podSelector:
    matchLabels:
      app: snowflake-mcp-server
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: allowed-client
    ports:
    - protocol: TCP
      port: 8000
```

### TLS/SSL Configuration

#### Docker with TLS

```bash
# Run with TLS certificates
docker run -d \
  -p 443:8000 \
  -v /path/to/certs:/certs:ro \
  -e TLS_CERT_FILE=/certs/server.crt \
  -e TLS_KEY_FILE=/certs/server.key \
  snowflake-mcp-server
```

#### Kubernetes with TLS

```bash
# Create TLS secret
kubectl create secret tls snowflake-mcp-tls \
  --cert=server.crt \
  --key=server.key \
  -n snowflake-mcp

# Reference in Ingress
# (see deploy/kubernetes/ingress.yaml)
```

## 🚨 Troubleshooting

### Common Issues

#### Container Won't Start

```bash
# Check logs
docker logs snowflake-mcp

# Common issues:
# - Missing environment variables
# - Invalid Snowflake credentials
# - Port conflicts
# - Insufficient resources
```

#### Connection Issues

```bash
# Test Snowflake connectivity
docker exec snowflake-mcp python -c "
import asyncio
from snowflake_mcp_server.main import test_snowflake_connection
print(asyncio.run(test_snowflake_connection()))
"
```

#### Resource Constraints

```bash
# Check resource usage
docker stats snowflake-mcp

# Kubernetes
kubectl top pods -n snowflake-mcp
kubectl describe pod <pod-name> -n snowflake-mcp
```

### Performance Issues

#### High Memory Usage

```bash
# Reduce connection pool size
export CONNECTION_POOL_MAX_SIZE=5
export CONNECTION_POOL_MIN_SIZE=2

# Add memory limits (Docker)
docker run --memory=1g snowflake-mcp-server

# Add memory limits (Kubernetes)
# See deployment.yaml resources section
```

#### High CPU Usage

```bash
# Reduce concurrent requests
export MAX_CONCURRENT_REQUESTS=25
export REQUEST_QUEUE_SIZE=100

# Add CPU limits
docker run --cpus=1.0 snowflake-mcp-server
```

## 📈 Scaling

### Horizontal Scaling

#### Docker Compose

```yaml
# Scale with compose
services:
  snowflake-mcp:
    # ... config ...
    deploy:
      replicas: 3
      update_config:
        parallelism: 1
        delay: 30s
      restart_policy:
        condition: on-failure
```

#### Kubernetes

```bash
# Scale deployment
kubectl scale deployment snowflake-mcp-server --replicas=5 -n snowflake-mcp

# Auto-scaling
kubectl autoscale deployment snowflake-mcp-server \
  --cpu-percent=70 \
  --min=2 \
  --max=10 \
  -n snowflake-mcp
```

#### AWS ECS

```bash
# Update service desired count
aws ecs update-service \
  --cluster snowflake-mcp-cluster \
  --service snowflake-mcp-service \
  --desired-count 5
```

### Load Balancing

#### Docker with NGINX

```bash
# Add NGINX load balancer
# See docker-compose.yml for example
```

#### Kubernetes

```yaml
# Service automatically load balances
apiVersion: v1
kind: Service
metadata:
  name: snowflake-mcp-service
spec:
  selector:
    app: snowflake-mcp-server
  ports:
  - port: 8000
    targetPort: 8000
  type: LoadBalancer
```

## 🔄 Updates and Rollbacks

### Rolling Updates

#### Docker Compose

```bash
# Update image
docker-compose pull
docker-compose up -d

# Zero-downtime update
docker-compose up -d --no-deps snowflake-mcp
```

#### Kubernetes

```bash
# Update image
kubectl set image deployment/snowflake-mcp-server \
  snowflake-mcp-server=snowflake-mcp-server:v1.1.0 \
  -n snowflake-mcp

# Check rollout status
kubectl rollout status deployment/snowflake-mcp-server -n snowflake-mcp

# Rollback if needed
kubectl rollout undo deployment/snowflake-mcp-server -n snowflake-mcp
```

### Backup and Restore

#### Configuration Backup

```bash
# Docker
docker run --rm -v snowflake-mcp_config:/source -v $(pwd):/backup \
  alpine tar czf /backup/config-backup.tar.gz -C /source .

# Kubernetes
kubectl get configmap snowflake-mcp-config -o yaml > config-backup.yaml
kubectl get secret snowflake-mcp-secrets -o yaml > secrets-backup.yaml
```

## 📚 Additional Resources

- **[Configuration Guide](../CONFIGURATION_GUIDE.md):** Detailed configuration options
- **[Migration Guide](../MIGRATION_GUIDE.md):** Upgrading from v0.2.0
- **[Operations Runbook](../OPERATIONS_RUNBOOK.md):** Day-to-day operations
- **[Architecture Overview](../CLAUDE.md):** Technical architecture details

---

## 🎯 Deployment Checklist

Before deploying to production:

- [ ] **Security review** of configuration and secrets
- [ ] **Resource sizing** appropriate for expected load
- [ ] **Monitoring** and alerting configured
- [ ] **Backup strategy** implemented
- [ ] **Network security** (firewalls, network policies)
- [ ] **TLS/SSL** certificates configured
- [ ] **Health checks** and readiness probes working
- [ ] **Logging** and log rotation configured
- [ ] **Update strategy** planned and tested
- [ ] **Rollback plan** documented and tested