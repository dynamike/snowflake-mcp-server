# Capacity Planning Guide

This guide provides detailed capacity planning methodologies for the Snowflake MCP Server to ensure optimal resource allocation and cost-effective scaling.

## 📊 Capacity Planning Overview

### Planning Objectives

- **Performance:** Maintain response times under 2 seconds (95th percentile)
- **Availability:** Achieve 99.9% uptime with proper redundancy
- **Cost Efficiency:** Optimize resource usage to minimize operational costs
- **Scalability:** Plan for 12-month growth projections
- **Reliability:** Handle peak loads without service degradation

### Key Performance Indicators (KPIs)

| Metric | Target | Warning Threshold | Critical Threshold |
|--------|--------|-------------------|-------------------|
| Response Time (p95) | <2s | >3s | >5s |
| CPU Utilization | <70% | >80% | >90% |
| Memory Utilization | <75% | >85% | >95% |
| Connection Pool Usage | <80% | >90% | >95% |
| Error Rate | <1% | >3% | >5% |
| Concurrent Users | Variable | 80% of capacity | 95% of capacity |

## 🔢 Capacity Planning Methodology

### 1. Baseline Measurements

#### Performance Benchmarking

```bash
#!/bin/bash
# baseline_measurement.sh - Establish performance baselines

echo "🔍 Starting baseline capacity measurements..."

# Configuration
MEASUREMENT_DURATION=300  # 5 minutes per test
WARMUP_DURATION=60       # 1 minute warmup
CLIENT_INCREMENTS=(1 2 5 10 15 20 25 30 40 50 75 100)
RESULTS_DIR="/tmp/capacity_baseline_$(date +%Y%m%d_%H%M%S)"

mkdir -p "$RESULTS_DIR"

# Create test payload
cat > "$RESULTS_DIR/test_payload.json" << 'EOF'
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "execute_query",
    "arguments": {
      "query": "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES"
    }
  }
}
EOF

# Results file
RESULTS_FILE="$RESULTS_DIR/baseline_results.csv"
echo "concurrent_clients,requests_per_second,mean_response_time,p95_response_time,p99_response_time,error_rate,cpu_avg,memory_avg,pool_utilization" > "$RESULTS_FILE"

for clients in "${CLIENT_INCREMENTS[@]}"; do
    echo "📊 Testing with $clients concurrent clients..."
    
    # Start system monitoring
    MONITOR_PID=$(nohup bash -c "
        while true; do
            timestamp=\$(date +%s)
            cpu=\$(top -bn1 | grep 'snowflake-mcp' | awk '{print \$9}' | head -1)
            memory=\$(top -bn1 | grep 'snowflake-mcp' | awk '{print \$10}' | head -1)
            pool_active=\$(curl -s http://localhost:8001/metrics | grep pool_connections_active | awk '{print \$2}')
            pool_max=\$(curl -s http://localhost:8001/metrics | grep pool_connections_max | awk '{print \$2}')
            echo \"\$timestamp,\$cpu,\$memory,\$pool_active,\$pool_max\" >> \"$RESULTS_DIR/monitoring_\${clients}.csv\"
            sleep 5
        done
    " &)
    
    # Warmup phase
    echo "🔥 Warming up..."
    ab -n $((clients * 10)) -c $clients \
       -T "application/json" \
       -p "$RESULTS_DIR/test_payload.json" \
       http://localhost:8000/ > /dev/null 2>&1
    
    sleep $WARMUP_DURATION
    
    # Actual measurement
    echo "⏱️ Measuring performance..."
    ab -n $((clients * 50)) -c $clients -t $MEASUREMENT_DURATION \
       -T "application/json" \
       -p "$RESULTS_DIR/test_payload.json" \
       http://localhost:8000/ > "$RESULTS_DIR/ab_results_${clients}.txt"
    
    # Stop monitoring
    kill $MONITOR_PID 2>/dev/null
    wait $MONITOR_PID 2>/dev/null
    
    # Parse results
    RPS=$(grep "Requests per second" "$RESULTS_DIR/ab_results_${clients}.txt" | awk '{print $4}')
    MEAN_TIME=$(grep "Time per request" "$RESULTS_DIR/ab_results_${clients}.txt" | head -1 | awk '{print $4}')
    P95_TIME=$(grep "95%" "$RESULTS_DIR/ab_results_${clients}.txt" | awk '{print $2}')
    P99_TIME=$(grep "99%" "$RESULTS_DIR/ab_results_${clients}.txt" | awk '{print $2}')
    ERROR_RATE=$(grep "Non-2xx responses" "$RESULTS_DIR/ab_results_${clients}.txt" | awk '{print $3}' | sed 's/[()]//g' || echo "0")
    
    # Calculate averages from monitoring data
    if [ -f "$RESULTS_DIR/monitoring_${clients}.csv" ]; then
        CPU_AVG=$(awk -F',' 'NR>1 && $2!="" {sum+=$2; count++} END {if(count>0) print sum/count; else print 0}' "$RESULTS_DIR/monitoring_${clients}.csv")
        MEMORY_AVG=$(awk -F',' 'NR>1 && $3!="" {sum+=$3; count++} END {if(count>0) print sum/count; else print 0}' "$RESULTS_DIR/monitoring_${clients}.csv")
        
        # Calculate pool utilization
        POOL_UTIL=$(awk -F',' 'NR>1 && $4!="" && $5!="" && $5>0 {util=$4/$5*100; if(util>max) max=util} END {print max+0}' "$RESULTS_DIR/monitoring_${clients}.csv")
    else
        CPU_AVG=0
        MEMORY_AVG=0
        POOL_UTIL=0
    fi
    
    # Save results
    echo "$clients,$RPS,$MEAN_TIME,$P95_TIME,$P99_TIME,$ERROR_RATE,$CPU_AVG,$MEMORY_AVG,$POOL_UTIL" >> "$RESULTS_FILE"
    
    echo "✅ Completed test with $clients clients (RPS: $RPS, P95: ${P95_TIME}ms, CPU: ${CPU_AVG}%)"
    
    # Cool down between tests
    sleep 30
done

echo "🎉 Baseline measurements completed!"
echo "📁 Results directory: $RESULTS_DIR"
echo "📊 Results file: $RESULTS_FILE"

# Generate analysis
python3 << EOF
import csv
import matplotlib.pyplot as plt
import json
from datetime import datetime

# Read results
results = []
with open('$RESULTS_FILE', 'r') as f:
    reader = csv.DictReader(f)
    results = [row for row in reader]

# Find capacity limits
capacity_analysis = {
    "max_sustainable_clients": 0,
    "cpu_limited_at": 0,
    "memory_limited_at": 0,
    "response_time_limited_at": 0,
    "error_rate_limited_at": 0
}

for row in results:
    clients = int(row['concurrent_clients'])
    cpu = float(row['cpu_avg'] or 0)
    memory = float(row['memory_avg'] or 0)
    p95_time = float(row['p95_response_time'] or 0)
    error_rate = float(row['error_rate'] or 0)
    
    # Check if within acceptable limits
    within_limits = (
        cpu < 70 and 
        memory < 75 and 
        p95_time < 2000 and 
        error_rate < 1
    )
    
    if within_limits:
        capacity_analysis["max_sustainable_clients"] = clients
    
    # Record first breach of each limit
    if cpu >= 70 and capacity_analysis["cpu_limited_at"] == 0:
        capacity_analysis["cpu_limited_at"] = clients
    if memory >= 75 and capacity_analysis["memory_limited_at"] == 0:
        capacity_analysis["memory_limited_at"] = clients
    if p95_time >= 2000 and capacity_analysis["response_time_limited_at"] == 0:
        capacity_analysis["response_time_limited_at"] = clients
    if error_rate >= 1 and capacity_analysis["error_rate_limited_at"] == 0:
        capacity_analysis["error_rate_limited_at"] = clients

# Save analysis
with open('$RESULTS_DIR/capacity_analysis.json', 'w') as f:
    json.dump(capacity_analysis, f, indent=2)

print(f"📈 Capacity Analysis Results:")
print(f"   Maximum sustainable clients: {capacity_analysis['max_sustainable_clients']}")
print(f"   CPU limited at: {capacity_analysis['cpu_limited_at']} clients")
print(f"   Memory limited at: {capacity_analysis['memory_limited_at']} clients")
print(f"   Response time limited at: {capacity_analysis['response_time_limited_at']} clients")
print(f"   Error rate limited at: {capacity_analysis['error_rate_limited_at']} clients")

EOF
```

