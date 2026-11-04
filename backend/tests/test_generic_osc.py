import asyncio
import socket
import struct
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

from stage_monitor.auth import can_command
from stage_monitor.contracts import Device, DeviceEndpoint, PluginDescriptor
from stage_monitor.main import create_app
from stage_monitor.plugins.generic_osc.plugin import OSCPlugin
from stage_monitor.plugins.generic_osc.protocol import decode, encode
from stage_monitor.sdk import PluginContext
from stage_monitor.settings import Settings
from stage_monitor.transports.udp_duplex import PeerReader


def descriptor():
    source = Path(__file__).parents[1] / 'src/stage_monitor/plugins/generic_osc/manifest.yaml'
    return PluginDescriptor.model_validate(yaml.safe_load(source.read_text(encoding='utf-8')))


def test_feedback_filters_foreign_ip_and_accepts_variable_port():
    reader = PeerReader(SimpleNamespace(remote=('127.0.0.1', 9000)))
    with patch('stage_monitor.transports.udp_duplex.DatagramReader.datagram_received') as receive:
        reader.datagram_received(b'test', ('127.0.0.2', 9000))
        receive.assert_not_called()
        reader.datagram_received(b'test', ('127.0.0.1', 19002))
        receive.assert_called_once()


def test_osc_wire_types_bundles_and_limits():
    packet = encode('/test', [{'type': 'i', 'value': -2}, {'type': 'f', 'value': .5},
                              {'type': 's', 'value': 'Scena ą'}, {'type': 'T'}, {'type': 'F'}, {'type': 'N'}])
    assert decode(packet) == [('/test', [-2, .5, 'Scena ą', True, False, None])]
    bundle = b'#bundle\0' + struct.pack('!Q', 1) + struct.pack('!I', len(packet)) + packet
    assert decode(bundle) == decode(packet)
    for invalid in [b'', packet[:-1], packet+b'xxxx', bundle[:-4],
                    b'#bundle\0'+struct.pack('!Q', 2), b'x'*20000,
                    b'/t\0\0,f\0\0'+struct.pack('!f', float('nan'))]:
        with pytest.raises(ValueError):
            decode(invalid)
    nested = packet
    for _ in range(10):
        nested = b'#bundle\0'+struct.pack('!Q', 1)+struct.pack('!I', len(nested))+nested
    with pytest.raises(ValueError):
        decode(nested)
    for argument in [{'type': 'i', 'value': True}, {'type': 'f', 'value': float('inf')},
                     {'type': 's', 'value': 'bad\0string'}, {'type': 'b', 'value': 0}]:
        with pytest.raises(ValueError):
            encode('/test', [argument])


