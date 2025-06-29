# Scaling Guide

This guide provides comprehensive recommendations for scaling the Snowflake MCP Server to handle increased load, multiple clients, and enterprise-level usage patterns.

## 📊 Scaling Overview

### Current Baseline Performance

| Metric | Single Instance | Optimized Instance | Load Balanced Cluster |
|--------|----------------|-------------------|----------------------|
| Concurrent Clients | 5-10 | 25-50 | 100-500+ |
| Requests/Second | 10-20 | 50-100 | 200-1000+ |
| Memory Usage | 256MB | 512MB-1GB | 2GB-8GB total |
| CPU Usage | 10-20% | 30-60% | Distributed |
| Response Time (p95) | <500ms | <1s | <2s |

### Scaling Triggers

Scale up when you observe:
- **Connection pool utilization > 80%** consistently
- **CPU usage > 70%** for extended periods  
- **Memory usage > 75%** of allocated resources
- **Response times > 5 seconds** at 95th percentile
- **Error rates > 2%** due to resource constraints
- **Queue length > 50** pending requests

## 🔧 Vertical Scaling (Scale Up)

### 1. Memory Scaling

#### Current Memory Usage Patterns

```bash
# Monitor memory usage
ps aux | grep snowflake-mcp | awk '{print $4, $5, $6}'
free -h

# Memory usage by component:
# - Base application: ~100MB
# - Connection pool: ~10MB per connection
# - Request buffers: ~1MB per concurrent request
# - Monitoring/metrics: ~20MB
```

#### Memory Optimization

```bash
# Optimal memory allocation formula:
# Total Memory = Base (200MB) + (Connections × 10MB) + (Concurrent Requests × 2MB) + Overhead (100MB)

# Examples:
# Small deployment (10 connections, 25 requests): 200 + 100 + 50 + 100 = 450MB
# Medium deployment (20 connections, 50 requests): 200 + 200 + 100 + 100 = 600MB  
# Large deployment (50 connections, 100 requests): 200 + 500 + 200 + 100 = 1000MB

# Configuration for different scales:

# Small Scale (.env)
CONNECTION_POOL_MIN_SIZE=3
CONNECTION_POOL_MAX_SIZE=10
MAX_CONCURRENT_REQUESTS=25
MAX_MEMORY_MB=512

# Medium Scale (.env)
CONNECTION_POOL_MIN_SIZE=5
CONNECTION_POOL_MAX_SIZE=20
MAX_CONCURRENT_REQUESTS=50
MAX_MEMORY_MB=1024

# Large Scale (.env)
CONNECTION_POOL_MIN_SIZE=10
CONNECTION_POOL_MAX_SIZE=50
MAX_CONCURRENT_REQUESTS=100
MAX_MEMORY_MB=2048
```

### 2. CPU Scaling

#### CPU Usage Analysis

```bash
# Monitor CPU patterns
top -p $(pgrep -f snowflake-mcp)
htop -p $(pgrep -f snowflake-mcp)

# CPU usage breakdown:
# - Query processing: 40-60%
# - Network I/O: 20-30%
# - JSON serialization: 10-20%
# - Connection management: 5-10%
# - Monitoring/logging: 5-10%
```

#### CPU Optimization

```bash
# CPU scaling recommendations:

# Single Core (up to 10 concurrent clients)
# - 1 vCPU
# - Basic workloads only

# Dual Core (10-25 concurrent clients)
# - 2 vCPUs  
# - Standard production workloads

# Quad Core (25-50 concurrent clients)
# - 4 vCPUs
# - High-frequency query workloads

# Multi-Core (50+ concurrent clients)
# - 8+ vCPUs
# - Enterprise workloads with complex queries

# Docker CPU limits
docker run --cpus=2.0 snowflake-mcp-server

# Kubernetes resource limits
resources:
  requests:
    cpu: 1000m
    memory: 1Gi
  limits:
    cpu: 2000m
    memory: 2Gi
```

### 3. Connection Pool Scaling

#### Pool Sizing Formula