### 2. Growth Projection Models

#### Linear Growth Model

```python
#!/usr/bin/env python3
# linear_growth_model.py - Project linear user growth

import json
import csv
from datetime import datetime, timedelta
import argparse

class LinearGrowthModel:
    def __init__(self, baseline_capacity):
        self.baseline_capacity = baseline_capacity
        
    def project_growth(self, current_users, growth_per_month, months=24):
        """Project linear growth over time."""
        projections = []
        users = current_users
        
        for month in range(1, months + 1):
            users += growth_per_month
            
            # Calculate required instances
            instances_needed = max(1, (users // self.baseline_capacity) + 1)
            
            # Calculate costs (example pricing)
            monthly_cost = instances_needed * 200  # $200 per instance
            
            projection = {
                "month": month,
                "date": (datetime.now() + timedelta(days=30*month)).strftime("%Y-%m"),
                "projected_users": int(users),
                "instances_required": instances_needed,
                "monthly_cost": monthly_cost,
                "annual_cost": monthly_cost * 12,
                "utilization": min(100, (users / (instances_needed * self.baseline_capacity)) * 100)
            }
            projections.append(projection)
        
        return projections

    def print_projections(self, projections):
        """Print formatted projections."""
        print("📈 Linear Growth Projections")
        print("=" * 80)
        print(f"{'Month':<6} {'Date':<8} {'Users':<8} {'Instances':<10} {'Utilization':<12} {'Cost/Month':<12}")
        print("-" * 80)
        
        for p in projections:
            print(f"{p['month']:<6} {p['date']:<8} {p['projected_users']:<8} "
                  f"{p['instances_required']:<10} {p['utilization']:<11.1f}% "
                  f"${p['monthly_cost']:<11}")
        
        # Summary
        final = projections[-1]
        print(f"\n📊 Final Projection ({final['date']}):")
        print(f"   Users: {final['projected_users']:,}")
        print(f"   Instances: {final['instances_required']}")
        print(f"   Monthly Cost: ${final['monthly_cost']:,}")
        print(f"   Annual Cost: ${final['annual_cost']:,}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Linear growth capacity planning')
    parser.add_argument('--current-users', type=int, required=True, help='Current number of users')
    parser.add_argument('--growth-per-month', type=int, required=True, help='User growth per month')
    parser.add_argument('--baseline-capacity', type=int, default=50, help='Users per instance (default: 50)')
    parser.add_argument('--months', type=int, default=24, help='Months to project (default: 24)')
    
    args = parser.parse_args()
    
    model = LinearGrowthModel(args.baseline_capacity)
    projections = model.project_growth(args.current_users, args.growth_per_month, args.months)
    model.print_projections(projections)
```

