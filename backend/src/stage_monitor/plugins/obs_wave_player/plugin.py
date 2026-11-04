import asyncio
import json
import math
import time

from stage_monitor.contracts import (
    ConnectionStatus, DeviceIdentity, DeviceNetwork, DeviceState, DeviceStatus,
    HealthCheckResult, Metric, PluginCommandResult, now,
)
from stage_monitor.sdk import BaseDevicePlugin
from stage_monitor.transports.http import HTTPTransport


class WavePlayerPlugin(BaseDevicePlugin):
    def __init__(self, *args):
        super().__init__(*args)
        endpoint = self.device.endpoint
        if not endpoint or not endpoint.host:
            raise ValueError('Wave Player requires a host')
        self.transport = HTTPTransport(
            f'http://{endpoint.host}:{endpoint.port or 8765}',
            timeout=30, max_bytes=1024 * 1024,
        )
        self.state = DeviceState(device_id=self.device.id)
        self.meter_task = None
        self.meter_frame = None
        self.meter_received = 0

    async def connect(self):
        password = await self.context.get_password()
        if not password or not password.strip() or '\n' in password or '\r' in password:
            raise ValueError('Enter the Wave Player connection key in credentials')
        self.transport.headers = {'Authorization': 'Bearer ' + password.strip(),
                                  'Content-Type': 'application/json'}
        await self.transport.connect()
        try:
            await self.poll()
        except Exception:
            await self.transport.disconnect()
            raise

    async def disconnect(self):
        await self.stop()
        await self.transport.disconnect()
        self.transport.headers = None

    async def start(self):
        if self.meter_task is None or self.meter_task.done():
            self.meter_task = self.context.create_task(self.receive_meters(), name='wave-player-meters')

    async def stop(self):
        if self.meter_task:
            self.meter_task.cancel()
            await asyncio.gather(self.meter_task, return_exceptions=True)
            self.meter_task = None

    def meter_metrics(self):
        frame = self.meter_frame or {}
        valid = frame.get('valid') is True and time.monotonic() - self.meter_received < 2
        metrics = []
        for channel in frame.get('channels', [{'channel': 'L'}, {'channel': 'R'}]):
            for key, field in [('audio.level', 'rmsDb'), ('audio.peak', 'peakDb')]:
                metrics.append(Metric(device_id=self.device.id, key=key, channel=channel['channel'],
                                      value=channel.get(field) if valid else None, unit='dBFS',
                                      quality='good' if valid else 'stale'))
        return metrics

    async def apply_meter_frame(self, frame):
        if frame.get('version') != 1 or type(frame.get('valid')) is not bool:
            raise ValueError('Invalid meter protocol')
        channels = frame.get('channels')
        if not isinstance(channels, list) or len(channels) != 2:
            raise ValueError('Invalid stereo channels')
        for index, channel in enumerate(channels):
            if channel.get('channel') != ['L', 'R'][index]:
                raise ValueError('Invalid stereo channel order')
            for field in ['rmsDb', 'peakDb']:
                value = channel.get(field)
                if frame['valid'] and (type(value) not in (float, int) or not math.isfinite(value) or not -96 <= value <= 12):
                    raise ValueError('Invalid audio level')
        self.meter_frame = frame
        self.meter_received = time.monotonic()
        self.state.metrics = [metric for metric in self.state.metrics if metric.key not in {'audio.level', 'audio.peak'}] + self.meter_metrics()
        self.state.extra = {**self.state.extra, 'meter_clipping': {channel['channel']: bool(channel.get('clip')) if frame['valid'] else False for channel in channels}}
        await self.context.publish_update(self.state)

    async def receive_meters(self):
        while self.transport.connected:
            try:
                async with self.transport.client.stream('GET', '/api/v1/meter-events', headers={'Accept': 'text/event-stream'}, timeout=5) as response:
                    response.raise_for_status()
                    if response.is_redirect or 'text/event-stream' not in response.headers.get('content-type', ''):
                        raise ConnectionError('Invalid meter stream')
                    buffer = b''
                    async for chunk in response.aiter_bytes():
                        if len(chunk) > 65536:
                            raise ValueError('Meter stream limit exceeded')
                        buffer += chunk
                        while b'\n\n' in buffer:
                            event, buffer = buffer.split(b'\n\n', 1)
                            if len(event) > 8192:
                                raise ValueError('Meter frame limit exceeded')
                            for line in event.splitlines():
                                if line.startswith(b'data: '):
                                    await self.apply_meter_frame(json.loads(line[6:]))
                        if len(buffer) > 8192:
                            raise ValueError('Meter frame limit exceeded')
                    raise ConnectionError('Meter stream closed')
            except Exception:
                self.meter_frame = None
                self.state.metrics = [metric for metric in self.state.metrics if metric.key not in {'audio.level', 'audio.peak'}] + self.meter_metrics()
                await self.context.publish_update(self.state)
                await asyncio.sleep(1)

    async def get_status(self):
        return self.state.status

    async def get_state(self):
        return self.state

    async def get_metrics(self):
        return self.state.metrics

    async def poll(self):
        data = (await self.transport.request('GET', '/api/v1/status')).json()
        if data.get('apiVersion') != 1:
            raise ValueError('Unsupported Wave Player API version')
        for field in ['position', 'remaining', 'duration', 'volume', 'bpm', 'age']:
            value = data.get(field)
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError('Invalid Wave Player state')
        if type(data.get('connected')) is not bool or type(data.get('playing')) is not bool:
            raise ValueError('Invalid Wave Player connection state')
        connected = data['connected'] and data['age'] <= 5000
        library = data.get('library') or {}
        self.state = DeviceState(
            device_id=self.device.id,
            status=DeviceStatus.ONLINE if connected else DeviceStatus.DEGRADED,
            connection_status=ConnectionStatus.CONNECTED,
            observed_at=now(),
            identity=DeviceIdentity(manufacturer='OBS Wave Player', model='MP3 Network Player',
                                    firmware='API 1'),
            network=DeviceNetwork(host=self.device.endpoint.host,
                                  port=self.device.endpoint.port or 8765, transport='http'),
            parameters={'title': str(data.get('title', '')), 'playing': data['playing'],
                        'volume': data['volume'], 'mode': library.get('mode', 'single')},
            metrics=[Metric(device_id=self.device.id, key='player.bpm', value=data['bpm'],
                            unit='BPM', quality='good' if connected else 'stale'), *self.meter_metrics()],
            extra={'wave_player': {key: data.get(key) for key in
                   ['title', 'playing', 'position', 'remaining', 'duration', 'bpm', 'volume',
                    'library']}, 'player_connected': connected},
        )
        return self.state

    async def execute_command(self, command, parameters):
        if command not in {item.name for item in self.get_commands()}:
            raise ValueError('Unsupported Wave Player command')
        result = (await self.transport.request(
            'POST', '/api/v1/command',
            content=json.dumps({'type': command, **parameters}).encode(),
        )).json()
        if result.get('ok') is not True:
            raise ConnectionError('Wave Player did not confirm execution')
        await self.context.publish_update(await self.poll())
        return PluginCommandResult(confirmed=True, result={'request_id': result['requestId']})

    async def health_check(self):
        await self.poll()
        return HealthCheckResult(reachable=True, degraded=not self.state.extra['player_connected'])