```bash
# Connection pool sizing guidelines:

# Minimum Connections = (Average Concurrent Users) / 3
# Maximum Connections = (Peak Concurrent Users) × 1.5
# Health Check Buffer = Max × 0.1

# Example calculations:
# 30 average users, 60 peak users:
# Min = 30/3 = 10 connections
# Max = 60 × 1.5 = 90 connections  
# Buffer = 90 × 0.1 = 9 connections
# Final: Min=10, Max=99

# Conservative scaling (fewer connections, more reuse)
CONNECTION_POOL_MIN_SIZE=5
CONNECTION_POOL_MAX_SIZE=25
CONNECTION_POOL_MAX_INACTIVE_TIME_MINUTES=10

# Aggressive scaling (more connections, less wait time)
CONNECTION_POOL_MIN_SIZE=15
CONNECTION_POOL_MAX_SIZE=75
CONNECTION_POOL_MAX_INACTIVE_TIME_MINUTES=30
```

#### Advanced Pool Configuration

```bash
# High-throughput configuration
CONNECTION_POOL_MIN_SIZE=20
CONNECTION_POOL_MAX_SIZE=100
CONNECTION_POOL_CONNECTION_TIMEOUT_SECONDS=15
CONNECTION_POOL_RETRY_ATTEMPTS=5
CONNECTION_POOL_HEALTH_CHECK_INTERVAL_MINUTES=2

# Low-latency configuration  
CONNECTION_POOL_MIN_SIZE=25
CONNECTION_POOL_MAX_SIZE=50
CONNECTION_POOL_CONNECTION_TIMEOUT_SECONDS=5
CONNECTION_POOL_RETRY_ATTEMPTS=2
CONNECTION_POOL_HEALTH_CHECK_INTERVAL_MINUTES=1
```

## 📈 Horizontal Scaling (Scale Out)

### 1. Load Balancer Setup

#### NGINX Load Balancer

```nginx
# /etc/nginx/sites-available/snowflake-mcp-lb
upstream snowflake_mcp_backend {
    least_conn;  # Use least connections algorithm
    
    server 10.0.1.10:8000 max_fails=3 fail_timeout=30s;
    server 10.0.1.11:8000 max_fails=3 fail_timeout=30s;
    server 10.0.1.12:8000 max_fails=3 fail_timeout=30s;
    
    # Health check
    keepalive 32;
}

server {
    listen 80;
    server_name snowflake-mcp.company.com;
    
    location / {
        proxy_pass http://snowflake_mcp_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts
        proxy_connect_timeout 30s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
        
        # Buffering
        proxy_buffering on;
        proxy_buffer_size 8k;
        proxy_buffers 16 8k;
    }
    
    location /health {
        proxy_pass http://snowflake_mcp_backend;
        access_log off;
    }
    
    location /metrics {
        proxy_pass http://snowflake_mcp_backend;
        allow 10.0.0.0/8;  # Restrict to internal monitoring
        deny all;
    }
}
```

#### HAProxy Load Balancer

```bash
# /etc/haproxy/haproxy.cfg
global
    daemon
    user haproxy
    group haproxy
    
defaults
    mode http
    timeout connect 5s
    timeout client 300s
    timeout server 300s
    option httplog
    
frontend snowflake_mcp_frontend
    bind *:80
    default_backend snowflake_mcp_servers
    
    # Health check endpoint
    acl health_check path_beg /health
    use_backend health_backend if health_check

backend snowflake_mcp_servers
    balance roundrobin
    option httpchk GET /health
    
    server mcp1 10.0.1.10:8000 check inter 30s
    server mcp2 10.0.1.11:8000 check inter 30s  
    server mcp3 10.0.1.12:8000 check inter 30s
    
backend health_backend
    server mcp1 10.0.1.10:8000 check
    server mcp2 10.0.1.11:8000 check
    server mcp3 10.0.1.12:8000 check
```

### 2. Docker Swarm Scaling

