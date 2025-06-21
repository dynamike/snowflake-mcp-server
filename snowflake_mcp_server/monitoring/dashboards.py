"""Performance monitoring dashboards and visualization."""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config import get_config
from .metrics import get_metrics


@dataclass
class DashboardPanel:
    """Represents a single dashboard panel."""
    
    id: str
    title: str
    type: str  # chart, stat, table, gauge
    query: str
    description: str = ""
    unit: str = ""
    thresholds: Dict[str, float] = field(default_factory=dict)
    refresh_interval: int = 30  # seconds
    time_range: str = "5m"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert panel to dictionary format."""
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "query": self.query,
            "description": self.description,
            "unit": self.unit,
            "thresholds": self.thresholds,
            "refresh_interval": self.refresh_interval,
            "time_range": self.time_range
        }


@dataclass
class Dashboard:
    """Represents a complete monitoring dashboard."""
    
    id: str
    title: str
    description: str
    panels: List[DashboardPanel] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    refresh_interval: int = 30
    created_at: datetime = field(default_factory=datetime.now)
    
    def add_panel(self, panel: DashboardPanel):
        """Add a panel to the dashboard."""
        self.panels.append(panel)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert dashboard to dictionary format."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "panels": [panel.to_dict() for panel in self.panels],
            "tags": self.tags,
            "refresh_interval": self.refresh_interval,
            "created_at": self.created_at.isoformat()
        }


class DashboardBuilder:
    """Builder for creating predefined dashboards."""
    
    @staticmethod
    def create_overview_dashboard() -> Dashboard:
        """Create main overview dashboard."""
        dashboard = Dashboard(
            id="mcp_overview",
            title="Snowflake MCP Server - Overview",
            description="High-level metrics for the MCP server",
            tags=["overview", "mcp", "snowflake"]
        )
        
        # Request metrics
        dashboard.add_panel(DashboardPanel(
            id="total_requests",
            title="Total Requests",
            type="stat",
            query="mcp_requests_total",
            description="Total number of MCP requests processed",
            unit="requests"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="request_rate",
            title="Request Rate",
            type="chart",
            query="rate(mcp_requests_total[5m])",
            description="Request rate per second",
            unit="req/s"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="avg_response_time",
            title="Average Response Time",
            type="gauge",
            query="mcp_request_duration_seconds",
            description="Average request response time",
            unit="seconds",
            thresholds={"warning": 1.0, "critical": 5.0}
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="error_rate",
            title="Error Rate",
            type="chart",
            query="rate(mcp_requests_total{status=\"error\"}[5m])",
            description="Error rate per second",
            unit="errors/s",
            thresholds={"warning": 0.1, "critical": 1.0}
        ))
        
        # Connection metrics
        dashboard.add_panel(DashboardPanel(
            id="active_connections",
            title="Active Connections",
            type="stat",
            query="mcp_active_connections",
            description="Number of active client connections",
            unit="connections"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="connection_pool_utilization",
            title="Connection Pool Utilization",
            type="gauge",
            query="mcp_connection_pool_utilization_percent",
            description="Database connection pool utilization",
            unit="percent",
            thresholds={"warning": 70.0, "critical": 90.0}
        ))
        
        # System metrics
        dashboard.add_panel(DashboardPanel(
            id="memory_usage",
            title="Memory Usage",
            type="chart",
            query="mcp_memory_usage_bytes{component=\"server\"}",
            description="Server memory usage",
            unit="bytes"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="cpu_usage",
            title="CPU Usage",
            type="gauge",
            query="mcp_cpu_usage_percent",
            description="Server CPU utilization",
            unit="percent",
            thresholds={"warning": 70.0, "critical": 90.0}
        ))
        
        return dashboard
    
    @staticmethod
    def create_performance_dashboard() -> Dashboard:
        """Create performance-focused dashboard."""
        dashboard = Dashboard(
            id="mcp_performance",
            title="Snowflake MCP Server - Performance",
            description="Detailed performance metrics and trends",
            tags=["performance", "latency", "throughput"]
        )
        
        # Request performance
        dashboard.add_panel(DashboardPanel(
            id="request_duration_percentiles",
            title="Request Duration Percentiles",
            type="chart",
            query="histogram_quantile(0.95, mcp_request_duration_seconds_bucket)",
            description="95th percentile request duration",
            unit="seconds"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="requests_by_tool",
            title="Requests by Tool",
            type="chart",
            query="sum by (tool_name) (rate(mcp_requests_total[5m]))",
            description="Request rate broken down by tool",
            unit="req/s"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="response_size_distribution",
            title="Response Size Distribution",
            type="chart",
            query="mcp_response_size_bytes_bucket",
            description="Distribution of response sizes",
            unit="bytes"
        ))
        
        # Database performance
        dashboard.add_panel(DashboardPanel(
            id="query_duration",
            title="Database Query Duration",
            type="chart",
            query="mcp_query_duration_seconds",
            description="Database query execution time",
            unit="seconds"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="queries_by_database",
            title="Queries by Database",
            type="chart",
            query="sum by (database) (rate(mcp_queries_total[5m]))",
            description="Query rate by database",
            unit="queries/s"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="rows_returned",
            title="Rows Returned",
            type="chart",
            query="mcp_query_rows_returned",
            description="Number of rows returned by queries",
            unit="rows"
        ))
        
        # Connection performance
        dashboard.add_panel(DashboardPanel(
            id="connection_acquisition_time",
            title="Connection Acquisition Time",
            type="chart",
            query="mcp_connection_acquisition_seconds",
            description="Time to acquire connections from pool",
            unit="seconds"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="connection_lease_duration",
            title="Connection Lease Duration",
            type="chart",
            query="mcp_connection_lease_seconds",
            description="How long connections are held",
            unit="seconds"
        ))
        
        return dashboard
    
    @staticmethod
    def create_client_dashboard() -> Dashboard:
        """Create client-focused dashboard."""
        dashboard = Dashboard(
            id="mcp_clients",
            title="Snowflake MCP Server - Clients",
            description="Client activity and resource usage metrics",
            tags=["clients", "sessions", "isolation"]
        )
        
        # Client activity
        dashboard.add_panel(DashboardPanel(
            id="active_clients",
            title="Active Clients",
            type="stat",
            query="mcp_active_clients",
            description="Number of active clients",
            unit="clients"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="sessions_by_type",
            title="Sessions by Type",
            type="chart",
            query="mcp_client_sessions",
            description="Client sessions by connection type",
            unit="sessions"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="requests_per_client",
            title="Requests per Client",
            type="table",
            query="sum by (client_id) (rate(mcp_requests_total[5m]))",
            description="Request rate by individual client",
            unit="req/s"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="client_request_rate",
            title="Client Request Rate",
            type="chart",
            query="mcp_client_requests_per_minute",
            description="Per-minute request rate by client",
            unit="req/min"
        ))
        
        # Resource allocation
        dashboard.add_panel(DashboardPanel(
            id="resource_allocation",
            title="Resource Allocation",
            type="chart",
            query="mcp_resource_allocation",
            description="Resource allocation per client",
            unit="units"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="resource_queue",
            title="Resource Queue Size",
            type="stat",
            query="mcp_resource_queue_size",
            description="Pending resource requests",
            unit="requests"
        ))
        
        # Isolation violations
        dashboard.add_panel(DashboardPanel(
            id="isolation_violations",
            title="Isolation Violations",
            type="chart",
            query="rate(mcp_client_isolation_violations_total[5m])",
            description="Client isolation violation rate",
            unit="violations/s",
            thresholds={"warning": 0.01, "critical": 0.1}
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="rate_limit_hits",
            title="Rate Limit Hits",
            type="chart",
            query="rate(mcp_rate_limit_hits_total[5m])",
            description="Rate limiting activations",
            unit="hits/s"
        ))
        
        return dashboard
    
    @staticmethod
    def create_errors_dashboard() -> Dashboard:
        """Create error tracking dashboard."""
        dashboard = Dashboard(
            id="mcp_errors",
            title="Snowflake MCP Server - Errors & Alerts",
            description="Error tracking and system health monitoring",
            tags=["errors", "health", "alerts"]
        )
        
        # Error metrics
        dashboard.add_panel(DashboardPanel(
            id="total_errors",
            title="Total Errors",
            type="stat",
            query="mcp_errors_total",
            description="Total error count",
            unit="errors"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="error_rate",
            title="Error Rate",
            type="chart",
            query="rate(mcp_errors_total[5m])",
            description="Error rate over time",
            unit="errors/s",
            thresholds={"warning": 0.1, "critical": 1.0}
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="errors_by_type",
            title="Errors by Type",
            type="chart",
            query="sum by (error_type) (rate(mcp_errors_total[5m]))",
            description="Error rate by error type",
            unit="errors/s"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="errors_by_component",
            title="Errors by Component",
            type="chart",
            query="sum by (component) (rate(mcp_errors_total[5m]))",
            description="Error rate by system component",
            unit="errors/s"
        ))
        
        # Health status
        dashboard.add_panel(DashboardPanel(
            id="health_status",
            title="Health Status",
            type="stat",
            query="mcp_health_status",
            description="Overall system health",
            unit="status"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="circuit_breaker_state",
            title="Circuit Breaker State",
            type="stat",
            query="mcp_circuit_breaker_state",
            description="Circuit breaker status",
            unit="state"
        ))
        
        # Connection failures
        dashboard.add_panel(DashboardPanel(
            id="failed_connections",
            title="Failed Connections",
            type="chart",
            query="rate(mcp_failed_connections_total[5m])",
            description="Connection failure rate",
            unit="failures/s"
        ))
        
        dashboard.add_panel(DashboardPanel(
            id="uptime",
            title="Server Uptime",
            type="stat",
            query="mcp_uptime_seconds",
            description="Server uptime",
            unit="seconds"
        ))
        
        return dashboard


class DashboardManager:
    """Manages dashboard creation and data collection."""
    
    def __init__(self):
        self.dashboards: Dict[str, Dashboard] = {}
        self.metrics = get_metrics()
        self.config = get_config()
        
        # Create default dashboards
        self._create_default_dashboards()
    
    def _create_default_dashboards(self):
        """Create the default set of dashboards."""
        builder = DashboardBuilder()
        
        self.dashboards["overview"] = builder.create_overview_dashboard()
        self.dashboards["performance"] = builder.create_performance_dashboard()
        self.dashboards["clients"] = builder.create_client_dashboard()
        self.dashboards["errors"] = builder.create_errors_dashboard()
    
    def get_dashboard(self, dashboard_id: str) -> Optional[Dashboard]:
        """Get a dashboard by ID."""
        return self.dashboards.get(dashboard_id)
    
    def list_dashboards(self) -> List[Dict[str, Any]]:
        """List all available dashboards."""
        return [
            {
                "id": dashboard.id,
                "title": dashboard.title,
                "description": dashboard.description,
                "tags": dashboard.tags,
                "panel_count": len(dashboard.panels)
            }
            for dashboard in self.dashboards.values()
        ]
    
    def get_dashboard_data(self, dashboard_id: str) -> Dict[str, Any]:
        """Get dashboard configuration and current data."""
        dashboard = self.get_dashboard(dashboard_id)
        if not dashboard:
            return {"error": "Dashboard not found"}
        
        # For now, return the dashboard structure
        # In a real implementation, you would query the metrics backend
        return {
            "dashboard": dashboard.to_dict(),
            "data": self._simulate_dashboard_data(dashboard),
            "last_updated": datetime.now().isoformat()
        }
    
    def _simulate_dashboard_data(self, dashboard: Dashboard) -> Dict[str, Any]:
        """Simulate dashboard data (placeholder for real metrics queries)."""
        # In a real implementation, this would query Prometheus or another metrics backend
        panel_data = {}
        
        for panel in dashboard.panels:
            if panel.type == "stat":
                panel_data[panel.id] = {"value": 42, "unit": panel.unit}
            elif panel.type == "gauge":
                panel_data[panel.id] = {"value": 65.0, "unit": panel.unit, "max": 100}
            elif panel.type == "chart":
                # Simulate time series data
                now = time.time()
                panel_data[panel.id] = {
                    "series": [
                        {
                            "timestamp": now - 300 + i * 30,
                            "value": 50 + (i % 10) * 5
                        }
                        for i in range(10)
                    ],
                    "unit": panel.unit
                }
            elif panel.type == "table":
                panel_data[panel.id] = {
                    "columns": ["Client ID", "Value"],
                    "rows": [
                        ["client_1", "25.5"],
                        ["client_2", "18.2"],
                        ["client_3", "31.1"]
                    ]
                }
        
        return panel_data
    
    def create_custom_dashboard(self, dashboard_config: Dict[str, Any]) -> Dashboard:
        """Create a custom dashboard from configuration."""
        dashboard = Dashboard(
            id=dashboard_config["id"],
            title=dashboard_config["title"],
            description=dashboard_config.get("description", ""),
            tags=dashboard_config.get("tags", [])
        )
        
        for panel_config in dashboard_config.get("panels", []):
            panel = DashboardPanel(
                id=panel_config["id"],
                title=panel_config["title"],
                type=panel_config["type"],
                query=panel_config["query"],
                description=panel_config.get("description", ""),
                unit=panel_config.get("unit", ""),
                thresholds=panel_config.get("thresholds", {}),
                refresh_interval=panel_config.get("refresh_interval", 30),
                time_range=panel_config.get("time_range", "5m")
            )
            dashboard.add_panel(panel)
        
        self.dashboards[dashboard.id] = dashboard
        return dashboard
    
    def export_dashboard(self, dashboard_id: str) -> str:
        """Export dashboard configuration as JSON."""
        dashboard = self.get_dashboard(dashboard_id)
        if not dashboard:
            return json.dumps({"error": "Dashboard not found"})
        
        return json.dumps(dashboard.to_dict(), indent=2)
    
    def import_dashboard(self, dashboard_json: str) -> Dashboard:
        """Import dashboard from JSON configuration."""
        config = json.loads(dashboard_json)
        return self.create_custom_dashboard(config)


# Global dashboard manager
_dashboard_manager: Optional[DashboardManager] = None


def get_dashboard_manager() -> DashboardManager:
    """Get the global dashboard manager instance."""
    global _dashboard_manager
    if _dashboard_manager is None:
        _dashboard_manager = DashboardManager()
    return _dashboard_manager


# FastAPI endpoints for dashboard API
async def get_dashboards_list() -> List[Dict[str, Any]]:
    """API endpoint to list all dashboards."""
    manager = get_dashboard_manager()
    return manager.list_dashboards()


async def get_dashboard_by_id(dashboard_id: str) -> Dict[str, Any]:
    """API endpoint to get dashboard data."""
    manager = get_dashboard_manager()
    return manager.get_dashboard_data(dashboard_id)


async def create_dashboard_endpoint(dashboard_config: Dict[str, Any]) -> Dict[str, Any]:
    """API endpoint to create a new dashboard."""
    manager = get_dashboard_manager()
    try:
        dashboard = manager.create_custom_dashboard(dashboard_config)
        return {"success": True, "dashboard_id": dashboard.id}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    # Test dashboard creation
    manager = DashboardManager()
    
    # List dashboards
    dashboards = manager.list_dashboards()
    print("Available dashboards:")
    for dashboard in dashboards:
        print(f"  - {dashboard['id']}: {dashboard['title']}")
    
    # Get overview dashboard data
    overview_data = manager.get_dashboard_data("overview")
    print(f"\nOverview dashboard: {len(overview_data['dashboard']['panels'])} panels")
    
    # Export dashboard
    exported = manager.export_dashboard("overview")
    print(f"\nExported dashboard size: {len(exported)} characters")