#### Exponential Growth Model

```python
#!/usr/bin/env python3
# exponential_growth_model.py - Project exponential user growth

import math
import json
from datetime import datetime, timedelta

class ExponentialGrowthModel:
    def __init__(self, baseline_capacity):
        self.baseline_capacity = baseline_capacity
        
    def project_growth(self, current_users, growth_rate_monthly, months=24):
        """Project exponential growth over time."""
        projections = []
        users = current_users
        
        for month in range(1, months + 1):
            users = users * (1 + growth_rate_monthly)
            
            # Calculate required instances with safety margin
            raw_instances = users / self.baseline_capacity
            instances_needed = max(1, math.ceil(raw_instances * 1.2))  # 20% safety margin
            
            # Calculate costs with volume discounts
            if instances_needed <= 5:
                cost_per_instance = 200
            elif instances_needed <= 20:
                cost_per_instance = 180  # 10% discount
            else:
                cost_per_instance = 160  # 20% discount
                
            monthly_cost = instances_needed * cost_per_instance
            
            projection = {
                "month": month,
                "date": (datetime.now() + timedelta(days=30*month)).strftime("%Y-%m"),
                "projected_users": int(users),
                "instances_required": instances_needed,
                "monthly_cost": monthly_cost,
                "cost_per_user": monthly_cost / users if users > 0 else 0,
                "utilization": min(100, (users / (instances_needed * self.baseline_capacity)) * 100),
                "growth_rate": ((users / current_users) ** (1/month) - 1) * 100
            }
            projections.append(projection)
        
        return projections
    
    def identify_scaling_milestones(self, projections):
        """Identify key scaling milestones."""
        milestones = []
        previous_instances = 1
        
        for p in projections:
            if p['instances_required'] > previous_instances:
                milestone = {
                    "date": p['date'],
                    "users": p['projected_users'],
                    "scale_from": previous_instances,
                    "scale_to": p['instances_required'],
                    "cost_impact": p['monthly_cost'],
                    "reason": self._determine_scaling_reason(p)
                }
                milestones.append(milestone)
                previous_instances = p['instances_required']
        
        return milestones
    
    def _determine_scaling_reason(self, projection):
        """Determine the primary reason for scaling."""
        if projection['utilization'] > 90:
            return "High utilization"
        elif projection['projected_users'] > 1000:
            return "Large user base"
        else:
            return "Growth trajectory"
    
    def print_analysis(self, projections, milestones):
        """Print comprehensive analysis."""
        print("🚀 Exponential Growth Analysis")
        print("=" * 90)
        
        # Key projections
        key_months = [6, 12, 18, 24]
        print(f"{'Period':<8} {'Date':<8} {'Users':<10} {'Instances':<10} {'Cost/Month':<12} {'Cost/User':<10}")
        print("-" * 90)
        
        for p in projections:
            if p['month'] in key_months:
                print(f"{p['month']}mo {p['date']:<8} {p['projected_users']:<10,} "
                      f"{p['instances_required']:<10} ${p['monthly_cost']:<11,} "
                      f"${p['cost_per_user']:<9.2f}")
        
        # Scaling milestones
        print(f"\n🎯 Scaling Milestones:")
        print("-" * 90)
        for milestone in milestones:
            print(f"📅 {milestone['date']}: Scale from {milestone['scale_from']} to "
                  f"{milestone['scale_to']} instances ({milestone['users']:,} users)")
            print(f"   💰 Monthly cost: ${milestone['cost_impact']:,} ({milestone['reason']})")
        
        # Cost analysis
        final = projections[-1]
        initial_monthly = projections[0]['monthly_cost']
        cost_multiplier = final['monthly_cost'] / initial_monthly if initial_monthly > 0 else 0
        
        print(f"\n💰 Cost Analysis:")
        print(f"   Initial monthly cost: ${initial_monthly:,}")
        print(f"   Final monthly cost: ${final['monthly_cost']:,}")
        print(f"   Cost multiplier: {cost_multiplier:.1f}x")
        print(f"   Final cost per user: ${final['cost_per_user']:.2f}")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Exponential growth capacity planning')
    parser.add_argument('--current-users', type=int, required=True, help='Current number of users')
    parser.add_argument('--growth-rate', type=float, required=True, help='Monthly growth rate (e.g., 0.15 for 15%)')
    parser.add_argument('--baseline-capacity', type=int, default=50, help='Users per instance')
    parser.add_argument('--months', type=int, default=24, help='Months to project')
    
    args = parser.parse_args()
    
    model = ExponentialGrowthModel(args.baseline_capacity)
    projections = model.project_growth(args.current_users, args.growth_rate, args.months)
    milestones = model.identify_scaling_milestones(projections)
    model.print_analysis(projections, milestones)
```

