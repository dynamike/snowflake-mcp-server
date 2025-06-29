"""Quota management system for per-client resource allocation."""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ..config import get_config
from ..monitoring import get_audit_logger, get_metrics, get_structured_logger

logger = logging.getLogger(__name__)


class QuotaType(Enum):
    """Types of quotas that can be managed."""
    REQUESTS_PER_HOUR = "requests_per_hour"
    REQUESTS_PER_DAY = "requests_per_day"
    QUERIES_PER_HOUR = "queries_per_hour"
    QUERIES_PER_DAY = "queries_per_day"
    DATA_TRANSFER_BYTES = "data_transfer_bytes"
    COMPUTE_SECONDS = "compute_seconds"
    STORAGE_BYTES = "storage_bytes"
    CONCURRENT_CONNECTIONS = "concurrent_connections"


class QuotaPeriod(Enum):
    """Quota reset periods."""
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CUSTOM = "custom"


@dataclass
class QuotaLimit:
    """Defines a quota limit for a specific resource."""
    
    quota_type: QuotaType
    limit: int  # Maximum allowed usage
    period: QuotaPeriod
    soft_limit: Optional[int] = None  # Warning threshold (percentage of limit)
    reset_time: Optional[datetime] = None  # Custom reset time for CUSTOM period
    rollover_allowed: bool = False  # Allow unused quota to roll over
    burst_allowance: int = 0  # Allow brief usage above limit
    
    def __post_init__(self):
        if self.soft_limit is None:
            self.soft_limit = int(self.limit * 0.8)  # Default to 80%


@dataclass
class QuotaUsage:
    """Tracks usage for a specific quota."""
    
    quota_type: QuotaType
    current_usage: int = 0
    peak_usage: int = 0
    last_reset: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    warning_triggered: bool = False
    limit_exceeded: bool = False
    burst_used: int = 0
    rollover_balance: int = 0
    
    # Usage tracking over time
    usage_history: List[Tuple[datetime, int]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "quota_type": self.quota_type.value,
            "current_usage": self.current_usage,
            "peak_usage": self.peak_usage,
            "last_reset": self.last_reset.isoformat(),
            "warning_triggered": self.warning_triggered,
            "limit_exceeded": self.limit_exceeded,
            "burst_used": self.burst_used,
            "rollover_balance": self.rollover_balance,
        }


class QuotaExceededError(Exception):
    """Raised when quota limit is exceeded."""
    
    def __init__(self, message: str, quota_type: str, current_usage: int, 
                 limit: int, reset_time: Optional[datetime] = None):
        super().__init__(message)
        self.quota_type = quota_type
        self.current_usage = current_usage
        self.limit = limit
        self.reset_time = reset_time