```yaml
# docker-compose.swarm.yml
version: '3.8'

services:
  snowflake-mcp:
    image: snowflake-mcp-server:latest
    deploy:
      replicas: 3
      update_config:
        parallelism: 1
        delay: 30s
        failure_action: rollback
      restart_policy:
        condition: on-failure
        delay: 5s
        max_attempts: 3
      resources:
        limits:
          cpus: '2.0'
          memory: 1G
        reservations:
          cpus: '1.0'
          memory: 512M
      placement:
        constraints:
          - node.role == worker
    ports:
      - "8000:8000"
    environment:
      - CONNECTION_POOL_MIN_SIZE=10
      - CONNECTION_POOL_MAX_SIZE=30
      - MAX_CONCURRENT_REQUESTS=50
    networks:
      - snowflake-mcp-network
    volumes:
      - /etc/snowflake-mcp/.env:/app/.env:ro

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
    deploy:
      replicas: 2
      placement:
        constraints:
          - node.role == manager
    configs:
      - source: nginx_config
        target: /etc/nginx/nginx.conf
    networks:
      - snowflake-mcp-network

networks:
  snowflake-mcp-network:
    driver: overlay
    
configs:
  nginx_config:
    external: true
```

### 3. Kubernetes Scaling

#### Horizontal Pod Autoscaler

```yaml
# hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: snowflake-mcp-hpa
  namespace: snowflake-mcp
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: snowflake-mcp-server
  minReplicas: 3
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
  - type: Pods
    pods:
      metric:
        name: requests_per_second
      target:
        type: AverageValue
        averageValue: "30"
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
      - type: Percent
        value: 10
        periodSeconds: 60
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
      - type: Percent
        value: 50
        periodSeconds: 60
      - type: Pods
        value: 2
        periodSeconds: 60
      selectPolicy: Max
```

#### Vertical Pod Autoscaler

```yaml
# vpa.yaml
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: snowflake-mcp-vpa
  namespace: snowflake-mcp
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: snowflake-mcp-server
  updatePolicy:
    updateMode: "Auto"
  resourcePolicy:
    containerPolicies:
    - containerName: snowflake-mcp-server
      minAllowed:
        cpu: 100m
        memory: 256Mi
      maxAllowed:
        cpu: 4000m
        memory: 4Gi
      controlledResources: ["cpu", "memory"]
```

## 🎯 Performance Optimization

### 1. Query Optimization

#### Query Caching Strategy

```python
# Example caching configuration
ENABLE_QUERY_CACHING=true
CACHE_TYPE=redis  # redis, memory, or file
CACHE_TTL_SECONDS=300  # 5 minutes
CACHE_MAX_SIZE_MB=256
CACHE_KEY_PREFIX=snowflake_mcp

# Redis configuration for distributed caching
REDIS_HOST=redis-cluster.company.com
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=cache_password
REDIS_SSL=true
```

#### Connection Multiplexing

```bash
# Enable connection sharing for similar queries
ENABLE_CONNECTION_MULTIPLEXING=true
CONNECTION_SHARING_TIMEOUT_SECONDS=60
MAX_SHARED_CONNECTIONS_PER_POOL=5

# Query batching for efficiency
ENABLE_QUERY_BATCHING=true
BATCH_SIZE=10
BATCH_TIMEOUT_MS=100
```

### 2. Network Optimization

#### TCP Tuning

```bash
# /etc/sysctl.conf optimizations
net.core.rmem_max = 67108864
net.core.wmem_max = 67108864
net.ipv4.tcp_rmem = 4096 87380 67108864
net.ipv4.tcp_wmem = 4096 65536 67108864
net.ipv4.tcp_congestion_control = bbr
net.core.netdev_max_backlog = 5000
net.ipv4.tcp_max_syn_backlog = 8192

# Apply settings
sysctl -p
```

#### HTTP/2 and Keep-Alive

```bash
# Server configuration for HTTP/2
HTTP_VERSION=2
KEEP_ALIVE_ENABLED=true
KEEP_ALIVE_TIMEOUT=60
MAX_KEEP_ALIVE_REQUESTS=1000

# Connection pooling for clients
CLIENT_POOL_SIZE=50
CLIENT_POOL_TIMEOUT=30
```

