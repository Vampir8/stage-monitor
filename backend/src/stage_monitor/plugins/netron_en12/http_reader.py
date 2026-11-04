import asyncio

import httpx

from stage_monitor.contracts import (
    ConnectionStatus, DeviceIdentity, DeviceNetwork, DeviceState, DeviceStatus, Metric, now,
)
from stage_monitor.transports import HTTPTransport


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid NETRON configuration field')
    return value


class HTTPReader:
    """Read-only JSON used by NETRON's V2.9.2 web panel; no live DMX inference."""

    def __init__(self, device):
        self.device = device
        self.transport = HTTPTransport(f'http://{device.endpoint.host}:{device.endpoint.port or 80}',
                                       timeout=3, max_bytes=32768, keepalive_connections=0,
                                       headers={'Connection':'close'})
        self.sample = None
        self.last_poll_ok = False

    async def poll(self):
        self.last_poll_ok = False
        # Fit a complete sample, including one transient retry, inside the core's
        # default five-second RPC deadline. Never publish a partially read sample.
        async with asyncio.timeout(4.4):
            return await self._poll()

    async def read_json(self, path):
        try:
            response = await self.transport.request('GET', path)
        except (httpx.TransportError, TimeoutError):
            await asyncio.sleep(0.1)
            response = await self.transport.request('GET', path)
        return response.json()

    async def _poll(self):
        # Sequential short connections: this firmware resets reused HTTP connections.
        settings = await self.read_json('/Setting.json')
        identity = await self.read_json('/index.json')
        network = await self.read_json('/IP.json')
        rows = await self.read_json('/DMXPorts.json')
        if not isinstance(settings, dict) or settings.get('DeviceType') != 'NETRON EN12':
            raise ValueError('HTTP endpoint is not NETRON EN12')
        if not isinstance(identity, dict) or not isinstance(network, dict):
            raise ValueError('Invalid NETRON identity/network')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 12:
            raise ValueError('Invalid NETRON port list')
        ports = []
        universe_mode = integer(settings.get('UniverseMode'), 0, 1)
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise ValueError('Invalid NETRON port')
            mode = integer(row.get('ptMode'), 0, 3)
            protocol = integer(row.get('ptProtocol'), 0, 2)
            universe = integer(row.get('ptUniverse'), 0, 63999)
            if protocol == 0 and universe > 32767:
                raise ValueError('Invalid Art-Net port address')
            frame = integer(row.get('ptFramerate'), 0, 6)
            ports.append({'index':index, 'mode':['disabled','input','output','send_value'][mode],
                          'protocol':['Art-Net','sACN','none'][protocol], 'universe':universe,
                          'universe_display': universe + 1 if protocol == 0 and universe_mode == 0 else universe,
                          'configured_framerate_hz':10 + frame * 5,
                          'rdm_enabled':bool(integer(row.get('ptRDM'), 0, 1)),
                          'merge_mode':['OFF','HTP','LTP','Toggle','Backup'][integer(row.get('ptMergeMode'), 0, 4)]})
        # Keep only known fields; never retain full settings or authentication data.
        self.sample = {'at':now(), 'ports':ports, 'name':str(settings.get('DeviceName',''))[:120],
                       'firmware':str(identity.get('FirmwareVer',''))[:80],
                       'mac':str(identity.get('MACAddress',''))[:32],
                       'uptime':str(identity.get('OnTime',''))[:80]}
        self.last_poll_ok = True
        return await self.state()

    async def state(self):
        online = self.last_poll_ok and self.transport.connected and self.sample is not None
        sample = self.sample or {}
        timestamp = sample.get('at')
        ports = sample.get('ports', [])
        partial = bool(sample) and len(ports) != 12
        metrics = []
        def add(key, value, *, channel=None, unit=None, runtime=False):
            metrics.append(Metric(device_id=self.device.id, key=key, value=value,
                                  channel=channel, unit=unit, observed_at=timestamp,
                                  quality='unknown' if value is None else 'good' if online else 'stale',
                                  labels={'source':'netron.http','kind':'runtime' if runtime else 'configuration'}))
        if timestamp:
            add('gateway.reported_ports',len(ports))
            add('gateway.configured_outputs',None if partial else sum(p['mode']=='output' for p in ports))
            add('gateway.active_outputs',None,runtime=True)
            add('gateway.port_errors',None,runtime=True)
            add('gateway.uptime',sample['uptime'],runtime=True)
            for port in ports:
                channel=str(port['index'])
                add('dmx.mode',port['mode'],channel=channel)
                add('dmx.configured_framerate_hz',port['configured_framerate_hz'],channel=channel,unit='Hz')
                add('dmx.rdm_enabled',port['rdm_enabled'],channel=channel)
                output = port['mode']=='output'
                add('dmx.output_universe',port['universe'] if output else None,channel=channel)
                add('dmx.output_protocol',port['protocol'] if output else None,channel=channel)
                add('dmx.output_active',None,channel=channel,runtime=True)
                add('dmx.output_short',None,channel=channel,runtime=True)
        return DeviceState(device_id=self.device.id, observed_at=timestamp,
                           status=DeviceStatus.DEGRADED if online and partial else DeviceStatus.ONLINE if online else DeviceStatus.OFFLINE,
                           connection_status=ConnectionStatus.CONNECTED if online else ConnectionStatus.DISCONNECTED,
                           identity=DeviceIdentity(manufacturer='Obsidian Control Systems',model='NETRON EN12',
                                                   firmware=sample.get('firmware')),
                           network=DeviceNetwork(host=self.device.endpoint.host,port=self.device.endpoint.port or 80,
                                                 mac=sample.get('mac'),transport='http'),
                           metrics=metrics,extra={'simulation':False,'ports':ports,'reported_name':sample.get('name'),
                                                 'partial_port_report':partial,'identity_verified':bool(sample),
                                                 'telemetry_source':'http.configuration',
                                                 'runtime_dmx_status_available':False,
                                                 'port_numbering':'physical ports 1-12',
                                                 'universe_numbering':'raw device configuration; see universe_display'})
