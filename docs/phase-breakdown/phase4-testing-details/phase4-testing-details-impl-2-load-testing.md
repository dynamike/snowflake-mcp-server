# Phase 4: Comprehensive Testing Suite Implementation Details

## Context & Overview

The architectural improvements introduce significant complexity that requires comprehensive testing to ensure reliability, performance, and correctness. The current testing is minimal and doesn't cover the new async operations, multi-client scenarios, or failure conditions.

**Current Testing Gaps:**
- Limited unit test coverage (basic connection tests only)
- No integration tests for async operations
- Missing load testing for concurrent scenarios
- No chaos engineering or failure simulation
- Insufficient performance regression testing
- No end-to-end testing with real MCP clients

**Target Architecture:**
- Comprehensive unit tests with >95% coverage
- Integration tests for all async operations and workflows
- Load testing with realistic concurrent scenarios
- Chaos engineering tests for resilience validation
- Automated regression testing with performance baselines
- End-to-end testing with multiple MCP client types

## Dependencies Required

Add to `pyproject.toml`:
```toml
[project.optional-dependencies]
testing = [
    "pytest>=7.4.0",            # Already present
    "pytest-asyncio>=0.21.0",   # Async test support
    "pytest-cov>=4.1.0",        # Coverage reporting
    "pytest-xdist>=3.3.0",      # Parallel test execution
    "pytest-benchmark>=4.0.0",   # Performance benchmarking
    "httpx>=0.25.0",            # HTTP client for testing
    "websockets>=12.0",         # WebSocket testing
    "locust>=2.17.0",           # Load testing framework
    "factory-boy>=3.3.0",       # Test data factories
    "freezegun>=1.2.0",         # Time manipulation for tests
    "responses>=0.23.0",        # HTTP request mocking
    "pytest-mock>=3.11.0",      # Enhanced mocking
]

chaos_testing = [
    "chaos-toolkit>=1.15.0",    # Chaos engineering
    "toxiproxy-python>=0.1.0",  # Network failure simulation
]
```

## Implementation Plan

### 2. Load Testing Framework {#load-testing}

**Step 1: Locust Load Testing**

Create `tests/load/locustfile.py`:

