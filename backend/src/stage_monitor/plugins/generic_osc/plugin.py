import asyncio
import ipaddress
import math
import time
from collections import deque
from contextlib import suppress

from jsonschema import Draft202012Validator

from stage_monitor.contracts import (
    ConnectionStatus, DeviceIdentity, DeviceNetwork, DeviceState, DeviceStatus,
    HealthCheckResult, Metric, PluginCommandResult, new_id, now,
)
from stage_monitor.sdk import BaseDevicePlugin
from stage_monitor.transports.udp_duplex import UDPDuplexTransport

from .protocol import MAX_PACKET, decode, encode
from .controls import arguments_for, validate_control


class OSCPlugin(BaseDevicePlugin):
    def __init__(self, *args):
        super().__init__(*args)
        config = {key: value['default'] for key, value in self.descriptor.configuration_schema['properties'].items()
                  if 'default' in value}
        config.update(self.device.custom_config)
        Draft202012Validator(self.descriptor.configuration_schema).validate(config)
        ipaddress.IPv4Address(config['bind_host'])
        self.config = config
        endpoint = self.device.endpoint
        if not endpoint or endpoint.transport != 'udp' or not endpoint.host:
            raise ValueError('OSC requires a remote UDP endpoint')
        self.statuses, self.buttons = config['statuses'], config['buttons']
        for items in [self.statuses, self.buttons]:
            if len({item['id'] for item in items}) != len(items):
                raise ValueError('OSC IDs must be unique within each list')
        status_by_id = {item['id']: item for item in self.statuses}
        for item in self.statuses:
            if item.get('display', 'status') != 'status' and not item['minimum'] < item['maximum']:
                raise ValueError('Invalid OSC meter range')
        for item in self.buttons:
            validate_control(item)
            feedback_id = item.get('feedback_id')
            if feedback_id and feedback_id not in status_by_id:
                raise ValueError('Unknown OSC feedback status')
            if item.get('kind') == 'slider' and feedback_id and status_by_id[feedback_id]['value_type'] != 'number':
                raise ValueError('Slider feedback must be numeric')
        self.queries = set()
        for item in self.statuses:
            if item.get('query_address'):
                self.queries.add(encode(item['query_address'], item.get('query_arguments', [])))
        self.packets = {item['id']: encode(item['address'], item.get('arguments', [])) for item in self.buttons}
        self.transport = UDPDuplexTransport(endpoint.host, endpoint.port or 9000,
            local_port=config['receive_port'], bind_host=config['bind_host'],
            timeout=2, max_bytes=MAX_PACKET, queue_size=256)
        self.reader = None
        self.samples = {}
        self.last_query = float('-inf')
        self.last_feedback = None
        self.bad_packets = 0
        self.connected_at = None
        self.control_lock = asyncio.Lock()
        self.held = {}
        self.lease_task = None
        self.capture_until = 0
        self.capture_id = new_id()
        self.capture_sequence = 0
        self.capture_messages = deque(maxlen=100)
        self.capture_window = 0
        self.capture_count = self.capture_dropped = 0

    async def connect(self):
        self.samples.clear()
        self.last_feedback = None
        self.last_query = float('-inf')
        await self.transport.connect()
        self.connected_at = now()

    async def start(self):
        if self.reader is None or self.reader.done():
            self.reader = self.context.create_task(self._receive(), name='osc-feedback')
        if self.lease_task is None or self.lease_task.done():
            self.lease_task = self.context.create_task(self._release_loop(), name='osc-momentary-leases')

    async def stop(self):
        if self.lease_task:
            self.lease_task.cancel()
            await asyncio.gather(self.lease_task, return_exceptions=True)
            self.lease_task = None
        async with self.control_lock:
            for entry in self.held.values():
                with suppress(Exception):
                    await self._send(entry['control'], 'release')
            self.held.clear()
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
            self.reader = None
        self.capture_until = 0
        self.capture_messages.clear()

    async def disconnect(self):
        await self.stop()
        await self.transport.disconnect()

    async def _receive(self):
        while self.transport.connected:
            try:
                packet = await self.transport.receive(timeout=60, disconnect_on_timeout=False)
            except TimeoutError:
                continue
            try:
                messages = decode(packet)
            except (ValueError, UnicodeError):
                self.bad_packets += 1
                continue
            for address, arguments in messages:
                self._capture('RX', address, arguments)
                for item in self.statuses:
                    index = item.get('argument_index', 0)
                    if item['address'] != address or index >= len(arguments):
                        continue
                    value = arguments[index]
                    kind = item['value_type']
                    valid = (type(value) in (int, float) and math.isfinite(value) if kind == 'number' else
                             isinstance(value, str) if kind == 'string' else
                             type(value) is bool or type(value) in (int, float) and value in (0, 1))
                    if kind == 'boolean' and valid:
                        value = bool(value)
                    timestamp = now()
                    self.samples[item['id']] = Metric(device_id=self.device.id, key='osc.'+item['id'],
                        value=value if valid else None, unit=item.get('unit') or None,
                        quality='good' if valid else 'invalid', observed_at=timestamp,
                        labels={'title': item['label'], 'osc_address': address})
                    self.last_feedback = timestamp

    async def poll(self):
        if self.context.failure:
            raise ConnectionError('OSC feedback reader failed')
        if self.queries and time.monotonic() - self.last_query >= self.config['query_interval']:
            for packet in self.queries:
                await self.transport.write(packet)
                if time.monotonic() < self.capture_until:
                    for address, arguments in decode(packet):
                        self._capture('TX', address, arguments)
            self.last_query = time.monotonic()
        return await self.get_state()

    async def get_state(self):
        timestamp, metrics = now(), []
        for item in self.statuses:
            metric = self.samples.get(item['id'])
            if metric:
                metric = metric.model_copy(deep=True)
                if (timestamp-metric.observed_at).total_seconds() > self.device.stale_after:
                    metric.quality = 'stale'
            metrics.append(metric or Metric(device_id=self.device.id, key='osc.'+item['id'],
                value=None, unit=item.get('unit') or None, quality='unknown'))
        fresh = bool(metrics) and all(metric.quality == 'good' for metric in metrics)
        connected = self.transport.connected
        return DeviceState(device_id=self.device.id,
            status=DeviceStatus.ONLINE if connected and fresh else DeviceStatus.DEGRADED if connected else DeviceStatus.OFFLINE,
            connection_status=ConnectionStatus.CONNECTED if connected else ConnectionStatus.DISCONNECTED,
            observed_at=self.last_feedback or self.connected_at or timestamp,
            identity=DeviceIdentity(manufacturer='Generic', model='OSC UDP'),
            network=DeviceNetwork(host=self.device.endpoint.host, port=self.device.endpoint.port or 9000, transport='udp'),
            metrics=metrics, extra={'osc': {'last_feedback': self.last_feedback.isoformat() if self.last_feedback else None,
                'receive_port': self.transport.socket.get_extra_info('sockname')[1] if connected else None,
                'malformed_packets': self.bad_packets, 'remote_confirmed': fresh}})

    async def get_status(self):
        return (await self.get_state()).status

    async def get_metrics(self):
        return (await self.get_state()).metrics

    async def health_check(self):
        return HealthCheckResult(reachable=self.transport.connected,
                                 degraded=(await self.get_status()) != DeviceStatus.ONLINE)

    async def execute_command(self, command, parameters):
        if command == 'test_osc':
            spec = next(c for c in self.descriptor.commands if c.name == command)
            Draft202012Validator(spec.parameters_schema).validate(parameters)
            control = parameters['control']
            validate_control(control)
            key = 'test:'+control['id']
        elif command == 'send_osc' and not set(parameters)-{'button_id', 'action', 'value'}:
            control = next((item for item in self.buttons if item['id'] == parameters.get('button_id')), None)
            if not control:
                raise ValueError('Unknown configured OSC control')
            key = control['id']
        else:
            raise ValueError('Unsupported OSC command')
        action = parameters.get('action', {'trigger': 'trigger', 'momentary': 'press', 'toggle': 'on', 'slider': 'set'}[control.get('kind', 'trigger')])
        if control.get('kind') == 'toggle' and command != 'test_osc':
            feedback = self.samples.get(control['feedback_id'])
            if not feedback or feedback.quality != 'good' or (now()-feedback.observed_at).total_seconds() > self.device.stale_after:
                raise ValueError('Toggle requires fresh feedback')
        arguments_for(control, action, parameters.get('value'))
        transmitted = False
        async with self.control_lock:
            if action == 'press' and key in self.held and self.held[key]['control'] != control:
                await self._send(self.held[key]['control'], 'release')
                self.held.pop(key)
            # Renew a held control without retransmitting its press edge.
            if action != 'press' or key not in self.held:
                await self._send(control, action, parameters.get('value'))
                transmitted = True
            if action == 'press':
                self.held[key] = {'control': control, 'expires': time.monotonic()+control.get('hold_timeout_ms', 750)/1000}
            elif action == 'release':
                self.held.pop(key, None)
        return PluginCommandResult(confirmed=False, result={'delivery': 'sent' if transmitted else 'lease_renewed', 'device_confirmed': False,
                                                          'button_id': control['id'], 'action': action})

    async def _send(self, control, action, value=None):
        address = control.get('release_address') if action == 'release' else control.get('off_address') if action == 'off' else None
        packet = encode(address or control['address'], arguments_for(control, action, value))
        await self.transport.write(packet)
        if time.monotonic() < self.capture_until:
            for address, arguments in decode(packet):
                self._capture('TX', address, arguments)

    async def _release_loop(self):
        while True:
            await asyncio.sleep(.05)
            async with self.control_lock:
                for key, entry in list(self.held.items()):
                    if time.monotonic() >= entry['expires']:
                        await self._send(entry['control'], 'release')
                        self.held.pop(key, None)

    def _capture(self, direction, address, arguments):
        timestamp = time.monotonic()
        if timestamp >= self.capture_until:
            return
        if timestamp-self.capture_window >= 1:
            self.capture_window, self.capture_count = timestamp, 0
        if self.capture_count >= 50:
            self.capture_dropped += 1
            return
        self.capture_count += 1
        self.capture_sequence += 1
        sample = [arg[:48] if isinstance(arg, str) else arg for arg in arguments[:8]]
        self.capture_messages.append({'sequence': self.capture_sequence, 'time': now().isoformat(),
            'direction': direction, 'address': address, 'arguments': sample,
            'truncated': len(arguments) > 8 or any(isinstance(arg, str) and len(arg) > 48 for arg in arguments)})

    async def inspect(self, action='read', seconds=30, cursor=0):
        if action == 'start':
            self.capture_id = new_id()
            self.capture_messages.clear()
            self.capture_dropped = self.capture_count = 0
            self.capture_until = time.monotonic()+max(1, min(60, seconds))
        elif action == 'stop':
            self.capture_until = 0
            self.capture_messages.clear()
        elif action != 'read':
            raise ValueError('Unknown inspection action')
        remaining = max(0, self.capture_until-time.monotonic())
        if not remaining:
            self.capture_messages.clear()
        return {'supported': True, 'session_id': self.capture_id, 'remaining': remaining,
                'messages': [record for record in self.capture_messages if record['sequence'] > cursor],
                'cursor': self.capture_sequence, 'dropped': self.capture_dropped}