### 3. Resource Optimization Calculator

```python
#!/usr/bin/env python3
# resource_optimizer.py - Optimize resource allocation

import json
import math
from dataclasses import dataclass
from typing import List, Dict, Any

@dataclass
class ResourceConfiguration:
    cpu_cores: int
    memory_gb: int
    connection_pool_size: int
    max_concurrent_requests: int
    estimated_users: int
    monthly_cost: float

class ResourceOptimizer:
    def __init__(self):
        # Base resource requirements per user
        self.cpu_per_user = 0.02  # CPU cores per user
        self.memory_per_user = 10  # MB per user
        self.connections_per_user = 0.15  # Connections per user
        
        # Infrastructure costs (example)
        self.cost_per_cpu_core = 30  # $30/month per core
        self.cost_per_gb_memory = 15  # $15/month per GB
        self.base_cost_per_instance = 50  # Base infrastructure cost
    
    def calculate_optimal_config(self, target_users: int, safety_margin: float = 0.2) -> ResourceConfiguration:
        """Calculate optimal resource configuration for target users."""
        
        # Apply safety margin
        effective_users = target_users * (1 + safety_margin)
        
        # Calculate base requirements
        base_cpu = effective_users * self.cpu_per_user
        base_memory_mb = effective_users * self.memory_per_user
        base_connections = effective_users * self.connections_per_user
        
        # Round up to practical values
        cpu_cores = max(1, math.ceil(base_cpu))
        memory_gb = max(1, math.ceil(base_memory_mb / 1024))
        connection_pool_size = max(5, math.ceil(base_connections))
        max_concurrent_requests = max(10, target_users // 2)
        
        # Calculate cost
        monthly_cost = (
            self.base_cost_per_instance +
            cpu_cores * self.cost_per_cpu_core +
            memory_gb * self.cost_per_gb_memory
        )
        
        return ResourceConfiguration(
            cpu_cores=cpu_cores,
            memory_gb=memory_gb,
            connection_pool_size=connection_pool_size,
            max_concurrent_requests=max_concurrent_requests,
            estimated_users=target_users,
            monthly_cost=monthly_cost
        )
    
    def generate_scaling_options(self, target_users: int) -> List[ResourceConfiguration]:
        """Generate multiple scaling options with different approaches."""
        
        options = []
        
        # Conservative option (high safety margin)
        conservative = self.calculate_optimal_config(target_users, safety_margin=0.5)
        conservative.monthly_cost *= 1.1  # Premium for conservative approach
        options.append(("Conservative", conservative))
        
        # Balanced option (standard safety margin)
        balanced = self.calculate_optimal_config(target_users, safety_margin=0.2)
        options.append(("Balanced", balanced))
        
        # Aggressive option (minimal safety margin)
        aggressive = self.calculate_optimal_config(target_users, safety_margin=0.05)
        aggressive.monthly_cost *= 0.9  # Discount for aggressive approach
        options.append(("Aggressive", aggressive))
        
        # High-performance option (over-provisioned)
        high_perf = self.calculate_optimal_config(target_users, safety_margin=0.3)
        high_perf.cpu_cores *= 2
        high_perf.memory_gb = int(high_perf.memory_gb * 1.5)
        high_perf.connection_pool_size = int(high_perf.connection_pool_size * 1.5)
        high_perf.monthly_cost = (
            self.base_cost_per_instance +
            high_perf.cpu_cores * self.cost_per_cpu_core +
            high_perf.memory_gb * self.cost_per_gb_memory
        ) * 1.2
        options.append(("High-Performance", high_perf))
        
        return options
    
    def print_scaling_options(self, target_users: int, options: List[tuple]):
        """Print formatted scaling options."""
        
        print(f"🎯 Resource Optimization for {target_users:,} Users")
        print("=" * 100)
        print(f"{'Option':<15} {'CPU':<5} {'Memory':<8} {'Pool':<8} {'Requests':<10} {'Cost/Month':<12} {'Cost/User':<10}")
        print("-" * 100)
        
        for name, config in options:
            cost_per_user = config.monthly_cost / target_users if target_users > 0 else 0
            print(f"{name:<15} {config.cpu_cores:<5} {config.memory_gb:<7}GB "
                  f"{config.connection_pool_size:<8} {config.max_concurrent_requests:<10} "
                  f"${config.monthly_cost:<11.2f} ${cost_per_user:<9.2f}")
        
        # Recommendations
        print(f"\n💡 Recommendations:")
        print(f"   🟢 Start with 'Balanced' option for most use cases")
        print(f"   ⚡ Use 'High-Performance' for latency-sensitive applications")
        print(f"   💰 Consider 'Aggressive' for cost-sensitive environments")
        print(f"   🛡️ Use 'Conservative' for mission-critical applications")
    
    def generate_environment_configs(self, config: ResourceConfiguration) -> Dict[str, str]:
        """Generate environment variable configuration."""
        
        return {
            "# Resource Configuration": f"# Optimized for {config.estimated_users:,} users",
            "CONNECTION_POOL_MIN_SIZE": str(max(2, config.connection_pool_size // 3)),
            "CONNECTION_POOL_MAX_SIZE": str(config.connection_pool_size),
            "MAX_CONCURRENT_REQUESTS": str(config.max_concurrent_requests),
            "MAX_MEMORY_MB": str(config.memory_gb * 1024),
            "": "",
            "# Docker/Kubernetes Resource Limits": "",
            "# CPU_LIMIT": f"{config.cpu_cores}000m",
            "# MEMORY_LIMIT": f"{config.memory_gb}Gi",
        }

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Resource optimization calculator')
    parser.add_argument('--target-users', type=int, required=True, help='Target number of users')
    parser.add_argument('--generate-config', action='store_true', help='Generate configuration files')
    
    args = parser.parse_args()
    
    optimizer = ResourceOptimizer()
    options = optimizer.generate_scaling_options(args.target_users)
    optimizer.print_scaling_options(args.target_users, options)
    
    if args.generate_config:
        # Generate config for balanced option
        balanced_config = next(config for name, config in options if name == "Balanced")
        env_config = optimizer.generate_environment_configs(balanced_config)
        
        print(f"\n📝 Environment Configuration (Balanced Option):")
        print("-" * 50)
        for key, value in env_config.items():
            if key.startswith("#"):
                print(value)
            elif key:
                print(f"{key}={value}")
            else:
                print()
```

