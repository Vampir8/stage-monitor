"""Bounded service diagnostics; raw protocol capture is opt-in and short lived."""

import asyncio
from collections import defaultdict, deque

from fastapi import Depends, HTTPException
from pydantic import Field
from typing import Literal

from .auth import can_access
from .contracts import HealthCheckResult, Model, now
from .journal import redact
from .runtime import PluginFailure


class Capture(Model):
    seconds: int = Field(default=30, ge=0, le=60)


class Inspection(Model):
    action: Literal['start', 'stop', 'read'] = 'read'
    seconds: int = Field(default=30, ge=1, le=60)
    cursor: int = Field(default=0, ge=0)


class Diagnostics:
    def __init__(self):
        self.logs = defaultdict(lambda: deque(maxlen=100))
        self.counters = defaultdict(lambda: {"rpc_calls": 0, "rpc_errors": 0, "reconnects": 0})
        self.capture_until = {}

    def rpc(self, device_id, operation, latency, error):
        counters = self.counters[device_id]
        counters["rpc_calls"] += 1
        counters["rpc_errors"] += int(error is not None)
        if operation == "connect":
            counters["reconnects"] += 1
        self.logs[device_id].append(
            {
                "time": now().isoformat(),
                "kind": "rpc",
                "operation": operation,
                "latency_ms": round(latency, 2),
                "error": error,
            }
        )

    def trace(self, device_id, data):
        if asyncio.get_running_loop().time() >= self.capture_until.get(device_id, 0):
            return
        if data.get("direction") not in {"TX", "RX"}:
            return
        payload = "[ukryte]" if data.get("sensitive", True) else redact(data.get("payload"))
        self.logs[device_id].append(
            {
                "time": now().isoformat(),
                "kind": "transport",
                "direction": data["direction"],
                "payload": payload,
                "truncated": bool(data.get("truncated")),
            }
        )

    def clear(self, device_id):
        self.logs.pop(device_id, None)
        self.counters.pop(device_id, None)
        self.capture_until.pop(device_id, None)


def register(app, current_user, admin):
    def device(key, user):
        if key not in app.state.manager.devices or not can_access(user, key):
            raise HTTPException(404, "Device not found")
        return app.state.manager.devices[key]

    @app.post('/api/devices/{device_id}/inspection')
    async def inspect_plugin(device_id: str, data: Inspection, user=Depends(admin)):
        d = device(device_id, user)
        worker = app.state.manager.supervisors.get(d.id)
        if not worker or not app.state.manager.active(d):
            raise HTTPException(409, 'Device worker unavailable')
        if data.action != 'read':
            await app.state.storage.audit(user['id'], 'device.inspection', d.id, data.model_dump())
        try:
            return await worker.rpc('inspect', data.model_dump(), timeout=3)
        except (PluginFailure, TimeoutError, ConnectionError):
            raise HTTPException(409, 'Plugin inspection unavailable') from None

    @app.get("/api/devices/{device_id}/diagnostics")
    async def diagnostics(device_id: str, user=Depends(current_user)):
        d = device(device_id, user)
        state = app.state.manager.states[device_id]
        entry = app.state.plugins.entries.get(d.plugin_id)
        result = {
            "device_id": d.id,
            "name": d.name,
            "config_revision": d.config_revision,
            "enabled": d.enabled,
            "status": state.status,
            "connection_status": state.connection_status,
            "identity": state.identity,
            "network": state.network,
            "health": state.health,
            "plugin_id": d.plugin_id,
            "plugin_version": entry["descriptor"].plugin_version
            if entry and entry["descriptor"]
            else None,
            "plugin_enabled": bool(entry and entry["enabled"]),
            "plugin_error": entry["error"] if entry else "Plugin missing",
            "counters": app.state.diagnostics.counters.get(device_id, {}),
        }
        if "admin" in user["roles"]:
            worker = app.state.manager.supervisors.get(device_id)
            result.update(
                worker={
                    "pid": worker.process.pid,
                    "alive": worker.process.is_alive(),
                    "last_error": worker.failed,
                }
                if worker and worker.process
                else None,
                queue_size=app.state.manager.queues[device_id].qsize()
                if device_id in app.state.manager.queues
                else 0,
                capture_remaining=max(
                    0,
                    round(
                        app.state.diagnostics.capture_until.get(device_id, 0)
                        - asyncio.get_running_loop().time()
                    ),
                ),
                logs=list(app.state.diagnostics.logs.get(device_id, [])),
            )
        return result

    @app.post("/api/devices/{device_id}/diagnostics/capture")
    async def capture(device_id: str, data: Capture, user=Depends(admin)):
        device(device_id, user)
        app.state.diagnostics.capture_until[device_id] = (
            asyncio.get_running_loop().time() + data.seconds
        )
        if not data.seconds:
            app.state.diagnostics.logs[device_id] = deque(
                (r for r in app.state.diagnostics.logs[device_id] if r["kind"] != "transport"),
                maxlen=100,
            )
        await app.state.storage.audit(
            user["id"], "device.capture", device_id, {"seconds": data.seconds}
        )
        return {"seconds": data.seconds}

    @app.post("/api/devices/{device_id}/diagnostics/test")
    async def test(device_id: str, user=Depends(admin)):
        device(device_id, user)
        worker = app.state.manager.supervisors.get(device_id)
        if not worker or not app.state.manager.active(app.state.manager.devices[device_id]):
            raise HTTPException(409, "Device worker unavailable")
        started = asyncio.get_running_loop().time()
        try:
            async with asyncio.timeout(4):
                outcome = HealthCheckResult.model_validate(
                    await worker.rpc("health_check", timeout=3)
                )
            result = {
                "reachable": outcome.reachable,
                "degraded": outcome.degraded,
                "error": outcome.error,
                "latency_ms": round((asyncio.get_running_loop().time() - started) * 1000, 2),
            }
        except Exception:
            result = {
                "reachable": False,
                "degraded": False,
                "error": "Health check failed or timed out",
                "latency_ms": None,
            }
        await app.state.storage.audit(
            user["id"], "device.connection.test", device_id, {"reachable": result["reachable"]}
        )
        return result
