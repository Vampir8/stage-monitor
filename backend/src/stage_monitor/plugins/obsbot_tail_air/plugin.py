import asyncio

from stage_monitor.contracts import (
    ConnectionStatus, DeviceIdentity, DeviceNetwork, DeviceState, DeviceStatus,
    HealthCheckResult, Metric, PluginCommandResult, now,
)
from stage_monitor.sdk import BaseDevicePlugin
from stage_monitor.transports import UDPTransport

from .protocol import command as encode_command, number, packet, unpack
from .portal import PortalTelemetry


class TailAirPlugin(BaseDevicePlugin):
    def __init__(self, *args):
        super().__init__(*args)
        endpoint = self.device.endpoint
        if not endpoint or endpoint.transport != 'udp' or not endpoint.host:
            raise ValueError('Tail Air requires a UDP host')
        config = self.device.custom_config
        self.control = config.get('enable_control', False)
        self.timeout = config.get('request_timeout', 1)
        self.transport = UDPTransport(endpoint.host, endpoint.port or 52381,
                                      timeout=4, max_bytes=64)
        self.lock = asyncio.Lock()
        self.sequence = 0
        self.parameters = {}
        self.observed_at = None
        self.portal = PortalTelemetry(self.device) if config.get('enable_portal_telemetry', True) else None

    def get_commands(self):
        return super().get_commands() if self.control else []

    def get_capabilities(self):
        return [c if self.control else c.model_copy(update={'writable': False})
                for c in super().get_capabilities()]

    async def connect(self):
        await self.transport.connect()
        if self.portal:
            self.portal.start()

    async def disconnect(self):
        if self.portal:
            await self.portal.stop()
        await self.transport.disconnect()
        self.observed_at = None

    async def start(self):
        # Reset per UDP controller session, then prove connectivity through inquiries.
        await self._exchange(b'\x01', kind=0x0200)
        await self.poll()

    async def stop(self):
        if self.portal:
            await self.portal.stop()

    async def _exchange(self, payload, *, kind=0x0110):
        async with self.lock:
            sequence = self.sequence
            self.sequence = (sequence + 1) & 0xffffffff
            try:
                async with asyncio.timeout(4 if kind == 0x0100 else self.timeout):
                    data = packet(kind, sequence, payload)
                    await self.transport.write(data)
                    await self.context.trace('TX', data.hex(), sensitive=False)
                    socket = None
                    for _ in range(32):
                        data = await self.transport.receive()
                        await self.context.trace('RX', data.hex(), sensitive=False)
                        reply_kind, reply_sequence, reply = unpack(data)
                        if reply_sequence != sequence:
                            continue  # Never allow a delayed/duplicate reply to confirm another command.
                        if kind == 0x0200 and reply_kind == 0x0201 and reply == b'\x01':
                            self.sequence = 0
                            return reply
                        if reply_kind != 0x0111 or len(reply) < 3 or reply[0] != 0x90 or reply[-1] != 0xff:
                            raise ConnectionError('Invalid VISCA response')
                        code = reply[1] & 0xf0
                        if code == 0x60:
                            raise ValueError('Camera rejected VISCA request')
                        if code == 0x40 and kind == 0x0100 and len(reply) == 3:
                            socket = reply[1] & 15
                            continue  # ACK only means accepted, not completed.
                        if code == 0x50:
                            if kind == 0x0100 and (len(reply) != 3 or (socket is not None and reply[1] & 15 != socket)):
                                raise ConnectionError('Invalid VISCA completion')
                            return reply[2:-1]
                    raise ConnectionError('Too many unrelated VISCA replies')
            except BaseException:
                # No command retries: an unanswered move may already have been executed.
                await self.transport.disconnect()
                raise

    async def poll(self):
        position = await self._exchange(bytes.fromhex('81 09 06 12 FF'))
        if len(position) != 8:
            raise ValueError('Invalid Tail Air position response')
        zoom = number(await self._exchange(bytes.fromhex('81 09 04 47 FF')))
        focus = await self._exchange(bytes.fromhex('81 09 04 38 FF'))
        if focus not in {b'\x02', b'\x03'}:
            raise ValueError('Invalid Tail Air focus response')
        tracking = await self._exchange(bytes.fromhex('81 09 8E 01 FF'))
        if tracking not in {b'\x00', b'\x01', b'\x02', b'\x03'}:
            raise ValueError('Invalid Tail Air tracking response')
        self.parameters = {'pan_deg': round(number(position[:4], signed=True) * .075, 3),
                           'tilt_deg': round(number(position[4:], signed=True) * .075, 3),
                           'zoom_ratio': zoom / 100, 'focus_mode': 'auto' if focus == b'\x02' else 'manual',
                           'tracking_mode': ['off', 'normal', 'upper_body', 'close_up'][tracking[0]]}
        self.observed_at = now()
        return await self.get_state()

    async def get_state(self):
        online = self.transport.connected and self.observed_at is not None
        units = {'pan_deg': 'deg', 'tilt_deg': 'deg', 'zoom_ratio': 'x'}
        return DeviceState(
            device_id=self.device.id, observed_at=self.observed_at,
            status=DeviceStatus.ONLINE if online else DeviceStatus.OFFLINE,
            connection_status=ConnectionStatus.CONNECTED if online else ConnectionStatus.DISCONNECTED,
            identity=DeviceIdentity(manufacturer='OBSBOT', model='Tail Air'),
            network=DeviceNetwork(host=self.transport.host, port=self.transport.port, transport='udp'),
            parameters=dict(self.parameters),
            metrics=[Metric(device_id=self.device.id, key='camera.' + key, value=value, unit=units.get(key),
                            observed_at=self.observed_at, quality='good' if online else 'stale')
                     for key, value in self.parameters.items()] + (self.portal.metrics() if self.portal else []) if self.observed_at else [],
            extra={'simulation': False, 'control_enabled': self.control,
                   'portal_telemetry': {'enabled': self.portal is not None,
                                        'connected': self.portal.transport.connected if self.portal else False,
                                        'last_error': self.portal.last_error if self.portal else None},
                   'video_sources': self.device.custom_config.get('video_sources', [])},
        )

    async def get_status(self):
        return (await self.get_state()).status

    async def get_metrics(self):
        return (await self.get_state()).metrics

    async def health_check(self):
        await self.poll()
        return HealthCheckResult(reachable=True)

    async def execute_command(self, command, parameters):
        if not self.control:
            raise ValueError('Tail Air control disabled')
        await self._exchange(encode_command(command, parameters), kind=0x0100)
        # Completion is confirmed even if a subsequent refresh fails; the regular poll
        # will refresh telemetry. Do not repeat an already completed move.
        return PluginCommandResult(confirmed=True, result={'completed': True})