## 📈 Usage Pattern Analysis

### 1. Peak Load Analysis

```bash
#!/bin/bash
# peak_load_analysis.sh - Analyze usage patterns and peak loads

echo "📊 Peak Load Analysis for Snowflake MCP Server"
echo "=" * 60

METRICS_URL="http://localhost:8001/metrics"
ANALYSIS_PERIOD=${1:-"7d"}  # Default: last 7 days

# Function to get metric value
get_metric() {
    local metric_name=$1
    curl -s "$METRICS_URL" | grep "^$metric_name" | awk '{print $2}' | head -1
}

# Current snapshot
echo "📸 Current System Snapshot:"
echo "   Active connections: $(get_metric 'pool_connections_active')"
echo "   Total requests: $(get_metric 'mcp_requests_total')"
echo "   Active sessions: $(get_metric 'mcp_active_sessions_total')"

# Historical analysis (requires Prometheus)
if command -v promtool > /dev/null; then
    echo -e "\n📈 Historical Analysis (Last $ANALYSIS_PERIOD):"
    
    # Peak concurrent users
    echo "   Calculating peak usage patterns..."
    
    # This would typically query Prometheus for historical data
    # For demonstration, we'll simulate the analysis
    cat << 'EOF'
   Peak Usage Patterns:
   - Weekday peak: 2-4 PM (40-60 concurrent users)
   - Weekend peak: 10 AM-12 PM (20-30 concurrent users)
   - Daily low: 2-6 AM (5-10 concurrent users)
   - Weekly pattern: Mon-Wed highest, Fri-Sun lowest
   
   Growth Trends:
   - 15% month-over-month user growth
   - 25% increase in query complexity
   - 10% improvement in response times (optimizations)
EOF
fi

# Generate capacity recommendations
echo -e "\n💡 Capacity Recommendations:"
CURRENT_USERS=$(get_metric 'mcp_active_sessions_total' | cut -d. -f1)
PEAK_MULTIPLIER=2.5  # Peak is typically 2.5x average

if [ -n "$CURRENT_USERS" ] && [ "$CURRENT_USERS" -gt 0 ]; then
    ESTIMATED_PEAK=$((CURRENT_USERS * 250 / 100))
    echo "   Current active users: $CURRENT_USERS"
    echo "   Estimated peak load: $ESTIMATED_PEAK users"
    echo "   Recommended capacity: $((ESTIMATED_PEAK * 120 / 100)) users (20% safety margin)"
else
    echo "   Unable to determine current usage - manual analysis required"
fi

# Seasonal pattern prediction
echo -e "\n📅 Seasonal Considerations:"
MONTH=$(date +%m)
case $MONTH in
    12|01|02) echo "   Winter: Expect 10-15% higher usage (holiday projects)" ;;
    03|04|05) echo "   Spring: Expect 20% higher usage (Q1 planning)" ;;
    06|07|08) echo "   Summer: Expect 5-10% lower usage (vacation period)" ;;
    09|10|11) echo "   Fall: Expect 15-20% higher usage (Q4 sprint)" ;;
esac

echo -e "\n✅ Peak load analysis completed"
```

