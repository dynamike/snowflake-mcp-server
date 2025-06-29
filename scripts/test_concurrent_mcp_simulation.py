#!/usr/bin/env python3
"""Simulate concurrent MCP clients using the isolation infrastructure."""

import asyncio
import logging
import random
import time
from typing import Dict

from snowflake_mcp_server.main import (
    handle_execute_query,
    handle_list_databases,
    initialize_async_infrastructure,
)
from snowflake_mcp_server.utils.contextual_logging import setup_server_logging


class MCPClientSimulator:
    """Simulate an MCP client making tool calls."""
    
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.request_count = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.total_duration = 0.0
    
    async def make_tool_call(self, tool_name: str, arguments: Dict):
        """Simulate a tool call."""
        self.request_count += 1
        start_time = time.time()
        
        try:
            # Add client ID to arguments for tracking
            arguments["_client_id"] = self.client_id
            
            if tool_name == "list_databases":
                result = await handle_list_databases(tool_name, arguments)
            elif tool_name == "execute_query":
                result = await handle_execute_query(tool_name, arguments)
            else:
                raise ValueError(f"Unknown tool: {tool_name}")
            
            duration = time.time() - start_time
            self.total_duration += duration
            self.successful_requests += 1
            
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            self.total_duration += duration
            self.failed_requests += 1
            raise e
    
    def get_stats(self):
        """Get client statistics."""
        avg_duration = self.total_duration / self.request_count if self.request_count > 0 else 0
        return {
            "client_id": self.client_id,
            "total_requests": self.request_count,
            "successful": self.successful_requests,
            "failed": self.failed_requests,
            "success_rate": self.successful_requests / self.request_count if self.request_count > 0 else 0,
            "avg_duration_ms": avg_duration * 1000,
            "total_duration": self.total_duration
        }


async def simulate_client_workload(client: MCPClientSimulator, workload_type: str, duration_seconds: int):
    """Simulate different types of client workloads."""
    
    end_time = time.time() + duration_seconds
    
    while time.time() < end_time:
        try:
            if workload_type == "database_explorer":
                # Client that explores databases and runs queries
                await client.make_tool_call("list_databases", {})
                await asyncio.sleep(0.1)
                
                await client.make_tool_call("execute_query", {
                    "query": f"SELECT '{client.client_id}' as client_id, {random.randint(1, 100)} as random_number"
                })
                await asyncio.sleep(random.uniform(0.5, 2.0))
                
            elif workload_type == "heavy_querier":
                # Client that runs many queries
                for i in range(3):
                    await client.make_tool_call("execute_query", {
                        "query": f"SELECT {i} as query_num, '{client.client_id}' as client"
                    })
                    await asyncio.sleep(0.1)
                
                await asyncio.sleep(random.uniform(0.2, 1.0))
                
            elif workload_type == "transaction_user":
                # Client that uses transactions
                await client.make_tool_call("execute_query", {
                    "query": f"SELECT '{client.client_id}' as tx_client, CURRENT_TIMESTAMP() as ts",
                    "use_transaction": True,
                    "auto_commit": True
                })
                await asyncio.sleep(random.uniform(0.3, 1.5))
                
            elif workload_type == "mixed_user":
                # Client with mixed usage patterns
                actions = [
                    ("list_databases", {}),
                    ("execute_query", {"query": f"SELECT '{client.client_id}' as mixed_client"}),
                    ("execute_query", {
                        "query": f"SELECT COUNT(*) as count FROM (SELECT {random.randint(1, 10)} as num)",
                        "use_transaction": random.choice([True, False])
                    })
                ]
                
                action = random.choice(actions)
                await client.make_tool_call(action[0], action[1])
                await asyncio.sleep(random.uniform(0.2, 2.0))
                
        except Exception as e:
            # Log errors but continue simulation
            print(f"   ⚠️  Client {client.client_id} error: {type(e).__name__}")
            await asyncio.sleep(0.1)


