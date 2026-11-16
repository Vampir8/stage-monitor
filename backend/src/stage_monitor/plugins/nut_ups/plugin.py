"""Read-only Network UPS Tools client for one UPS exposed by upsd."""

import asyncio
import math
import re
import shlex

from stage_monitor.contracts import (
    ConnectionStatus,
    DeviceIdentity,
    DeviceNetwork,
    DeviceState,
    DeviceStatus,
    HealthCheckResult,
    Metric,
    now,
)
from stage_monitor.sdk import BaseDevicePlugin
from stage_monitor.transports.streams import TCPTransport

DEFAULT_PORT = 3493
UPS_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
MEASUREMENTS = {
    "battery.charge": ("battery.percent", "%"),
    "battery.runtime": ("battery.runtime", "s"),
    "battery.voltage": ("battery.voltage", "V"),
    "ups.load": ("ups.load", "%"),
    "ups.realpower": ("power.active", "W"),
    "ups.power": ("power.apparent", "VA"),
    "ups.temperature": ("ups.temperature", "°C"),
    "input.voltage": ("input.voltage", "V"),
    "input.frequency": ("input.frequency", "Hz"),
    "output.voltage": ("output.voltage", "V"),
    "output.frequency": ("output.frequency", "Hz"),
}


class NutProtocolError(ConnectionError):
    pass


def number(values, key):
    try:
        value = float(values.get(key, ""))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


