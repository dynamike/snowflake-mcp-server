"""Load testing scenarios for Snowflake MCP server."""

import asyncio
import logging
import random
import statistics
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from snowflake_mcp_server.utils.async_database import get_isolated_database_ops
from snowflake_mcp_server.utils.async_pool import (
    AsyncConnectionPool,
    ConnectionPoolConfig,
    initialize_connection_pool,
    close_connection_pool,
)
from snowflake_mcp_server.utils.request_context import request_context
from snowflake_mcp_server.utils.session_manager import get_session_manager
from snowflake_mcp_server.utils.snowflake_conn import SnowflakeConfig


@dataclass
class LoadTestResult:
    """Results from a load test scenario."""
    scenario_name: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    total_time: float
    min_response_time: float
    max_response_time: float
    avg_response_time: float
    median_response_time: float
    p95_response_time: float
    p99_response_time: float
    throughput_rps: float
    error_rate: float
    concurrent_clients: int
    
    def __str__(self) -> str:
        return (
            f"\n📊 {self.scenario_name} Results:\n"
            f"   Total Requests: {self.total_requests:,}\n"
            f"   Success Rate: {(1-self.error_rate)*100:.1f}%\n"
            f"   Throughput: {self.throughput_rps:.1f} req/s\n"
            f"   Response Times (ms):\n"
            f"     Min: {self.min_response_time*1000:.1f}\n"
            f"     Avg: {self.avg_response_time*1000:.1f}\n"
            f"     Median: {self.median_response_time*1000:.1f}\n"
            f"     95th %: {self.p95_response_time*1000:.1f}\n"
            f"     99th %: {self.p99_response_time*1000:.1f}\n"
            f"     Max: {self.max_response_time*1000:.1f}\n"
            f"   Concurrent Clients: {self.concurrent_clients}\n"
        )


class LoadTestClient:
    """Simulated client for load testing."""
    
    def __init__(self, client_id: str, scenario_config: Dict[str, Any]):
        self.client_id = client_id
        self.scenario_config = scenario_config
        self.request_count = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.response_times: List[float] = []
        self.errors: List[Exception] = []
    
    async def make_database_request(self, operation_type: str) -> Tuple[bool, float]:
        """Make a database request and return success status and response time."""
        start_time = time.time()
        
        try:
            async with request_context(
                operation_type, 
                {"load_test": True}, 
                self.client_id
            ) as ctx:
                async with get_isolated_database_ops(ctx) as db_ops:
                    # Simulate different operation types
                    if operation_type == "list_databases":
                        await db_ops.execute_query_isolated("SHOW DATABASES")
                    elif operation_type == "list_views":
                        await db_ops.execute_query_isolated("SHOW VIEWS IN DATABASE TEST_DB")
                    elif operation_type == "execute_query":
                        # Simulate various query complexities
                        complexity = random.choice(["simple", "medium", "complex"])
                        if complexity == "simple":
                            await db_ops.execute_query_isolated("SELECT 1")
                        elif complexity == "medium":
                            await db_ops.execute_query_isolated("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES")
                        else:
                            await db_ops.execute_query_isolated("SELECT * FROM INFORMATION_SCHEMA.COLUMNS LIMIT 100")
                    elif operation_type == "describe_view":
                        await db_ops.execute_query_isolated("DESCRIBE VIEW TEST_DB.PUBLIC.TEST_VIEW")
                    
                    # Add artificial delay to simulate real query processing
                    delay = self.scenario_config.get("query_delay", 0.05)
                    await asyncio.sleep(delay)
            
            response_time = time.time() - start_time
            self.response_times.append(response_time)
            self.successful_requests += 1
            return True, response_time
            
        except Exception as e:
            response_time = time.time() - start_time
            self.response_times.append(response_time)
            self.failed_requests += 1
            self.errors.append(e)
            return False, response_time
        
        finally:
            self.request_count += 1
    
    async def run_scenario(self, operations: List[str], duration: float) -> None:
        """Run load test scenario for specified duration."""
        end_time = time.time() + duration
        
        while time.time() < end_time:
            operation = random.choice(operations)
            await self.make_database_request(operation)
            
            # Add slight randomization to avoid thundering herd
            jitter = random.uniform(0.01, 0.05)
            await asyncio.sleep(jitter)