### 3. Resource Monitoring and Alerting

#### Advanced Monitoring Setup

```yaml
# prometheus-alerts.yml
groups:
- name: snowflake-mcp-scaling
  rules:
  - alert: HighCPUUsage
    expr: rate(process_cpu_seconds_total[5m]) * 100 > 80
    for: 5m
    labels:
      severity: warning
      action: scale_up
    annotations:
      summary: "High CPU usage detected"
      description: "CPU usage is {{ $value }}% for 5+ minutes"
      
  - alert: HighMemoryUsage
    expr: (process_memory_bytes / process_memory_limit_bytes) * 100 > 85
    for: 3m
    labels:
      severity: warning
      action: scale_up
    annotations:
      summary: "High memory usage detected"
      description: "Memory usage is {{ $value }}% of limit"
      
  - alert: ConnectionPoolExhaustion
    expr: (pool_connections_active / pool_connections_max) * 100 > 90
    for: 2m
    labels:
      severity: critical
      action: scale_out
    annotations:
      summary: "Connection pool nearly exhausted"
      description: "Pool utilization: {{ $value }}%"
      
  - alert: HighRequestLatency
    expr: histogram_quantile(0.95, rate(mcp_request_duration_seconds_bucket[5m])) > 5
    for: 5m
    labels:
      severity: warning
      action: optimize
    annotations:
      summary: "High request latency"
      description: "95th percentile latency: {{ $value }}s"
```

## 📊 Capacity Planning

### 1. Baseline Measurements

#### Performance Benchmarking Script

```bash
#!/bin/bash
# benchmark_scaling.sh - Measure current capacity

echo "🔍 Starting capacity benchmark..."

# Test configuration
TEST_DURATION=300  # 5 minutes
CONCURRENT_CLIENTS=(1 5 10 25 50 100)
RESULTS_FILE="/tmp/scaling_benchmark_$(date +%Y%m%d_%H%M%S).csv"

echo "concurrent_clients,requests_per_second,avg_response_time,p95_response_time,error_rate,cpu_usage,memory_usage" > "$RESULTS_FILE"

for clients in "${CONCURRENT_CLIENTS[@]}"; do
    echo "📊 Testing with $clients concurrent clients..."
    
    # Start monitoring
    MONITOR_PID=$(nohup top -b -d 1 -p $(pgrep -f snowflake-mcp) | awk '/snowflake-mcp/ {print systime()","$9","$10}' > "/tmp/resources_$clients.log" &)
    
    # Run load test (using Apache Bench or similar)
    ab -n $((clients * 100)) -c $clients -t $TEST_DURATION \
       -H "Content-Type: application/json" \
       -p /tmp/mcp_request.json \
       http://localhost:8000/ > "/tmp/ab_results_$clients.txt"
    
    # Stop monitoring
    kill $MONITOR_PID 2>/dev/null
    
    # Parse results
    RPS=$(grep "Requests per second" "/tmp/ab_results_$clients.txt" | awk '{print $4}')
    AVG_TIME=$(grep "Time per request" "/tmp/ab_results_$clients.txt" | head -1 | awk '{print $4}')
    P95_TIME=$(grep "95%" "/tmp/ab_results_$clients.txt" | awk '{print $2}')
    ERROR_RATE=$(grep "Non-2xx responses" "/tmp/ab_results_$clients.txt" | awk '{print $3}' || echo "0")
    
    # Calculate resource usage
    CPU_AVG=$(awk -F',' '{sum+=$2; count++} END {print sum/count}' "/tmp/resources_$clients.log" 2>/dev/null || echo "0")
    MEM_AVG=$(awk -F',' '{sum+=$3; count++} END {print sum/count}' "/tmp/resources_$clients.log" 2>/dev/null || echo "0")
    
    echo "$clients,$RPS,$AVG_TIME,$P95_TIME,$ERROR_RATE,$CPU_AVG,$MEM_AVG" >> "$RESULTS_FILE"
    
    # Wait between tests
    sleep 30
done

echo "✅ Benchmark completed. Results saved to: $RESULTS_FILE"

# Generate scaling recommendations
python3 << EOF
import csv
import sys

with open('$RESULTS_FILE', 'r') as f:
    reader = csv.DictReader(f)
    data = list(reader)

print("\n📈 Scaling Recommendations:")
print("=" * 50)

for row in data:
    clients = int(row['concurrent_clients'])
    cpu = float(row['cpu_usage'] or 0)
    memory = float(row['memory_usage'] or 0)
    rps = float(row['requests_per_second'] or 0)
    response_time = float(row['p95_response_time'] or 0)
    
    if cpu > 80:
        print(f"⚠️  At {clients} clients: CPU usage {cpu:.1f}% - Scale UP needed")
    elif memory > 80:
        print(f"⚠️  At {clients} clients: Memory usage {memory:.1f}% - Scale UP needed")
    elif response_time > 5000:  # 5 seconds
        print(f"⚠️  At {clients} clients: Response time {response_time:.0f}ms - Scale OUT needed")
    else:
        print(f"✅ At {clients} clients: Performance acceptable (CPU: {cpu:.1f}%, MEM: {memory:.1f}%, RT: {response_time:.0f}ms)")

# Find maximum sustainable load
sustainable_clients = 0
for row in data:
    clients = int(row['concurrent_clients'])
    cpu = float(row['cpu_usage'] or 0)
    memory = float(row['memory_usage'] or 0)
    response_time = float(row['p95_response_time'] or 0)
    
    if cpu < 70 and memory < 75 and response_time < 2000:
        sustainable_clients = clients

print(f"\n🎯 Maximum sustainable load: {sustainable_clients} concurrent clients")
print(f"🔧 Recommended scale-out trigger: {int(sustainable_clients * 0.8)} clients")
EOF
```

