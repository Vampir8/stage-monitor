import asyncio
import contextlib
import json
import math

from stage_monitor.contracts import Metric, now
from stage_monitor.transports import WebSocketTransport


FIELDS = {
    'battery_cap': ('battery.percent', '%', 0, 100),
    'battery_temperature': ('battery.temperature', 'degC', -40, 120),
    'lens_temperature': ('camera.lens_temperature', 'degC', -40, 120),
}


class PortalTelemetry:
    """Optional read-only channel. Its failure must not break VISCA control."""

    def __init__(self, device):
        host = device.endpoint.host
        if ':' in host:
            host = '[' + host + ']'
        port = device.custom_config.get('portal_port', 80)
        self.transport = WebSocketTransport(f'ws://{host}:{port}/ws/', timeout=5, max_bytes=16384)
        self.device = device
        self.task = None
        self.values = {}
        self.last_error = None

    def accept(self, frame):
        if not isinstance(frame, str):
            raise ValueError('Expected portal JSON text')
        data = json.loads(frame)
        status = data.get('device_status') if isinstance(data, dict) else None
        if not isinstance(status, dict):
            raise ValueError('Missing portal device_status')
        timestamp = now()
        # Replace the sample, so missing or invalid values cannot retain good quality.
        self.values = {}
        for field, (key, unit, minimum, maximum) in FIELDS.items():
            value = status.get(field)
            valid = type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum
            sensor = 'is_battery_online' if field.startswith('battery') else 'is_lens_online'
            if status.get(sensor) is False:
                valid = False
            self.values[key] = Metric(device_id=self.device.id, key=key,
                                      value=value if valid else None, unit=unit,
                                      observed_at=timestamp, quality='good' if valid else 'unknown',
                                      labels={'source': 'obsbot.device_portal'})
        # Do not store or trace the full frame (may include stream keys).
        self.last_error = None

    def metrics(self):
        stale = not self.transport.connected
        result = []
        for key, unit, _, _ in FIELDS.values():
            metric = self.values.get(key)
            if metric is None:
                metric = Metric(device_id=self.device.id, key=key, value=None, unit=unit, quality='unknown')
            elif stale or (now() - metric.observed_at).total_seconds() > self.device.stale_after:
                metric = metric.model_copy(update={'quality': 'stale'})
            result.append(metric)
        return result

    async def run(self):
        delay = 1
        while True:
            try:
                await self.transport.connect()
                while True:
                    self.accept(await self.transport.receive())
                    delay = 1
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Error type only: URLs and payloads must not leak into telemetry/logs.
                self.last_error = type(error).__name__
            finally:
                await self.transport.disconnect()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        await self.transport.disconnect()