class NutUpsPlugin(BaseDevicePlugin):
    def __init__(self, *args):
        super().__init__(*args)
        endpoint = self.device.endpoint
        if endpoint is None or endpoint.transport != "tcp" or not endpoint.host:
            raise ValueError("NUT UPS requires an upsd TCP endpoint")
        self.network = DeviceNetwork(
            host=endpoint.host, port=endpoint.port or DEFAULT_PORT, transport="tcp"
        )
        self.configured_name = self.device.custom_config.get("ups_name")
        if self.configured_name is not None and not UPS_NAME.fullmatch(self.configured_name):
            raise ValueError("Invalid NUT UPS name")
        self.ups_name = None
        self.timeout = self.device.custom_config.get("request_timeout", 2)
        self.state = DeviceState(device_id=self.device.id, network=self.network)

    async def _list(self, kind, name=None):
        command = f"LIST {kind}" + (f" {name}" if name else "")
        transport = TCPTransport(
            self.network.host, self.network.port, timeout=self.timeout, max_bytes=2048,
            trace=self.context.trace,
        )
        async with asyncio.timeout(self.timeout), transport:
            await transport.write(f"{command}\n".encode("ascii"))
            lines = []
            end = f"END {command}"
            for index in range(514):
                reply = (await transport.read_until()).decode("utf-8", errors="replace").rstrip("\r\n")
                if reply.startswith("ERR "):
                    raise NutProtocolError(reply)
                if index == 0:
                    if reply != f"BEGIN {command}":
                        raise NutProtocolError("Unexpected NUT list header")
                    continue
                if reply == end:
                    return lines
                try:
                    fields = shlex.split(reply)
                except ValueError as exc:
                    raise NutProtocolError("Invalid NUT list row") from exc
                if name:
                    if len(fields) != 4 or fields[:2] != [kind, name]:
                        raise NutProtocolError("Unexpected NUT variable row")
                    lines.append((fields[2], fields[3]))
                else:
                    if len(fields) != 3 or fields[0] != kind:
                        raise NutProtocolError("Unexpected NUT UPS row")
                    if UPS_NAME.fullmatch(fields[1]):
                        lines.append((fields[1], fields[2]))
            raise NutProtocolError("NUT list exceeded its row limit")

    async def connect(self):
        if self.configured_name:
            self.ups_name = self.configured_name
            return
        available = await self._list("UPS")
        if len(available) != 1:
            raise NutProtocolError("Select a UPS name when the server does not list exactly one UPS")
        self.ups_name = available[0][0]

    async def disconnect(self):
        self.ups_name = None

    async def start(self):
        await self.poll()

    async def stop(self):
        pass

    async def inspect(self, action="read", seconds=30, cursor=0):
        if action != "configuration:ups_name":
            return await super().inspect(action, seconds, cursor)
        return [
            {"value": name, "label": description[:160] if description != "Unavailable" else ""}
            for name, description in (await self._list("UPS"))[:100]
        ]

    async def poll(self):
        if not self.ups_name:
            raise ConnectionError("NUT UPS is disconnected")
        try:
            values = dict(await self._list("VAR", self.ups_name))
            status = values.get("ups.status", "").strip()
            if not status:
                raise NutProtocolError("NUT response has no UPS status")
        except OSError:
            await self.disconnect()
            raise
        flags = set(status.split())
        observed = now()
        # Legrand HID 0.2 on the Keor PDU can report underscaled voltages.
        keor_legacy_hid = (
            values.get("ups.vendorid", "").lower() == "1cb0"
            and values.get("ups.productid", "").lower() == "0038"
            and values.get("driver.version.data") == "Legrand HID 0.2"
        )
        keor_legacy_battery = keor_legacy_hid and values.get("battery.voltage.nominal") == "1.2"
        metrics = [
            Metric(device_id=self.device.id, key="ups.status", value=status, observed_at=observed)
        ]
        for source, (key, unit) in MEASUREMENTS.items():
            value = number(values, source)
            if value is None:
                continue
            labels = {}
            quality = "good"
            if (
                (keor_legacy_battery and key == "battery.voltage" and 0 < value < 3)
                or (keor_legacy_hid and key == "output.voltage" and 0 < value < 30)
            ):
                labels = {"nut_raw_value": values[source], "nut_scale_factor": "10"}
                value *= 10
                if key == "output.voltage":
                    quality = "unknown"
            metrics.append(Metric(
                device_id=self.device.id, key=key, value=value, unit=unit, observed_at=observed,
                labels=labels, quality=quality,
            ))
        if number(values, "ups.realpower") is None:
            load = number(values, "ups.load")
            rated_watts = number(values, "ups.realpower.nominal")
            if load is not None and load >= 0 and rated_watts is not None and rated_watts > 0:
                estimated_watts = (load / 100) * rated_watts
                if math.isfinite(estimated_watts):
                    metrics.append(Metric(
                        device_id=self.device.id, key="ups.estimated_realpower",
                        value=round(estimated_watts, 0), unit="W", observed_at=observed,
                    ))
        self.state = DeviceState(
            device_id=self.device.id, status=DeviceStatus.ONLINE,
            connection_status=ConnectionStatus.CONNECTED, observed_at=observed,
            network=self.network,
            identity=DeviceIdentity(
                manufacturer=values.get("ups.mfr") or values.get("device.mfr"),
                model=values.get("ups.model") or values.get("device.model"),
                serial=values.get("ups.serial") or values.get("device.serial"),
                firmware=values.get("ups.firmware"),
            ),
            metrics=metrics,
            parameters={"ups.on_battery": "OB" in flags, "ups.low_battery": "LB" in flags},
            extra={"nut_ups_name": self.ups_name},
        )
        return self.state

    async def get_state(self):
        observed = self.state.observed_at
        if self.ups_name and observed and (now() - observed).total_seconds() < self.device.stale_after:
            return self.state
        return self.state.model_copy(update={
            "status": DeviceStatus.OFFLINE,
            "connection_status": ConnectionStatus.DISCONNECTED,
            "metrics": [metric.model_copy(update={"quality": "stale"}) for metric in self.state.metrics],
        })

    async def get_status(self):
        return (await self.get_state()).status

    async def get_metrics(self):
        return (await self.get_state()).metrics

    async def health_check(self):
        return HealthCheckResult(reachable=await self.get_status() == DeviceStatus.ONLINE)

    async def execute_command(self, command, parameters):
        raise PermissionError("NUT UPS monitoring is read-only")