def create_mock_snowflake_environment():
    """Create comprehensive mocked Snowflake environment for load testing."""
    
    def create_mock_connection(connection_id: int = 0):
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        mock_conn.close = MagicMock()
        
        # Create mock cursor with realistic responses
        mock_cursor = MagicMock()
        mock_cursor.close = MagicMock()
        
        # Mock different query responses
        def mock_execute(query: str):
            if "SHOW DATABASES" in query.upper():
                mock_cursor.fetchall.return_value = [("DB1",), ("DB2",), ("DB3",)]
                mock_cursor.description = [("name",)]
            elif "SHOW VIEWS" in query.upper():
                mock_cursor.fetchall.return_value = [("view1",), ("view2",), ("view3",)]
                mock_cursor.description = [("name",)]
            elif "SELECT 1" in query.upper():
                mock_cursor.fetchall.return_value = [(1,)]
                mock_cursor.description = [("1",)]
            elif "COUNT(*)" in query.upper():
                mock_cursor.fetchall.return_value = [(42,)]
                mock_cursor.description = [("count",)]
            elif "INFORMATION_SCHEMA.COLUMNS" in query.upper():
                # Generate mock column data
                columns = [(f"column_{i}", "VARCHAR", f"table_{i//5}") for i in range(100)]
                mock_cursor.fetchall.return_value = columns
                mock_cursor.description = [("column_name",), ("data_type",), ("table_name",)]
            elif "DESCRIBE VIEW" in query.upper():
                mock_cursor.fetchall.return_value = [("col1", "VARCHAR"), ("col2", "INTEGER")]
                mock_cursor.description = [("name",), ("type",)]
            else:
                mock_cursor.fetchall.return_value = [("default_result",)]
                mock_cursor.description = [("result",)]
        
        mock_cursor.execute = MagicMock(side_effect=mock_execute)
        mock_cursor.fetchone = MagicMock(return_value=("single_result",))
        mock_cursor.fetchmany = MagicMock(return_value=[("limited_result",)])
        
        mock_conn.cursor = MagicMock(return_value=mock_cursor)
        return mock_conn


@pytest.mark.asyncio
async def test_low_concurrency_baseline():
    """Baseline test with low concurrency to establish performance baseline."""
    
    # Setup mock environment
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create, \
         patch('snowflake_mcp_server.main.initialize_async_infrastructure') as mock_init:
        
        # Create multiple mock connections
        mock_connections = [create_mock_snowflake_environment() for _ in range(5)]
        mock_create.side_effect = mock_connections
        mock_init.return_value = None
        
        # Initialize async infrastructure
        await mock_init()
        
        # Test configuration
        scenario_config = {
            "query_delay": 0.02,  # 20ms simulated query time
            "operations": ["list_databases", "execute_query", "list_views"]
        }
        
        concurrent_clients = 5
        test_duration = 2.0  # 2 seconds
        
        # Create clients
        clients = [
            LoadTestClient(f"baseline_client_{i}", scenario_config)
            for i in range(concurrent_clients)
        ]
        
        # Run load test
        start_time = time.time()
        tasks = [
            client.run_scenario(scenario_config["operations"], test_duration)
            for client in clients
        ]
        
        await asyncio.gather(*tasks)
        end_time = time.time()
        
        # Collect results
        total_requests = sum(client.request_count for client in clients)
        successful_requests = sum(client.successful_requests for client in clients)
        failed_requests = sum(client.failed_requests for client in clients)
        all_response_times = []
        for client in clients:
            all_response_times.extend(client.response_times)
        
        result = LoadTestResult(
            scenario_name="Low Concurrency Baseline",
            total_requests=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            total_time=end_time - start_time,
            min_response_time=min(all_response_times) if all_response_times else 0,
            max_response_time=max(all_response_times) if all_response_times else 0,
            avg_response_time=statistics.mean(all_response_times) if all_response_times else 0,
            median_response_time=statistics.median(all_response_times) if all_response_times else 0,
            p95_response_time=statistics.quantiles(all_response_times, n=20)[18] if len(all_response_times) > 20 else 0,
            p99_response_time=statistics.quantiles(all_response_times, n=100)[98] if len(all_response_times) > 100 else 0,
            throughput_rps=total_requests / (end_time - start_time),
            error_rate=failed_requests / total_requests if total_requests > 0 else 0,
            concurrent_clients=concurrent_clients
        )
        
        print(result)
        
        # Assertions for baseline performance
        assert result.error_rate < 0.05, f"Error rate too high: {result.error_rate:.2%}"
        assert result.throughput_rps > 10, f"Throughput too low: {result.throughput_rps:.1f} req/s"
        assert result.avg_response_time < 0.5, f"Average response time too high: {result.avg_response_time:.3f}s"


