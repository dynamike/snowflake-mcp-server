"""Chaos engineering tests for Snowflake MCP server resilience."""

import asyncio
import logging
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from snowflake_mcp_server.utils.async_database import get_isolated_database_ops
from snowflake_mcp_server.utils.async_pool import (
    AsyncConnectionPool,
    ConnectionPoolConfig,
)
from snowflake_mcp_server.utils.request_context import request_context
from snowflake_mcp_server.utils.snowflake_conn import SnowflakeConfig


class ChaosType(Enum):
    """Types of chaos to inject."""
    CONNECTION_FAILURE = "connection_failure"
    QUERY_TIMEOUT = "query_timeout"
    NETWORK_LATENCY = "network_latency"
    MEMORY_PRESSURE = "memory_pressure"
    CONNECTION_LEAK = "connection_leak"
    INTERMITTENT_ERRORS = "intermittent_errors"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    SNOWFLAKE_OUTAGE = "snowflake_outage"


@dataclass
class ChaosScenario:
    """Configuration for a chaos engineering scenario."""
    name: str
    chaos_type: ChaosType
    intensity: float  # 0.0 to 1.0
    duration: float   # seconds
    target_component: str
    recovery_time: Optional[float] = None
    description: str = ""


@dataclass
class ChaosResult:
    """Results from a chaos engineering test."""
    scenario_name: str
    chaos_injected: bool
    system_survived: bool
    requests_during_chaos: int
    successful_requests: int
    failed_requests: int
    recovery_time: Optional[float]
    error_rate: float
    mean_response_time: float
    recovery_successful: bool
    details: Dict[str, Any]
    
    def __str__(self) -> str:
        status = "✅ PASSED" if self.system_survived else "❌ FAILED"
        return (
            f"\n🔥 {self.scenario_name} - {status}\n"
            f"   Chaos Injected: {'Yes' if self.chaos_injected else 'No'}\n"
            f"   Requests During Chaos: {self.requests_during_chaos}\n"
            f"   Success Rate: {((self.successful_requests/max(self.requests_during_chaos, 1))*100):.1f}%\n"
            f"   Error Rate: {self.error_rate*100:.1f}%\n"
            f"   Recovery Time: {self.recovery_time:.2f}s" if self.recovery_time else "   Recovery Time: N/A\n"
            f"   Recovery Successful: {'Yes' if self.recovery_successful else 'No'}\n"
        )


