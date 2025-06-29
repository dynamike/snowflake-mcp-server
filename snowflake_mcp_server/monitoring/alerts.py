"""Alerting system for connection failures and critical events."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from ..config import get_config
from .metrics import get_metrics
from .structured_logging import get_audit_logger, get_structured_logger

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class AlertStatus(Enum):
    """Alert status."""
    FIRING = "firing"
    RESOLVED = "resolved"
    ACKNOWLEDGED = "acknowledged"
    SILENCED = "silenced"


@dataclass
class AlertRule:
    """Defines an alert rule with conditions and thresholds."""
    
    id: str
    name: str
    description: str
    severity: AlertSeverity
    metric_name: str
    condition: str  # "gt", "lt", "eq", "ne", "gte", "lte"
    threshold: float
    duration_seconds: int = 300  # How long condition must be true
    evaluation_interval: int = 60  # How often to evaluate
    labels: Dict[str, str] = field(default_factory=dict)
    annotations: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    
    def evaluate(self, current_value: float, timestamp: float) -> bool:
        """Evaluate if the alert condition is met."""
        if not self.enabled:
            return False
        
        conditions = {
            "gt": current_value > self.threshold,
            "lt": current_value < self.threshold,
            "eq": current_value == self.threshold,
            "ne": current_value != self.threshold,
            "gte": current_value >= self.threshold,
            "lte": current_value <= self.threshold,
        }
        
        return conditions.get(self.condition, False)


@dataclass
class Alert:
    """Represents an active alert."""
    
    rule_id: str
    name: str
    description: str
    severity: AlertSeverity
    status: AlertStatus
    labels: Dict[str, str]
    annotations: Dict[str, str]
    started_at: datetime
    resolved_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    silenced_until: Optional[datetime] = None
    current_value: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert alert to dictionary format."""
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "description": self.description,
            "severity": self.severity.value,
            "status": self.status.value,
            "labels": self.labels,
            "annotations": self.annotations,
            "started_at": self.started_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "silenced_until": self.silenced_until.isoformat() if self.silenced_until else None,
            "current_value": self.current_value,
        }


class AlertNotifier:
    """Base class for alert notification channels."""
    
    async def send_alert(self, alert: Alert) -> bool:
        """Send alert notification. Returns True if successful."""
        raise NotImplementedError
    
    async def send_resolution(self, alert: Alert) -> bool:
        """Send alert resolution notification. Returns True if successful."""
        raise NotImplementedError


class LogNotifier(AlertNotifier):
    """Sends alerts to structured logs."""
    
    def __init__(self):
        self.logger = get_structured_logger().get_logger("alerts")
        self.audit = get_audit_logger()
    
    async def send_alert(self, alert: Alert) -> bool:
        """Log alert firing."""
        try:
            self.logger.error(
                "Alert fired",
                alert_name=alert.name,
                severity=alert.severity.value,
                description=alert.description,
                labels=alert.labels,
                current_value=alert.current_value,
                event_type="alert_fired"
            )
            
            self.audit.log_error(
                error_type="alert_fired",
                error_message=f"Alert: {alert.name}",
                component="alerting",
                additional_context={
                    "severity": alert.severity.value,
                    "labels": alert.labels,
                    "current_value": alert.current_value,
                }
            )
            
            return True
        except Exception as e:
            logger.error(f"Failed to log alert: {e}")
            return False
    
    async def send_resolution(self, alert: Alert) -> bool:
        """Log alert resolution."""
        try:
            self.logger.info(
                "Alert resolved",
                alert_name=alert.name,
                severity=alert.severity.value,
                description=alert.description,
                duration_seconds=(alert.resolved_at - alert.started_at).total_seconds(),
                event_type="alert_resolved"
            )
            
            return True
        except Exception as e:
            logger.error(f"Failed to log alert resolution: {e}")
            return False