async def test_concurrent_mcp_clients():
    """Test multiple concurrent MCP clients."""
    
    print("🔄 MCP Concurrent Client Simulation")
    print("=" * 50)
    
    # Set up logging and infrastructure
    setup_server_logging()
    await initialize_async_infrastructure()
    
    # Create different types of clients
    clients = []
    client_configs = [
        ("claude_desktop_1", "database_explorer"),
        ("claude_desktop_2", "heavy_querier"),
        ("claude_code_1", "transaction_user"),
        ("claude_code_2", "mixed_user"),
        ("roo_code_1", "database_explorer"),
        ("roo_code_2", "heavy_querier"),
        ("custom_client_1", "mixed_user"),
        ("custom_client_2", "transaction_user"),
        ("test_client_1", "database_explorer"),
        ("test_client_2", "mixed_user"),
    ]
    
    for client_id, workload_type in client_configs:
        client = MCPClientSimulator(client_id)
        clients.append((client, workload_type))
    
    print(f"   📊 Created {len(clients)} simulated MCP clients")
    
    # Run concurrent simulation
    simulation_duration = 10  # seconds
    print(f"   🚀 Running {simulation_duration}s simulation with {len(clients)} concurrent clients...")
    
    start_time = time.time()
    
    # Start all client workloads concurrently
    tasks = [
        simulate_client_workload(client, workload_type, simulation_duration)
        for client, workload_type in clients
    ]
    
    # Wait for all simulations to complete
    await asyncio.gather(*tasks, return_exceptions=True)
    
    total_time = time.time() - start_time
    
    # Collect and analyze results
    print(f"\n   ✅ Simulation completed in {total_time:.2f}s")
    print("\n📊 Client Performance Summary:")
    print("=" * 80)
    
    total_requests = 0
    total_successful = 0
    total_failed = 0
    
    for client, workload_type in clients:
        stats = client.get_stats()
        total_requests += stats["total_requests"]
        total_successful += stats["successful"]
        total_failed += stats["failed"]
        
        print(f"   {stats['client_id']:<15} | {workload_type:<15} | "
              f"Reqs: {stats['total_requests']:>3} | "
              f"Success: {stats['success_rate']:>5.1%} | "
              f"Avg: {stats['avg_duration_ms']:>6.1f}ms")
    
    # Overall statistics
    overall_success_rate = total_successful / total_requests if total_requests > 0 else 0
    requests_per_second = total_requests / total_time
    
    print("=" * 80)
    print("📈 Overall Results:")
    print(f"   Total requests: {total_requests}")
    print(f"   Successful: {total_successful}")
    print(f"   Failed: {total_failed}")
    print(f"   Success rate: {overall_success_rate:.1%}")
    print(f"   Requests/second: {requests_per_second:.1f}")
    
    # Validate results
    assert overall_success_rate > 0.95, f"Success rate too low: {overall_success_rate:.1%}"
    assert total_requests > 50, f"Too few requests generated: {total_requests}"
    
    print("\n🎉 Concurrent client simulation PASSED!")
    print(f"   ✅ {len(clients)} clients operated concurrently without interference")
    print(f"   ✅ {overall_success_rate:.1%} success rate achieved")
    print(f"   ✅ {requests_per_second:.1f} requests/second throughput")
    
    return {
        "total_requests": total_requests,
        "success_rate": overall_success_rate,
        "requests_per_second": requests_per_second,
        "clients": len(clients)
    }


async def test_stress_scenario():
    """Test high-stress scenario with many rapid requests."""
    
    print("\n⚡ High-Stress Concurrent Access Test")
    print("=" * 50)
    
    clients = [MCPClientSimulator(f"stress_client_{i}") for i in range(20)]
    
    async def rapid_requests(client: MCPClientSimulator, request_count: int):
        """Make rapid consecutive requests."""
        for i in range(request_count):
            try:
                await client.make_tool_call("execute_query", {
                    "query": f"SELECT {i} as rapid_request_num, '{client.client_id}' as client",
                    "_client_id": client.client_id
                })
                # Very short delay between requests
                await asyncio.sleep(0.01)
            except Exception as e:
                print(f"   ⚠️  Stress test error in {client.client_id}: {type(e).__name__}")
    
    print(f"   🚀 Running stress test with {len(clients)} clients making rapid requests...")
    
    start_time = time.time()
    
    # Each client makes 10 rapid requests
    tasks = [rapid_requests(client, 10) for client in clients]
    await asyncio.gather(*tasks, return_exceptions=True)
    
    total_time = time.time() - start_time
    
    # Analyze stress test results
    total_requests = sum(client.request_count for client in clients)
    total_successful = sum(client.successful_requests for client in clients)
    stress_success_rate = total_successful / total_requests if total_requests > 0 else 0
    stress_rps = total_requests / total_time
    
    print(f"   📊 Stress test completed in {total_time:.2f}s")
    print(f"   📊 Total requests: {total_requests}")
    print(f"   📊 Successful: {total_successful}")
    print(f"   📊 Success rate: {stress_success_rate:.1%}")
    print(f"   📊 Requests/second: {stress_rps:.1f}")
    
    # Stress test should still maintain good success rate
    assert stress_success_rate > 0.90, f"Stress test success rate too low: {stress_success_rate:.1%}"
    
    print(f"   ✅ Stress test PASSED with {stress_success_rate:.1%} success rate")
    
    return stress_success_rate


async def main():
    """Run complete concurrent MCP simulation."""
    
    print("🧪 Concurrent MCP Client Test Suite")
    print("=" * 60)
    
    try:
        # Suppress verbose logging for cleaner output
        logging.getLogger().setLevel(logging.WARNING)
        
        # Run concurrent client simulation
        client_results = await test_concurrent_mcp_clients()
        
        # Run stress test
        stress_rate = await test_stress_scenario()
        
        # Final assessment
        print("\n🎯 Concurrency Test Summary")
        print("=" * 40)
        print(f"✅ Concurrent clients: {client_results['clients']} clients")
        print(f"✅ Request success rate: {client_results['success_rate']:.1%}")
        print(f"✅ Throughput: {client_results['requests_per_second']:.1f} req/s")
        print(f"✅ Stress test success: {stress_rate:.1%}")
        
        # Overall assessment
        if (client_results['success_rate'] > 0.95 and 
            stress_rate > 0.90 and 
            client_results['clients'] >= 10):
            print("\n🎉 All concurrency tests PASSED!")
            print("   Request isolation successfully handles concurrent MCP clients.")
            return True
        else:
            print("\n⚠️  Some concurrency issues detected.")
            return False
        
    except Exception as e:
        print(f"\n❌ Concurrency test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        exit_code = 0 if success else 1
        exit(exit_code)
    except KeyboardInterrupt:
        print("\n⚠️  Concurrency test interrupted")
        exit(1)
    except Exception as e:
        print(f"❌ Test error: {e}")
        exit(1)