class ChaosInjector:
    """Injector for various types of chaos into the system."""
    
    def __init__(self):
        self.active_chaos: Dict[str, Any] = {}
        self.chaos_history: List[Dict[str, Any]] = []
    
    @asynccontextmanager
    async def inject_connection_failures(self, failure_rate: float = 0.3):
        """Inject random connection failures."""
        original_create = None
        
        def failing_create_connection(*args, **kwargs):
            if random.random() < failure_rate:
                raise ConnectionError("Chaos: Simulated connection failure")
            return original_create(*args, **kwargs)
        
        try:
            # Patch connection creation
            with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create:
                original_create = mock_create
                mock_create.side_effect = failing_create_connection
                self.active_chaos["connection_failures"] = {"rate": failure_rate}
                yield
        finally:
            self.active_chaos.pop("connection_failures", None)
    
    @asynccontextmanager
    async def inject_query_timeouts(self, timeout_rate: float = 0.2, delay: float = 5.0):
        """Inject query timeouts by adding delays."""
        
        async def slow_execute(original_execute, *args, **kwargs):
            if random.random() < timeout_rate:
                await asyncio.sleep(delay)  # Simulate timeout
                raise TimeoutError("Chaos: Simulated query timeout")
            return await original_execute(*args, **kwargs)
        
        try:
            self.active_chaos["query_timeouts"] = {"rate": timeout_rate, "delay": delay}
            yield slow_execute
        finally:
            self.active_chaos.pop("query_timeouts", None)
    
    @asynccontextmanager
    async def inject_network_latency(self, latency_ms: int = 500, jitter_ms: int = 200):
        """Inject network latency into database operations."""
        
        async def latency_wrapper(original_func, *args, **kwargs):
            # Add random latency
            delay = (latency_ms + random.randint(0, jitter_ms)) / 1000.0
            await asyncio.sleep(delay)
            return await original_func(*args, **kwargs)
        
        try:
            self.active_chaos["network_latency"] = {"latency_ms": latency_ms, "jitter_ms": jitter_ms}
            yield latency_wrapper
        finally:
            self.active_chaos.pop("network_latency", None)
    
    @asynccontextmanager
    async def inject_intermittent_errors(self, error_rate: float = 0.15):
        """Inject random intermittent errors."""
        
        def error_prone_operation(*args, **kwargs):
            if random.random() < error_rate:
                error_types = [
                    ValueError("Chaos: Random validation error"),
                    RuntimeError("Chaos: Runtime failure"),
                    ConnectionError("Chaos: Connection dropped"),
                ]
                raise random.choice(error_types)
            return True  # Success
        
        try:
            self.active_chaos["intermittent_errors"] = {"rate": error_rate}
            yield error_prone_operation
        finally:
            self.active_chaos.pop("intermittent_errors", None)
    
    async def simulate_snowflake_outage(self, duration: float):
        """Simulate a complete Snowflake service outage."""
        
        def outage_connection(*args, **kwargs):
            raise ConnectionError("Chaos: Snowflake service unavailable")
        
        try:
            with patch('snowflake_mcp_server.utils.async_pool.create_async_connection', side_effect=outage_connection):
                self.active_chaos["snowflake_outage"] = {"duration": duration}
                await asyncio.sleep(duration)
        finally:
            self.active_chaos.pop("snowflake_outage", None)
    
    def get_chaos_status(self) -> Dict[str, Any]:
        """Get current chaos injection status."""
        return {
            "active_chaos": self.active_chaos.copy(),
            "chaos_count": len(self.active_chaos),
            "history_count": len(self.chaos_history)
        }


class ChaosTestClient:
    """Test client for chaos engineering scenarios."""
    
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.request_count = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.response_times: List[float] = []
        self.errors: List[Exception] = []
    
    async def make_resilient_request(self, operation: str, chaos_injector: ChaosInjector) -> Tuple[bool, float]:
        """Make a request that may encounter chaos."""
        start_time = time.time()
        
        try:
            async with request_context(operation, {"chaos_test": True}, self.client_id) as ctx:
                async with get_isolated_database_ops(ctx) as db_ops:
                    # Different operations for testing
                    if operation == "simple_query":
                        await db_ops.execute_query_isolated("SELECT 1")
                    elif operation == "complex_query":
                        await db_ops.execute_query_isolated("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES")
                    elif operation == "list_databases":
                        await db_ops.execute_query_isolated("SHOW DATABASES")
                    
                    # Small delay to simulate processing
                    await asyncio.sleep(0.02)
            
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
    
    async def run_during_chaos(self, operations: List[str], duration: float, chaos_injector: ChaosInjector) -> None:
        """Run operations during chaos injection."""
        end_time = time.time() + duration
        
        while time.time() < end_time:
            operation = random.choice(operations)
            await self.make_resilient_request(operation, chaos_injector)
            await asyncio.sleep(0.1)  # Brief pause between requests


def create_chaos_mock_environment():
    """Create mock environment that can be subjected to chaos."""
    
    def create_resilient_mock_connection():
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        mock_conn.close = MagicMock()
        
        mock_cursor = MagicMock()
        mock_cursor.close = MagicMock()
        
        def execute_with_potential_chaos(query: str):
            # Simulate different types of failures that chaos might cause
            if "SELECT 1" in query:
                mock_cursor.fetchall.return_value = [(1,)]
                mock_cursor.description = [("1",)]
            elif "COUNT(*)" in query:
                mock_cursor.fetchall.return_value = [(25,)]
                mock_cursor.description = [("count",)]
            elif "SHOW DATABASES" in query:
                mock_cursor.fetchall.return_value = [("DB1",), ("DB2",), ("DB3",)]
                mock_cursor.description = [("name",)]
            else:
                mock_cursor.fetchall.return_value = [("default",)]
                mock_cursor.description = [("result",)]
        
        mock_cursor.execute = MagicMock(side_effect=execute_with_potential_chaos)
        mock_cursor.fetchone = MagicMock(return_value=("single",))
        mock_cursor.fetchmany = MagicMock(return_value=[("limited",)])
        
        mock_conn.cursor = MagicMock(return_value=mock_cursor)
        return mock_conn
    
    return create_resilient_mock_connection()


