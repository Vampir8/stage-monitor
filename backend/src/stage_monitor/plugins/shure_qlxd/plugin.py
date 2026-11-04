import asyncio
import re
from contextlib import suppress
from decimal import Decimal

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
from stage_monitor.transports import TCPTransport

from .protocol import numeric, parse_frame, sample


class QLXDPlugin(BaseDevicePlugin):
    def __init__(self, *args):
        super().__init__(*args)
        endpoint = self.device.endpoint
        if not endpoint or endpoint.transport != "tcp" or not endpoint.host:
            raise ValueError("QLXD4 requires a TCP host")
        config = self.device.custom_config
        self.control = config.get("enable_control", False)
        self.min_frequency = config.get("min_frequency_mhz")
        self.max_frequency = config.get("max_frequency_mhz")
        if self.control and (
            self.min_frequency is None
            or self.max_frequency is None
            or self.min_frequency >= self.max_frequency
        ):
            raise ValueError("Configure the receiver's actual frequency band before control")
        self.timeout = config.get("request_timeout", 1)
        self.meter_interval = config.get("meter_interval_ms", 250)
        self.transport = TCPTransport(
            endpoint.host, endpoint.port or 2202, timeout=3, max_bytes=2048
        )
        self.identity = DeviceIdentity(manufacturer="Shure", model="QLXD4")
        self.network = DeviceNetwork(
            host=endpoint.host, port=endpoint.port or 2202, transport="tcp"
        )
        self.parameters, self.metrics, self.vendor = {}, {}, {}
        self.observed_at = self.last_sample = None
        self.reader_task, self.pending = None, None
        self.request_lock = asyncio.Lock()
        self.last_publish = 0.0

    def get_commands(self):
        return super().get_commands() if self.control else []

    def get_capabilities(self):
        return [
            c if self.control else c.model_copy(update={"writable": False})
            for c in super().get_capabilities()
        ]

    async def connect(self):
        await self.transport.connect()

    async def disconnect(self):
        await self.transport.disconnect()

    async def start(self):
        self.reader_task = self.context.create_task(self._reader(), "qlxd-reader")
        await self._send("GET 1 ALL")
        await self._exchange("FW_VER", "GET FW_VER")
        await self._exchange("FREQUENCY", "GET 1 FREQUENCY")
        await self._send("GET MAC_ADDR")
        await self._exchange("METER_RATE", f"SET 1 METER_RATE {self.meter_interval:05d}")

    async def stop(self):
        if self.transport.connected:
            with suppress(Exception):
                async with asyncio.timeout(0.5):
                    await self._send("SET 1 METER_RATE 00000")
        if self.reader_task:
            self.reader_task.cancel()
            await asyncio.gather(self.reader_task, return_exceptions=True)
            self.reader_task = None

    async def _send(self, content):
        data = f"< {content} >".encode("ascii")
        await self.transport.write(data)
        # These generated commands contain numeric protocol fields, never credentials.
        await self.context.trace("TX", data.decode("ascii"), sensitive=False)

    async def _exchange(self, key, command):
        async with self.request_lock:
            future = asyncio.get_running_loop().create_future()
            self.pending = key, future
            try:
                async with asyncio.timeout(self.timeout):
                    await self._send(command)
                    return await future
            except BaseException:
                # No request IDs in this protocol: discard the connection after a timeout.
                # A late reply must not acknowledge a later command.
                await self.transport.disconnect()
                raise
            finally:
                self.pending = None

    def _metric(self, key, value, unit):
        self.metrics[key] = Metric(
            device_id=self.device.id,
            key=key,
            channel="1",
            value=value,
            unit=unit,
            quality="good" if value is not None else "unknown",
        )

    def _accept(self, kind, key, value):
        if kind == "SAMPLE":
            values = sample(value)
            if values is None:
                return False
            antenna, rf, af = values
            self.vendor["antenna"] = antenna
            self._metric("rf.level", rf, "raw")
            self._metric("audio.level", af, "raw")
            self.last_sample = now()
        elif key == "FW_VER":
            self.identity.firmware = value[:64]
        elif key == "MAC_ADDR":
            if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", value):
                return False
            self.network.mac = value
        elif key == "FREQUENCY":
            number = numeric(value, 100000, 999999)
            if number is None:
                return False
            self.parameters["frequency_mhz"] = number / 1000
        elif key == "AUDIO_GAIN":
            number = numeric(value, 0, 60)
            if number is None:
                return False
            self.parameters["gain_db"] = number - 18
        elif key in {
            "CHAN_NAME",
            "DEVICE_ID",
            "TX_TYPE",
            "BATT_TYPE",
            "GROUP_CHAN",
            "ENCRYPTION_WARNING",
            "TX_RF_PWR",
            "TX_MUTE_STATUS",
            "METER_RATE",
        }:
            self.vendor[key.lower()] = value[:120]
            if key == "BATT_TYPE" and value != "LION":
                for metric, unit in [
                    ("battery.percent", "%"),
                    ("battery.runtime_minutes", "min"),
                    ("battery.temperature", "degC"),
                ]:
                    self._metric(metric, None, unit)
            if key == "TX_TYPE" and value == "UNKN":
                for metric in list(self.metrics):
                    if metric.startswith("battery."):
                        self._metric(metric, None, self.metrics[metric].unit)
        elif key in {"BATT_CHARGE", "BATT_BARS", "BATT_RUN_TIME", "BATT_TEMP_C"}:
            mapping = {
                "BATT_CHARGE": ("battery.percent", "%", 100),
                "BATT_BARS": ("battery.bars", "bars", 5),
                "BATT_RUN_TIME": ("battery.runtime_minutes", "min", 65531),
                "BATT_TEMP_C": ("battery.temperature", "degC", 251),
            }
            metric, unit, maximum = mapping[key]
            number = numeric(value, 0, maximum)
            if key != "BATT_BARS" and self.vendor.get("batt_type") not in {None, "LION"}:
                number = None
            self._metric(
                metric, number - 40 if key == "BATT_TEMP_C" and number is not None else number, unit
            )
        else:
            return False
        self.observed_at = now()
        return True

    async def _reader(self):
        try:
            while True:
                frame = await self.transport.read_until(b">")
                parsed = parse_frame(frame)
                if not parsed:
                    continue
                kind, key, value = parsed
                if not self._accept(kind, key, value):
                    continue
                # Classify parsed numeric telemetry; free-text names remain masked.
                safe = key in {
                    "ALL",
                    "FREQUENCY",
                    "AUDIO_GAIN",
                    "METER_RATE",
                    "BATT_CHARGE",
                    "BATT_BARS",
                    "BATT_RUN_TIME",
                    "BATT_TEMP_C",
                }
                safe = safe and (
                    value.isdecimal()
                    or (
                        kind == "SAMPLE"
                        and re.fullmatch(r"(?:AX|XB|XX) \d{1,3} \d{1,3}", value) is not None
                    )
                )
                await self.context.trace(
                    "RX", {"kind": kind, "parameter": key, "value": value}, sensitive=not safe
                )
                if kind == "REP" and self.pending and self.pending[0] == key:
                    future = self.pending[1]
                    if not future.done():
                        future.set_result(value)
                clock = asyncio.get_running_loop().time()
                if clock - self.last_publish >= 0.25:
                    await self.context.publish_update(await self.get_state())
                    self.last_publish = clock
        except BaseException:
            if self.pending and not self.pending[1].done():
                self.pending[1].set_exception(ConnectionError("QLXD stream closed"))
            raise

    async def get_state(self):
        metrics = [
            self.metrics[t["key"]].model_copy(deep=True)
            for t in self.descriptor.telemetry
            if t["key"] in self.metrics
        ]
        for metric in metrics:
            if (now() - metric.observed_at).total_seconds() > self.device.stale_after:
                metric.quality = "stale"
        degraded = (
            self.last_sample is None
            or (now() - self.last_sample).total_seconds() > self.device.stale_after
            or self.vendor.get("encryption_warning") == "ON"
        )
        return DeviceState(
            device_id=self.device.id,
            observed_at=self.observed_at,
            status=DeviceStatus.OFFLINE
            if not self.transport.connected
            else DeviceStatus.DEGRADED
            if degraded
            else DeviceStatus.ONLINE,
            connection_status=ConnectionStatus.CONNECTED
            if self.transport.connected
            else ConnectionStatus.DISCONNECTED,
            identity=self.identity.model_copy(),
            network=self.network.model_copy(),
            parameters=dict(self.parameters),
            metrics=metrics,
            extra={
                "simulation": False,
                "vendor_specific": dict(self.vendor),
                "meter_scales": {"rf.level": [0, 115], "audio.level": [0, 50]},
                "control_enabled": self.control,
            },
        )

    async def get_status(self):
        return (await self.get_state()).status

    async def get_metrics(self):
        return (await self.get_state()).metrics

    async def poll(self):
        # Metering is push; refresh slow, change-only battery/configuration reports periodically.
        # Waiting for firmware after battery queries avoids inventing observation timestamps.
        for key in [
            "TX_TYPE",
            "BATT_TYPE",
            "BATT_BARS",
            "BATT_CHARGE",
            "BATT_RUN_TIME",
            "BATT_TEMP_C",
        ]:
            await self._send(f"GET 1 {key}")
        await self._exchange("FW_VER", "GET FW_VER")
        return await self.get_state()

    async def health_check(self):
        if not self.transport.connected or not self.reader_task or self.reader_task.done():
            return HealthCheckResult(reachable=False)
        await self._exchange("FW_VER", "GET FW_VER")
        return HealthCheckResult(
            reachable=True, degraded=(await self.get_state()).status == DeviceStatus.DEGRADED
        )

    async def execute_command(self, command, parameters):
        if not self.control or command not in {c.name for c in self.get_commands()}:
            raise ValueError("QLXD control disabled or command unsupported")
        if command == "set_frequency":
            frequency = Decimal(str(parameters["frequency_mhz"]))
            if (
                not Decimal(str(self.min_frequency))
                <= frequency
                <= Decimal(str(self.max_frequency))
            ):
                raise ValueError("Frequency outside the configured receiver band")
            khz = frequency * 1000
            if khz != khz.to_integral_value():
                raise ValueError("Frequency must have at most three decimal places")
            expected, key = f"{int(khz):06d}", "FREQUENCY"
        else:
            gain = parameters["gain_db"]
            if isinstance(gain, bool) or not isinstance(gain, int) or not -18 <= gain <= 42:
                raise ValueError("Invalid gain")
            expected, key = f"{gain + 18:03d}", "AUDIO_GAIN"
        reply = await self._exchange(key, f"SET 1 {key} {expected}")
        await self.context.publish_update(await self.get_state())
        return PluginCommandResult(confirmed=reply == expected, result=dict(self.parameters))