@pytest.mark.asyncio
async def test_medium_concurrency_scaling():
    """Test medium concurrency to verify scaling behavior."""
    
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create, \
         patch('snowflake_mcp_server.main.initialize_async_infrastructure') as mock_init:
        
        # Create more connections for higher concurrency
        mock_connections = [create_mock_snowflake_environment() for _ in range(15)]
        mock_create.side_effect = mock_connections
        mock_init.return_value = None
        
        await mock_init()
        
        scenario_config = {
            "query_delay": 0.03,  # Slightly higher delay
            "operations": ["list_databases", "execute_query", "list_views", "describe_view"]
        }
        
        concurrent_clients = 15
        test_duration = 3.0
        
        clients = [
            LoadTestClient(f"medium_client_{i}", scenario_config)
            for i in range(concurrent_clients)
        ]
        
        start_time = time.time()
        tasks = [
            client.run_scenario(scenario_config["operations"], test_duration)
            for client in clients
        ]
        
        await asyncio.gather(*tasks)
        end_time = time.time()
        
        # Collect and analyze results
        total_requests = sum(client.request_count for client in clients)
        successful_requests = sum(client.successful_requests for client in clients)
        failed_requests = sum(client.failed_requests for client in clients)
        all_response_times = []
        for client in clients:
            all_response_times.extend(client.response_times)
        
        result = LoadTestResult(
            scenario_name="Medium Concurrency Scaling",
            total_requests=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            total_time=end_time - start_time,
            min_response_time=min(all_response_times) if all_response_times else 0,
            max_response_time=max(all_response_times) if all_response_times else 0,
            avg_response_time=statistics.mean(all_response_times) if all_response_times else 0,
            median_response_time=statistics.median(all_response_times) if all_response_times else 0,
            p95_response_time=statistics.quantiles(all_response_times, n=20)[18] if len(all_response_times) > 20 else 0,
            p99_response_time=statistics.quantiles(all_response_times, n=100)[98] if len(all_response_times) > 100 else 0,
            throughput_rps=total_requests / (end_time - start_time),
            error_rate=failed_requests / total_requests if total_requests > 0 else 0,
            concurrent_clients=concurrent_clients
        )
        
        print(result)
        
        # Assertions for medium concurrency
        assert result.error_rate < 0.1, f"Error rate too high: {result.error_rate:.2%}"
        assert result.throughput_rps > 20, f"Throughput should scale: {result.throughput_rps:.1f} req/s"
        assert result.p95_response_time < 1.0, f"95th percentile too high: {result.p95_response_time:.3f}s"


