import asyncio
import hashlib
import math
import struct
import time
from contextlib import suppress

from stage_monitor.contracts import (
    ConnectionStatus, DeviceIdentity, DeviceNetwork, DeviceState, DeviceStatus,
    HealthCheckResult, Metric, now,
)
from stage_monitor.sdk import BaseDevicePlugin
from stage_monitor.transports import TCPTransport

from .protocol import MAX_FRAME, announce, discovery, response, string, subscribe, u32

FIELDS = {
    'CurrentBPM': ('player.bpm', 'number', 'BPM', 0, 999),
    'Play': ('player.playing', 'boolean', None, None, None),
    'Track/SongName': ('player.title', 'string', None, None, None),
    'Track/ArtistName': ('player.artist', 'string', None, None, None),
    'Track/SongLoaded': ('player.loaded', 'boolean', None, None, None),
    'Track/CurrentKeyIndex': ('player.key_index', 'number', None, 0, 23),
    'SyncMode': ('player.sync', 'boolean', None, None, None),
    'DeckIsMaster': ('player.master', 'boolean', None, None, None),
    'Track/LoopEnableState': ('player.loop', 'boolean', None, None, None),
}


class SC6000Plugin(BaseDevicePlugin):
    def __init__(self, *args):
        super().__init__(*args)
        endpoint = self.device.endpoint
        if not endpoint or not endpoint.host or endpoint.transport != 'udp':
            raise ValueError('SC6000 requires a StageLinQ UDP endpoint')
        self.host, self.port = endpoint.host, endpoint.port or 51337
        self.token = bytes(8) + hashlib.sha256(self.device.id.encode()).digest()[:8]
        self.transport = self.context.udp_transport(self.host, self.port, timeout=2,
                                                   local_port=51337, max_bytes=4096)
        self.directory = self.states = self.server = None
        self.peer = None
        self.values = {}
        self.received = 0
        self.observed = None
        self.tasks = []
        self.layers = [1, 2] if self.device.custom_config.get('layers', 'both') == 'both' else \
            [1 if self.device.custom_config['layers'] == 'A' else 2]
        self.paths = {f'/Engine/Deck{layer}/{name}': (str(layer), spec)
                      for layer in self.layers for name, spec in FIELDS.items()}

    async def _incoming(self, reader, writer):
        # Our deliberately low token makes this a dialing peer; no incoming services.
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()

    async def _string(self, transport):
        size = struct.unpack('!I', await transport.read_exactly(4))[0]
        if size > 2048 or size % 2:
            raise ValueError('Invalid service name')
        return (await transport.read_exactly(size)).decode('utf-16-be') if size else ''

    async def _directory_message(self):
        kind = struct.unpack('!I', await self.directory.read_exactly(4))[0]
        token = await self.directory.read_exactly(16)
        if token != self.peer['token']:
            raise ValueError('Foreign directory token')
        if kind == 0:
            name = await self._string(self.directory)
            port = struct.unpack('!H', await self.directory.read_exactly(2))[0]
            return name, port
        if kind == 1:
            sender = await self.directory.read_exactly(16)
            value = await self.directory.read_exactly(8)
            if sender == bytes(16):
                await self.directory.write(u32(1) + token + self.token + value)
        elif kind != 2:
            raise ValueError('Unknown directory message')
        return None

    async def connect(self):
        self.values.clear()
        self.received, self.observed = 0, None
        self.server = await asyncio.start_server(self._incoming, '0.0.0.0', 0, backlog=2)
        self.local_port = self.server.sockets[0].getsockname()[1]
        try:
            await self.transport.connect()
            await self.transport.write(announce(self.token, self.local_port))
            async with asyncio.timeout(2):
                while True:
                    data = await self.transport.receive()
                    try:
                        peer = discovery(data)
                    except (ValueError, UnicodeError):
                        continue
                    if peer['token'] == self.token or 'offline' in peer['software'].casefold():
                        continue
                    self.peer = peer
                    break
            self.directory = TCPTransport(self.host, self.peer['port'], timeout=30, max_bytes=MAX_FRAME)
            async with asyncio.timeout(2):
                await self.directory.connect()
                await self.directory.write(u32(2) + self.token)
                while True:
                    entry = await self._directory_message()
                    if entry and entry[0] == 'StateMap' and entry[1]:
                        state_port = entry[1]
                        break
            self.states = TCPTransport(self.host, state_port, timeout=30, max_bytes=MAX_FRAME)
            async with asyncio.timeout(2):
                await self.states.connect()
                local = self.states.writer.get_extra_info('sockname')[1]
                await self.states.write(u32(0) + self.token + string('StateMap') + struct.pack('!H', local))
            self.connected_at = time.monotonic()
            self.tasks = [self.context.create_task(self._read_states(), 'denon-states'),
                          self.context.create_task(self._keep_directory(), 'denon-directory'),
                          self.context.create_task(self._announce(), 'denon-discovery')]
        except BaseException:
            await self.disconnect()
            raise

    async def _announce(self):
        while True:
            await asyncio.sleep(1)
            await self.transport.write(announce(self.token, self.local_port))

    async def _keep_directory(self):
        while True:
            await self._directory_message()

    def accept(self, path, value):
        if path not in self.paths:
            return
        channel, (key, kind, unit, minimum, maximum) = self.paths[path]
        raw = value.get({'number':'value', 'boolean':'state', 'string':'string'}[kind]) if isinstance(value, dict) else None
        valid = (type(raw) is bool if kind == 'boolean' else
                 isinstance(raw, str) and len(raw) <= 1000 if kind == 'string' else
                 type(raw) in (int, float) and math.isfinite(raw) and minimum <= raw <= maximum)
        timestamp = now()
        self.values[path] = Metric(device_id=self.device.id, key=key, channel=channel,
            value=raw if valid else None, unit=unit, quality='good' if valid else 'unknown',
            observed_at=timestamp)
        self.received, self.observed = time.monotonic(), timestamp

    async def _read_states(self):
        while True:
            size = struct.unpack('!I', await self.states.read_exactly(4))[0]
            if not 16 <= size <= MAX_FRAME:
                raise ValueError('Invalid StateMap frame size')
            path, value = response(await self.states.read_exactly(size))
            self.accept(path, value)

    async def start(self):
        await self._subscribe()

    async def _subscribe(self):
        interval = self.device.custom_config.get('update_interval_ms', 250)
        await self.states.write(b''.join(subscribe(path, interval) for path in self.paths))

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    async def disconnect(self):
        await self.stop()
        for stream in (self.states, self.directory, self.transport):
            if stream:
                await stream.disconnect()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def poll(self):
        if self.context.failure:
            raise ConnectionError('StageLinQ reader failed')
        await self._subscribe()
        if time.monotonic() - (self.received or self.connected_at) > self.device.stale_after:
            raise TimeoutError('No recent SC6000 states')
        return await self.get_state()

    async def get_state(self):
        metrics = []
        for path, (channel, (key, _, unit, _, _)) in self.paths.items():
            sample = self.values.get(path)
            if sample:
                sample = sample.model_copy(deep=True)
                if (now() - sample.observed_at).total_seconds() > self.device.stale_after:
                    sample.quality = 'stale'
            metrics.append(sample or Metric(device_id=self.device.id, key=key, channel=channel,
                                            unit=unit, value=None, quality='unknown'))
        current = any(m.quality == 'good' for m in metrics)
        return DeviceState(device_id=self.device.id,
            status=DeviceStatus.ONLINE if current else DeviceStatus.DEGRADED,
            connection_status=ConnectionStatus.CONNECTED, observed_at=self.observed,
            identity=DeviceIdentity(manufacturer='Denon DJ', model='SC6000',
                firmware=self.peer['version'] if self.peer else None),
            network=DeviceNetwork(host=self.host, port=self.port, transport='udp'), metrics=metrics,
            extra={'protocol':'StageLinQ', 'read_only':True, 'hardware_verified':False,
                   'layers':{str(n):'A' if n == 1 else 'B' for n in self.layers}})

    async def get_metrics(self):
        return (await self.get_state()).metrics

    async def get_status(self):
        return (await self.get_state()).status

    async def health_check(self):
        return HealthCheckResult(reachable=self.states is not None and self.states.connected,
            degraded=(await self.get_status()) != DeviceStatus.ONLINE)

    async def execute_command(self, command, parameters):
        raise PermissionError('SC6000 plugin is read-only')
