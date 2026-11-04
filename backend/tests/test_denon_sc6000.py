import asyncio
import json
import struct
from pathlib import Path

import pytest
import yaml

from stage_monitor.contracts import Device, DeviceEndpoint, PluginDescriptor
from stage_monitor.plugins.denon_sc6000.plugin import SC6000Plugin
from stage_monitor.plugins.denon_sc6000.protocol import (
    MAX_FRAME, announce, discovery, read_string, response, string, u32,
)
from stage_monitor.sdk import PluginContext
from stage_monitor.transports import UDPTransport


def descriptor():
    path = Path(__file__).parents[1] / 'src/stage_monitor/plugins/denon_sc6000/manifest.yaml'
    return PluginDescriptor.model_validate(yaml.safe_load(path.read_text(encoding='utf-8')))


def test_bounded_wire_parser():
    frame = announce(bytes(16), 1234, 'SC6000', 'JP13', '5.0-test')
    assert discovery(frame)['port'] == 1234
    assert discovery(frame)['name'] == 'SC6000'
    for invalid in [b'', frame[:-1], frame + b'junk', b'airD' + bytes(16) + u32(MAX_FRAME+2)]:
        with pytest.raises(ValueError):
            discovery(invalid)
    body = b'smaa' + u32(0) + string('/Engine/Deck1/Play') + string('{"state":true}')
    assert response(body) == ('/Engine/Deck1/Play', {'state':True})
    assert response(b'smaa' + u32(0) + string('missing') + bytes([255])*4) == ('missing', None)
    with pytest.raises(ValueError):
        response(body + b'junk')


def test_stage_linq_emulator_discovery_directory_push_layers_and_readonly():
    async def run():
        peer = b'SC6000-test-peer'
        requests = []
        writers = []
        sample = asyncio.Event()

        async def text(reader):
            size = struct.unpack('!I', await reader.readexactly(4))[0]
            return (await reader.readexactly(size)).decode('utf-16-be')

        async def states(reader, writer):
            writers.append(writer)
            try:
                assert await reader.readexactly(4) == u32(0)
                await reader.readexactly(16)
                assert await text(reader) == 'StateMap'
                await reader.readexactly(2)
                while True:
                    size = struct.unpack('!I', await reader.readexactly(4))[0]
                    body = await reader.readexactly(size)
                    assert body[:8] == b'smaa' + u32(2002)
                    path, _ = read_string(body, 8)
                    requests.append(path)
                    field = path.split('/Deck',1)[1].split('/',1)[1]
                    value = {'value':128.5} if field == 'CurrentBPM' else \
                        {'string':'Utwór A' if '/Deck1/' in path else 'Utwór B'} if field == 'Track/SongName' else \
                        {'string':'Artysta'} if field == 'Track/ArtistName' else \
                        {'value':7} if field == 'Track/CurrentKeyIndex' else {'state':True}
                    answer = b'smaa' + u32(0) + string(path) + string(json.dumps(value))
                    data = u32(len(answer)) + answer
                    writer.write(data[:3])
                    await writer.drain()
                    writer.write(data[3:])
                    await writer.drain()
                    if len(requests) >= 18:
                        sample.set()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writer.close()

        state_server = await asyncio.start_server(states, '127.0.0.1', 0)
        port = state_server.sockets[0].getsockname()[1]

        async def directory(reader, writer):
            writers.append(writer)
            try:
                assert await reader.readexactly(4) == u32(2)
                token = await reader.readexactly(16)
                assert token < peer
                writer.write(u32(2) + peer + u32(0) + peer + string('StateMap') + struct.pack('!H', port))
                await writer.drain()
                await reader.read()
            finally:
                writer.close()

        directory_server = await asyncio.start_server(directory, '127.0.0.1', 0)
        directory_port = directory_server.sockets[0].getsockname()[1]

        class Discovery(asyncio.DatagramProtocol):
            def connection_made(self, socket):
                self.socket = socket

            def datagram_received(self, data, address):
                assert discovery(data)['name'] == 'StageControl'
                self.socket.sendto(announce(peer, directory_port, 'SC6000', 'JP13', '5.test'), address)

        udp, _ = await asyncio.get_running_loop().create_datagram_endpoint(Discovery, local_addr=('127.0.0.1',0))
        device = Device(name='Denon', plugin_id='denon.sc6000', category='audio',
                        endpoint=DeviceEndpoint(transport='udp', host='127.0.0.1', port=51337))
        context = PluginContext(lambda state: None)
        plugin = SC6000Plugin(device, context, descriptor())
        plugin.transport = UDPTransport('127.0.0.1', udp.get_extra_info('sockname')[1], timeout=2)
        try:
            await plugin.connect()
            await plugin.start()
            await asyncio.wait_for(sample.wait(), 2)
            for _ in range(30):
                state = await plugin.get_state()
                if all(m.quality == 'good' for m in state.metrics):
                    break
                await asyncio.sleep(.01)
            assert state.status == 'online'
            assert len(state.metrics) == 18
            assert {m.value for m in state.metrics if m.key == 'player.title'} == {'Utwór A','Utwór B'}
            assert all(m.value == 128.5 for m in state.metrics if m.key == 'player.bpm')
            assert (await plugin.health_check()).reachable
            assert plugin.get_commands() == []
            with pytest.raises(PermissionError):
                await plugin.execute_command('play', {})
            plugin.accept('/Engine/Deck1/CurrentBPM', {'value':float('nan')})
            assert next(m for m in (await plugin.get_metrics()) if m.key == 'player.bpm' and m.channel == '1').quality == 'unknown'
            assert not context.failure
        finally:
            await plugin.disconnect()
            await context.close()
            udp.close()
            for writer in writers:
                writer.close()
            directory_server.close()
            state_server.close()
            await directory_server.wait_closed()
            await state_server.wait_closed()
        assert not plugin.tasks and plugin.server is None
    asyncio.run(run())