@pytest.mark.asyncio
async def test_high_concurrency_stress():
    """Stress test with high concurrency to find breaking points."""
    
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create, \
         patch('snowflake_mcp_server.main.initialize_async_infrastructure') as mock_init:
        
        # Create maximum connections for stress testing
        mock_connections = [create_mock_snowflake_environment() for _ in range(25)]
        mock_create.side_effect = mock_connections
        mock_init.return_value = None
        
        await mock_init()
        
        scenario_config = {
            "query_delay": 0.05,  # Higher delay to simulate complex queries
            "operations": ["list_databases", "execute_query", "list_views", "describe_view"]
        }
        
        concurrent_clients = 25
        test_duration = 4.0
        
        clients = [
            LoadTestClient(f"stress_client_{i}", scenario_config)
            for i in range(concurrent_clients)
        ]
        
        start_time = time.time()
        tasks = [
            client.run_scenario(scenario_config["operations"], test_duration)
            for client in clients
        ]
        
        # Use return_exceptions to handle any failures gracefully
        results = await asyncio.gather(*tasks, return_exceptions=True)
        end_time = time.time()
        
        # Check for any task exceptions
        exceptions = [r for r in results if isinstance(r, Exception)]
        if exceptions:
            print(f"⚠️  {len(exceptions)} tasks failed with exceptions")
        
        # Collect results from successful clients
        total_requests = sum(client.request_count for client in clients)
        successful_requests = sum(client.successful_requests for client in clients)
        failed_requests = sum(client.failed_requests for client in clients)
        all_response_times = []
        for client in clients:
            all_response_times.extend(client.response_times)
        
        result = LoadTestResult(
            scenario_name="High Concurrency Stress Test",
            total_requests=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            total_time=end_time - start_time,
            min_response_time=min(all_response_times) if all_response_times else 0,
            max_response_time=max(all_response_times) if all_response_times else 0,
            avg_response_time=statistics.mean(all_response_times) if all_response_times else 0,
            median_response_time=statistics.median(all_response_times) if all_response_times else 0,
            p95_response_time=statistics.quantiles(all_response_times, n=20)[18] if len(all_response_times) > 20 else 0,
            p99_response_time=statistics.quantiles(all_response_times, n=100)[98] if len(all_response_times) > 100 else 0,
            throughput_rps=total_requests / (end_time - start_time),
            error_rate=failed_requests / total_requests if total_requests > 0 else 0,
            concurrent_clients=concurrent_clients
        )
        
        print(result)
        
        # More lenient assertions for stress test
        assert result.error_rate < 0.2, f"Error rate acceptable for stress test: {result.error_rate:.2%}"
        assert result.throughput_rps > 15, f"Should maintain reasonable throughput: {result.throughput_rps:.1f} req/s"
        assert total_requests > 100, f"Should complete significant work: {total_requests} requests"


@pytest.mark.asyncio
async def test_sustained_load():
    """Test sustained load over longer duration."""
    
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create, \
         patch('snowflake_mcp_server.main.initialize_async_infrastructure') as mock_init:
        
        mock_connections = [create_mock_snowflake_environment() for _ in range(10)]
        mock_create.side_effect = mock_connections
        mock_init.return_value = None
        
        await mock_init()
        
        scenario_config = {
            "query_delay": 0.04,
            "operations": ["list_databases", "execute_query", "list_views", "describe_view"]
        }
        
        concurrent_clients = 10
        test_duration = 8.0  # Longer sustained test
        
        clients = [
            LoadTestClient(f"sustained_client_{i}", scenario_config)
            for i in range(concurrent_clients)
        ]
        
        # Track performance over time
        performance_samples = []
        sample_interval = 2.0  # Sample every 2 seconds
        
        async def performance_monitor():
            """Monitor performance during the test."""
            start_time = time.time()
            while time.time() - start_time < test_duration:
                await asyncio.sleep(sample_interval)
                
                # Sample current performance
                current_requests = sum(client.request_count for client in clients)
                current_successful = sum(client.successful_requests for client in clients)
                current_time = time.time() - start_time
                
                sample = {
                    "timestamp": current_time,
                    "requests": current_requests,
                    "successful": current_successful,
                    "throughput": current_requests / current_time if current_time > 0 else 0
                }
                performance_samples.append(sample)
        
        start_time = time.time()
        
        # Run clients and monitor concurrently
        client_tasks = [
            client.run_scenario(scenario_config["operations"], test_duration)
            for client in clients
        ]
        monitor_task = performance_monitor()
        
        await asyncio.gather(*client_tasks, monitor_task)
        end_time = time.time()
        
        # Analyze sustained performance
        total_requests = sum(client.request_count for client in clients)
        successful_requests = sum(client.successful_requests for client in clients)
        failed_requests = sum(client.failed_requests for client in clients)
        all_response_times = []
        for client in clients:
            all_response_times.extend(client.response_times)
        
        result = LoadTestResult(
            scenario_name="Sustained Load Test",
            total_requests=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            total_time=end_time - start_time,
            min_response_time=min(all_response_times) if all_response_times else 0,
            max_response_time=max(all_response_times) if all_response_times else 0,
            avg_response_time=statistics.mean(all_response_times) if all_response_times else 0,
            median_response_time=statistics.median(all_response_times) if all_response_times else 0,
            p95_response_time=statistics.quantiles(all_response_times, n=20)[18] if len(all_response_times) > 20 else 0,
            p99_response_time=statistics.quantiles(all_response_times, n=100)[98] if len(all_response_times) > 100 else 0,
            throughput_rps=total_requests / (end_time - start_time),
            error_rate=failed_requests / total_requests if total_requests > 0 else 0,
            concurrent_clients=concurrent_clients
        )
        
        print(result)
        
        # Analyze throughput stability
        if len(performance_samples) > 1:
            throughputs = [sample["throughput"] for sample in performance_samples[1:]]  # Skip first sample
            throughput_variance = statistics.variance(throughputs) if len(throughputs) > 1 else 0
            print(f"   Throughput Stability: σ² = {throughput_variance:.2f}")
            
            # Throughput should remain relatively stable
            assert throughput_variance < 100, f"Throughput too variable: {throughput_variance:.2f}"
        
        # Sustained performance assertions
        assert result.error_rate < 0.1, f"Error rate should be low for sustained load: {result.error_rate:.2%}"
        assert result.throughput_rps > 12, f"Should maintain good throughput: {result.throughput_rps:.1f} req/s"
        assert total_requests > 200, f"Should complete substantial work: {total_requests} requests"