### 2. Cost Optimization Analysis

```python
#!/usr/bin/env python3
# cost_optimization.py - Analyze and optimize costs

import json
import csv
from datetime import datetime, timedelta
import argparse

class CostOptimizer:
    def __init__(self):
        # Cost models (example pricing)
        self.pricing = {
            "compute": {
                "small": {"cpu": 1, "memory": 2, "cost": 50},
                "medium": {"cpu": 2, "memory": 4, "cost": 100},
                "large": {"cpu": 4, "memory": 8, "cost": 200},
                "xlarge": {"cpu": 8, "memory": 16, "cost": 400}
            },
            "snowflake": {
                "warehouse_xs": {"cost_per_hour": 1.0},
                "warehouse_small": {"cost_per_hour": 2.0},
                "warehouse_medium": {"cost_per_hour": 4.0},
                "warehouse_large": {"cost_per_hour": 8.0}
            },
            "networking": {
                "data_transfer_gb": 0.05,
                "load_balancer": 20
            }
        }
    
    def analyze_current_costs(self, current_config):
        """Analyze current cost structure."""
        
        monthly_costs = {
            "compute": 0,
            "snowflake": 0,
            "networking": 0,
            "monitoring": 10,  # Base monitoring cost
            "total": 0
        }
        
        # Compute costs
        instance_type = current_config.get("instance_type", "medium")
        instance_count = current_config.get("instance_count", 1)
        monthly_costs["compute"] = (
            self.pricing["compute"][instance_type]["cost"] * instance_count
        )
        
        # Snowflake costs (estimated)
        warehouse_size = current_config.get("warehouse_size", "warehouse_small")
        avg_hours_per_day = current_config.get("usage_hours_per_day", 8)
        monthly_costs["snowflake"] = (
            self.pricing["snowflake"][warehouse_size]["cost_per_hour"] * 
            avg_hours_per_day * 30
        )
        
        # Networking costs
        data_transfer_gb = current_config.get("data_transfer_gb_per_month", 100)
        monthly_costs["networking"] = (
            data_transfer_gb * self.pricing["networking"]["data_transfer_gb"] +
            self.pricing["networking"]["load_balancer"]
        )
        
        monthly_costs["total"] = sum(monthly_costs.values())
        
        return monthly_costs
    
    def generate_optimization_scenarios(self, current_config, target_users):
        """Generate cost optimization scenarios."""
        
        scenarios = []
        
        # Scenario 1: Right-sizing
        rightsized_config = current_config.copy()
        if target_users < 25:
            rightsized_config["instance_type"] = "small"
            rightsized_config["instance_count"] = 1
        elif target_users < 75:
            rightsized_config["instance_type"] = "medium"
            rightsized_config["instance_count"] = 1
        else:
            rightsized_config["instance_type"] = "large"
            rightsized_config["instance_count"] = max(1, target_users // 50)
        
        scenarios.append(("Right-sized", rightsized_config))
        
        # Scenario 2: Cost-optimized
        cost_optimized = current_config.copy()
        cost_optimized["instance_type"] = "medium"
        cost_optimized["instance_count"] = max(1, target_users // 60)  # Higher density
        cost_optimized["warehouse_size"] = "warehouse_xs"  # Smaller warehouse
        cost_optimized["usage_hours_per_day"] = 6  # Reduced usage
        
        scenarios.append(("Cost-optimized", cost_optimized))
        
        # Scenario 3: Performance-optimized
        perf_optimized = current_config.copy()
        perf_optimized["instance_type"] = "large"
        perf_optimized["instance_count"] = max(1, target_users // 40)  # Lower density
        perf_optimized["warehouse_size"] = "warehouse_medium"  # Larger warehouse
        
        scenarios.append(("Performance-optimized", perf_optimized))
        
        return scenarios
    
    def calculate_roi(self, base_costs, optimized_costs, performance_improvement=0.0):
        """Calculate return on investment for optimization."""
        
        monthly_savings = base_costs["total"] - optimized_costs["total"]
        annual_savings = monthly_savings * 12
        
        # Factor in performance improvements (reduced downtime, faster responses)
        if performance_improvement > 0:
            # Assume 1% performance improvement = $1000 annual value
            performance_value = performance_improvement * 1000
            total_annual_value = annual_savings + performance_value
        else:
            total_annual_value = annual_savings
        
        return {
            "monthly_savings": monthly_savings,
            "annual_savings": annual_savings,
            "performance_value": performance_improvement * 1000 if performance_improvement > 0 else 0,
            "total_annual_value": total_annual_value,
            "payback_months": 0 if monthly_savings <= 0 else 1,  # Immediate for cost reductions
            "roi_percentage": (total_annual_value / base_costs["total"] / 12) * 100 if base_costs["total"] > 0 else 0
        }
    
    def print_cost_analysis(self, current_config, scenarios, target_users):
        """Print comprehensive cost analysis."""
        
        print(f"💰 Cost Optimization Analysis for {target_users:,} Users")
        print("=" * 80)
        
        # Current costs
        current_costs = self.analyze_current_costs(current_config)
        print(f"Current Monthly Costs:")
        for category, cost in current_costs.items():
            if category != "total":
                print(f"   {category.title()}: ${cost:.2f}")
        print(f"   Total: ${current_costs['total']:.2f}")
        
        print(f"\n📊 Optimization Scenarios:")
        print(f"{'Scenario':<20} {'Compute':<10} {'Snowflake':<12} {'Total':<10} {'Savings':<10} {'ROI':<8}")
        print("-" * 80)
        
        for name, config in scenarios:
            scenario_costs = self.analyze_current_costs(config)
            roi = self.calculate_roi(current_costs, scenario_costs)
            
            print(f"{name:<20} ${scenario_costs['compute']:<9.2f} "
                  f"${scenario_costs['snowflake']:<11.2f} ${scenario_costs['total']:<9.2f} "
                  f"${roi['monthly_savings']:<9.2f} {roi['roi_percentage']:<7.1f}%")
        
        # Recommendations
        best_scenario = min(scenarios, key=lambda x: self.analyze_current_costs(x[1])["total"])
        best_costs = self.analyze_current_costs(best_scenario[1])
        best_roi = self.calculate_roi(current_costs, best_costs)
        
        print(f"\n💡 Recommendations:")
        print(f"   🎯 Best scenario: {best_scenario[0]}")
        print(f"   💵 Monthly savings: ${best_roi['monthly_savings']:.2f}")
        print(f"   📈 Annual savings: ${best_roi['annual_savings']:.2f}")
        print(f"   ⚡ ROI: {best_roi['roi_percentage']:.1f}%")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Cost optimization analysis')
    parser.add_argument('--target-users', type=int, required=True, help='Target number of users')
    parser.add_argument('--current-instances', type=int, default=1, help='Current instance count')
    parser.add_argument('--instance-type', default='medium', help='Current instance type')
    
    args = parser.parse_args()
    
    # Current configuration
    current_config = {
        "instance_type": args.instance_type,
        "instance_count": args.current_instances,
        "warehouse_size": "warehouse_small",
        "usage_hours_per_day": 8,
        "data_transfer_gb_per_month": 100
    }
    
    optimizer = CostOptimizer()
    scenarios = optimizer.generate_optimization_scenarios(current_config, args.target_users)
    optimizer.print_cost_analysis(current_config, scenarios, args.target_users)
```