def test_osc_feedback_query_commands_stale_and_cleanup():
    async def run():
        received = asyncio.Queue()

        class Peer(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                received.put_nowait((data, addr))

        peer, _ = await asyncio.get_running_loop().create_datagram_endpoint(Peer, local_addr=('127.0.0.1', 0))
        async def publish(state):
            pass
        plugin = OSCPlugin(Device(name='OSC', plugin_id='generic.osc', category='audio',
            endpoint=DeviceEndpoint(transport='udp', host='127.0.0.1', port=peer.get_extra_info('sockname')[1]),
            custom_config={'receive_port': 0, 'statuses': [
                {'id': 'level', 'label': 'Poziom', 'address': '/level', 'argument_index': 1,
                 'value_type': 'number', 'unit': 'dBFS', 'query_address': '/get/level'}],
                'buttons': [{'id': 'go', 'label': 'GO', 'address': '/go', 'arguments': [{'type': 'i', 'value': 1}]}]}),
            PluginContext(publish), descriptor())
        try:
            await plugin.connect()
            await plugin.start()
            assert (await plugin.get_state()).status.value == 'degraded'
            await plugin.poll()
            assert decode((await asyncio.wait_for(received.get(), 1))[0]) == [('/get/level', [])]
            local = plugin.transport.socket.get_extra_info('sockname')[1]
            # Feedback can originate from a different port on the configured peer IP.
            feedback, _ = await asyncio.get_running_loop().create_datagram_endpoint(Peer, local_addr=('127.0.0.1', 0))
            try:
                feedback.sendto(b'invalid!', ('127.0.0.1', local))
                feedback.sendto(encode('/unknown', [{'type': 'i', 'value': 9}]), ('127.0.0.1', local))
                feedback.sendto(encode('/level', [{'type': 'i', 'value': 2}, {'type': 'f', 'value': -12.5}]), ('127.0.0.1', local))
                async with asyncio.timeout(2):
                    while not plugin.last_feedback:
                        await asyncio.sleep(.01)
                state = await plugin.get_state()
                assert state.status.value == 'online'
                assert state.metrics[0].key == 'osc.level'
                assert state.metrics[0].value == -12.5
                assert plugin.bad_packets == 1
                result = await plugin.execute_command('send_osc', {'button_id': 'go'})
                assert not result.confirmed and result.result['delivery'] == 'sent'
                assert decode((await asyncio.wait_for(received.get(), 1))[0]) == [('/go', [1])]
                with pytest.raises(ValueError):
                    await plugin.execute_command('send_osc', {'button_id': 'arbitrary'})
                plugin.samples['level'].observed_at -= timedelta(seconds=60)
                assert (await plugin.get_state()).metrics[0].quality == 'stale'
                assert (await plugin.health_check()).degraded
                assert plugin.transport.connected
            finally:
                feedback.close()
        finally:
            await plugin.disconnect()
            peer.close()
            await plugin.context.close()
        assert not plugin.transport.connected and plugin.reader is None
    asyncio.run(run())


def test_osc_api_permission_configuration_and_real_udp(tmp_path):
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(('127.0.0.1', 0))
    peer.settimeout(2)
    app = create_app(Settings(data_dir=tmp_path, mock_mode=False, bootstrap_password='osc-preview-test-password'))
    try:
        with TestClient(app) as client:
            assert client.get('/osc-ui.js').status_code == 200
            session = client.post('/api/auth/login', json={'username': 'admin', 'password': 'osc-preview-test-password'}).json()
            client.headers['X-CSRF-Token'] = session['csrf_token']
            assert any(p['plugin_id'] == 'generic.osc' and not p['load_error'] for p in client.get('/api/plugins').json())
            config = {'name': 'OSC test', 'plugin_id': 'generic.osc', 'category': 'audio',
                      'poll_interval': .1, 'stale_after': 2,
                      'endpoint': {'transport': 'udp', 'host': '127.0.0.1', 'port': peer.getsockname()[1]},
                      'custom_config': {'receive_port': 0, 'statuses': [], 'buttons': [
                          {'id': 'go', 'label': 'GO', 'address': '/go', 'arguments': [{'type': 'i', 'value': 7}]}]}}
            response = client.post('/api/devices', json=config)
            assert response.status_code == 201, response.text
            device = response.json()['id']
            deadline = time.monotonic()+10
            while client.get(f'/api/devices/{device}/state').json()['status'] != 'degraded':
                assert time.monotonic() < deadline
                time.sleep(.05)
            result = client.post(f'/api/devices/{device}/commands', json={'command': 'send_osc', 'parameters': {'button_id': 'go'}})
            assert result.status_code == 202
            assert decode(peer.recv(4096)) == [('/go', [7])]
            command_id = result.json()['command_id']
            while True:
                result = client.get('/api/commands/'+command_id).json()
                if result['status'] == 'unknown':
                    break
                assert time.monotonic() < deadline
                time.sleep(.02)
            assert result['result']['delivery'] == 'sent' and result['confirmed'] is False
            invalid = {**config, 'custom_config': {'buttons': [{'id': 'bad', 'label': 'bad', 'address': '/test',
                        'arguments': [{'type': 'i', 'value': 'text'}]}]}}
            assert client.post('/api/devices', json=invalid).status_code == 422
            assert client.post('/api/users', json={'username': 'viewer', 'password': 'osc-preview-test-password', 'roles': ['viewer']}).status_code == 201
            session = client.post('/api/auth/login', json={'username': 'viewer', 'password': 'osc-preview-test-password'}).json()
            client.headers['X-CSRF-Token'] = session['csrf_token']
            assert client.get(f'/api/devices/{device}/state').status_code == 200
            assert not client.get(f'/api/devices/{device}/commands').json()[0]['can_execute']
            assert client.post(f'/api/devices/{device}/commands', json={'command': 'send_osc', 'parameters': {'button_id': 'go'}}).status_code == 403
            assert can_command({'id': 'operator', 'roles': ['operator'], 'device_ids': None}, device, 'osc.control')
    finally:
        peer.close()