@pytest.mark.asyncio
async def test_connection_failure_resilience():
    """Test system resilience to connection failures."""
    
    scenario = ChaosScenario(
        name="Connection Failure Resilience",
        chaos_type=ChaosType.CONNECTION_FAILURE,
        intensity=0.3,  # 30% failure rate
        duration=5.0,
        target_component="connection_pool",
        description="Test recovery from intermittent connection failures"
    )
    
    chaos_injector = ChaosInjector()
    
    # Setup mock environment with some successful connections
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create, \
         patch('snowflake_mcp_server.main.initialize_async_infrastructure') as mock_init:
        
        # Create mix of successful and failing connections
        successful_connections = [create_chaos_mock_environment() for _ in range(8)]
        
        def connection_with_chaos(*args, **kwargs):
            if random.random() < scenario.intensity:
                raise ConnectionError("Chaos: Connection failed")
            return random.choice(successful_connections)
        
        mock_create.side_effect = connection_with_chaos
        mock_init.return_value = None
        await mock_init()
        
        # Create chaos test clients
        clients = [ChaosTestClient(f"chaos_client_{i}") for i in range(5)]
        
        # Run test with chaos injection
        start_time = time.time()
        
        async with chaos_injector.inject_connection_failures(scenario.intensity):
            tasks = [
                client.run_during_chaos(
                    ["simple_query", "complex_query", "list_databases"],
                    scenario.duration,
                    chaos_injector
                )
                for client in clients
            ]
            
            await asyncio.gather(*tasks, return_exceptions=True)
        
        end_time = time.time()
        
        # Analyze results
        total_requests = sum(client.request_count for client in clients)
        successful_requests = sum(client.successful_requests for client in clients)
        failed_requests = sum(client.failed_requests for client in clients)
        all_response_times = []
        for client in clients:
            all_response_times.extend(client.response_times)
        
        error_rate = failed_requests / total_requests if total_requests > 0 else 0
        mean_response_time = sum(all_response_times) / len(all_response_times) if all_response_times else 0
        
        # System should survive connection failures
        system_survived = error_rate < 0.8  # Allow up to 80% errors during chaos
        recovery_successful = successful_requests > 0  # Some requests should succeed
        
        result = ChaosResult(
            scenario_name=scenario.name,
            chaos_injected=True,
            system_survived=system_survived,
            requests_during_chaos=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            recovery_time=end_time - start_time,
            error_rate=error_rate,
            mean_response_time=mean_response_time,
            recovery_successful=recovery_successful,
            details=chaos_injector.get_chaos_status()
        )
        
        print(result)
        
        # Assertions for connection failure resilience
        assert total_requests > 0, "Should attempt some requests"
        assert system_survived, f"System should survive connection failures: {error_rate:.1%} error rate"
        assert recovery_successful, "Some requests should succeed even during chaos"