### 2. Growth Planning

#### Capacity Planning Calculator

```python
#!/usr/bin/env python3
# capacity_planner.py - Calculate scaling requirements

import math
import json
from datetime import datetime, timedelta

class CapacityPlanner:
    def __init__(self):
        # Base performance metrics (from benchmarking)
        self.base_metrics = {
            "max_clients_per_instance": 50,
            "cpu_per_client": 1.5,  # CPU percentage per client
            "memory_per_client": 20,  # MB per client
            "response_time_base": 100,  # Base response time in ms
            "response_time_factor": 0.05,  # Response time increase per client
        }
    
    def calculate_instances_needed(self, target_clients, safety_margin=0.2):
        """Calculate number of instances needed for target client count."""
        
        # Apply safety margin
        adjusted_clients = target_clients * (1 + safety_margin)
        
        # Calculate instances needed
        instances = math.ceil(adjusted_clients / self.base_metrics["max_clients_per_instance"])
        
        return {
            "target_clients": target_clients,
            "adjusted_clients": adjusted_clients,
            "instances_needed": instances,
            "clients_per_instance": adjusted_clients / instances,
            "total_cpu_cores": instances * 4,  # 4 cores per instance
            "total_memory_gb": instances * 2,  # 2GB per instance
            "estimated_cost_monthly": instances * 150,  # $150 per instance
        }
    
    def project_growth(self, current_clients, growth_rate_monthly, months=12):
        """Project scaling needs over time."""
        
        projections = []
        clients = current_clients
        
        for month in range(1, months + 1):
            clients = clients * (1 + growth_rate_monthly)
            capacity = self.calculate_instances_needed(int(clients))
            capacity["month"] = month
            capacity["date"] = (datetime.now() + timedelta(days=30*month)).strftime("%Y-%m")
            projections.append(capacity)
        
        return projections
    
    def print_scaling_plan(self, current_clients, growth_rate):
        """Print a comprehensive scaling plan."""
        
        print("🎯 Snowflake MCP Server Scaling Plan")
        print("=" * 50)
        print(f"Current Clients: {current_clients}")
        print(f"Monthly Growth Rate: {growth_rate*100:.1f}%")
        print()
        
        projections = self.project_growth(current_clients, growth_rate)
        
        print("📈 Growth Projections:")
        print("Month | Date    | Clients | Instances | CPU Cores | Memory (GB) | Cost/Month")
        print("-" * 75)
        
        for p in projections:
            print(f"{p['month']:5d} | {p['date']} | {p['target_clients']:7.0f} | {p['instances_needed']:9d} | "
                  f"{p['total_cpu_cores']:9d} | {p['total_memory_gb']:11d} | ${p['estimated_cost_monthly']:10.0f}")
        
        # Scaling milestones
        print("\n🚀 Scaling Milestones:")
        current_instances = 1
        for p in projections:
            if p['instances_needed'] > current_instances:
                print(f"📅 {p['date']}: Scale to {p['instances_needed']} instances "
                      f"({p['target_clients']:.0f} clients)")
                current_instances = p['instances_needed']
        
        # Resource recommendations
        print("\n💡 Recommendations:")
        final_projection = projections[-1]
        
        if final_projection['instances_needed'] <= 3:
            print("✅ Single availability zone deployment sufficient")
        elif final_projection['instances_needed'] <= 10:
            print("⚠️  Consider multi-AZ deployment for redundancy")
        else:
            print("🔧 Enterprise deployment with multi-region consideration")
        
        if final_projection['total_cpu_cores'] > 50:
            print("🔧 Consider dedicated infrastructure or reserved instances")
        
        if final_projection['estimated_cost_monthly'] > 5000:
            print("💰 High cost projection - optimize for resource efficiency")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 3:
        print("Usage: python3 capacity_planner.py <current_clients> <growth_rate>")
        print("Example: python3 capacity_planner.py 25 0.15  # 25 clients, 15% monthly growth")
        sys.exit(1)
    
    current_clients = int(sys.argv[1])
    growth_rate = float(sys.argv[2])
    
    planner = CapacityPlanner()
    planner.print_scaling_plan(current_clients, growth_rate)
```

