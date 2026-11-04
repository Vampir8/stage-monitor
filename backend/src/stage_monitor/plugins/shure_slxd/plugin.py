import asyncio
from contextlib import suppress
from decimal import Decimal, InvalidOperation

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

from .protocol import number, parse_frame, sample


class SLXDPlugin(BaseDevicePlugin):
    def __init__(self, *args):
        super().__init__(*args)
        endpoint, config = self.device.endpoint, self.device.custom_config
        if not endpoint or endpoint.transport != "tcp" or not endpoint.host:
            raise ValueError("SLX-D requires a TCP host")
        self.channel = config.get("channel", 1)
        self.control = config.get("enable_control", False)
        self.timeout = config.get("request_timeout", 1)
        self.interval = config.get("meter_interval_ms", 250)
        self.minimum = config.get("min_frequency_mhz")
        self.maximum = config.get("max_frequency_mhz")
        if self.channel not in {1, 2} or isinstance(self.channel, bool):
            raise ValueError("Select channel 1 or 2")
        if self.control and (
            self.minimum is None or self.maximum is None or self.minimum >= self.maximum
        ):
            raise ValueError("Configure the actual receiver frequency band before enabling control")
        self.transport = TCPTransport(
            endpoint.host, endpoint.port or 2202, timeout=3, max_bytes=2048
        )
        self.identity = DeviceIdentity(manufacturer="Shure", model="SLX-D")
        self.network = DeviceNetwork(
            host=endpoint.host, port=endpoint.port or 2202, transport="tcp"
        )
        self.metrics, self.parameters, self.vendor = {}, {}, {}
        self.observed = self.last_sample = None
        self.reader_task = self.pending = None
        self.lock = asyncio.Lock()
        self.metering_started = False
        self.last_publish = 0.0

    def get_commands(self):
        return super().get_commands() if self.control else []

    async def connect(self):
        await self.transport.connect()

    async def disconnect(self):
        await self.transport.disconnect()

    async def start(self):
        self.reader_task = self.context.create_task(self._reader(), name="slxd-reader")
        model = await self._exchange(None, "MODEL", "GET MODEL")
        if model not in {"SLXD4", "SLXD4D"} or (model == "SLXD4" and self.channel != 1):
            raise ValueError(
                "Expected SLXD4 channel 1 or SLXD4D channel 1/2; SLX-D+ is not supported"
            )
        await self._exchange(None, "FW_VER", "GET FW_VER")
        await self._send(f"GET {self.channel} ALL")
        await self._exchange(self.channel, "FREQUENCY", f"GET {self.channel} FREQUENCY")
        # Meter subscription is required for monitoring, even with RF/audio control disabled.
        await self._exchange(
            self.channel, "METER_RATE", f"SET {self.channel} METER_RATE {self.interval:05d}"
        )
        self.metering_started = True

    async def stop(self):
        if self.metering_started and self.transport.connected:
            with suppress(Exception):
                async with asyncio.timeout(0.5):
                    await self._send(f"SET {self.channel} METER_RATE 00000")
        self.metering_started = False
        if self.reader_task:
            self.reader_task.cancel()
            await asyncio.gather(self.reader_task, return_exceptions=True)
            self.reader_task = None

    async def _send(self, command):
        await self.transport.write(f"< {command} >".encode("ascii"))
        await self.context.trace("TX", command, sensitive=False)

    async def _exchange(self, channel, key, command):
        async with self.lock:
            future = asyncio.get_running_loop().create_future()
            self.pending = (channel, key), future
            try:
                async with asyncio.timeout(self.timeout):
                    await self._send(command)
                    return await future
            except BaseException:
                # No transaction IDs: an expired session cannot acknowledge a later command.
                await self.transport.disconnect()
                raise
            finally:
                self.pending = None

    def _metric(self, key, value, unit, quality=None):
        self.metrics[key] = Metric(
            device_id=self.device.id,
            key=key,
            channel=str(self.channel),
            value=value,
            unit=unit,
            quality=quality or ("good" if value is not None else "unknown"),
        )

    def _accept(self, kind, channel, key, value):
        if channel is not None and channel != self.channel:
            return False
        if kind == "SAMPLE":
            values = sample(value)
            if values is None:
                return False
            peak, rms, rssi = values
            for metric, reading, unit in [
                ("audio.level", rms, "dBFS"),
                ("audio.rms", rms, "dBFS"),
                ("audio.peak", peak, "dBFS"),
                ("rf.level", rssi, "dBm"),
                ("rf.rssi", rssi, "dBm"),
            ]:
                self._metric(metric, reading, unit)
            self.last_sample = now()
        elif channel is None and key in {"MODEL", "FW_VER", "RF_BAND", "DEVICE_ID", "LOCK_STATUS"}:
            self.vendor[key.lower()] = value[:64]
            if key == "MODEL":
                self.identity.model = value[:32]
            if key == "FW_VER":
                self.identity.firmware = value[:24]
        elif channel == self.channel and key == "FREQUENCY":
            frequency = number(value)
            if frequency is None or not 100000 <= frequency <= 999999:
                return False
            self.parameters["frequency_mhz"] = frequency / 1000
        elif channel == self.channel and key == "AUDIO_GAIN":
            gain = number(value, 60)
            if gain is None:
                return False
            self.parameters["gain_db"] = gain - 18
        elif channel == self.channel and key in {
            "CHAN_NAME",
            "GROUP_CHANNEL",
            "AUDIO_OUT_LVL_SWITCH",
            "METER_RATE",
            "TX_MODEL",
        }:
            self.vendor[key.lower()] = value[:64]
            if key == "CHAN_NAME":
                self.parameters["channel_name"] = value[:31]
            if key == "TX_MODEL" and value == "UNKNOWN":
                self._metric("battery.bars", None, "bars")
                self._metric("battery.runtime_minutes", None, "min")
        elif channel == self.channel and key in {"TX_BATT_BARS", "TX_BATT_MINS"}:
            raw = number(value, 65535)
            if raw is None:
                return False
            bars = key == "TX_BATT_BARS"
            maximum = 5 if bars else 65532
            unknown = raw == 255 if bars else raw >= 65533
            quality = "unknown" if unknown else "good" if raw <= maximum else "invalid"
            if self.vendor.get("tx_model") == "UNKNOWN":
                quality = "unknown"
            self._metric(
                "battery.bars" if bars else "battery.runtime_minutes",
                raw if quality == "good" else None,
                "bars" if bars else "min",
                quality,
            )
            if not bars:
                self.vendor["battery_runtime_state"] = {
                    65533: "warning",
                    65534: "calculating",
                    65535: "unknown",
                }.get(raw, "known")
        else:
            return False
        self.observed = now()
        return True

    async def _reader(self):
        try:
            while True:
                frame = await self.transport.read_until(b">")
                # Names may contain angle brackets inside the protocol's braced string.
                while frame.count(b"{") > frame.count(b"}"):
                    frame += await self.transport.read_until(b">")
                    if len(frame) > 2048:
                        raise ValueError("SLX-D frame exceeds size limit")
                parsed = parse_frame(frame)
                if not parsed or not self._accept(*parsed):
                    continue
                kind, channel, key, value = parsed
                if kind == "REP" and self.pending and self.pending[0] == (channel, key):
                    if not self.pending[1].done():
                        self.pending[1].set_result(value)
                clock = asyncio.get_running_loop().time()
                if clock - self.last_publish >= 0.25:
                    await self.context.publish_update(await self.get_state())
                    self.last_publish = clock
        except BaseException:
            if self.pending and not self.pending[1].done():
                self.pending[1].set_exception(ConnectionError("SLX-D stream closed"))
            raise

    async def poll(self):
        for key in ["TX_MODEL", "TX_BATT_BARS", "TX_BATT_MINS", "CHAN_NAME", "AUDIO_GAIN"]:
            await self._send(f"GET {self.channel} {key}")
        await self._exchange(None, "FW_VER", "GET FW_VER")
        return await self.get_state()

    async def get_state(self):
        stamp = now()
        connected = self.transport.connected
        metrics = [m.model_copy(deep=True) for m in self.metrics.values()]
        for metric in metrics:
            if metric.quality == "good" and (
                not connected
                or (stamp - metric.observed_at).total_seconds() > self.device.stale_after
            ):
                metric.quality = "stale"
        degraded = (
            not self.last_sample
            or (stamp - self.last_sample).total_seconds() > self.device.stale_after
            or "*" in (self.identity.firmware or "")
            or any(m.quality == "invalid" for m in metrics)
        )
        state = DeviceState(
            device_id=self.device.id,
            observed_at=self.observed,
            status=DeviceStatus.OFFLINE
            if not connected
            else DeviceStatus.DEGRADED
            if degraded
            else DeviceStatus.ONLINE,
            connection_status=ConnectionStatus.CONNECTED
            if connected
            else ConnectionStatus.DISCONNECTED,
            identity=self.identity.model_copy(),
            network=self.network.model_copy(),
            metrics=metrics,
            parameters=dict(self.parameters),
            extra={
                "vendor_specific": dict(self.vendor),
                "receiver_channel": self.channel,
                "control_enabled": self.control,
                "meter_scales": {"rf.level": [-120, 0], "audio.level": [-120, 0]},
            },
        )
        state.health.last_seen = self.observed
        return state

    async def get_status(self):
        return (await self.get_state()).status

    async def get_metrics(self):
        return (await self.get_state()).metrics

    async def health_check(self):
        if not self.transport.connected or not self.reader_task or self.reader_task.done():
            return HealthCheckResult(reachable=False)
        await self._exchange(None, "FW_VER", "GET FW_VER")
        return HealthCheckResult(
            reachable=True, degraded=(await self.get_state()).status == DeviceStatus.DEGRADED
        )

    async def execute_command(self, command, parameters):
        if not self.control or command not in {"set_frequency", "set_gain"}:
            raise PermissionError("SLX-D control disabled or command unsupported")
        if command == "set_frequency":
            try:
                frequency = Decimal(str(parameters["frequency_mhz"]))
            except (InvalidOperation, KeyError):
                raise ValueError("Invalid frequency") from None
            if not frequency.is_finite() or not Decimal(str(self.minimum)) <= frequency <= Decimal(
                str(self.maximum)
            ):
                raise ValueError("Frequency outside the configured receiver band")
            khz = frequency * 1000
            if khz != khz.to_integral_value():
                raise ValueError("Frequency must have at most three decimal places")
            key, expected = "FREQUENCY", int(khz)
            payload = str(expected)  # SLX-D SET accepts kHz; REP is padded to seven digits.
        else:
            gain = parameters.get("gain_db")
            if type(gain) is not int or not -18 <= gain <= 42:
                raise ValueError("Invalid gain")
            key, expected, payload = "AUDIO_GAIN", gain + 18, f"{gain + 18:03d}"
        reply = await self._exchange(self.channel, key, f"SET {self.channel} {key} {payload}")
        await self.context.publish_update(await self.get_state())
        return PluginCommandResult(
            confirmed=number(reply) == expected, result=dict(self.parameters)
        )