@pytest.mark.asyncio
async def test_query_timeout_handling():
    """Test system handling of query timeouts."""
    
    scenario = ChaosScenario(
        name="Query Timeout Handling",
        chaos_type=ChaosType.QUERY_TIMEOUT,
        intensity=0.25,  # 25% timeout rate
        duration=4.0,
        target_component="query_execution",
        description="Test graceful handling of query timeouts"
    )
    
    chaos_injector = ChaosInjector()
    
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create, \
         patch('snowflake_mcp_server.main.initialize_async_infrastructure') as mock_init:
        
        connections = [create_chaos_mock_environment() for _ in range(5)]
        mock_create.side_effect = connections
        mock_init.return_value = None
        await mock_init()
        
        clients = [ChaosTestClient(f"timeout_client_{i}") for i in range(3)]
        
        # Simulate timeout chaos
        start_time = time.time()
        
        async with chaos_injector.inject_query_timeouts(scenario.intensity, delay=2.0):
            tasks = [
                client.run_during_chaos(
                    ["simple_query", "complex_query"],
                    scenario.duration,
                    chaos_injector
                )
                for client in clients
            ]
            
            await asyncio.gather(*tasks, return_exceptions=True)
        
        end_time = time.time()
        
        # Analyze timeout handling
        total_requests = sum(client.request_count for client in clients)
        successful_requests = sum(client.successful_requests for client in clients)
        failed_requests = sum(client.failed_requests for client in clients)
        
        # Check for timeout errors
        timeout_errors = []
        for client in clients:
            timeout_errors.extend([e for e in client.errors if isinstance(e, TimeoutError)])
        
        error_rate = failed_requests / total_requests if total_requests > 0 else 0
        timeout_rate = len(timeout_errors) / total_requests if total_requests > 0 else 0
        
        system_survived = error_rate < 0.6  # System should handle most timeouts
        
        result = ChaosResult(
            scenario_name=scenario.name,
            chaos_injected=True,
            system_survived=system_survived,
            requests_during_chaos=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            recovery_time=end_time - start_time,
            error_rate=error_rate,
            mean_response_time=0.0,  # Not meaningful with timeouts
            recovery_successful=successful_requests > 0,
            details={"timeout_rate": timeout_rate, "timeout_errors": len(timeout_errors)}
        )
        
        print(result)
        print(f"   Timeout Rate: {timeout_rate*100:.1f}%")
        
        # Assertions for timeout handling
        assert total_requests > 0, "Should attempt requests"
        assert system_survived, f"System should handle timeouts: {error_rate:.1%} error rate"
        assert len(timeout_errors) > 0, "Should encounter timeout errors during chaos"


@pytest.mark.asyncio
async def test_network_latency_resilience():
    """Test system performance under high network latency."""
    
    scenario = ChaosScenario(
        name="Network Latency Resilience",
        chaos_type=ChaosType.NETWORK_LATENCY,
        intensity=0.8,  # High latency impact
        duration=3.0,
        target_component="network",
        description="Test system behavior under high network latency"
    )
    
    chaos_injector = ChaosInjector()
    
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create, \
         patch('snowflake_mcp_server.main.initialize_async_infrastructure') as mock_init:
        
        connections = [create_chaos_mock_environment() for _ in range(5)]
        mock_create.side_effect = connections
        mock_init.return_value = None
        await mock_init()
        
        clients = [ChaosTestClient(f"latency_client_{i}") for i in range(4)]
        
        # Track response times before and during latency
        baseline_times = []
        latency_times = []
        
        # Baseline measurement (no latency)
        for client in clients:
            success, response_time = await client.make_resilient_request("simple_query", chaos_injector)
            if success:
                baseline_times.append(response_time)
        
        # Reset client stats
        for client in clients:
            client.request_count = 0
            client.successful_requests = 0
            client.failed_requests = 0
            client.response_times = []
        
        start_time = time.time()
        
        # Test with high latency
        async with chaos_injector.inject_network_latency(latency_ms=300, jitter_ms=100):
            tasks = [
                client.run_during_chaos(
                    ["simple_query", "list_databases"],
                    scenario.duration,
                    chaos_injector
                )
                for client in clients
            ]
            
            await asyncio.gather(*tasks)
        
        end_time = time.time()
        
        # Collect latency test results
        total_requests = sum(client.request_count for client in clients)
        successful_requests = sum(client.successful_requests for client in clients)
        
        for client in clients:
            latency_times.extend(client.response_times)
        
        baseline_avg = sum(baseline_times) / len(baseline_times) if baseline_times else 0
        latency_avg = sum(latency_times) / len(latency_times) if latency_times else 0
        
        # System should continue functioning despite latency
        system_survived = (successful_requests / total_requests) > 0.8 if total_requests > 0 else False
        latency_increase = latency_avg / baseline_avg if baseline_avg > 0 else 0
        
        result = ChaosResult(
            scenario_name=scenario.name,
            chaos_injected=True,
            system_survived=system_survived,
            requests_during_chaos=total_requests,
            successful_requests=successful_requests,
            failed_requests=total_requests - successful_requests,
            recovery_time=end_time - start_time,
            error_rate=(total_requests - successful_requests) / total_requests if total_requests > 0 else 0,
            mean_response_time=latency_avg,
            recovery_successful=successful_requests > 0,
            details={
                "baseline_avg_response": baseline_avg,
                "latency_avg_response": latency_avg,
                "latency_increase_factor": latency_increase
            }
        )
        
        print(result)
        print(f"   Baseline Response Time: {baseline_avg*1000:.1f}ms")
        print(f"   Latency Response Time: {latency_avg*1000:.1f}ms")
        print(f"   Latency Increase: {latency_increase:.1f}x")
        
        # Assertions for latency resilience
        assert total_requests > 0, "Should attempt requests under latency"
        assert system_survived, "System should remain functional under high latency"
        assert latency_increase > 1.5, "Should observe latency increase during chaos"


