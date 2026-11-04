import asyncio

from stage_monitor.contracts import (
    ConnectionStatus, DeviceIdentity, DeviceNetwork, DeviceState, DeviceStatus,
    HealthCheckResult, Metric, now,
)
from stage_monitor.sdk import BaseDevicePlugin
from stage_monitor.transports import UDPTransport

from .protocol import ART_POLL, parse_reply
from .http_reader import HTTPReader


class EN12Plugin(BaseDevicePlugin):
    def __init__(self, *args):
        super().__init__(*args)
        endpoint = self.device.endpoint
        if not endpoint or endpoint.transport not in {'udp','http'} or not endpoint.host:
            raise ValueError('NETRON EN12 requires a UDP or HTTP host')
        self.http = HTTPReader(self.device) if endpoint.transport == 'http' else None
        self.window = self.device.custom_config.get('response_window', 3)
        self.transport = self.http.transport if self.http else UDPTransport(
            endpoint.host, endpoint.port or 6454, local_port=6454, timeout=3, max_bytes=1024, queue_size=64)
        self.pages = {}
        self.observed_at = None
        self.partial = False
        self.last_poll_ok = False
        self.lock = asyncio.Lock()

    async def connect(self):
        await self.transport.connect()

    async def disconnect(self):
        await self.transport.disconnect()

    async def start(self):
        await self.poll()

    async def stop(self):
        pass

    async def poll(self):
        if self.http:
            async with self.lock:
                return await self.http.poll()
        async with self.lock:
            self.last_poll_ok = False
            pages = {}
            await self.transport.write(ART_POLL)
            await self.context.trace('TX', 'ArtPoll (read-only)', sensitive=False)
            deadline = asyncio.get_running_loop().time() + self.window
            for _ in range(64):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    data = await self.transport.receive(timeout=remaining, disconnect_on_timeout=False)
                except TimeoutError:
                    break
                try:
                    page = parse_reply(data)
                except ValueError:
                    continue  # Other Art-Net frames cannot make a device online.
                pages[page['bind_index']] = (page, now())
                if sum(len(p['ports']) for p, _ in pages.values()) >= 12:
                    break
            if not pages:
                raise TimeoutError('No valid ArtPollReply from NETRON endpoint')
            self.pages = pages  # Do not carry missing ports forward as fresh data.
            self.observed_at = max(t for _, t in pages.values())
            self.partial = sum(len(p['ports']) for p, _ in pages.values()) != 12
            self.last_poll_ok = True
        return await self.get_state()

    async def get_state(self):
        if self.http:
            return await self.http.state()
        online = self.transport.connected and self.observed_at is not None and self.last_poll_ok
        metrics, ports, reports = [], [], []
        quality = 'good' if online else 'stale'
        for bind, (page, timestamp) in sorted(self.pages.items()):
            reports.append({'bind_index': bind, 'report': page['report'], 'code': page['report_code']})
            for port in page['ports']:
                channel = f'{bind}:{port["index"]}'
                ports.append({'bind_index': bind, **port})
                for key in ['input_universe', 'output_universe', 'input_active', 'output_active',
                            'input_error', 'output_short', 'merging', 'input_protocol', 'output_protocol']:
                    value = port[key]
                    metrics.append(Metric(device_id=self.device.id, key='dmx.' + key, channel=channel,
                                          value=value, observed_at=timestamp,
                                          quality=quality if value is not None else 'unknown'))
        first = next(iter(self.pages.values()))[0] if self.pages else {}
        errors = sum(bool(p['input_error'] or p['output_short']) for p in ports)
        fault = any(p['code'] in {2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15} for p in reports)
        if self.observed_at:
            for key, value in [('gateway.reported_ports', len(ports)),
                               ('gateway.active_outputs', sum(p['output_active'] is True for p in ports)),
                               ('gateway.port_errors', errors),
                               ('gateway.firmware_revision', first.get('firmware_revision'))]:
                metrics.append(Metric(device_id=self.device.id, key=key, value=value,
                                      observed_at=self.observed_at,
                                      quality='stale' if not online else 'unknown' if self.partial and key != 'gateway.firmware_revision' else 'good'))
        return DeviceState(device_id=self.device.id, observed_at=self.observed_at,
                           status=DeviceStatus.DEGRADED if online and (self.partial or errors or fault) else DeviceStatus.ONLINE if online else DeviceStatus.OFFLINE,
                           connection_status=ConnectionStatus.CONNECTED if online else ConnectionStatus.DISCONNECTED,
                           identity=DeviceIdentity(manufacturer='Obsidian Control Systems', model='NETRON EN12',
                                                   firmware=f'Art-Net revision {first["firmware_revision"]}' if first else None),
                           network=DeviceNetwork(host=self.transport.host, port=self.transport.port,
                                                 mac=first.get('mac'), transport='udp'),
                           metrics=metrics,
                           extra={'simulation': False, 'partial_port_report': self.partial,
                                  'reported_name': first.get('long_name'), 'ports': ports,
                                  'reports': reports, 'identity_verified': False,
                                  'universe_numbering': 'Art-Net zero-based',
                                  'port_numbering': 'BindIndex:slot (physical mapping unverified)'})

    async def get_status(self):
        return (await self.get_state()).status

    async def get_metrics(self):
        return (await self.get_state()).metrics

    async def health_check(self):
        await self.poll()
        return HealthCheckResult(reachable=True)

    async def execute_command(self, command, parameters):
        raise ValueError('NETRON EN12 adapter is read-only')