## 🎯 Capacity Planning Recommendations

### Small Deployment (1-25 Users)

```bash
# Recommended Configuration
CONNECTION_POOL_MIN_SIZE=2
CONNECTION_POOL_MAX_SIZE=8
MAX_CONCURRENT_REQUESTS=15
MAX_MEMORY_MB=512

# Resource Allocation
CPU: 1-2 cores
Memory: 512MB-1GB
Disk: 10GB
Network: Standard

# Estimated Cost: $50-100/month
```

### Medium Deployment (25-100 Users)

```bash
# Recommended Configuration
CONNECTION_POOL_MIN_SIZE=5
CONNECTION_POOL_MAX_SIZE=20
MAX_CONCURRENT_REQUESTS=50
MAX_MEMORY_MB=1024

# Resource Allocation
CPU: 2-4 cores
Memory: 1GB-2GB
Disk: 20GB
Network: Standard with load balancer

# Estimated Cost: $200-400/month
```

### Large Deployment (100-500 Users)

```bash
# Recommended Configuration
CONNECTION_POOL_MIN_SIZE=10
CONNECTION_POOL_MAX_SIZE=50
MAX_CONCURRENT_REQUESTS=150
MAX_MEMORY_MB=2048

# Resource Allocation
CPU: 4-8 cores
Memory: 2GB-4GB
Disk: 50GB
Network: Load balancer + CDN

# Estimated Cost: $500-1200/month
```

