import asyncio
import json
import os
import re
import ssl
import time

import httpx

from stage_monitor.contracts import (
    ConnectionStatus, DeviceIdentity, DeviceNetwork, DeviceState, DeviceStatus,
    HealthCheckResult, Metric, now,
)
from stage_monitor.sdk import BaseDevicePlugin
from stage_monitor.transports import HTTPTransport


def number(value):
    try:
        result = int(value) if isinstance(value, int) or isinstance(value, str) and value.isdigit() else float(value)
        return result if result >= 0 and result < 1e30 else None
    except (TypeError, ValueError):
        return None


def boolean(value):
    return {'true': True, 'false': False, 'yes': True, 'no': False}.get(str(value).lower())


class CRS326Plugin(BaseDevicePlugin):
    """Allowlisted reads only. Counter resets and missing fields never become traffic."""
    board_prefix = 'CRS326-24G-2S+'
    interface_types = ('ether',)

    def interface_query(self):
        query = []
        for kind in self.interface_types:
            query.append('type=' + kind)
            if len(query) > 1:
                query.append('#|')
        return query

    def __init__(self, *args):
        super().__init__(*args)
        self.transport = None
        self.sample = None
        self.previous = {}
        self.last_poll_ok = False
        self.lock = asyncio.Lock()
        self.clock = time.monotonic

    async def connect(self):
        endpoint, config = self.device.endpoint, self.device.custom_config
        if not endpoint or not endpoint.host or endpoint.transport not in {'http', 'https'}:
            raise ValueError('CRS requires a RouterOS HTTP/HTTPS endpoint')
        env_key = config.get('password_env', 'STAGE_CRS_PASSWORD')
        if not re.fullmatch(r'STAGE_[A-Z0-9_]{1,100}', env_key):
            raise ValueError('Invalid credential environment reference')
        password = await self.context.get_password()
        if password is None:
            password = os.environ.get(env_key)
        if password is None:
            raise ValueError('CRS password environment variable is not configured')
        if endpoint.transport == 'http' and not config.get('allow_plain_http', False):
            raise ValueError('Plain HTTP requires explicit configuration')
        verify = ssl.create_default_context(cafile=config['ca_file']) if config.get('ca_file') else True
        port = endpoint.port or (443 if endpoint.transport == 'https' else 80)
        host = f'[{endpoint.host}]' if ':' in endpoint.host else endpoint.host
        self.transport = HTTPTransport(f'{endpoint.transport}://{host}:{port}', timeout=2,
                                       max_bytes=131072, verify=verify,
                                       headers={'Content-Type':'application/json'},
                                       auth=httpx.BasicAuth(config.get('username', 'admin'), password))
        await self.transport.connect()

    async def disconnect(self):
        self.last_poll_ok = False
        if self.transport:
            await self.transport.disconnect()

    async def start(self):
        await self.poll()

    async def stop(self):
        pass

    async def read(self, path, *, body=None, properties=None, optional=False):
        try:
            response = await self.transport.request(
                'POST' if body is not None else 'GET', path,
                content=json.dumps(body).encode() if body is not None else None,
                params={'.proplist': properties} if properties else None)
        except httpx.HTTPStatusError as error:
            if optional and error.response.status_code in {400, 403, 404}:
                return []
            raise
        data = response.json()
        rows = [data] if isinstance(data, dict) else data
        if not isinstance(rows, list) or len(rows) > 128 or any(not isinstance(r, dict) for r in rows):
            raise ValueError('Invalid RouterOS response')
        return rows

    async def poll(self):
        async with self.lock:
            self.last_poll_ok = False
            async with asyncio.timeout(4.4):
                resource = await self.read('/rest/system/resource', properties='board-name,version,cpu-load,free-memory,total-memory,uptime')
                if len(resource) != 1 or not resource[0].get('board-name') or (
                        self.board_prefix and not str(resource[0]['board-name']).startswith(self.board_prefix)):
                    raise ValueError('Endpoint model does not match the RouterOS adapter')
                resource = resource[0]
                identity = await self.read('/rest/system/identity', properties='name')
                interfaces = await self.read('/rest/interface/print', body={
                    'stats': '', '.proplist': '.id,name,default-name,type,master-interface,running,disabled,rx-byte,tx-byte,rx-error,tx-error,link-downs',
                    '.query': self.interface_query()})
                interfaces = [row for row in interfaces if not row.get('master-interface') and row.get('type') in self.interface_types]
                ids = [row.get('.id') for row in interfaces]
                if any(not isinstance(i, str) or not re.fullmatch(r'\*[0-9A-Fa-f]+', i) for i in ids):
                    raise ValueError('Invalid RouterOS interface IDs')
                ethernet_ids = [row['.id'] for row in interfaces if row.get('type') == 'ether']
                links = await self.read('/rest/interface/ethernet/monitor', body={
                    'numbers': ','.join(ethernet_ids), 'once': '', '.proplist': 'name,status,rate,full-duplex'}, optional=True) if ethernet_ids else []
                health = await self.read('/rest/system/health', properties='name,value,type', optional=True)
            current_time, timestamp = self.clock(), now()
            rebooted = self.sample is not None and self.sample['uptime'] != resource.get('uptime') and self._uptime(resource.get('uptime')) < self._uptime(self.sample['uptime'])
            links = {row.get('name'): row for row in links}
            ports, counters = [], {}
            for row in interfaces:
                name, index = row.get('name'), row['.id']
                if not isinstance(name, str) or not 1 <= len(name) <= 120:
                    raise ValueError('Invalid interface name')
                running, disabled = boolean(row.get('running')), boolean(row.get('disabled'))
                previous = None if rebooted else self.previous.get(index)
                rx, tx = number(row.get('rx-byte')), number(row.get('tx-byte'))
                rates = []
                for key, counter in [('rx', rx), ('tx', tx)]:
                    old = previous.get(key) if previous else None
                    elapsed = current_time - previous['time'] if previous else 0
                    valid = counter is not None and old is not None and counter >= old and elapsed > 0 and previous['name'] == name
                    rates.append((counter - old) * 8 / elapsed if valid else None)
                counters[index] = {'rx': rx, 'tx': tx, 'time': current_time, 'name': name}
                port = {'id': index, 'name': name, 'type': row['type'], 'up': running, 'disabled': disabled,
                        'default_name':str(row.get('default-name',name))[:120],
                        'rate': str(links.get(name, {}).get('rate', ''))[:40] or None,
                        'rx_bps': rates[0], 'tx_bps': rates[1],
                        'rx_errors': number(row.get('rx-error')), 'tx_errors': number(row.get('tx-error')),
                        'link_downs': number(row.get('link-downs'))}
                ports.append(port)
            self.previous = counters
            self.sample = {'at': timestamp, 'cpu': number(resource.get('cpu-load')),
                           'memory_free': number(resource.get('free-memory')),
                           'memory_total': number(resource.get('total-memory')),
                           'firmware': str(resource.get('version', ''))[:80],
                           'model': str(resource['board-name'])[:120], 'uptime': resource.get('uptime'),
                           'name': str(identity[0].get('name', ''))[:120] if identity else '',
                           'temperature': next((number(r.get('value')) for r in health if r.get('name') in {'temperature', 'cpu-temperature'}), None),
                           'ports': ports}
            self.last_poll_ok = True
            return await self.get_state()

    @staticmethod
    def _uptime(value):
        parts = re.findall(r'(\d+)([wdhms])', str(value))
        return sum(int(n) * {'w':604800, 'd':86400, 'h':3600, 'm':60, 's':1}[unit] for n, unit in parts)

    async def get_state(self):
        sample = self.sample or {}
        online = self.last_poll_ok and self.transport is not None and self.transport.connected
        metrics, ports = [], sample.get('ports', [])
        def add(key, value, unit=None, channel=None):
            metrics.append(Metric(device_id=self.device.id, key=key, value=value, unit=unit,
                                  channel=channel, observed_at=sample['at'],
                                  quality='unknown' if value is None else 'good' if online else 'stale'))
        if sample:
            add('system.cpu_percent', sample['cpu'], '%')
            add('temperature', sample['temperature'], 'degC')
            add('system.uptime', sample['uptime'])
            add('system.memory_free', sample['memory_free'], 'B')
            add('system.memory_total', sample['memory_total'], 'B')
            add('network.ports_total', len(ports))
            add('network.ports_up', sum(p['up'] is True for p in ports) if all(p['up'] is not None for p in ports) else None)
            ethernet=[p for p in ports if re.fullmatch(r'ether\d+',p['default_name'])]
            add('network.ports_summary', f"{sum(p['up'] is True for p in ethernet)}/{len(ethernet)}" if ethernet and all(p['up'] is not None for p in ethernet) else None)
            for index in [1,2]:
                sfp=next((p for p in ports if p['default_name'] in {f'sfp-sfpplus{index}',f'sfp{index}'}),None)
                add(f'network.sfp{index}_status',None if not sfp or sfp['up'] is None else 'DISABLED' if sfp['disabled'] else 'DOWN' if not sfp['up'] else sfp['rate'] or 'UP')
            for direction in ['rx', 'tx']:
                values = [p[direction+'_bps'] for p in ports]
                add('network.'+direction+'_bps', sum(values) if values and all(v is not None for v in values) else None, 'bit/s')
            for port in ports:
                for key, field, unit in [('network.port_up','up',None), ('network.port_disabled','disabled',None),
                                         ('network.port_rate','rate',None), ('network.port_rx_bps','rx_bps','bit/s'),
                                         ('network.port_tx_bps','tx_bps','bit/s'), ('network.port_rx_errors','rx_errors',None),
                                         ('network.port_tx_errors','tx_errors',None), ('network.port_link_downs','link_downs',None)]:
                    add(key,port[field],unit,port['name'])
        return DeviceState(device_id=self.device.id, observed_at=sample.get('at'),
                           status=DeviceStatus.ONLINE if online else DeviceStatus.OFFLINE,
                           connection_status=ConnectionStatus.CONNECTED if online else ConnectionStatus.DISCONNECTED,
                           identity=DeviceIdentity(manufacturer='MikroTik',model=sample.get('model'),firmware=sample.get('firmware')),
                           network=DeviceNetwork(host=self.device.endpoint.host,port=self.device.endpoint.port,
                                                 transport=self.device.endpoint.transport), metrics=metrics,
                           extra={'simulation':False, 'ports':ports,'reported_name':sample.get('name'),
                                  'traffic_scope':'sum of physical Ethernet/SFP ingress and egress; not unique end-to-end traffic'})

    async def get_status(self):
        return (await self.get_state()).status

    async def get_metrics(self):
        return (await self.get_state()).metrics

    async def health_check(self):
        # Polling already proves reachability; avoid a second batch of requests.
        fresh = self.sample and (now()-self.sample['at']).total_seconds() <= self.device.stale_after
        return HealthCheckResult(reachable=bool(self.last_poll_ok and fresh and self.transport.connected))

    async def execute_command(self, command, parameters):
        raise ValueError('CRS326 adapter is read-only')