### 3. Auto-Scaling Configuration

#### Kubernetes Auto-Scaling

```yaml
# complete-autoscaling.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: scaling-config
  namespace: snowflake-mcp
data:
  scaling_policy.json: |
    {
      "scaling_rules": {
        "scale_up_triggers": [
          {"metric": "cpu_usage", "threshold": 70, "duration": "5m"},
          {"metric": "memory_usage", "threshold": 80, "duration": "3m"},
          {"metric": "response_time_p95", "threshold": 5000, "duration": "5m"},
          {"metric": "connection_pool_usage", "threshold": 85, "duration": "2m"}
        ],
        "scale_down_triggers": [
          {"metric": "cpu_usage", "threshold": 30, "duration": "10m"},
          {"metric": "memory_usage", "threshold": 40, "duration": "10m"},
          {"metric": "response_time_p95", "threshold": 1000, "duration": "10m"}
        ],
        "scaling_limits": {
          "min_replicas": 2,
          "max_replicas": 50,
          "scale_up_rate": "50% or 4 pods per minute",
          "scale_down_rate": "10% per minute"
        }
      }
    }

---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: snowflake-mcp-hpa-advanced
  namespace: snowflake-mcp
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: snowflake-mcp-server
  minReplicas: 2
  maxReplicas: 50
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
  - type: Object
    object:
      metric:
        name: response_time_p95
      target:
        type: Value
        value: "2000"  # 2 seconds
      describedObject:
        apiVersion: v1
        kind: Service
        name: snowflake-mcp-service
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 600  # 10 minutes
      policies:
      - type: Percent
        value: 10
        periodSeconds: 60
      - type: Pods
        value: 1
        periodSeconds: 60
      selectPolicy: Min
    scaleUp:
      stabilizationWindowSeconds: 300  # 5 minutes
      policies:
      - type: Percent
        value: 50
        periodSeconds: 60
      - type: Pods
        value: 4
        periodSeconds: 60
      selectPolicy: Max
```

## 🔍 Monitoring Scaling Performance

