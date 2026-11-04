"""A real HTTP adapter template, not a device simulator.

Expected GET /api/status: {"temperature": 22.4, "humidity": 48, "output": false}.
Optional PUT /api/output accepts {"enabled": true}; confirmation requires readback.
Only use this adapter with a device that implements this documented example API.
"""
import asyncio
import json
import math

from stage_monitor.contracts import (
    ConnectionStatus,
    DeviceIdentity,
    DeviceNetwork,
    DeviceState,
    DeviceStatus,
    HealthCheckResult,
    Metric,
    PluginCommandResult,
    now,
)
from stage_monitor.sdk import BaseDevicePlugin
from stage_monitor.transports.http import HTTPTransport


class HTTPSensorPlugin(BaseDevicePlugin):
    def __init__(self, device, context, descriptor):
        super().__init__(device, context, descriptor)
        endpoint = device.endpoint
        if not endpoint or endpoint.transport not in {"http", "https"} or not endpoint.host:
            raise ValueError("Configure an HTTP(S) device endpoint")
        host = f"[{endpoint.host}]" if ":" in endpoint.host else endpoint.host
        port = f":{endpoint.port}" if endpoint.port else ""
        self.transport = HTTPTransport(f"{endpoint.transport}://{host}{port}",
                                       timeout=2, max_bytes=16384, trace=context.trace)
        self.path = device.custom_config.get("status_path", "/api/status")
        self.control_enabled = device.custom_config.get("enable_control", False)
        self.lock = asyncio.Lock()
        self.state = DeviceState(device_id=device.id,
            identity=DeviceIdentity(manufacturer=descriptor.manufacturer, model=descriptor.model),
            network=DeviceNetwork(host=endpoint.host, port=endpoint.port,
                                  transport=endpoint.transport))

    def get_commands(self):
        # The runtime may narrow the manifest, but cannot invent or change commands.
        return super().get_commands() if self.control_enabled else []

    async def connect(self):
        await self.transport.connect()
        await self.poll()  # An HTTP pool alone does not prove that a device responded.

    async def disconnect(self):
        await self.transport.disconnect()
        self.state.connection_status = ConnectionStatus.DISCONNECTED
        self.state.status = DeviceStatus.OFFLINE

    async def start(self):
        pass  # Device Manager schedules polling; do not start a duplicate polling loop.

    async def stop(self):
        pass  # Push adapters cancel subscriptions here; this polling adapter has none.

    async def poll(self):
        async with self.lock:
            return await self._read_status()

    async def _read_status(self):
        response = await self.transport.request("GET", self.path)
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError("Expected a status object")
        stamp = now()
        metrics = []
        for key, source, unit, limits in [
            ("environment.temperature", "temperature", "°C", (-100, 200)),
            ("environment.humidity", "humidity", "%", (0, 100)),
        ]:
            value = data.get(source)
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            valid = numeric and math.isfinite(value) and limits[0] <= value <= limits[1]
            quality = "good" if valid else "unknown" if value is None else "invalid"
            metrics.append(Metric(device_id=self.device.id, key=key,
                value=value if valid else None, unit=unit, quality=quality,
                observed_at=stamp, received_at=stamp))
        output = data.get("output")
        parameters = {"output_enabled": output} if isinstance(output, bool) else {}
        self.state = DeviceState(device_id=self.device.id,
            status=DeviceStatus.ONLINE if all(m.quality == "good" for m in metrics)
                else DeviceStatus.DEGRADED,
            connection_status=ConnectionStatus.CONNECTED,
            observed_at=stamp, identity=self.state.identity, network=self.state.network,
            metrics=metrics, parameters=parameters)
        self.state.health.last_seen = stamp
        return self.state.model_copy(deep=True)

    async def get_state(self):
        return self.state.model_copy(deep=True)

    async def get_status(self):
        return self.state.status

    async def get_metrics(self):
        return [metric.model_copy(deep=True) for metric in self.state.metrics]

    async def execute_command(self, command, parameters):
        if command != "set_output" or not self.control_enabled:
            raise PermissionError("Output control is unavailable")
        if set(parameters) != {"enabled"} or type(parameters["enabled"]) is not bool:
            raise ValueError("Expected a boolean enabled parameter")
        async with self.lock:
            await self.transport.request("PUT", "/api/output",
                content=json.dumps({"enabled": parameters["enabled"]}).encode("utf-8"))
            state = await self._read_status()
            confirmed = state.parameters.get("output_enabled") is parameters["enabled"]
            return PluginCommandResult(confirmed=confirmed,
                result={"requested_enabled": parameters["enabled"], "readback_matches": confirmed})

    async def health_check(self):
        seen = self.state.health.last_seen
        reachable = bool(self.transport.connected and seen and
                         (now() - seen).total_seconds() < self.device.stale_after)
        return HealthCheckResult(reachable=reachable,
                                 degraded=self.state.status == DeviceStatus.DEGRADED)
