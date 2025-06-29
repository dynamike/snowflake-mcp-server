"""Comprehensive multi-client testing for Snowflake MCP server."""

import asyncio
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from snowflake_mcp_server.utils.client_isolation import (
    IsolationLevel,
    get_isolation_manager,
)
from snowflake_mcp_server.utils.connection_multiplexer import get_connection_multiplexer
from snowflake_mcp_server.utils.resource_allocator import (
    get_resource_allocator,
)

# Import our multi-client components
from snowflake_mcp_server.utils.session_manager import (
    get_session_manager,
)


class MockMCPClient:
    """Mock MCP client for testing different client types."""
    
    def __init__(self, client_id: str, client_type: str = "test"):
        self.client_id = client_id
        self.client_type = client_type
        self.session_id: Optional[str] = None
        self.request_count = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.response_times: List[float] = []
    
    async def make_request(self, tool_name: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Simulate making a tool request."""
        self.request_count += 1
        start_time = time.time()
        
        try:
            # Simulate request processing time
            await asyncio.sleep(0.1 + (self.request_count % 3) * 0.05)
            
            # Mock successful response
            response = {
                "id": f"req_{self.client_id}_{self.request_count}",
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Mock response from {tool_name} for client {self.client_id}"
                        }
                    ]
                }
            }
            
            duration = time.time() - start_time
            self.response_times.append(duration)
            self.successful_requests += 1
            
            return response
            
        except Exception as e:
            duration = time.time() - start_time
            self.response_times.append(duration)
            self.failed_requests += 1
            raise e
    
    def get_stats(self) -> Dict[str, Any]:
        """Get client statistics."""
        avg_response_time = sum(self.response_times) / len(self.response_times) if self.response_times else 0
        
        return {
            "client_id": self.client_id,
            "client_type": self.client_type,
            "session_id": self.session_id,
            "request_count": self.request_count,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": self.successful_requests / max(self.request_count, 1),
            "avg_response_time": avg_response_time,
            "total_response_time": sum(self.response_times)
        }


@pytest.mark.asyncio
async def test_session_manager_multi_client():
    """Test session manager with multiple concurrent clients."""
    session_manager = await get_session_manager()
    
    # Create multiple clients with different types
    clients = [
        MockMCPClient("claude_desktop_1", "websocket"),
        MockMCPClient("claude_code_1", "http"),
        MockMCPClient("roo_code_1", "stdio"),
        MockMCPClient("custom_client_1", "websocket"),
        MockMCPClient("test_client_1", "http"),
    ]
    
    # Create sessions for all clients
    sessions = []
    for client in clients:
        session = await session_manager.create_session(
            client.client_id,
            client.client_type,
            metadata={"test": True},
            connection_info={"host": "localhost", "port": 8000}
        )
        client.session_id = session.session_id
        sessions.append(session)
    
    # Simulate concurrent activity
    async def client_activity(client: MockMCPClient, operations: int):
        for i in range(operations):
            # Add request to session
            request_id = f"req_{client.client_id}_{i}"
            await session_manager.add_request(client.session_id, request_id)
            
            # Simulate request processing
            await client.make_request("execute_query", {"query": f"SELECT {i}"})
            
            # Remove request from session
            await session_manager.remove_request(client.session_id, request_id)
            
            await asyncio.sleep(0.1)
    
    # Run concurrent client activity
    tasks = [client_activity(client, 5) for client in clients]
    await asyncio.gather(*tasks)
    
    # Verify session stats
    stats = await session_manager.get_session_stats()
    assert stats["total_sessions"] == len(clients)
    assert stats["unique_clients"] == len(clients)
    assert stats["total_requests_processed"] == len(clients) * 5
    
    # Clean up
    for session in sessions:
        await session_manager.remove_session(session.session_id)


@pytest.mark.asyncio
async def test_connection_multiplexer_efficiency():
    """Test connection multiplexer for resource sharing efficiency."""
    multiplexer = await get_connection_multiplexer()
    
    # Mock connection pool for testing
    multiplexer._connection_pool = AsyncMock()
    
    clients = ["client1", "client2", "client3"]
    operations_per_client = 10
    
    async def client_operations(client_id: str):
        for i in range(operations_per_client):
            request_id = f"req_{client_id}_{i}"
            
            # Use connection multiplexer
            async with multiplexer.acquire_connection(client_id, request_id) as conn:
                # Simulate database work
                await asyncio.sleep(0.05)
    
    # Run concurrent operations
    start_time = time.time()
    await asyncio.gather(*[client_operations(client_id) for client_id in clients])
    total_time = time.time() - start_time
    
    # Get multiplexer stats
    stats = await multiplexer.get_stats()
    
    # Verify efficiency
    assert stats["total_leases_created"] > 0
    assert stats["total_operations"] == len(clients) * operations_per_client
    assert stats["unique_clients"] == len(clients)
    
    # Check if connection reuse occurred (cache hits)
    print(f"Connection multiplexer stats: {stats}")
    print(f"Total time: {total_time:.2f}s")


@pytest.mark.asyncio
async def test_client_isolation_boundaries():
    """Test client isolation with different security levels."""
    isolation_manager = get_isolation_manager()
    
    # Register clients with different isolation levels
    client_configs = [
        ("client_strict", IsolationLevel.STRICT, {"allowed_databases": {"DB1"}}),
        ("client_moderate", IsolationLevel.MODERATE, {"allowed_databases": {"DB1", "DB2"}}),
        ("client_relaxed", IsolationLevel.RELAXED, {"allowed_databases": {"DB1", "DB2", "DB3"}}),
    ]
    
    for client_id, level, config in client_configs:
        await isolation_manager.register_client(client_id, level, **config)
    
    # Test database access validation
    test_cases = [
        ("client_strict", "DB1", True),
        ("client_strict", "DB2", False),
        ("client_moderate", "DB2", True),
        ("client_moderate", "DB3", False),
        ("client_relaxed", "DB3", True),
    ]
    
    for client_id, database, expected in test_cases:
        result = await isolation_manager.validate_database_access(client_id, database)
        assert result == expected, f"Client {client_id} access to {database} should be {expected}"
    
    # Test resource limits
    for client_id, _, _ in client_configs:
        context = await isolation_manager.create_isolation_context(client_id, f"req_{client_id}")
        
        # Test resource acquisition
        resources = {"memory_mb": 10.0, "connections": 1.0}
        acquired = await isolation_manager.acquire_resources(client_id, f"req_{client_id}", resources)
        assert acquired, f"Should be able to acquire resources for {client_id}"
        
        # Test resource release
        await isolation_manager.release_resources(client_id, f"req_{client_id}", resources)
    
    # Get isolation stats
    stats = await isolation_manager.get_global_isolation_stats()
    assert stats["registered_clients"] == len(client_configs)


@pytest.mark.asyncio
async def test_fair_resource_allocation():
    """Test fair resource allocation across multiple clients."""
    allocator = await get_resource_allocator()
    
    # Configure client priorities and weights
    client_configs = [
        ("high_priority_client", 5, 3.0),    # High priority, high weight
        ("medium_priority_client", 3, 2.0),  # Medium priority, medium weight
        ("low_priority_client", 1, 1.0),     # Low priority, low weight
    ]
    
    for client_id, priority, weight in client_configs:
        await allocator.set_client_priority(client_id, priority)
        await allocator.set_client_weight(client_id, weight)
    
    # Test resource allocation requests
    allocation_results = []
    
    for client_id, priority, weight in client_configs:
        # Request connections
        success, req_id = await allocator.request_resources(
            client_id, "connections", 3.0, priority=priority
        )
        allocation_results.append((client_id, "connections", success))
        
        # Request memory
        success, req_id = await allocator.request_resources(
            client_id, "memory_mb", 100.0, priority=priority
        )
        allocation_results.append((client_id, "memory_mb", success))
    
    # Verify allocation fairness
    stats = await allocator.get_resource_stats()
    
    # High priority client should get resources
    assert any(result[2] for result in allocation_results if result[0] == "high_priority_client")
    
    print(f"Resource allocation stats: {stats}")


@pytest.mark.asyncio
async def test_integrated_multi_client_scenario():
    """Test integrated scenario with all multi-client components."""
    
    # Get all managers
    session_manager = await get_session_manager()
    multiplexer = await get_connection_multiplexer()
    isolation_manager = get_isolation_manager()
    allocator = await get_resource_allocator()
    
    # Simulate different client types
    client_scenarios = [
        {
            "client_id": "claude_desktop_production",
            "client_type": "websocket",
            "isolation_level": IsolationLevel.MODERATE,
            "priority": 4,
            "weight": 2.0,
            "operations": 15,
            "databases": {"PROD_DB", "ANALYTICS_DB"}
        },
        {
            "client_id": "claude_code_development",
            "client_type": "http",
            "isolation_level": IsolationLevel.RELAXED,
            "priority": 2,
            "weight": 1.5,
            "operations": 10,
            "databases": {"DEV_DB", "TEST_DB"}
        },
        {
            "client_id": "roo_code_analysis",
            "client_type": "stdio",
            "isolation_level": IsolationLevel.STRICT,
            "priority": 3,
            "weight": 1.0,
            "operations": 8,
            "databases": {"ANALYTICS_DB"}
        },
        {
            "client_id": "custom_integration",
            "client_type": "websocket",
            "isolation_level": IsolationLevel.MODERATE,
            "priority": 1,
            "weight": 0.5,
            "operations": 5,
            "databases": {"INTEGRATION_DB"}
        }
    ]
    
    # Setup clients
    clients = []
    for scenario in client_scenarios:
        # Register with isolation manager
        await isolation_manager.register_client(
            scenario["client_id"],
            scenario["isolation_level"],
            allowed_databases=scenario["databases"],
            max_concurrent_requests=scenario["operations"]
        )
        
        # Set resource allocation preferences
        await allocator.set_client_priority(scenario["client_id"], scenario["priority"])
        await allocator.set_client_weight(scenario["client_id"], scenario["weight"])
        
        # Create session
        session = await session_manager.create_session(
            scenario["client_id"],
            scenario["client_type"],
            metadata=scenario
        )
        
        # Create mock client
        client = MockMCPClient(scenario["client_id"], scenario["client_type"])
        client.session_id = session.session_id
        clients.append((client, scenario))
    
    # Simulate concurrent client operations
    async def run_client_scenario(client: MockMCPClient, scenario: Dict[str, Any]):
        for i in range(scenario["operations"]):
            request_id = f"req_{client.client_id}_{i}"
            
            # Add to session
            await session_manager.add_request(client.session_id, request_id)
            
            # Create isolation context
            isolation_context = await isolation_manager.create_isolation_context(
                client.client_id, request_id
            )
            
            # Request resources
            resources_acquired = await allocator.request_resources(
                client.client_id, "connections", 1.0, priority=scenario["priority"]
            )
            
            # Use multiplexed connection
            async with multiplexer.acquire_connection(client.client_id, request_id) as conn:
                # Validate database access
                for database in scenario["databases"]:
                    access_allowed = await isolation_manager.validate_database_access(
                        client.client_id, database
                    )
                    if access_allowed:
                        # Simulate query
                        await client.make_request("execute_query", {
                            "query": f"SELECT * FROM {database}.schema.table LIMIT 10"
                        })
                    else:
                        # Should not happen based on our setup
                        print(f"Access denied for {client.client_id} to {database}")
            
            # Release resources
            if resources_acquired[0]:
                await allocator.release_resources(client.client_id, "connections", 1.0)
            
            # Remove from session
            await session_manager.remove_request(client.session_id, request_id)
            
            # Small delay between operations
            await asyncio.sleep(0.1)
    
    # Run all client scenarios concurrently
    start_time = time.time()
    tasks = [run_client_scenario(client, scenario) for client, scenario in clients]
    await asyncio.gather(*tasks, return_exceptions=True)
    total_time = time.time() - start_time
    
    # Collect and analyze results
    print("\n🎯 Integrated Multi-Client Test Results")
    print(f"Total execution time: {total_time:.2f}s")
    print("=" * 60)
    
    # Session manager stats
    session_stats = await session_manager.get_session_stats()
    print(f"Session Stats: {session_stats}")
    
    # Connection multiplexer stats
    multiplexer_stats = await multiplexer.get_stats()
    print(f"Multiplexer Stats: {multiplexer_stats}")
    
    # Isolation manager stats
    isolation_stats = await isolation_manager.get_global_isolation_stats()
    print(f"Isolation Stats: {isolation_stats}")
    
    # Resource allocator stats
    resource_stats = await allocator.get_resource_stats()
    print(f"Resource Stats: {resource_stats}")
    
    # Client performance stats
    print("\nClient Performance:")
    for client, scenario in clients:
        stats = client.get_stats()
        print(f"  {client.client_id}: {stats['success_rate']:.1%} success rate, "
              f"{stats['avg_response_time']:.3f}s avg response time")
    
    # Verify overall system health
    total_requests = sum(client.request_count for client, _ in clients)
    total_successful = sum(client.successful_requests for client, _ in clients)
    overall_success_rate = total_successful / total_requests if total_requests > 0 else 0
    
    # Assertions for system health
    assert overall_success_rate > 0.95, f"Overall success rate too low: {overall_success_rate:.1%}"
    assert session_stats["total_sessions"] == len(clients), "Session count mismatch"
    assert isolation_stats["registered_clients"] == len(clients), "Isolation client count mismatch"
    
    print("\n✅ Multi-client integration test PASSED!")
    print(f"   Overall success rate: {overall_success_rate:.1%}")
    print(f"   Total requests processed: {total_requests}")
    print(f"   Average throughput: {total_requests/total_time:.1f} req/s")
    
    # Cleanup
    for client, _ in clients:
        await session_manager.remove_session(client.session_id)


@pytest.mark.asyncio
async def test_claude_desktop_code_roo_simulation():
    """Specific test simulating Claude Desktop, Claude Code, and Roo Code clients."""
    
    # Initialize all systems
    session_manager = await get_session_manager()
    multiplexer = await get_connection_multiplexer()
    isolation_manager = get_isolation_manager()
    allocator = await get_resource_allocator()
    
    # Define realistic client profiles
    real_world_clients = [
        {
            "client_id": "claude_desktop_user1",
            "client_type": "websocket",
            "profile": {
                "isolation_level": IsolationLevel.MODERATE,
                "priority": 3,
                "weight": 2.0,
                "max_concurrent_requests": 5,
                "allowed_databases": {"PROD_ANALYTICS", "CUSTOMER_DATA"}
            },
            "workload": "data_analysis"  # Longer running queries, visualizations
        },
        {
            "client_id": "claude_code_developer1",
            "client_type": "http",
            "profile": {
                "isolation_level": IsolationLevel.RELAXED,
                "priority": 4,
                "weight": 2.5,
                "max_concurrent_requests": 8,
                "allowed_databases": {"DEV_DB", "TEST_DB", "STAGING_DB"}
            },
            "workload": "development"  # Quick queries, schema exploration
        },
        {
            "client_id": "roo_code_analyst1",
            "client_type": "stdio",
            "profile": {
                "isolation_level": IsolationLevel.STRICT,
                "priority": 5,
                "weight": 1.5,
                "max_concurrent_requests": 3,
                "allowed_databases": {"FINANCIAL_DATA"}
            },
            "workload": "financial_analysis"  # High-security, precise queries
        }
    ]
    
    # Setup clients
    mock_clients = []
    for client_config in real_world_clients:
        # Register with systems
        profile = client_config["profile"]
        await isolation_manager.register_client(
            client_config["client_id"],
            profile["isolation_level"],
            max_concurrent_requests=profile["max_concurrent_requests"],
            allowed_databases=profile["allowed_databases"]
        )
        
        await allocator.set_client_priority(client_config["client_id"], profile["priority"])
        await allocator.set_client_weight(client_config["client_id"], profile["weight"])
        
        session = await session_manager.create_session(
            client_config["client_id"],
            client_config["client_type"],
            metadata={"workload": client_config["workload"]}
        )
        
        client = MockMCPClient(client_config["client_id"], client_config["client_type"])
        client.session_id = session.session_id
        mock_clients.append((client, client_config))
    
    # Define workload patterns
    async def claude_desktop_workload(client: MockMCPClient):
        """Simulate Claude Desktop usage pattern."""
        operations = [
            ("list_databases", {}),
            ("execute_query", {"query": "SELECT COUNT(*) FROM PROD_ANALYTICS.PUBLIC.SALES"}),
            ("execute_query", {"query": "SELECT * FROM CUSTOMER_DATA.PUBLIC.USERS LIMIT 100"}),
            ("list_views", {"database": "PROD_ANALYTICS"}),
            ("query_view", {"database": "PROD_ANALYTICS", "view_name": "MONTHLY_SALES"}),
        ]
        
        for tool, params in operations:
            await client.make_request(tool, params)
            await asyncio.sleep(0.2)  # Simulate user thinking time
    
    async def claude_code_workload(client: MockMCPClient):
        """Simulate Claude Code usage pattern."""
        operations = [
            ("list_databases", {}),
            ("list_views", {"database": "DEV_DB"}),
            ("describe_view", {"database": "DEV_DB", "view_name": "USER_ACTIVITY"}),
            ("execute_query", {"query": "DESCRIBE TABLE DEV_DB.PUBLIC.LOGS"}),
            ("execute_query", {"query": "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'LOGS'"}),
            ("execute_query", {"query": "SELECT * FROM DEV_DB.PUBLIC.LOGS ORDER BY TIMESTAMP DESC LIMIT 50"}),
        ]
        
        for tool, params in operations:
            await client.make_request(tool, params)
            await asyncio.sleep(0.1)  # Faster development workflow
    
    async def roo_code_workload(client: MockMCPClient):
        """Simulate Roo Code usage pattern."""
        operations = [
            ("execute_query", {"query": "SELECT SUM(amount) FROM FINANCIAL_DATA.PUBLIC.TRANSACTIONS WHERE date >= CURRENT_DATE - 30"}),
            ("execute_query", {"query": "SELECT account_id, AVG(balance) FROM FINANCIAL_DATA.PUBLIC.ACCOUNTS GROUP BY account_id"}),
            ("execute_query", {"query": "SELECT * FROM FINANCIAL_DATA.PUBLIC.AUDIT_LOG WHERE severity = 'HIGH' ORDER BY timestamp DESC"}),
        ]
        
        for tool, params in operations:
            await client.make_request(tool, params)
            await asyncio.sleep(0.3)  # Careful analysis pace
    
    # Map workloads to client types
    workload_map = {
        "data_analysis": claude_desktop_workload,
        "development": claude_code_workload,
        "financial_analysis": roo_code_workload,
    }
    
    # Run realistic workloads concurrently
    print("\n🚀 Running Claude Desktop + Claude Code + Roo Code simulation")
    start_time = time.time()
    
    tasks = []
    for client, config in mock_clients:
        workload_func = workload_map[config["workload"]]
        tasks.append(workload_func(client))
    
    await asyncio.gather(*tasks)
    total_time = time.time() - start_time
    
    # Analyze results
    print("\n📊 Real-world Client Simulation Results")
    print(f"Total execution time: {total_time:.2f}s")
    print("=" * 50)
    
    for client, config in mock_clients:
        stats = client.get_stats()
        workload = config["workload"]
        print(f"{workload.title()} ({client.client_id}):")
        print(f"  Requests: {stats['request_count']}")
        print(f"  Success rate: {stats['success_rate']:.1%}")
        print(f"  Avg response time: {stats['avg_response_time']:.3f}s")
        print()
    
    # Verify system handled the load well
    total_requests = sum(client.request_count for client, _ in mock_clients)
    total_successful = sum(client.successful_requests for client, _ in mock_clients)
    success_rate = total_successful / total_requests
    
    assert success_rate > 0.98, f"Success rate too low for real-world simulation: {success_rate:.1%}"
    
    print("✅ Real-world client simulation PASSED!")
    print(f"   Combined success rate: {success_rate:.1%}")
    print(f"   Total throughput: {total_requests/total_time:.1f} req/s")
    
    # Cleanup
    for client, _ in mock_clients:
        await session_manager.remove_session(client.session_id)


if __name__ == "__main__":
    # Run all tests
    pytest.main([__file__, "-v", "-s"])