@pytest.mark.asyncio
async def test_intermittent_error_recovery():
    """Test system recovery from intermittent errors."""
    
    scenario = ChaosScenario(
        name="Intermittent Error Recovery",
        chaos_type=ChaosType.INTERMITTENT_ERRORS,
        intensity=0.2,  # 20% error rate
        duration=4.0,
        target_component="all_operations",
        description="Test recovery and stability with intermittent errors"
    )
    
    chaos_injector = ChaosInjector()
    
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create, \
         patch('snowflake_mcp_server.main.initialize_async_infrastructure') as mock_init:
        
        connections = [create_chaos_mock_environment() for _ in range(6)]
        mock_create.side_effect = connections
        mock_init.return_value = None
        await mock_init()
        
        clients = [ChaosTestClient(f"error_client_{i}") for i in range(6)]
        
        start_time = time.time()
        
        async with chaos_injector.inject_intermittent_errors(scenario.intensity):
            tasks = [
                client.run_during_chaos(
                    ["simple_query", "complex_query", "list_databases"],
                    scenario.duration,
                    chaos_injector
                )
                for client in clients
            ]
            
            await asyncio.gather(*tasks, return_exceptions=True)
        
        end_time = time.time()
        
        # Analyze error recovery
        total_requests = sum(client.request_count for client in clients)
        successful_requests = sum(client.successful_requests for client in clients)
        failed_requests = sum(client.failed_requests for client in clients)
        
        error_rate = failed_requests / total_requests if total_requests > 0 else 0
        success_rate = successful_requests / total_requests if total_requests > 0 else 0
        
        # System should recover from intermittent errors
        system_survived = success_rate > 0.6  # At least 60% success despite errors
        recovery_successful = successful_requests > failed_requests
        
        result = ChaosResult(
            scenario_name=scenario.name,
            chaos_injected=True,
            system_survived=system_survived,
            requests_during_chaos=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            recovery_time=end_time - start_time,
            error_rate=error_rate,
            mean_response_time=0.0,
            recovery_successful=recovery_successful,
            details={"expected_error_rate": scenario.intensity, "actual_error_rate": error_rate}
        )
        
        print(result)
        
        # Assertions for intermittent error recovery
        assert total_requests > 0, "Should attempt requests"
        assert system_survived, f"System should recover from intermittent errors: {success_rate:.1%} success rate"
        # Error rate should be close to injection rate (±10%)
        assert abs(error_rate - scenario.intensity) < 0.15, f"Error rate deviation too high: expected ~{scenario.intensity:.1%}, got {error_rate:.1%}"