```python
"""Load testing scenarios using Locust."""

import json
import random
from locust import HttpUser, task, between


class MCPUser(HttpUser):
    """Simulated MCP client user."""
    
    wait_time = between(1, 3)  # Wait 1-3 seconds between requests
    
    def on_start(self):
        """Setup user session."""
        self.client_id = f"load_test_client_{random.randint(1000, 9999)}"
        self.databases = ["TEST_DB", "ANALYTICS_DB", "STAGING_DB"]
        self.schemas = ["PUBLIC", "STAGING", "REPORTING"]
    
    @task(3)
    def list_databases(self):
        """List databases (most common operation)."""
        payload = {
            "jsonrpc": "2.0",
            "id": f"req_{random.randint(1, 10000)}",
            "method": "list_databases",
            "params": {"_client_id": self.client_id}
        }
        
        with self.client.post("/mcp/tools/call", json=payload, catch_response=True) as response:
            if response.status_code == 200:
                result = response.json()
                if "result" in result:
                    response.success()
                else:
                    response.failure("No result in response")
            else:
                response.failure(f"HTTP {response.status_code}")
    
    @task(2)
    def list_views(self):
        """List views in database."""
        database = random.choice(self.databases)
        schema = random.choice(self.schemas)
        
        payload = {
            "jsonrpc": "2.0",
            "id": f"req_{random.randint(1, 10000)}",
            "method": "list_views", 
            "params": {
                "_client_id": self.client_id,
                "database": database,
                "schema": schema
            }
        }
        
        with self.client.post("/mcp/tools/call", json=payload, catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")
    
    @task(1)
    def execute_query(self):
        """Execute simple query."""
        database = random.choice(self.databases)
        
        queries = [
            "SELECT 1 as test_column",
            "SELECT CURRENT_DATABASE()",
            "SELECT CURRENT_TIMESTAMP()",
            f"SHOW TABLES IN {database}.PUBLIC LIMIT 5"
        ]
        
        query = random.choice(queries)
        
        payload = {
            "jsonrpc": "2.0",
            "id": f"req_{random.randint(1, 10000)}",
            "method": "execute_query",
            "params": {
                "_client_id": self.client_id,
                "query": query,
                "database": database,
                "limit": 10
            }
        }
        
        with self.client.post("/mcp/tools/call", json=payload, catch_response=True) as response:
            if response.status_code == 200:
                result = response.json()
                if "result" in result:
                    response.success()
                else:
                    response.failure("Query execution failed")
            elif response.status_code == 429:
                response.failure("Rate limited")
            else:
                response.failure(f"HTTP {response.status_code}")
    
    @task(1)
    def health_check(self):
        """Check server health."""
        with self.client.get("/health", catch_response=True) as response:
            if response.status_code == 200:
                health_data = response.json()
                if health_data.get("status") == "healthy":
                    response.success()
                else:
                    response.failure("Server unhealthy")
            else:
                response.failure(f"HTTP {response.status_code}")


class HeavyMCPUser(HttpUser):
    """Heavy usage MCP client."""
    
    wait_time = between(0.1, 0.5)  # Rapid requests
    
    def on_start(self):
        self.client_id = f"heavy_client_{random.randint(1000, 9999)}"
    
    @task
    def rapid_database_queries(self):
        """Rapid database queries to test limits."""
        payload = {
            "jsonrpc": "2.0",
            "id": f"req_{random.randint(1, 10000)}",
            "method": "execute_query",
            "params": {
                "_client_id": self.client_id,
                "query": "SELECT 1",
                "limit": 1
            }
        }
        
        self.client.post("/mcp/tools/call", json=payload)


class WebSocketMCPUser(HttpUser):
    """WebSocket-based MCP client."""
    
    def on_start(self):
        """Setup WebSocket connection."""
        import websockets
        import asyncio
        
        self.client_id = f"ws_client_{random.randint(1000, 9999)}"
        
        # Note: In real implementation, would use actual WebSocket connection
        # This is simplified for load testing framework compatibility
    
    @task
    def websocket_operation(self):
        """Simulate WebSocket operation via HTTP for load testing."""
        # Simulate WebSocket overhead with additional HTTP call
        self.client.get("/status")
        
        # Then perform actual operation
        payload = {
            "jsonrpc": "2.0",
            "id": f"ws_req_{random.randint(1, 10000)}",
            "method": "list_databases",
            "params": {"_client_id": self.client_id}
        }
        
        self.client.post("/mcp/tools/call", json=payload)
```

**Step 2: Performance Testing Script**

Create `scripts/performance_test.py`:

```python
#!/usr/bin/env python3
"""Performance testing script for MCP server."""

import asyncio
import time
import statistics
import concurrent.futures
from typing import List, Dict, Any
import aiohttp
import json


class PerformanceTester:
    """Performance testing framework."""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.results: List[Dict[str, Any]] = []
    
    async def single_request_test(self, session: aiohttp.ClientSession, request_data: Dict) -> Dict[str, Any]:
        """Single request performance test."""
        start_time = time.time()
        
        try:
            async with session.post(
                f"{self.base_url}/mcp/tools/call",
                json=request_data
            ) as response:
                result = await response.json()
                duration = time.time() - start_time
                
                return {
                    "duration": duration,
                    "status_code": response.status,
                    "success": response.status == 200 and "result" in result,
                    "response_size": len(str(result))
                }
        
        except Exception as e:
            return {
                "duration": time.time() - start_time,
                "status_code": 0,
                "success": False,
                "error": str(e)
            }
    
    async def concurrent_requests_test(self, num_requests: int, num_concurrent: int) -> Dict[str, Any]:
        """Test concurrent request performance."""
        
        async with aiohttp.ClientSession() as session:
            semaphore = asyncio.Semaphore(num_concurrent)
            
            async def limited_request(request_id: int):
                async with semaphore:
                    request_data = {
                        "jsonrpc": "2.0",
                        "id": f"perf_test_{request_id}",
                        "method": "list_databases",
                        "params": {"_client_id": f"perf_client_{request_id}"}
                    }
                    return await self.single_request_test(session, request_data)
            
            # Run concurrent requests
            start_time = time.time()
            tasks = [limited_request(i) for i in range(num_requests)]
            results = await asyncio.gather(*tasks)
            total_time = time.time() - start_time
            
            # Analyze results
            successful_results = [r for r in results if r["success"]]
            failed_results = [r for r in results if not r["success"]]
            
            durations = [r["duration"] for r in successful_results]
            
            return {
                "total_requests": num_requests,
                "concurrent_limit": num_concurrent,
                "total_time": total_time,
                "successful_requests": len(successful_results),
                "failed_requests": len(failed_results),
                "success_rate": len(successful_results) / num_requests,
                "throughput": num_requests / total_time,
                "avg_response_time": statistics.mean(durations) if durations else 0,
                "median_response_time": statistics.median(durations) if durations else 0,
                "p95_response_time": statistics.quantiles(durations, n=20)[18] if len(durations) > 20 else 0,
                "p99_response_time": statistics.quantiles(durations, n=100)[98] if len(durations) > 100 else 0
            }
    
    async def ramp_up_test(self, max_concurrent: int, step_size: int = 5, step_duration: int = 30) -> List[Dict[str, Any]]:
        """Ramp up test to find breaking point."""
        results = []
        
        for concurrent in range(step_size, max_concurrent + 1, step_size):
            print(f"Testing with {concurrent} concurrent requests...")
            
            test_result = await self.concurrent_requests_test(
                num_requests=concurrent * 10,  # 10 requests per concurrent user
                num_concurrent=concurrent
            )
            
            test_result["concurrent_users"] = concurrent
            results.append(test_result)
            
            # Stop if success rate drops below 95%
            if test_result["success_rate"] < 0.95:
                print(f"Breaking point reached at {concurrent} concurrent users")
                break
            
            await asyncio.sleep(step_duration)
        
        return results
    
    async def sustained_load_test(self, concurrent: int, duration_minutes: int = 10) -> Dict[str, Any]:
        """Sustained load test."""
        print(f"Running sustained load test: {concurrent} concurrent users for {duration_minutes} minutes")
        
        end_time = time.time() + (duration_minutes * 60)
        results = []
        
        while time.time() < end_time:
            test_result = await self.concurrent_requests_test(
                num_requests=concurrent * 5,
                num_concurrent=concurrent
            )
            results.append(test_result)
            
            await asyncio.sleep(10)  # 10 second intervals
        
        # Aggregate results
        avg_throughput = statistics.mean([r["throughput"] for r in results])
        avg_success_rate = statistics.mean([r["success_rate"] for r in results])
        avg_response_time = statistics.mean([r["avg_response_time"] for r in results])
        
        return {
            "test_duration_minutes": duration_minutes,
            "concurrent_users": concurrent,
            "total_test_cycles": len(results),
            "avg_throughput": avg_throughput,
            "avg_success_rate": avg_success_rate,
            "avg_response_time": avg_response_time,
            "detailed_results": results
        }


async def main():
    """Run performance tests."""
    tester = PerformanceTester()
    
    print("Starting MCP Server Performance Tests")
    print("=" * 50)
    
    # Test 1: Basic concurrent request test
    print("\n1. Basic Concurrent Request Test (50 requests, 10 concurrent)")
    basic_result = await tester.concurrent_requests_test(50, 10)
    print(f"Success Rate: {basic_result['success_rate']:.1%}")
    print(f"Throughput: {basic_result['throughput']:.1f} req/s")
    print(f"Average Response Time: {basic_result['avg_response_time']:.3f}s")
    print(f"95th Percentile: {basic_result['p95_response_time']:.3f}s")
    
    # Test 2: Ramp up test
    print("\n2. Ramp Up Test (finding breaking point)")
    ramp_results = await tester.ramp_up_test(max_concurrent=50, step_size=5)
    
    print("\nRamp Up Results:")
    for result in ramp_results:
        print(f"  {result['concurrent_users']} users: "
              f"{result['success_rate']:.1%} success, "
              f"{result['throughput']:.1f} req/s")
    
    # Test 3: Sustained load test (if ramp up was successful)
    if ramp_results and ramp_results[-1]['success_rate'] >= 0.95:
        optimal_concurrent = ramp_results[-1]['concurrent_users']
        print(f"\n3. Sustained Load Test ({optimal_concurrent} concurrent users, 5 minutes)")
        
        sustained_result = await tester.sustained_load_test(optimal_concurrent, 5)
        print(f"Average Success Rate: {sustained_result['avg_success_rate']:.1%}")
        print(f"Average Throughput: {sustained_result['avg_throughput']:.1f} req/s")
        print(f"Average Response Time: {sustained_result['avg_response_time']:.3f}s")
    
    print("\nPerformance tests completed!")


if __name__ == "__main__":
    asyncio.run(main())
```

