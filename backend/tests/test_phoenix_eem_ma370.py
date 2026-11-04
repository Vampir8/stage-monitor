import asyncio
import struct
from pathlib import Path

import pytest
import yaml

from stage_monitor.contracts import Device, DeviceEndpoint, PluginDescriptor
from stage_monitor.sdk import PluginContext
from stage_monitor.plugins.phoenix_eem_ma370.plugin import EEMMA370Plugin, decode_float


def descriptor():
    return PluginDescriptor.model_validate(yaml.safe_load((Path(__file__).parents[1]/'src/stage_monitor/plugins/phoenix_eem_ma370/manifest.yaml').read_text(encoding='utf8')))


def test_documented_float_and_invalid_values():
    # Independent IEEE-754 reference example: 123.456 = 0x42F6E979.
    assert decode_float([0xE979, 0x42F6]) == pytest.approx(123.456)
    assert decode_float([0x42F6, 0xE979], 'high_first') == pytest.approx(123.456)
    assert decode_float([0, 0x7F80]) is None
    assert decode_float([0, 0x7FC0]) is None
    with pytest.raises(ValueError):
        decode_float([1])
    with pytest.raises(ValueError):
        decode_float([True, 1])


def test_real_modbus_tcp_reads_atomic_failure_and_reconnect():
    async def run():
        requests = []
        fail_energy = False
        invalid_frequency = False
        async def meter(reader, writer):
            try:
                while True:
                    header = await reader.readexactly(7)
                    transaction, protocol, length, unit = struct.unpack('>HHHB', header)
                    request = await reader.readexactly(length-1)
                    function, address, count = struct.unpack('>BHH', request)
                    requests.append((function, address, count, unit))
                    assert function == 3 and protocol == 0 and unit == 1
                    if fail_energy and address == 0x8100:
                        response = bytes([0x83, 2])
                    else:
                        values = {0x8006:230.8, 0x8008:229.9, 0x800A:231.6,
                                  0x800E:8.7, 0x8010:7.9, 0x8012:9.1,
                                  0x800C:float('nan') if invalid_frequency else 50.01,
                                  0x8016:5420, 0x801C:.97, 0x8100:1842600, 0x8106:1250}
                        words = [0]*count
                        for a, value in values.items():
                            if address <= a and a+1 < address+count:
                                high, low = struct.unpack('>HH', struct.pack('>f', value))
                                words[a-address:a-address+2] = [low, high]
                        response = bytes([3, count*2])+struct.pack('>'+('H'*count), *words)
                    writer.write(struct.pack('>HHHB', transaction, 0, len(response)+1, unit)+response)
                    await writer.drain()
            except asyncio.IncompleteReadError:
                pass
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(meter, '127.0.0.1', 0)
        plugin = EEMMA370Plugin(Device(name='Power', plugin_id='phoenix.eem_ma370', category='power',
            endpoint=DeviceEndpoint(transport='modbus', host='127.0.0.1', port=server.sockets[0].getsockname()[1])),
            PluginContext(lambda s: None), descriptor())
        try:
            await plugin.connect()
            state = await plugin.poll()
            assert state.status == 'online' and state.extra.get('simulation',False) is False
            assert requests == [(3, 0x8000, 30, 1), (3, 0x8100, 8, 1)]
            metrics = {(m.key, m.channel):m for m in state.metrics}
            assert metrics['power.voltage','L1'].value == pytest.approx(230.8)
            assert metrics['power.current','L3'].value == pytest.approx(9.1)
            assert metrics['power.energy_import',None].value == 1842600
            assert plugin.get_commands() == []
            with pytest.raises(PermissionError):
                await plugin.execute_command('reset_energy', {})
            before = plugin.observed
            fail_energy = True
            with pytest.raises(ConnectionError):
                await plugin.poll()
            stale = await plugin.get_state()
            assert stale.observed_at == before and stale.status == 'offline'
            assert all(m.quality == 'stale' for m in stale.metrics)
            assert not (await plugin.health_check()).reachable
            fail_energy = False
            invalid_frequency = True
            await plugin.connect()
            state = await plugin.poll()
            assert state.status == 'degraded'
            frequency = next(m for m in state.metrics if m.key == 'power.frequency')
            assert frequency.value is None and frequency.quality == 'invalid'
            assert (await plugin.health_check()).degraded
        finally:
            await plugin.disconnect()
            server.close()
            await server.wait_closed()
    asyncio.run(run())