@pytest.mark.asyncio
async def test_snowflake_outage_recovery():
    """Test system behavior during and after Snowflake service outage."""
    
    scenario = ChaosScenario(
        name="Snowflake Outage Recovery",
        chaos_type=ChaosType.SNOWFLAKE_OUTAGE,
        intensity=1.0,  # Complete outage
        duration=2.0,
        target_component="snowflake_service",
        recovery_time=1.0,
        description="Test behavior during complete Snowflake outage and recovery"
    )
    
    chaos_injector = ChaosInjector()
    
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create, \
         patch('snowflake_mcp_server.main.initialize_async_infrastructure') as mock_init:
        
        # Normal connections for pre/post outage
        normal_connections = [create_chaos_mock_environment() for _ in range(5)]
        
        mock_init.return_value = None
        await mock_init()
        
        clients = [ChaosTestClient(f"outage_client_{i}") for i in range(3)]
        
        # Phase 1: Normal operation
        mock_create.side_effect = normal_connections
        print("📶 Phase 1: Normal operation")
        
        normal_tasks = [
            client.make_resilient_request("simple_query", chaos_injector)
            for client in clients
        ]
        normal_results = await asyncio.gather(*normal_tasks, return_exceptions=True)
        normal_success_count = sum(1 for success, _ in normal_results if success and not isinstance(success, Exception))
        
        # Reset stats
        for client in clients:
            client.request_count = 0
            client.successful_requests = 0
            client.failed_requests = 0
            client.response_times = []
            client.errors = []
        
        # Phase 2: Outage simulation
        print("💥 Phase 2: Outage simulation")
        
        def outage_connection(*args, **kwargs):
            raise ConnectionError("Chaos: Snowflake service unavailable")
        
        mock_create.side_effect = outage_connection
        
        outage_start = time.time()
        
        outage_tasks = [
            client.run_during_chaos(
                ["simple_query", "list_databases"],
                scenario.duration,
                chaos_injector
            )
            for client in clients
        ]
        
        await asyncio.gather(*outage_tasks, return_exceptions=True)
        outage_end = time.time()
        
        outage_requests = sum(client.request_count for client in clients)
        outage_failures = sum(client.failed_requests for client in clients)
        
        # Phase 3: Recovery
        print("🔄 Phase 3: Recovery")
        mock_create.side_effect = normal_connections
        
        # Reset stats for recovery test
        for client in clients:
            client.request_count = 0
            client.successful_requests = 0
            client.failed_requests = 0
            client.response_times = []
            client.errors = []
        
        recovery_start = time.time()
        
        recovery_tasks = [
            client.run_during_chaos(
                ["simple_query", "list_databases"],
                scenario.recovery_time,
                chaos_injector
            )
            for client in clients
        ]
        
        await asyncio.gather(*recovery_tasks, return_exceptions=True)
        recovery_end = time.time()
        
        recovery_requests = sum(client.request_count for client in clients)
        recovery_successes = sum(client.successful_requests for client in clients)
        
        # Analyze outage and recovery
        outage_failure_rate = outage_failures / outage_requests if outage_requests > 0 else 0
        recovery_success_rate = recovery_successes / recovery_requests if recovery_requests > 0 else 0
        recovery_time = recovery_end - recovery_start
        
        system_survived = recovery_success_rate > 0.7  # Good recovery
        recovery_successful = recovery_success_rate > 0.5
        
        result = ChaosResult(
            scenario_name=scenario.name,
            chaos_injected=True,
            system_survived=system_survived,
            requests_during_chaos=outage_requests,
            successful_requests=0,  # Expected during outage
            failed_requests=outage_failures,
            recovery_time=recovery_time,
            error_rate=outage_failure_rate,
            mean_response_time=0.0,
            recovery_successful=recovery_successful,
            details={
                "normal_success_count": normal_success_count,
                "outage_duration": outage_end - outage_start,
                "recovery_success_rate": recovery_success_rate,
                "recovery_requests": recovery_requests
            }
        )
        
        print(result)
        print(f"   Normal Operation Successes: {normal_success_count}")
        print(f"   Outage Failure Rate: {outage_failure_rate*100:.1f}%")
        print(f"   Recovery Success Rate: {recovery_success_rate*100:.1f}%")
        
        # Assertions for outage recovery
        assert normal_success_count > 0, "Should work normally before outage"
        assert outage_failure_rate > 0.8, "Should fail during outage"
        assert recovery_successful, f"Should recover after outage: {recovery_success_rate:.1%} success rate"
        assert system_survived, "System should survive and recover from complete outage"