### Scaling Metrics Dashboard

```yaml
# grafana-scaling-dashboard.json
{
  "dashboard": {
    "title": "Snowflake MCP Scaling Metrics",
    "panels": [
      {
        "title": "Instance Count vs Load",
        "type": "graph",
        "targets": [
          {
            "expr": "up{job=\"snowflake-mcp-server\"}",
            "legendFormat": "Active Instances"
          },
          {
            "expr": "sum(rate(mcp_requests_total[5m]))",
            "legendFormat": "Requests/sec"
          }
        ]
      },
      {
        "title": "Resource Utilization",
        "type": "graph",
        "targets": [
          {
            "expr": "avg(rate(process_cpu_seconds_total[5m]) * 100)",
            "legendFormat": "CPU %"
          },
          {
            "expr": "avg(process_memory_bytes / process_memory_limit_bytes * 100)",
            "legendFormat": "Memory %"
          }
        ]
      },
      {
        "title": "Scaling Events",
        "type": "table",
        "targets": [
          {
            "expr": "increase(kube_hpa_status_current_replicas[1h])",
            "legendFormat": "Scale Events"
          }
        ]
      }
    ]
  }
}
```

### Automated Scaling Reports

```bash
#!/bin/bash
# scaling_report.sh - Generate scaling performance report

REPORT_DATE=$(date +%Y-%m-%d)
REPORT_FILE="/tmp/scaling_report_$REPORT_DATE.html"

cat > "$REPORT_FILE" << 'EOF'
<!DOCTYPE html>
<html>
<head>
    <title>Snowflake MCP Scaling Report</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        .metric { background: #f5f5f5; padding: 10px; margin: 10px 0; border-radius: 5px; }
        .good { border-left: 5px solid #4CAF50; }
        .warning { border-left: 5px solid #FF9800; }
        .critical { border-left: 5px solid #F44336; }
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
    </style>
</head>
<body>
    <h1>🚀 Snowflake MCP Scaling Report</h1>
    <p><strong>Report Date:</strong> $REPORT_DATE</p>
    
    <h2>📊 Current Status</h2>
EOF

# Get current metrics
CURRENT_INSTANCES=$(kubectl get pods -n snowflake-mcp -l app=snowflake-mcp-server --no-headers | wc -l)
CURRENT_CPU=$(curl -s http://localhost:8001/metrics | grep process_cpu_seconds_total | tail -1 | awk '{print $2}')
CURRENT_MEMORY=$(curl -s http://localhost:8001/metrics | grep process_memory_bytes | grep -v limit | tail -1 | awk '{print $2}')
CURRENT_RPS=$(curl -s http://localhost:8001/metrics | grep mcp_requests_total | awk -F' ' '{sum+=$2} END {print sum/NR}')

# Determine status colors
if (( $(echo "$CURRENT_CPU > 0.8" | bc -l) )); then
    CPU_CLASS="critical"
elif (( $(echo "$CURRENT_CPU > 0.6" | bc -l) )); then
    CPU_CLASS="warning"
else
    CPU_CLASS="good"
fi

cat >> "$REPORT_FILE" << EOF
    <div class="metric $CPU_CLASS">
        <strong>Active Instances:</strong> $CURRENT_INSTANCES<br>
        <strong>CPU Usage:</strong> $(echo "$CURRENT_CPU * 100" | bc)%<br>
        <strong>Memory Usage:</strong> $(echo "scale=1; $CURRENT_MEMORY / 1024 / 1024" | bc) MB<br>
        <strong>Requests/Second:</strong> $CURRENT_RPS
    </div>
    
    <h2>📈 Scaling Events (Last 24h)</h2>
    <table>
        <tr><th>Time</th><th>Event</th><th>From</th><th>To</th><th>Trigger</th></tr>
EOF

# Get recent scaling events (if available)
kubectl get events -n snowflake-mcp --field-selector involvedObject.name=snowflake-mcp-hpa --sort-by='.lastTimestamp' | tail -10 | while read line; do
    echo "        <tr><td>$(echo "$line" | awk '{print $1}')</td><td>Scaling</td><td>-</td><td>-</td><td>Auto</td></tr>" >> "$REPORT_FILE"
done

cat >> "$REPORT_FILE" << 'EOF'
    </table>
    
    <h2>💡 Recommendations</h2>
    <ul>
EOF

# Generate recommendations based on current state
if [ "$CURRENT_INSTANCES" -lt 3 ]; then
    echo "        <li>⚠️ Consider increasing minimum replica count for better availability</li>" >> "$REPORT_FILE"
fi

if (( $(echo "$CURRENT_CPU > 0.7" | bc -l) )); then
    echo "        <li>🔧 High CPU usage detected - consider vertical scaling</li>" >> "$REPORT_FILE"
fi

cat >> "$REPORT_FILE" << 'EOF'
    </ul>
    
    <p><em>Report generated automatically on $(date)</em></p>
</body>
</html>
EOF

echo "📊 Scaling report generated: $REPORT_FILE"

# Email report (optional)
if [ -n "$SCALING_REPORT_EMAIL" ]; then
    mail -s "Snowflake MCP Scaling Report - $REPORT_DATE" -a "Content-Type: text/html" "$SCALING_REPORT_EMAIL" < "$REPORT_FILE"
fi
```