class WebhookNotifier(AlertNotifier):
    """Sends alerts to webhook endpoints."""
    
    def __init__(self, webhook_url: str, timeout: int = 30):
        self.webhook_url = webhook_url
        self.timeout = timeout
    
    async def send_alert(self, alert: Alert) -> bool:
        """Send alert to webhook."""
        try:
            import httpx
            
            payload = {
                "event": "alert.fired",
                "alert": alert.to_dict(),
                "timestamp": datetime.now().isoformat(),
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                    timeout=self.timeout
                )
                
                return response.status_code == 200
                
        except Exception as e:
            logger.error(f"Failed to send webhook alert: {e}")
            return False
    
    async def send_resolution(self, alert: Alert) -> bool:
        """Send resolution to webhook."""
        try:
            import httpx
            
            payload = {
                "event": "alert.resolved",
                "alert": alert.to_dict(),
                "timestamp": datetime.now().isoformat(),
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                    timeout=self.timeout
                )
                
                return response.status_code == 200
                
        except Exception as e:
            logger.error(f"Failed to send webhook resolution: {e}")
            return False


class AlertManager:
    """Manages alert rules, evaluation, and notifications."""
    
    def __init__(self):
        self.config = get_config()
        self.metrics = get_metrics()
        self.logger = get_structured_logger().get_logger("alert_manager")
        
        # Alert state
        self.rules: Dict[str, AlertRule] = {}
        self.active_alerts: Dict[str, Alert] = {}
        self.alert_history: List[Alert] = []
        self.notifiers: List[AlertNotifier] = []
        
        # Evaluation state
        self.last_evaluation: Dict[str, float] = {}
        self.condition_start_time: Dict[str, float] = {}
        
        # Background task
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # Initialize default rules and notifiers
        self._init_default_rules()
        self._init_notifiers()
    
    def _init_default_rules(self):
        """Initialize default alert rules for connection failures."""
        # Connection failure rate
        self.add_rule(AlertRule(
            id="connection_failure_rate",
            name="High Connection Failure Rate",
            description="Connection failure rate is above threshold",
            severity=AlertSeverity.CRITICAL,
            metric_name="mcp_failed_connections_total",
            condition="gt",
            threshold=5.0,  # 5 failures per minute
            duration_seconds=120,
            labels={"component": "database", "type": "connection"},
            annotations={
                "summary": "Connection failures are occurring at {{ $value }} per minute",
                "description": "Multiple connection failures detected. Check Snowflake connectivity.",
                "runbook": "Check network connectivity and Snowflake service status"
            }
        ))
        
        # High error rate
        self.add_rule(AlertRule(
            id="high_error_rate",
            name="High Error Rate",
            description="Overall error rate is above threshold",
            severity=AlertSeverity.WARNING,
            metric_name="mcp_errors_total",
            condition="gt",
            threshold=10.0,  # 10 errors per minute
            duration_seconds=180,
            labels={"severity": "warning"},
            annotations={
                "summary": "Error rate is {{ $value }} per minute",
                "description": "The server is experiencing elevated error rates"
            }
        ))
        
        # High response time
        self.add_rule(AlertRule(
            id="high_response_time",
            name="High Response Time",
            description="Average response time is above threshold",
            severity=AlertSeverity.WARNING,
            metric_name="mcp_request_duration_seconds",
            condition="gt",
            threshold=5.0,  # 5 seconds
            duration_seconds=300,
            labels={"performance": "latency"},
            annotations={
                "summary": "Average response time is {{ $value }} seconds",
                "description": "Requests are taking longer than expected to complete"
            }
        ))
        
        # Connection pool exhaustion
        self.add_rule(AlertRule(
            id="connection_pool_exhausted",
            name="Connection Pool Exhausted",
            description="Connection pool utilization is critically high",
            severity=AlertSeverity.CRITICAL,
            metric_name="mcp_connection_pool_utilization_percent",
            condition="gt",
            threshold=90.0,  # 90% utilization
            duration_seconds=60,
            labels={"component": "pool", "type": "resource"},
            annotations={
                "summary": "Connection pool utilization is {{ $value }}%",
                "description": "Connection pool is nearly exhausted. Scale up or optimize queries.",
                "runbook": "Check for connection leaks and consider increasing pool size"
            }
        ))
        
        # Memory usage warning
        self.add_rule(AlertRule(
            id="high_memory_usage",
            name="High Memory Usage",
            description="Server memory usage is above threshold",
            severity=AlertSeverity.WARNING,
            metric_name="mcp_memory_usage_bytes",
            condition="gt",
            threshold=1024 * 1024 * 1024,  # 1GB
            duration_seconds=300,
            labels={"component": "server", "type": "resource"},
            annotations={
                "summary": "Memory usage is {{ $value | humanizeBytes }}",
                "description": "Server memory usage is elevated"
            }
        ))
        
        # Circuit breaker open
        self.add_rule(AlertRule(
            id="circuit_breaker_open",
            name="Circuit Breaker Open",
            description="Circuit breaker is in open state",
            severity=AlertSeverity.CRITICAL,
            metric_name="mcp_circuit_breaker_state",
            condition="eq",
            threshold=1.0,  # Open state
            duration_seconds=30,
            labels={"component": "circuit_breaker", "type": "fault_tolerance"},
            annotations={
                "summary": "Circuit breaker is open for {{ $labels.component }}",
                "description": "Circuit breaker has opened due to repeated failures",
                "runbook": "Check downstream service health and error logs"
            }
        ))
    
    def _init_notifiers(self):
        """Initialize alert notifiers based on configuration."""
        # Always add log notifier
        self.notifiers.append(LogNotifier())
        
        # Add webhook notifier if configured
        webhook_url = getattr(self.config.monitoring, 'alert_webhook_url', None)
        if webhook_url:
            self.notifiers.append(WebhookNotifier(webhook_url))
    
    def add_rule(self, rule: AlertRule):
        """Add an alert rule."""
        self.rules[rule.id] = rule
        self.logger.info(f"Added alert rule: {rule.name}", rule_id=rule.id)
    
    def remove_rule(self, rule_id: str):
        """Remove an alert rule."""
        if rule_id in self.rules:
            del self.rules[rule_id]
            self.logger.info(f"Removed alert rule: {rule_id}")
    
    def add_notifier(self, notifier: AlertNotifier):
        """Add an alert notifier."""
        self.notifiers.append(notifier)
    
    async def start(self):
        """Start the alert manager."""
        self._running = True
        self._task = asyncio.create_task(self._evaluation_loop())
        self.logger.info("Alert manager started")
    
    async def stop(self):
        """Stop the alert manager."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.logger.info("Alert manager stopped")
    
    async def _evaluation_loop(self):
        """Main evaluation loop for alert rules."""
        while self._running:
            try:
                current_time = time.time()
                
                for rule in self.rules.values():
                    if not rule.enabled:
                        continue
                    
                    # Check if it's time to evaluate this rule
                    last_eval = self.last_evaluation.get(rule.id, 0)
                    if current_time - last_eval < rule.evaluation_interval:
                        continue
                    
                    await self._evaluate_rule(rule, current_time)
                    self.last_evaluation[rule.id] = current_time
                
                # Sleep for a short interval
                await asyncio.sleep(10)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in alert evaluation loop: {e}")
                await asyncio.sleep(30)
    
    async def _evaluate_rule(self, rule: AlertRule, current_time: float):
        """Evaluate a single alert rule."""
        try:
            # Get current metric value
            current_value = await self._get_metric_value(rule.metric_name, rule.labels)
            
            if current_value is None:
                return
            
            # Evaluate condition
            condition_met = rule.evaluate(current_value, current_time)
            
            alert_id = f"{rule.id}_{hash(frozenset(rule.labels.items()))}"
            
            if condition_met:
                # Check if condition has been true long enough
                if rule.id not in self.condition_start_time:
                    self.condition_start_time[rule.id] = current_time
                
                condition_duration = current_time - self.condition_start_time[rule.id]
                
                if condition_duration >= rule.duration_seconds:
                    if alert_id not in self.active_alerts:
                        # Fire new alert
                        await self._fire_alert(rule, current_value, current_time)
                    else:
                        # Update existing alert
                        self.active_alerts[alert_id].current_value = current_value
            else:
                # Condition not met, clear start time
                if rule.id in self.condition_start_time:
                    del self.condition_start_time[rule.id]
                
                # Resolve alert if it exists
                if alert_id in self.active_alerts:
                    await self._resolve_alert(alert_id, current_time)
        
        except Exception as e:
            self.logger.error(f"Error evaluating rule {rule.id}: {e}")
    
    async def _get_metric_value(self, metric_name: str, labels: Dict[str, str]) -> Optional[float]:
        """Get current value of a metric."""
        try:
            # This is a simplified implementation
            # In a real system, you'd query the metrics backend
            
            # For rate metrics, calculate rate over the last minute
            if "rate" in metric_name or "per_minute" in metric_name:
                return 5.0  # Placeholder rate value
            
            # For gauge metrics, return current value
            if metric_name == "mcp_connection_pool_utilization_percent":
                return 45.0  # Placeholder utilization
            elif metric_name == "mcp_memory_usage_bytes":
                import os

                import psutil
                process = psutil.Process(os.getpid())
                return float(process.memory_info().rss)
            elif metric_name == "mcp_request_duration_seconds":
                return 2.5  # Placeholder duration
            elif metric_name == "mcp_circuit_breaker_state":
                return 0.0  # Closed state
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting metric value for {metric_name}: {e}")
            return None
    
    async def _fire_alert(self, rule: AlertRule, current_value: float, timestamp: float):
        """Fire a new alert."""
        alert_id = f"{rule.id}_{hash(frozenset(rule.labels.items()))}"
        
        alert = Alert(
            rule_id=rule.id,
            name=rule.name,
            description=rule.description,
            severity=rule.severity,
            status=AlertStatus.FIRING,
            labels=rule.labels.copy(),
            annotations=rule.annotations.copy(),
            started_at=datetime.fromtimestamp(timestamp),
            current_value=current_value
        )
        
        # Add to active alerts
        self.active_alerts[alert_id] = alert
        self.alert_history.append(alert)
        
        # Send notifications
        for notifier in self.notifiers:
            try:
                await notifier.send_alert(alert)
            except Exception as e:
                self.logger.error(f"Failed to send alert via {type(notifier).__name__}: {e}")
        
        self.logger.warning(
            f"Alert fired: {rule.name}",
            rule_id=rule.id,
            severity=rule.severity.value,
            current_value=current_value
        )
    
    async def _resolve_alert(self, alert_id: str, timestamp: float):
        """Resolve an active alert."""
        if alert_id not in self.active_alerts:
            return
        
        alert = self.active_alerts[alert_id]
        alert.status = AlertStatus.RESOLVED
        alert.resolved_at = datetime.fromtimestamp(timestamp)
        
        # Remove from active alerts
        del self.active_alerts[alert_id]
        
        # Send resolution notifications
        for notifier in self.notifiers:
            try:
                await notifier.send_resolution(alert)
            except Exception as e:
                self.logger.error(f"Failed to send resolution via {type(notifier).__name__}: {e}")
        
        self.logger.info(
            f"Alert resolved: {alert.name}",
            rule_id=alert.rule_id,
            duration_seconds=(alert.resolved_at - alert.started_at).total_seconds()
        )
    
    def acknowledge_alert(self, alert_id: str, acknowledged_by: str = "system"):
        """Acknowledge an active alert."""
        if alert_id in self.active_alerts:
            alert = self.active_alerts[alert_id]
            alert.status = AlertStatus.ACKNOWLEDGED
            alert.acknowledged_at = datetime.now()
            
            self.logger.info(
                f"Alert acknowledged: {alert.name}",
                alert_id=alert_id,
                acknowledged_by=acknowledged_by
            )
    
    def silence_alert(self, alert_id: str, duration_minutes: int, silenced_by: str = "system"):
        """Silence an active alert for a duration."""
        if alert_id in self.active_alerts:
            alert = self.active_alerts[alert_id]
            alert.status = AlertStatus.SILENCED
            alert.silenced_until = datetime.now() + timedelta(minutes=duration_minutes)
            
            self.logger.info(
                f"Alert silenced: {alert.name}",
                alert_id=alert_id,
                duration_minutes=duration_minutes,
                silenced_by=silenced_by
            )
    
    def get_active_alerts(self) -> List[Dict[str, Any]]:
        """Get all active alerts."""
        return [alert.to_dict() for alert in self.active_alerts.values()]
    
    def get_alert_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get alert history."""
        return [alert.to_dict() for alert in self.alert_history[-limit:]]
    
    def get_rules(self) -> List[Dict[str, Any]]:
        """Get all alert rules."""
        return [
            {
                "id": rule.id,
                "name": rule.name,
                "description": rule.description,
                "severity": rule.severity.value,
                "metric_name": rule.metric_name,
                "condition": rule.condition,
                "threshold": rule.threshold,
                "duration_seconds": rule.duration_seconds,
                "enabled": rule.enabled,
                "labels": rule.labels,
                "annotations": rule.annotations,
            }
            for rule in self.rules.values()
        ]