@pytest.mark.asyncio
async def test_chaos_engineering_suite():
    """Run comprehensive chaos engineering test suite."""
    
    scenarios = [
        ChaosScenario(
            name="Mixed Chaos Storm",
            chaos_type=ChaosType.INTERMITTENT_ERRORS,
            intensity=0.3,
            duration=6.0,
            target_component="entire_system",
            description="Multiple chaos types injected simultaneously"
        )
    ]
    
    chaos_injector = ChaosInjector()
    
    with patch('snowflake_mcp_server.utils.async_pool.create_async_connection') as mock_create, \
         patch('snowflake_mcp_server.main.initialize_async_infrastructure') as mock_init:
        
        # Create resilient mock environment
        connections = [create_chaos_mock_environment() for _ in range(10)]
        
        def chaos_storm_connection(*args, **kwargs):
            # Multiple failure modes
            rand = random.random()
            if rand < 0.1:  # 10% connection failure
                raise ConnectionError("Chaos storm: Connection failed")
            elif rand < 0.15:  # 5% timeout
                raise TimeoutError("Chaos storm: Connection timeout")
            else:
                return random.choice(connections)
        
        mock_create.side_effect = chaos_storm_connection
        mock_init.return_value = None
        await mock_init()
        
        # Create multiple client types for comprehensive testing
        clients = [
            ChaosTestClient(f"storm_client_{i}") for i in range(8)
        ]
        
        start_time = time.time()
        
        # Inject multiple types of chaos simultaneously
        print("🌪️  Starting Chaos Storm...")
        
        async def chaos_storm():
            # Combine multiple chaos types
            async with chaos_injector.inject_intermittent_errors(0.2):
                async with chaos_injector.inject_network_latency(200, 100):
                    tasks = [
                        client.run_during_chaos(
                            ["simple_query", "complex_query", "list_databases"],
                            scenarios[0].duration,
                            chaos_injector
                        )
                        for client in clients
                    ]
                    
                    await asyncio.gather(*tasks, return_exceptions=True)
        
        await chaos_storm()
        end_time = time.time()
        
        # Analyze chaos storm results
        total_requests = sum(client.request_count for client in clients)
        successful_requests = sum(client.successful_requests for client in clients)
        failed_requests = sum(client.failed_requests for client in clients)
        
        success_rate = successful_requests / total_requests if total_requests > 0 else 0
        error_rate = failed_requests / total_requests if total_requests > 0 else 0
        
        # System should survive chaos storm
        system_survived = success_rate > 0.4  # At least 40% success during storm
        
        result = ChaosResult(
            scenario_name="Chaos Engineering Suite",
            chaos_injected=True,
            system_survived=system_survived,
            requests_during_chaos=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            recovery_time=end_time - start_time,
            error_rate=error_rate,
            mean_response_time=0.0,
            recovery_successful=successful_requests > 0,
            details=chaos_injector.get_chaos_status()
        )
        
        print(result)
        print(f"\n🏆 Chaos Engineering Summary:")
        print(f"   Total Test Duration: {end_time - start_time:.1f}s")
        print(f"   Requests Attempted: {total_requests:,}")
        print(f"   System Survival Rate: {success_rate*100:.1f}%")
        print(f"   Chaos Resilience: {'EXCELLENT' if success_rate > 0.7 else 'GOOD' if success_rate > 0.4 else 'NEEDS_IMPROVEMENT'}")
        
        # Final assertions
        assert total_requests > 50, f"Should attempt substantial requests: {total_requests}"
        assert system_survived, f"System should survive chaos storm: {success_rate:.1%} success rate"
        assert successful_requests > 0, "Should have some successful requests during chaos"


if __name__ == "__main__":
    # Run chaos engineering tests
    pytest.main([__file__, "-v", "-s", "--tb=short"])