@pytest.mark.asyncio
async def test_burst_load_pattern():
    """Test burst load patterns simulating real-world usage spikes."""
    
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create, \
         patch('snowflake_mcp_server.main.initialize_async_infrastructure') as mock_init:
        
        mock_connections = [create_mock_snowflake_environment() for _ in range(20)]
        mock_create.side_effect = mock_connections
        mock_init.return_value = None
        
        await mock_init()
        
        scenario_config = {
            "query_delay": 0.03,
            "operations": ["list_databases", "execute_query", "list_views"]
        }
        
        # Simulate burst pattern: ramp up, peak, ramp down
        burst_pattern = [
            (2, 1.0),   # Start with 2 clients for 1 second
            (8, 1.5),   # Ramp to 8 clients for 1.5 seconds
            (20, 2.0),  # Peak at 20 clients for 2 seconds
            (8, 1.5),   # Ramp down to 8 clients for 1.5 seconds
            (2, 1.0),   # End with 2 clients for 1 second
        ]
        
        all_clients = []
        phase_results = []
        
        for phase_num, (client_count, duration) in enumerate(burst_pattern):
            print(f"\n🔥 Burst Phase {phase_num + 1}: {client_count} clients for {duration}s")
            
            # Create clients for this phase
            phase_clients = [
                LoadTestClient(f"burst_p{phase_num}_c{i}", scenario_config)
                for i in range(client_count)
            ]
            
            start_time = time.time()
            tasks = [
                client.run_scenario(scenario_config["operations"], duration)
                for client in phase_clients
            ]
            
            await asyncio.gather(*tasks)
            end_time = time.time()
            
            # Collect phase results
            phase_requests = sum(client.request_count for client in phase_clients)
            phase_successful = sum(client.successful_requests for client in phase_clients)
            phase_failed = sum(client.failed_requests for client in phase_clients)
            phase_response_times = []
            for client in phase_clients:
                phase_response_times.extend(client.response_times)
            
            phase_result = {
                "phase": phase_num + 1,
                "clients": client_count,
                "duration": duration,
                "requests": phase_requests,
                "successful": phase_successful,
                "failed": phase_failed,
                "throughput": phase_requests / (end_time - start_time),
                "avg_response_time": statistics.mean(phase_response_times) if phase_response_times else 0,
                "error_rate": phase_failed / phase_requests if phase_requests > 0 else 0
            }
            
            phase_results.append(phase_result)
            all_clients.extend(phase_clients)
            
            print(f"   Phase {phase_num + 1} Results: {phase_requests} requests, "
                  f"{phase_result['throughput']:.1f} req/s, "
                  f"{phase_result['error_rate']:.1%} error rate")
        
        # Overall burst test analysis
        total_requests = sum(client.request_count for client in all_clients)
        total_successful = sum(client.successful_requests for client in all_clients)
        total_failed = sum(client.failed_requests for client in all_clients)
        
        print(f"\n🎯 Burst Load Test Summary:")
        print(f"   Total Requests: {total_requests:,}")
        print(f"   Overall Success Rate: {(total_successful/total_requests)*100:.1f}%")
        print(f"   Peak Phase Throughput: {max(p['throughput'] for p in phase_results):.1f} req/s")
        
        # Assertions for burst handling
        overall_error_rate = total_failed / total_requests if total_requests > 0 else 0
        assert overall_error_rate < 0.15, f"Overall error rate too high: {overall_error_rate:.2%}"
        
        # Peak phase should handle load reasonably well
        peak_phase = phase_results[2]  # 20 clients phase
        assert peak_phase["error_rate"] < 0.25, f"Peak phase error rate too high: {peak_phase['error_rate']:.2%}"
        assert peak_phase["throughput"] > 25, f"Peak throughput too low: {peak_phase['throughput']:.1f} req/s"