class ClientQuota:
    """Manages quotas for a specific client."""
    
    def __init__(self, client_id: str, quota_limits: Dict[QuotaType, QuotaLimit]):
        self.client_id = client_id
        self.quota_limits = quota_limits
        self.quota_usage: Dict[QuotaType, QuotaUsage] = {}
        
        # Initialize usage tracking
        for quota_type in quota_limits:
            self.quota_usage[quota_type] = QuotaUsage(quota_type)
        
        # Logging
        self.logger = get_structured_logger().get_logger("quota_manager")
        self.audit_logger = get_audit_logger()
        
        # Concurrent access protection
        self._lock = asyncio.Lock()
    
    async def consume_quota(self, quota_type: QuotaType, amount: int = 1) -> bool:
        """
        Attempt to consume quota of the specified type.
        
        Args:
            quota_type: Type of quota to consume
            amount: Amount to consume
            
        Returns:
            True if quota was consumed successfully, False if limit exceeded
            
        Raises:
            QuotaExceededError: If quota limit is exceeded
        """
        async with self._lock:
            if quota_type not in self.quota_limits:
                # No limit defined for this quota type, allow unlimited usage
                return True
            
            await self._check_reset_needed(quota_type)
            
            limit = self.quota_limits[quota_type]
            usage = self.quota_usage[quota_type]
            
            # Calculate available quota including rollover and burst
            available_quota = (limit.limit + 
                             usage.rollover_balance + 
                             (limit.burst_allowance - usage.burst_used))
            
            if usage.current_usage + amount <= available_quota:
                # Quota available
                usage.current_usage += amount
                usage.peak_usage = max(usage.peak_usage, usage.current_usage)
                
                # Track if we're using burst allowance
                if usage.current_usage > limit.limit + usage.rollover_balance:
                    burst_used = usage.current_usage - (limit.limit + usage.rollover_balance)
                    usage.burst_used = burst_used
                
                # Record usage in history
                usage.usage_history.append((datetime.now(timezone.utc), amount))
                self._trim_usage_history(usage)
                
                # Check for soft limit warning
                effective_limit = limit.limit + usage.rollover_balance
                if (not usage.warning_triggered and 
                    usage.current_usage >= limit.soft_limit):
                    usage.warning_triggered = True
                    await self._trigger_soft_limit_warning(quota_type, usage.current_usage, effective_limit)
                
                # Log quota consumption
                self.logger.debug(
                    f"Quota consumed for client {self.client_id}",
                    client_id=self.client_id,
                    quota_type=quota_type.value,
                    amount=amount,
                    current_usage=usage.current_usage,
                    limit=effective_limit,
                    event_type="quota_consumed"
                )
                
                return True
            else:
                # Quota exceeded
                usage.limit_exceeded = True
                
                # Log quota exceeded
                self.logger.warning(
                    f"Quota exceeded for client {self.client_id}",
                    client_id=self.client_id,
                    quota_type=quota_type.value,
                    requested_amount=amount,
                    current_usage=usage.current_usage,
                    limit=available_quota,
                    event_type="quota_exceeded"
                )
                
                # Audit log
                self.audit_logger.log_authorization(
                    user_id=self.client_id,
                    resource=f"quota_{quota_type.value}",
                    action="consume",
                    granted=False,
                    reason=f"Quota limit exceeded: {usage.current_usage + amount} > {available_quota}"
                )
                
                # Calculate when quota will reset
                reset_time = await self._get_next_reset_time(quota_type)
                
                raise QuotaExceededError(
                    f"Quota exceeded for {quota_type.value}: {usage.current_usage + amount} > {available_quota}",
                    quota_type=quota_type.value,
                    current_usage=usage.current_usage,
                    limit=available_quota,
                    reset_time=reset_time
                )
    
    async def check_quota_available(self, quota_type: QuotaType, amount: int = 1) -> Tuple[bool, int]:
        """
        Check if quota is available without consuming it.
        
        Returns:
            (available, remaining_quota)
        """
        async with self._lock:
            if quota_type not in self.quota_limits:
                return True, float('inf')
            
            await self._check_reset_needed(quota_type)
            
            limit = self.quota_limits[quota_type]
            usage = self.quota_usage[quota_type]
            
            available_quota = (limit.limit + 
                             usage.rollover_balance + 
                             (limit.burst_allowance - usage.burst_used))
            
            remaining = available_quota - usage.current_usage
            return remaining >= amount, remaining
    
    async def get_quota_status(self, quota_type: Optional[QuotaType] = None) -> Dict[str, Any]:
        """Get current quota status."""
        async with self._lock:
            if quota_type:
                if quota_type not in self.quota_limits:
                    return {"error": f"No quota limit defined for {quota_type.value}"}
                
                await self._check_reset_needed(quota_type)
                
                limit = self.quota_limits[quota_type]
                usage = self.quota_usage[quota_type]
                
                available_quota = limit.limit + usage.rollover_balance
                remaining = available_quota - usage.current_usage
                utilization = usage.current_usage / available_quota if available_quota > 0 else 0
                
                return {
                    "quota_type": quota_type.value,
                    "limit": limit.limit,
                    "soft_limit": limit.soft_limit,
                    "current_usage": usage.current_usage,
                    "remaining": remaining,
                    "utilization_percent": utilization * 100,
                    "peak_usage": usage.peak_usage,
                    "burst_allowance": limit.burst_allowance,
                    "burst_used": usage.burst_used,
                    "rollover_balance": usage.rollover_balance,
                    "warning_triggered": usage.warning_triggered,
                    "limit_exceeded": usage.limit_exceeded,
                    "last_reset": usage.last_reset.isoformat(),
                    "next_reset": (await self._get_next_reset_time(quota_type)).isoformat(),
                }
            else:
                # Return status for all quotas
                status = {}
                for qt in self.quota_limits:
                    status[qt.value] = await self.get_quota_status(qt)
                return status
    
    async def reset_quota(self, quota_type: QuotaType, force: bool = False):
        """Reset quota for the specified type."""
        async with self._lock:
            if quota_type not in self.quota_limits:
                return
            
            limit = self.quota_limits[quota_type]
            usage = self.quota_usage[quota_type]
            
            # Handle rollover if allowed
            if limit.rollover_allowed and not force:
                unused_quota = max(0, limit.limit - usage.current_usage)
                rollover_amount = min(unused_quota, limit.limit // 2)  # Max 50% rollover
                usage.rollover_balance = rollover_amount
            else:
                usage.rollover_balance = 0
            
            # Reset usage counters
            usage.current_usage = 0
            usage.peak_usage = 0
            usage.burst_used = 0
            usage.warning_triggered = False
            usage.limit_exceeded = False
            usage.last_reset = datetime.now(timezone.utc)
            
            self.logger.info(
                f"Quota reset for client {self.client_id}",
                client_id=self.client_id,
                quota_type=quota_type.value,
                rollover_balance=usage.rollover_balance,
                force_reset=force,
                event_type="quota_reset"
            )
    
    async def _check_reset_needed(self, quota_type: QuotaType):
        """Check if quota needs to be reset based on period."""
        limit = self.quota_limits[quota_type]
        usage = self.quota_usage[quota_type]
        now = datetime.now(timezone.utc)
        
        reset_needed = False
        
        if limit.period == QuotaPeriod.HOURLY:
            if now.hour != usage.last_reset.hour or now.date() != usage.last_reset.date():
                reset_needed = True
        elif limit.period == QuotaPeriod.DAILY:
            if now.date() != usage.last_reset.date():
                reset_needed = True
        elif limit.period == QuotaPeriod.WEEKLY:
            # Reset on Monday
            if now.weekday() == 0 and now.date() != usage.last_reset.date():
                reset_needed = True
        elif limit.period == QuotaPeriod.MONTHLY:
            if now.month != usage.last_reset.month or now.year != usage.last_reset.year:
                reset_needed = True
        elif limit.period == QuotaPeriod.CUSTOM:
            if limit.reset_time and now >= limit.reset_time:
                reset_needed = True
        
        if reset_needed:
            await self.reset_quota(quota_type)
    
    async def _get_next_reset_time(self, quota_type: QuotaType) -> datetime:
        """Get the next reset time for the quota."""
        limit = self.quota_limits[quota_type]
        now = datetime.now(timezone.utc)
        
        if limit.period == QuotaPeriod.HOURLY:
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            return next_hour
        elif limit.period == QuotaPeriod.DAILY:
            next_day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            return next_day
        elif limit.period == QuotaPeriod.WEEKLY:
            days_until_monday = (7 - now.weekday()) % 7
            if days_until_monday == 0:
                days_until_monday = 7
            next_monday = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_until_monday)
            return next_monday
        elif limit.period == QuotaPeriod.MONTHLY:
            if now.month == 12:
                next_month = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            else:
                next_month = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
            return next_month
        elif limit.period == QuotaPeriod.CUSTOM:
            return limit.reset_time or now + timedelta(hours=1)
        
        return now + timedelta(hours=1)  # Default to 1 hour
    
    async def _trigger_soft_limit_warning(self, quota_type: QuotaType, current_usage: int, limit: int):
        """Trigger soft limit warning."""
        utilization = (current_usage / limit) * 100 if limit > 0 else 0
        
        self.logger.warning(
            f"Soft quota limit reached for client {self.client_id}",
            client_id=self.client_id,
            quota_type=quota_type.value,
            current_usage=current_usage,
            limit=limit,
            utilization_percent=utilization,
            event_type="quota_soft_limit_warning"
        )
        
        # Audit log
        self.audit_logger.log_authorization(
            user_id=self.client_id,
            resource=f"quota_{quota_type.value}",
            action="warning",
            granted=True,
            reason=f"Soft limit reached: {utilization:.1f}% utilization"
        )
    
    def _trim_usage_history(self, usage: QuotaUsage, max_entries: int = 1000):
        """Trim usage history to prevent memory growth."""
        if len(usage.usage_history) > max_entries:
            usage.usage_history = usage.usage_history[-max_entries:]