# Global alert manager instance
_alert_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    """Get the global alert manager instance."""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager


async def start_alerting():
    """Start the alerting system."""
    manager = get_alert_manager()
    await manager.start()


async def stop_alerting():
    """Stop the alerting system."""
    global _alert_manager
    if _alert_manager:
        await _alert_manager.stop()


# FastAPI endpoints for alert management
async def get_alerts_endpoint() -> Dict[str, Any]:
    """API endpoint to get active alerts."""
    manager = get_alert_manager()
    return {
        "active_alerts": manager.get_active_alerts(),
        "alert_count": len(manager.active_alerts),
        "timestamp": datetime.now().isoformat(),
    }


async def get_alert_history_endpoint(limit: int = 100) -> Dict[str, Any]:
    """API endpoint to get alert history."""
    manager = get_alert_manager()
    return {
        "alert_history": manager.get_alert_history(limit),
        "timestamp": datetime.now().isoformat(),
    }


async def acknowledge_alert_endpoint(alert_id: str, acknowledged_by: str = "api") -> Dict[str, Any]:
    """API endpoint to acknowledge an alert."""
    manager = get_alert_manager()
    try:
        manager.acknowledge_alert(alert_id, acknowledged_by)
        return {"success": True, "message": f"Alert {alert_id} acknowledged"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def silence_alert_endpoint(alert_id: str, duration_minutes: int, silenced_by: str = "api") -> Dict[str, Any]:
    """API endpoint to silence an alert."""
    manager = get_alert_manager()
    try:
        manager.silence_alert(alert_id, duration_minutes, silenced_by)
        return {"success": True, "message": f"Alert {alert_id} silenced for {duration_minutes} minutes"}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    # Test alerting system
    import asyncio
    
    async def test_alerts():
        manager = AlertManager()
        
        # Add test webhook notifier
        # manager.add_notifier(WebhookNotifier("http://localhost:8080/webhook"))
        
        # Start manager
        await manager.start()
        
        # Simulate alert conditions
        print("Simulating high error rate...")
        
        # Wait for evaluation
        await asyncio.sleep(5)
        
        # Check active alerts
        active = manager.get_active_alerts()
        print(f"Active alerts: {len(active)}")
        
        # Stop manager
        await manager.stop()
    
    asyncio.run(test_alerts())