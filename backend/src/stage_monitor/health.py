"""Read-only health projection; no polling or manufacturer-specific knowledge."""
from collections import Counter
from typing import Literal

from fastapi import Depends
from pydantic import AwareDatetime, Field

from .auth import can_access
from .contracts import Model, now

HealthStatus = Literal["ok", "degraded", "error", "unknown"]


class DeviceHealthView(Model):
    device_id: str
    name: str
    category: str
    enabled: bool
    monitoring_active: bool = True
    status: str
    connection_status: str
    condition: HealthStatus
    last_seen: AwareDatetime | None = None
    last_error: str | None = None
    reconnect_attempts: int = 0
    next_retry_at: AwareDatetime | None = None
    latency_ms: float | None = None
    telemetry: dict[str, int] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    alerts: int = 0


class CategoryHealth(Model):
    category: str
    condition: HealthStatus
    devices: int
    counts: dict[str, int]
    alerts: int


class SystemHealth(Model):
    time: AwareDatetime
    mock_mode: bool
    backend: HealthStatus
    fleet: HealthStatus
    counts: dict[str, int]
    alert_counts: dict[str, int]
    modules: dict[str, str]
    categories: list[CategoryHealth]
    devices: list[DeviceHealthView]
    diagnostics: dict | None = None


def worst(conditions):
    values = set(conditions)
    return next((s for s in ("error", "degraded", "unknown", "ok") if s in values), "unknown")


def fresh(timestamp, clock, ttl):
    return timestamp is not None and 0 <= (clock - timestamp).total_seconds() <= ttl


def snapshot(state, user, mock_mode, clock=None):
    clock = clock or now()
    visible = [d for d in state.manager.devices.values() if can_access(user, d.id)]
    alarms = [a for a in state.alerts.open_alerts.values()
              if can_access(user, a.device_id) and a.status in {"ACTIVE", "ACKNOWLEDGED"}]
    by_device = Counter(a.device_id for a in alarms)
    critical = {a.device_id for a in alarms if a.severity == "critical"}
    devices = []
    counts = dict.fromkeys(["online", "offline", "error", "degraded", "connecting", "unknown", "disabled"], 0)
    for device in visible:
        current = state.manager.states.get(device.id)
        row = DeviceHealthView(
            device_id=device.id, name=device.name, category=device.category, enabled=device.enabled,
            monitoring_active=getattr(state.manager, 'monitored', lambda d: d.enabled)(device),
            status=str(current.status) if current else "unknown",
            connection_status=str(current.connection_status) if current else "disconnected",
            condition="unknown", alerts=by_device[device.id],
            telemetry=dict.fromkeys(["fresh", "stale", "invalid", "unknown"], 0),
        )
        if current:
            row.last_seen = current.health.last_seen
            row.last_error = current.health.last_error.message if current.health.last_error else None
            row.reconnect_attempts = current.health.reconnect_attempts
            row.next_retry_at = current.health.next_retry_at
            row.latency_ms = current.health.latency_ms
            for metric in current.metrics:
                quality = metric.quality
                if quality == "good":
                    quality = "fresh" if metric.value is not None and fresh(metric.observed_at, clock, device.stale_after) else "stale"
                row.telemetry[quality] += 1
        if not row.monitoring_active:
            counts["disabled"] += 1
            row.reasons.append("disabled" if not device.enabled else "outside_show")
        else:
            bucket = "connecting" if row.connection_status == "connecting" else row.status
            counts[bucket] += 1
            if bucket in {"error", "offline"}:
                row.condition = "error"
                row.reasons.append(bucket)
            elif bucket == "connecting":
                row.reasons.append("connecting")
            elif bucket == "unknown":
                row.reasons.append("no_state")
            else:
                row.condition = "degraded" if bucket == "degraded" else "ok"
                if bucket == "degraded":
                    row.reasons.append("degraded")
                if row.last_seen is None:
                    row.condition = worst([row.condition, "unknown"])
                    row.reasons.append("never_seen")
                elif not fresh(row.last_seen, clock, device.stale_after):
                    row.condition = "degraded"
                    row.reasons.append("stale_connection")
                if any(row.telemetry[k] for k in ("stale", "invalid", "unknown")):
                    row.condition = worst([row.condition, "degraded"])
                    row.reasons.append("telemetry_quality")
            if row.alerts:
                row.condition = worst([row.condition, "error" if device.id in critical else "degraded"])
                row.reasons.append("unresolved_alert")
        devices.append(row)
    groups = []
    enabled = [d for d in devices if d.monitoring_active]
    for category in sorted({d.category for d in enabled}):
        members = [d for d in enabled if d.category == category]
        groups.append(CategoryHealth(
            category=category, condition=worst(d.condition for d in members), devices=len(members),
            counts=dict(Counter("connecting" if d.connection_status == "connecting" else d.status for d in members)),
            alerts=sum(d.alerts for d in members),
        ))
    weather = state.weather.snapshot
    weather_status = "unknown" if weather is None else (
        "degraded" if weather.stale or weather.last_error or not fresh(weather.fetched_at, clock, state.weather.interval * 2) else "ok"
    )
    modules = {
        "storage": "error" if state.storage.last_error else "ok",
        "alerts": "error" if state.alerts.last_error else "ok",
        "chat": "error" if state.chat.last_error else "ok",
        "weather": weather_status,
    }
    plugin_errors = [p for p in state.plugins.list() if p["load_error"]]
    modules["plugins"] = "degraded" if plugin_errors else "ok"
    for key in ("journal", "presets", "discovery"):
        service = getattr(state, key, None)
        modules[key] = "error" if service and service.last_error else "ok"
    if getattr(getattr(state,"bus",None),"recording_error",False):
        modules["journal"] = "error"
    diagnostics = None
    if "admin" in user["roles"]:
        diagnostics = {
            "plugin_errors": plugin_errors,
            "workers": [{"device_id": device_id, "alive": supervisor.process.is_alive(),
                         "pid": supervisor.process.pid, "last_error": supervisor.failed}
                        for device_id, supervisor in state.manager.supervisors.items()],
            "queues": {device_id: queue.qsize() for device_id, queue in state.manager.queues.items()},
            "module_errors": {"storage": state.storage.last_error, "alerts": state.alerts.last_error,
                              "chat": state.chat.last_error},
        }
    return SystemHealth(
        time=clock, mock_mode=mock_mode,
        backend=worst(s for s in modules.values() if s != "unknown"),
        fleet=worst(d.condition for d in enabled), counts=counts,
        alert_counts={"ACTIVE": sum(a.status == "ACTIVE" for a in alarms),
                      "ACKNOWLEDGED": sum(a.status == "ACKNOWLEDGED" for a in alarms),
                      "critical": sum(a.severity == "critical" for a in alarms)},
        modules=modules, categories=groups, devices=devices, diagnostics=diagnostics,
    )


def register(app, current_user, settings):
    @app.get("/api/system/health", response_model=SystemHealth, response_model_exclude_none=True)
    async def system_health(user=Depends(current_user)):
        return snapshot(app.state, user, settings.mock_mode)