class QuotaManager:
    """Central manager for all client quotas."""
    
    def __init__(self):
        self.config = get_config()
        self.metrics = get_metrics()
        self.logger = get_structured_logger().get_logger("quota_manager")
        
        # Client quotas
        self.client_quotas: Dict[str, ClientQuota] = {}
        self.default_quotas = self._get_default_quotas()
        
        # Global quota tracking
        self.global_quotas: Dict[QuotaType, QuotaLimit] = self._get_global_quotas()
        self.global_usage: Dict[QuotaType, QuotaUsage] = {}
        
        # Initialize global usage tracking
        for quota_type in self.global_quotas:
            self.global_usage[quota_type] = QuotaUsage(quota_type)
        
        # Background tasks
        self._cleanup_task: Optional[asyncio.Task] = None
        self._monitoring_task: Optional[asyncio.Task] = None
        self._running = False
    
    def _get_default_quotas(self) -> Dict[QuotaType, QuotaLimit]:
        """Get default quota limits for new clients."""
        return {
            QuotaType.REQUESTS_PER_HOUR: QuotaLimit(
                quota_type=QuotaType.REQUESTS_PER_HOUR,
                limit=getattr(self.config.quotas, 'default_requests_per_hour', 1000),
                period=QuotaPeriod.HOURLY,
                soft_limit=800,
                burst_allowance=100
            ),
            QuotaType.REQUESTS_PER_DAY: QuotaLimit(
                quota_type=QuotaType.REQUESTS_PER_DAY,
                limit=getattr(self.config.quotas, 'default_requests_per_day', 10000),
                period=QuotaPeriod.DAILY,
                soft_limit=8000,
                rollover_allowed=True
            ),
            QuotaType.QUERIES_PER_HOUR: QuotaLimit(
                quota_type=QuotaType.QUERIES_PER_HOUR,
                limit=getattr(self.config.quotas, 'default_queries_per_hour', 500),
                period=QuotaPeriod.HOURLY,
                soft_limit=400,
                burst_allowance=50
            ),
            QuotaType.DATA_TRANSFER_BYTES: QuotaLimit(
                quota_type=QuotaType.DATA_TRANSFER_BYTES,
                limit=getattr(self.config.quotas, 'default_data_transfer_mb', 1000) * 1024 * 1024,  # Convert MB to bytes
                period=QuotaPeriod.DAILY,
                soft_limit=int(800 * 1024 * 1024),  # 800 MB
                rollover_allowed=True
            ),
            QuotaType.CONCURRENT_CONNECTIONS: QuotaLimit(
                quota_type=QuotaType.CONCURRENT_CONNECTIONS,
                limit=getattr(self.config.quotas, 'default_concurrent_connections', 10),
                period=QuotaPeriod.CUSTOM,  # Not time-based
                soft_limit=8
            ),
        }
    
    def _get_global_quotas(self) -> Dict[QuotaType, QuotaLimit]:
        """Get global quota limits."""
        return {
            QuotaType.REQUESTS_PER_HOUR: QuotaLimit(
                quota_type=QuotaType.REQUESTS_PER_HOUR,
                limit=getattr(self.config.quotas, 'global_requests_per_hour', 100000),
                period=QuotaPeriod.HOURLY,
                soft_limit=80000
            ),
            QuotaType.QUERIES_PER_HOUR: QuotaLimit(
                quota_type=QuotaType.QUERIES_PER_HOUR,
                limit=getattr(self.config.quotas, 'global_queries_per_hour', 50000),
                period=QuotaPeriod.HOURLY,
                soft_limit=40000
            ),
            QuotaType.CONCURRENT_CONNECTIONS: QuotaLimit(
                quota_type=QuotaType.CONCURRENT_CONNECTIONS,
                limit=getattr(self.config.quotas, 'global_concurrent_connections', 1000),
                period=QuotaPeriod.CUSTOM,
                soft_limit=800
            ),
        }
    
    def get_client_quota(self, client_id: str) -> ClientQuota:
        """Get or create quota manager for a client."""
        if client_id not in self.client_quotas:
            # Check for custom quotas (could be loaded from database)
            custom_quotas = self._get_custom_quotas(client_id)
            quotas = custom_quotas if custom_quotas else self.default_quotas
            
            self.client_quotas[client_id] = ClientQuota(client_id, quotas)
            
            self.logger.info(
                f"Created quota manager for client {client_id}",
                client_id=client_id,
                quota_types=[qt.value for qt in quotas.keys()]
            )
        
        return self.client_quotas[client_id]
    
    def _get_custom_quotas(self, client_id: str) -> Optional[Dict[QuotaType, QuotaLimit]]:
        """Get custom quotas for a specific client."""
        # This would typically load from a database or configuration
        # For now, return None to use default quotas
        return None
    
    async def consume_quota(self, client_id: str, quota_type: QuotaType, amount: int = 1) -> bool:
        """Consume quota for a client, checking both client and global limits."""
        # Check global quota first
        if quota_type in self.global_quotas:
            global_usage = self.global_usage[quota_type]
            global_limit = self.global_quotas[quota_type]
            
            # Check global limit
            if global_usage.current_usage + amount > global_limit.limit:
                raise QuotaExceededError(
                    f"Global quota exceeded for {quota_type.value}",
                    quota_type=f"global_{quota_type.value}",
                    current_usage=global_usage.current_usage,
                    limit=global_limit.limit
                )
            
            # Consume global quota
            global_usage.current_usage += amount
        
        # Check and consume client quota
        client_quota = self.get_client_quota(client_id)
        success = await client_quota.consume_quota(quota_type, amount)
        
        # Update metrics
        if success:
            self.metrics.resource_allocation.labels(
                client_id=client_id,
                resource_type=quota_type.value
            ).set(client_quota.quota_usage[quota_type].current_usage)
        
        return success
    
    async def check_quota_available(self, client_id: str, quota_type: QuotaType, 
                                  amount: int = 1) -> Tuple[bool, int]:
        """Check if quota is available for a client."""
        # Check global quota first
        if quota_type in self.global_quotas:
            global_usage = self.global_usage[quota_type]
            global_limit = self.global_quotas[quota_type]
            global_remaining = global_limit.limit - global_usage.current_usage
            
            if global_remaining < amount:
                return False, global_remaining
        
        # Check client quota
        client_quota = self.get_client_quota(client_id)
        return await client_quota.check_quota_available(quota_type, amount)
    
    async def get_client_quota_status(self, client_id: str, 
                                    quota_type: Optional[QuotaType] = None) -> Dict[str, Any]:
        """Get quota status for a client."""
        client_quota = self.get_client_quota(client_id)
        return await client_quota.get_quota_status(quota_type)
    
    async def get_global_quota_status(self) -> Dict[str, Any]:
        """Get global quota status."""
        status = {}
        for quota_type, limit in self.global_quotas.items():
            usage = self.global_usage[quota_type]
            remaining = limit.limit - usage.current_usage
            utilization = usage.current_usage / limit.limit if limit.limit > 0 else 0
            
            status[quota_type.value] = {
                "limit": limit.limit,
                "current_usage": usage.current_usage,
                "remaining": remaining,
                "utilization_percent": utilization * 100,
                "peak_usage": usage.peak_usage,
            }
        
        return status
    
    def set_client_quotas(self, client_id: str, quotas: Dict[QuotaType, QuotaLimit]):
        """Set custom quotas for a client."""
        if client_id in self.client_quotas:
            # Remove existing quota manager
            del self.client_quotas[client_id]
        
        # Create new quota manager with custom limits
        self.client_quotas[client_id] = ClientQuota(client_id, quotas)
        
        self.logger.info(
            f"Updated quotas for client {client_id}",
            client_id=client_id,
            quotas={qt.value: ql.limit for qt, ql in quotas.items()}
        )
    
    async def reset_client_quotas(self, client_id: str, quota_type: Optional[QuotaType] = None):
        """Reset quotas for a client."""
        if client_id not in self.client_quotas:
            return
        
        client_quota = self.client_quotas[client_id]
        
        if quota_type:
            await client_quota.reset_quota(quota_type, force=True)
        else:
            for qt in client_quota.quota_limits:
                await client_quota.reset_quota(qt, force=True)
        
        self.logger.info(
            f"Reset quotas for client {client_id}",
            client_id=client_id,
            quota_type=quota_type.value if quota_type else "all"
        )
    
    async def get_quota_summary(self) -> Dict[str, Any]:
        """Get overall quota summary."""
        summary = {
            "total_clients": len(self.client_quotas),
            "global_quotas": await self.get_global_quota_status(),
            "client_utilization": {},
            "top_consumers": {},
        }
        
        # Calculate client utilization statistics
        total_utilization = defaultdict(list)
        
        for client_id, client_quota in self.client_quotas.items():
            client_status = await client_quota.get_quota_status()
            
            for quota_type_str, status in client_status.items():
                if isinstance(status, dict) and "utilization_percent" in status:
                    total_utilization[quota_type_str].append({
                        "client_id": client_id,
                        "utilization": status["utilization_percent"],
                        "usage": status["current_usage"]
                    })
        
        # Calculate average utilization and top consumers
        for quota_type, client_data in total_utilization.items():
            if client_data:
                avg_utilization = sum(c["utilization"] for c in client_data) / len(client_data)
                top_consumers = sorted(client_data, key=lambda x: x["usage"], reverse=True)[:5]
                
                summary["client_utilization"][quota_type] = {
                    "average_utilization_percent": avg_utilization,
                    "total_clients": len(client_data),
                }
                
                summary["top_consumers"][quota_type] = [
                    {"client_id": c["client_id"], "usage": c["usage"], "utilization_percent": c["utilization"]}
                    for c in top_consumers
                ]
        
        return summary
    
    async def start_background_tasks(self):
        """Start background monitoring and cleanup tasks."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self._monitoring_task = asyncio.create_task(self._monitoring_loop())
        self.logger.info("Started quota manager background tasks")
    
    async def stop_background_tasks(self):
        """Stop background tasks."""
        self._running = False
        
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
        
        self.logger.info("Stopped quota manager background tasks")
    
    async def _cleanup_loop(self):
        """Background task to clean up inactive clients."""
        while self._running:
            try:
                current_time = datetime.now(timezone.utc)
                cleanup_threshold = current_time - timedelta(hours=24)  # 24 hour threshold
                
                clients_to_remove = []
                for client_id, client_quota in self.client_quotas.items():
                    # Check if client has been inactive
                    last_activity = max(
                        usage.last_reset for usage in client_quota.quota_usage.values()
                    ) if client_quota.quota_usage else current_time - timedelta(days=2)
                    
                    if last_activity < cleanup_threshold:
                        clients_to_remove.append(client_id)
                
                for client_id in clients_to_remove:
                    del self.client_quotas[client_id]
                    self.logger.info(f"Cleaned up inactive client quota: {client_id}")
                
                await asyncio.sleep(3600)  # Check every hour
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in quota cleanup loop: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes on error
    
    async def _monitoring_loop(self):
        """Background task to monitor and update metrics."""
        while self._running:
            try:
                # Update resource allocation metrics
                for client_id, client_quota in self.client_quotas.items():
                    for quota_type, usage in client_quota.quota_usage.items():
                        self.metrics.resource_allocation.labels(
                            client_id=client_id,
                            resource_type=quota_type.value
                        ).set(usage.current_usage)
                
                # Update queue size metrics (for clients near limits)
                queue_size = sum(
                    1 for client_quota in self.client_quotas.values()
                    for usage in client_quota.quota_usage.values()
                    if usage.warning_triggered
                )
                
                self.metrics.resource_queue_size.labels(resource_type="quota_warnings").set(queue_size)
                
                await asyncio.sleep(60)  # Update every minute
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in quota monitoring loop: {e}")
                await asyncio.sleep(60)


# Global quota manager instance
_quota_manager: Optional[QuotaManager] = None


def get_quota_manager() -> QuotaManager:
    """Get the global quota manager instance."""
    global _quota_manager
    if _quota_manager is None:
        _quota_manager = QuotaManager()
    return _quota_manager


# Decorator for quota enforcement
def enforce_quota(quota_type: QuotaType, amount: int = 1):
    """Decorator to enforce quota limits on functions."""
    def decorator(func):
        from functools import wraps
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            quota_manager = get_quota_manager()
            client_id = kwargs.get('client_id', 'unknown')
            
            # Check and consume quota
            await quota_manager.consume_quota(client_id, quota_type, amount)
            
            return await func(*args, **kwargs)
        
        return wrapper
    
    return decorator


# FastAPI endpoints for quota management
async def get_quota_status_endpoint(client_id: Optional[str] = None, 
                                  quota_type: Optional[str] = None) -> Dict[str, Any]:
    """API endpoint to get quota status."""
    quota_manager = get_quota_manager()
    
    if client_id:
        qt = QuotaType(quota_type) if quota_type else None
        return {
            "client_quota_status": await quota_manager.get_client_quota_status(client_id, qt),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    else:
        return {
            "global_quota_status": await quota_manager.get_global_quota_status(),
            "quota_summary": await quota_manager.get_quota_summary(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


async def reset_quota_endpoint(client_id: str, quota_type: Optional[str] = None) -> Dict[str, Any]:
    """API endpoint to reset client quotas."""
    quota_manager = get_quota_manager()
    
    try:
        qt = QuotaType(quota_type) if quota_type else None
        await quota_manager.reset_client_quotas(client_id, qt)
        
        return {
            "success": True, 
            "message": f"Reset {'all quotas' if not quota_type else quota_type} for client {client_id}"
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


async def update_client_quotas_endpoint(client_id: str, quotas: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """API endpoint to update client quotas."""
    quota_manager = get_quota_manager()
    
    try:
        # Convert API format to internal format
        internal_quotas = {}
        for quota_type_str, quota_config in quotas.items():
            quota_type = QuotaType(quota_type_str)
            period = QuotaPeriod(quota_config["period"])
            
            internal_quotas[quota_type] = QuotaLimit(
                quota_type=quota_type,
                limit=quota_config["limit"],
                period=period,
                soft_limit=quota_config.get("soft_limit"),
                rollover_allowed=quota_config.get("rollover_allowed", False),
                burst_allowance=quota_config.get("burst_allowance", 0)
            )
        
        quota_manager.set_client_quotas(client_id, internal_quotas)
        
        return {"success": True, "message": f"Updated quotas for client {client_id}"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    # Test quota management
    import asyncio
    
    async def test_quotas():
        manager = QuotaManager()
        client_id = "test_client"
        
        # Test quota consumption
        try:
            for i in range(15):  # Should exceed some limits
                await manager.consume_quota(
                    client_id, 
                    QuotaType.REQUESTS_PER_HOUR, 
                    100  # Large amount to trigger limits faster
                )
                print(f"Consumed quota {i+1}: Success")
        except QuotaExceededError as e:
            print(f"Quota exceeded: {e}")
        
        # Check status
        status = await manager.get_client_quota_status(client_id)
        print("\nClient quota status:")
        for quota_type, data in status.items():
            if isinstance(data, dict):
                print(f"  {quota_type}: {data.get('current_usage', 0)}/{data.get('limit', 0)} ({data.get('utilization_percent', 0):.1f}%)")
        
        # Get summary
        summary = await manager.get_quota_summary()
        print(f"\nQuota summary: {summary['total_clients']} clients")
    
    asyncio.run(test_quotas())