## 📚 Best Practices

### 1. Scaling Strategies

- **Start Small, Scale Gradually:** Begin with minimal resources and scale based on real usage
- **Monitor Leading Indicators:** Watch trends before hitting limits
- **Automate Everything:** Use auto-scaling to respond quickly to load changes
- **Plan for Spikes:** Provision for peak loads, not just average usage
- **Test Scaling:** Regularly test auto-scaling behavior under load

### 2. Cost Optimization

- **Right-Size Resources:** Don't over-provision unnecessarily
- **Use Reserved Instances:** For predictable workloads
- **Implement Auto-Shutdown:** For development environments
- **Monitor Unused Resources:** Regular cleanup of idle instances
- **Optimize Connection Pooling:** Reduce Snowflake costs through efficient connections

### 3. Performance Optimization

- **Connection Reuse:** Maximize connection pool efficiency
- **Query Optimization:** Cache frequently accessed data
- **Async Processing:** Use non-blocking I/O patterns
- **Load Balancing:** Distribute requests evenly
- **Regional Deployment:** Reduce latency with geographic distribution

---

## 🆘 Troubleshooting Scaling Issues

### Common Scaling Problems

| Problem | Symptoms | Solution |
|---------|----------|----------|
| Slow scale-up | High latency during traffic spikes | Reduce stabilization window, increase scale-up rate |
| Excessive scale-down | Instances terminating too quickly | Increase scale-down stabilization time |
| Resource waste | Many idle instances | Tune auto-scaling thresholds, implement predictive scaling |
| Connection limits | Pool exhaustion errors | Increase pool size, implement connection sharing |
| Memory leaks | Gradual memory increase | Implement regular restarts, fix application leaks |

### Scaling Debug Commands

```bash
# Check auto-scaling status
kubectl describe hpa snowflake-mcp-hpa -n snowflake-mcp

# View scaling events
kubectl get events -n snowflake-mcp --field-selector involvedObject.name=snowflake-mcp-hpa

# Monitor resource usage in real-time
watch kubectl top pods -n snowflake-mcp

# Check load balancer health
curl -s http://load-balancer/health | jq '.'

# Test connection pool under load
./scripts/benchmark_scaling.sh
```

---

## 📞 Support

For scaling assistance:
1. **Review monitoring dashboards** for performance trends
2. **Run capacity planning tools** to project future needs  
3. **Test scaling configuration** in non-production environment
4. **Contact operations team** for infrastructure scaling support

**Related Documentation:**
- [Operations Runbook](OPERATIONS_RUNBOOK.md)
- [Configuration Guide](CONFIGURATION_GUIDE.md) 
- [Deployment Guide](deploy/DEPLOYMENT_README.md)
- [Performance Monitoring](deploy/monitoring/)