@pytest.mark.asyncio
async def test_connection_pool_stress():
    """Specific test for connection pool behavior under stress."""
    
    config = SnowflakeConfig(
        account="test", user="test", password="test",
        warehouse="test", database="test", schema_name="test"
    )
    
    pool_config = ConnectionPoolConfig(
        min_size=5,
        max_size=15,
        connection_timeout=2.0,
        retry_attempts=3
    )
    
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create:
        # Create mock connections that simulate some failures
        mock_connections = []
        for i in range(25):  # More than max pool size
            mock_conn = create_mock_snowflake_environment()
            # Simulate some connection failures (10% failure rate)
            if i % 10 == 0:
                mock_conn.is_closed.return_value = True
            mock_connections.append(mock_conn)
        
        mock_create.side_effect = mock_connections
        
        pool = AsyncConnectionPool(config, pool_config)
        await pool.initialize()
        
        # Stress test the pool
        async def pool_stress_operation(operation_id: int, duration: float):
            """Stress operation that uses pool connections."""
            end_time = time.time() + duration
            operation_count = 0
            
            while time.time() < end_time:
                try:
                    async with pool.acquire() as conn:
                        # Simulate database work
                        await asyncio.sleep(0.02)
                        operation_count += 1
                except Exception as e:
                    # Track connection pool errors
                    print(f"Pool error in operation {operation_id}: {e}")
                
                await asyncio.sleep(0.01)  # Brief pause
            
            return operation_count
        
        # Run many concurrent operations
        concurrent_operations = 30  # More than max pool size
        operation_duration = 3.0
        
        start_time = time.time()
        tasks = [
            pool_stress_operation(i, operation_duration)
            for i in range(concurrent_operations)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        end_time = time.time()
        
        # Analyze pool stress results
        successful_operations = [r for r in results if isinstance(r, int)]
        failed_operations = [r for r in results if isinstance(r, Exception)]
        
        total_operations = sum(successful_operations)
        
        print(f"\n🏊 Connection Pool Stress Test Results:")
        print(f"   Concurrent Operations: {concurrent_operations}")
        print(f"   Successful Tasks: {len(successful_operations)}")
        print(f"   Failed Tasks: {len(failed_operations)}")
        print(f"   Total DB Operations: {total_operations}")
        print(f"   Pool Stats: {pool.get_stats()}")
        print(f"   Operations/sec: {total_operations / (end_time - start_time):.1f}")
        
        # Pool should handle stress reasonably well
        assert len(successful_operations) > concurrent_operations * 0.8, "Too many task failures"
        assert total_operations > 200, f"Should complete substantial operations: {total_operations}"
        assert pool.total_connection_count <= pool_config.max_size, "Pool size exceeded"
        
        await pool.close()


if __name__ == "__main__":
    # Run specific load test scenarios
    pytest.main([__file__, "-v", "-s", "--tb=short", "-k", "test_low_concurrency_baseline"])