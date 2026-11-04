"""Read-only EMpro EEM-MA370 Modbus TCP adapter; addresses are zero-based."""
import asyncio
import math
import struct

from stage_monitor.contracts import (
    ConnectionStatus, DeviceIdentity, DeviceNetwork, DeviceState, DeviceStatus,
    HealthCheckResult, Metric, now,
)
from stage_monitor.sdk import BaseDevicePlugin
from stage_monitor.transports.modbus import ModbusTransport


# Manufacturer's virtual WBM 1.7.1 map. Values are primary quantities: do not
# multiply by CT/VT ratios again. Energy Ea+t is lifetime imported active energy.
REGISTERS = [
    (0x800C, 'power.frequency', None, 'Hz'),
    (0x8016, 'power.active', None, 'W'),
    (0x8100, 'power.energy_import', None, 'Wh'),
    *[(address, key, phase, unit) for phase, current, voltage in
      [('L1', 0x800E, 0x8006), ('L2', 0x8010, 0x8008), ('L3', 0x8012, 0x800A)]
      for address, key, unit in [(current, 'power.current', 'A'), (voltage, 'power.voltage', 'V')]],
    (0x801C, 'power.factor', None, None),
    (0x8000, 'power.voltage_ll', 'L1-L2', 'V'),
    (0x8002, 'power.voltage_ll', 'L2-L3', 'V'),
    (0x8004, 'power.voltage_ll', 'L3-L1', 'V'),
    (0x8014, 'power.current_neutral', None, 'A'),
    (0x8018, 'power.reactive', None, 'var'),
    (0x801A, 'power.apparent', None, 'VA'),
    (0x8106, 'power.energy_export', None, 'Wh'),
]


def decode_float(words, word_order='low_first'):
    if len(words) != 2 or any(type(w) is not int or not 0 <= w <= 65535 for w in words):
        raise ValueError('Expected two uint16 registers')
    if word_order not in {'low_first', 'high_first'}:
        raise ValueError('Unknown Modbus word order')
    high, low = words[::-1] if word_order == 'low_first' else words
    value = struct.unpack('>f', struct.pack('>HH', high, low))[0]
    return value if math.isfinite(value) else None


class EEMMA370Plugin(BaseDevicePlugin):
    def __init__(self, *args):
        super().__init__(*args)
        endpoint = self.device.endpoint
        if not endpoint or endpoint.transport != 'modbus' or not endpoint.host:
            raise ValueError('EEM-MA370 requires a Modbus TCP endpoint')
        self.word_order = self.device.custom_config.get('word_order', 'low_first')
        if self.word_order not in {'low_first', 'high_first'}:
            raise ValueError('Invalid word order')
        self.transport = ModbusTransport(endpoint.host, endpoint.port or 502,
            device_id=self.device.custom_config.get('unit_id', 1),
            timeout=self.device.custom_config.get('request_timeout', 2))
        self.network = DeviceNetwork(host=endpoint.host, port=endpoint.port or 502,
                                     transport='modbus')
        self.identity = DeviceIdentity(manufacturer='Phoenix Contact', model='EEM-MA370')
        self.metrics = []
        self.observed = None
        self.connected = False
        self.lock = asyncio.Lock()

    async def connect(self):
        if self.transport:
            await self.transport.connect()
        self.connected = True

    async def disconnect(self):
        self.connected = False
        if self.transport:
            await self.transport.disconnect()

    async def start(self):
        await self.poll()

    async def stop(self):
        pass


    async def poll(self):
        async with self.lock:
            if not self.connected:
                raise ConnectionError('Meter disconnected')
            try:
                # Two bounded FC03 reads. Publish only after both succeed.
                instantaneous = await self.transport.read_registers(0x8000, 30)
                energy = await self.transport.read_registers(0x8100, 8)
                values = {address: decode_float(
                    (instantaneous if address < 0x8100 else energy)[
                        address-(0x8000 if address < 0x8100 else 0x8100):
                        address-(0x8000 if address < 0x8100 else 0x8100)+2], self.word_order)
                    for address, _, _, _ in REGISTERS}
                timestamp = now()
                self.metrics = [Metric(device_id=self.device.id, key=key, channel=channel,
                    value=values[address], unit=unit, observed_at=timestamp,
                    quality='good' if values[address] is not None else 'invalid')
                    for address, key, channel, unit in REGISTERS]
                self.observed = timestamp
            except BaseException:
                await self.disconnect()
                raise
        return await self.get_state()

    async def get_status(self):
        return (await self.get_state()).status

    async def get_metrics(self):
        return (await self.get_state()).metrics

    async def get_state(self):
        fresh = self.connected and self.transport.connected and self.observed is not None and (now()-self.observed).total_seconds() < self.device.stale_after
        invalid = any(m.quality == 'invalid' for m in self.metrics)
        return DeviceState(device_id=self.device.id,
            status=(DeviceStatus.DEGRADED if invalid else DeviceStatus.ONLINE) if fresh else DeviceStatus.OFFLINE,
            connection_status=ConnectionStatus.CONNECTED if fresh else ConnectionStatus.DISCONNECTED,
            identity=self.identity, network=self.network, observed_at=self.observed,
            metrics=self.metrics if fresh else [m.model_copy(update={'quality':'stale'}) for m in self.metrics],
            extra={'word_order': self.word_order})

    async def health_check(self):
        state = await self.get_state()
        return HealthCheckResult(reachable=state.status != DeviceStatus.OFFLINE,
                                 degraded=state.status == DeviceStatus.DEGRADED)

    async def execute_command(self, command, parameters):
        raise PermissionError('EEM-MA370 monitoring adapter is read-only')