### Enterprise Deployment (500+ Users)

```bash
# Recommended Configuration
CONNECTION_POOL_MIN_SIZE=20
CONNECTION_POOL_MAX_SIZE=100
MAX_CONCURRENT_REQUESTS=300
MAX_MEMORY_MB=4096

# Resource Allocation
CPU: 8+ cores (distributed)
Memory: 4GB+ per instance
Disk: 100GB+
Network: Multi-region load balancing

# Estimated Cost: $1500+/month
```

## 📋 Capacity Planning Checklist

### Monthly Review
- [ ] Analyze usage patterns and trends
- [ ] Review resource utilization metrics
- [ ] Update growth projections
- [ ] Assess cost optimization opportunities
- [ ] Plan scaling activities

### Quarterly Planning
- [ ] Comprehensive capacity assessment
- [ ] Budget planning for next quarter
- [ ] Infrastructure roadmap updates
- [ ] Performance benchmarking
- [ ] Disaster recovery capacity validation

### Annual Planning
- [ ] Long-term growth strategy alignment
- [ ] Technology refresh planning
- [ ] Cost optimization program
- [ ] Capacity model validation
- [ ] Business continuity planning

---

## 📞 Support and Tools

### Capacity Planning Tools
- **baseline_measurement.sh**: Establish performance baselines
- **linear_growth_model.py**: Project linear growth patterns
- **exponential_growth_model.py**: Model exponential growth scenarios
- **resource_optimizer.py**: Optimize resource configurations
- **cost_optimization.py**: Analyze and optimize costs

### Related Documentation
- [Scaling Guide](SCALING_GUIDE.md): Detailed scaling procedures
- [Operations Runbook](OPERATIONS_RUNBOOK.md): Day-to-day operations
- [Configuration Guide](CONFIGURATION_GUIDE.md): Configuration management
- [Monitoring Setup](deploy/monitoring/): Performance monitoring

For capacity planning assistance, contact the operations team with your usage patterns and growth projections.