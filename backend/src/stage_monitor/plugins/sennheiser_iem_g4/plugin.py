"""TI 1254 media control protocol, UDP 53212. No WSM reverse engineering."""
import asyncio
import re

from stage_monitor.contracts import (
    ConnectionStatus, DeviceIdentity, DeviceNetwork, DeviceState, DeviceStatus,
    HealthCheckResult, Metric, now,
)
from stage_monitor.sdk import BaseDevicePlugin


def parse_frames(data):
    """Datagrams can contain multiple CR-terminated cyclic/configuration fields."""
    text = data.decode('ascii', errors='strict')
    result = {}
    for frame in re.split(r'[\r\n;]+', text):
        parts = frame.strip().split(maxsplit=1)
        if len(parts) == 2 and not parts[0].endswith(':'):
            result['AF' if parts[0] == 'Af' else parts[0]] = parts[1]
    return result


class IEMG4Plugin(BaseDevicePlugin):
    def __init__(self, *args):
        super().__init__(*args)
        endpoint = self.device.endpoint
        if not endpoint or endpoint.transport != 'udp' or not endpoint.host:
            raise ValueError('SR IEM G4 requires a UDP host')
        self.timeout = self.device.custom_config.get('request_timeout', 2)
        self.transport = self.context.udp_transport(endpoint.host, endpoint.port or 53212,
                                      timeout=self.timeout, max_bytes=4096, queue_size=64)
        self.identity = DeviceIdentity(manufacturer='Sennheiser', model='SR IEM G4')
        self.network = DeviceNetwork(host=endpoint.host, port=endpoint.port or 53212, transport='udp')
        self.values = {}
        self.metrics = []
        self.parameters = {}
        self.observed = None
        self.lock = asyncio.Lock()

    async def _read(self, commands, required):
        values = {}
        async with self.lock, asyncio.timeout(self.timeout):
            for command in commands:
                await self.transport.write((command+'\r').encode('ascii'))
                await self.context.trace('TX', command, sensitive=False)
            while not required <= values.keys():
                data = await self.transport.receive(timeout=self.timeout)
                await self.context.trace('RX', data.decode('ascii', errors='replace'), sensitive=False)
                values.update(parse_frames(data))
        return values

    async def connect(self):
        await self.transport.connect()
        values = await self._read(['FirmwareRevision', 'Mode'], {'FirmwareRevision', 'Mode'})
        version = values['FirmwareRevision']
        if not re.fullmatch(r'\d+\.\d+\.\d+', version):
            raise ValueError('Invalid G4 firmware response')
        # Actual SR units report 1.2.0 and implement these reads; negotiate by
        # supported responses rather than blocking by an EM firmware threshold.
        if values['Mode'] not in {'0', '1'}:
            raise ValueError('Device did not confirm SR mono/stereo mode')
        self.identity.firmware = version

    async def disconnect(self):
        await self.transport.disconnect()

    async def start(self):
        await self.poll()

    async def stop(self):
        # One-shot Push requests expire immediately; no RF/audio settings are changed.
        pass

    def _accept(self, values):
        af = values.get('AF', values.get('Af', '')).split()
        if len(af) not in {4, 5} or any(not v.isdigit() or not 0 <= int(v) <= 100 for v in af[:4]):
            raise ValueError('Invalid SR stereo AF telemetry')
        frequency = values['Frequency'].split()
        if len(frequency) != 3 or not all(v.isdigit() for v in frequency):
            raise ValueError('Invalid SR frequency response')
        khz, bank, channel = map(int, frequency)
        if not 100000 <= khz <= 999999 or not 0 <= bank <= 26 or not 0 <= channel <= 64:
            raise ValueError('SR frequency response outside protocol bounds')
        mode = values['Mode']
        if mode not in {'0', '1'}:
            raise ValueError('Invalid SR mode')
        timestamp = now()
        self.metrics = [Metric(device_id=self.device.id, key=key, channel=str(i+1),
                               value=int(af[offset+i]), unit='%', observed_at=timestamp)
                        for key, offset in [('audio.level', 0), ('audio.peak', 2)] for i in range(2)]
        self.parameters = {'frequency_mhz': khz/1000, 'bank': bank, 'channel': channel,
                           'mode': 'stereo' if mode == '1' else 'mono'}
        if values.get('Mute') in {'0', '1'}:
            self.parameters['mute'] = values['Mute'] == '1'
        if values.get('Name'):
            self.parameters['channel_name'] = values['Name'][:120]
        sensitivity = values.get('Sensitivity', '')
        if re.fullmatch(r'-?\d+', sensitivity) and -42 <= int(sensitivity) <= 0 and int(sensitivity) % 3 == 0:
            self.parameters['sensitivity_db'] = int(sensitivity)
        self.values = values
        self.observed = timestamp

    async def poll(self):
        values = await self._read(['Name', 'RfConfig', 'Sensitivity', 'Mute', 'Frequency', 'Mode', 'Push 0 100 1'],
                                  {'Name', 'RfConfig', 'Sensitivity', 'Mute', 'Frequency', 'Mode', 'Config', 'AF', 'Msg'})
        # Some firmware splits AF and Config across datagrams. Both are mandatory.
        if not ({'AF', 'Af'} & values.keys()):
            raise ValueError('SR cyclic response contained no stereo AF levels')
        self._accept(values)
        return await self.get_state()

    async def get_status(self):
        return (await self.get_state()).status

    async def get_metrics(self):
        return self.metrics

    async def get_state(self):
        fresh = self.transport.connected and self.observed is not None and (now()-self.observed).total_seconds() < max(10, self.device.poll_interval*3)
        warnings = self.values.get('Msg', 'OK')
        return DeviceState(device_id=self.device.id,
                           status=DeviceStatus.DEGRADED if fresh and warnings != 'OK' else DeviceStatus.ONLINE if fresh else DeviceStatus.OFFLINE,
                           connection_status=ConnectionStatus.CONNECTED if fresh else ConnectionStatus.DISCONNECTED,
                           identity=self.identity, network=self.network, observed_at=self.observed,
                           metrics=self.metrics if fresh else [m.model_copy(update={'quality':'stale'}) for m in self.metrics],
                           parameters=self.parameters, extra={'warnings':warnings, 'rf_config':self.values.get('RfConfig'),
                           'telemetry_scope':'SR transmitter: stereo AF; no bodypack RF or battery feedback'})

    async def health_check(self):
        state = await self.get_state()
        return HealthCheckResult(reachable=self.transport.connected and state.status in {DeviceStatus.ONLINE, DeviceStatus.DEGRADED},
                                 degraded=state.status == DeviceStatus.DEGRADED)

    async def execute_command(self, command, parameters):
        raise PermissionError('This initial SR IEM G4 adapter provides monitoring only')
