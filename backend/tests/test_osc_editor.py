import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from stage_monitor.contracts import Device, DeviceEndpoint, Metric
from stage_monitor.main import create_app
from stage_monitor.plugins.generic_osc.plugin import OSCPlugin
from stage_monitor.plugins.generic_osc.protocol import decode
from stage_monitor.runtime import encode as ipc_encode
from stage_monitor.sdk import PluginContext
from stage_monitor.settings import Settings
from test_generic_osc import descriptor


def controls():
    return [
        {'id': 'hold', 'label': 'HOLD', 'kind': 'momentary', 'address': '/hold',
         'arguments': [{'type': 'i', 'value': 1}], 'release_arguments': [{'type': 'i', 'value': 0}], 'hold_timeout_ms': 300},
        {'id': 'toggle', 'label': 'ON/OFF', 'kind': 'toggle', 'address': '/toggle',
         'arguments': [{'type': 'i', 'value': 1}], 'off_arguments': [{'type': 'i', 'value': 0}], 'feedback_id': 'playing'},
        {'id': 'level', 'label': 'LEVEL', 'kind': 'slider', 'address': '/level',
         'arguments': [{'type': 's', 'value': 'main'}, {'type': 'f', 'value': 0}],
         'value_index': 1, 'minimum': -60, 'maximum': 0, 'step': .5},
    ]


def make_plugin():
    async def publish(state):
        pass
    return OSCPlugin(Device(name='Editor test', plugin_id='generic.osc', category='audio',
        endpoint=DeviceEndpoint(transport='udp', host='127.0.0.1', port=9000),
        custom_config={'receive_port': 0, 'statuses': [{'id': 'playing', 'label': 'Play',
            'address': '/playing', 'value_type': 'boolean'}], 'buttons': controls()}), PluginContext(publish), descriptor())


def test_momentary_lease_renewal_timeout_and_shutdown_release():
    async def run():
        sent = []
        plugin = make_plugin()

        async def write(packet):
            sent.extend(decode(packet))
        plugin.transport.write = write
        await plugin.start()
        try:
            await plugin.execute_command('send_osc', {'button_id': 'hold', 'action': 'press'})
            renewal = await plugin.execute_command('send_osc', {'button_id': 'hold', 'action': 'press'})
            assert renewal.result['delivery'] == 'lease_renewed'
            assert sent == [('/hold', [1])]
            async with asyncio.timeout(2):
                while plugin.held:
                    await asyncio.sleep(.02)
            assert sent[-1] == ('/hold', [0])
            await plugin.execute_command('send_osc', {'button_id': 'hold', 'action': 'press'})
            await plugin.execute_command('send_osc', {'button_id': 'hold', 'action': 'release'})
            assert sent[-1] == ('/hold', [0]) and not plugin.held
            await plugin.execute_command('send_osc', {'button_id': 'hold', 'action': 'press'})
            await plugin.stop()
            assert sent[-1] == ('/hold', [0]) and not plugin.held
        finally:
            await plugin.stop()
            await plugin.context.close()
    asyncio.run(run())


def test_slider_bounds_steps_toggle_feedback_and_unsaved_test():
    async def run():
        plugin, sent = make_plugin(), []

        async def write(packet):
            sent.extend(decode(packet))
        plugin.transport.write = write
        await plugin.execute_command('send_osc', {'button_id': 'level', 'action': 'set', 'value': -12.2})
        assert sent == [('/level', ['main', -12.0])]
        for value in [-61, 1, True, float('nan')]:
            with pytest.raises(ValueError):
                await plugin.execute_command('send_osc', {'button_id': 'level', 'action': 'set', 'value': value})
        with pytest.raises(ValueError):
            await plugin.execute_command('send_osc', {'button_id': 'toggle', 'action': 'on'})
        plugin.samples['playing'] = Metric(device_id=plugin.device.id, key='osc.playing', value=False)
        await plugin.execute_command('send_osc', {'button_id': 'toggle', 'action': 'on'})
        assert sent[-1] == ('/toggle', [1])
        assert plugin.samples['playing'].value is False  # No optimistic feedback.
        plugin.samples['playing'].observed_at -= timedelta(seconds=60)
        with pytest.raises(ValueError):
            await plugin.execute_command('send_osc', {'button_id': 'toggle', 'action': 'off'})
        draft = {'id': 'unsaved', 'label': 'Draft', 'address': '/draft', 'arguments': [{'type': 's', 'value': 'test'}]}
        result = await plugin.execute_command('test_osc', {'control': draft})
        assert sent[-1] == ('/draft', ['test']) and result.result['delivery'] == 'sent'
        assert all(item['id'] != 'unsaved' for item in plugin.buttons)
    asyncio.run(run())


def test_capture_optin_expiry_bounds_and_unknown_addresses():
    async def run():
        plugin = make_plugin()
        plugin._capture('RX', '/unmapped', [42])
        assert not (await plugin.inspect())['messages']
        result = await plugin.inspect('start', seconds=60)
        assert result['remaining'] > 0
        plugin._capture('RX', '/unmapped', [42, True, 'title'])
        result = await plugin.inspect(cursor=0)
        assert result['messages'][0]['address'] == '/unmapped'
        assert result['messages'][0]['arguments'] == [42, True, 'title']
        for _ in range(110):
            plugin.capture_window -= 2
            plugin._capture('RX', '/'+('😀'*250), ['😀'*200]*32)
        result = await plugin.inspect(cursor=0)
        assert len(result['messages']) == 100
        assert len(ipc_encode(result)) < 1024*1024
        assert result['messages'][-1]['truncated']
        plugin.capture_until = 0
        assert not (await plugin.inspect())['messages']
    asyncio.run(run())


def test_mapping_presets_library_roundtrip_and_permission(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, mock_mode=False, bootstrap_password='mapping-editor-password'))
    with TestClient(app) as client:
        session = client.post('/api/auth/login', json={'username': 'admin', 'password': 'mapping-editor-password'}).json()
        client.headers['X-CSRF-Token'] = session['csrf_token']
        for asset in ['/mapping-editor.js', '/meter-ui.js']:
            assert client.get(asset).status_code == 200
        preset = {'schema_version': 1, 'name': 'Studio', 'plugin_id': 'generic.osc',
                  'mapping': {'statuses': [{'id': 'playing', 'label': 'Play', 'address': '/playing', 'value_type': 'boolean'}], 'buttons': controls()}}
        response = client.post('/api/mapping-presets', json=preset)
        assert response.status_code == 201, response.text
        saved = response.json()
        listed = client.get('/api/mapping-presets?plugin_id=generic.osc').json()
        assert listed[0]['mapping'] == preset['mapping']
        assert client.post('/api/mapping-presets', json=preset).status_code == 409
        assert client.post('/api/mapping-presets', json={**preset, 'name': 'Invalid', 'mapping': {'receive_port': 9001}}).status_code == 422
        assert client.post('/api/mapping-presets', json={**preset, 'schema_version': 2}).status_code == 422
        client.post('/api/users', json={'username': 'operator', 'password': 'mapping-editor-password', 'roles': ['operator']})
        session = client.post('/api/auth/login', json={'username': 'operator', 'password': 'mapping-editor-password'}).json()
        client.headers['X-CSRF-Token'] = session['csrf_token']
        assert client.get('/api/mapping-presets?plugin_id=generic.osc').status_code == 403
        assert client.post('/api/mapping-presets', json=preset).status_code == 403
        from stage_monitor.auth import can_command
        user = {'id': 'operator', 'roles': ['operator'], 'device_ids': None}
        assert can_command(user, 'device', 'osc.control')
        assert not can_command(user, 'device', 'configuration.osc_test')
        session = client.post('/api/auth/login', json={'username': 'admin', 'password': 'mapping-editor-password'}).json()
        client.headers['X-CSRF-Token'] = session['csrf_token']
        assert client.delete('/api/mapping-presets/'+saved['id']).status_code == 204
        assert not client.get('/api/mapping-presets?plugin_id=generic.osc').json()
    with TestClient(create_app(Settings(data_dir=tmp_path, mock_mode=False))) as client:
        session = client.post('/api/auth/login', json={'username': 'admin', 'password': 'mapping-editor-password'}).json()
        client.headers['X-CSRF-Token'] = session['csrf_token']
        assert client.post('/api/mapping-presets', json={**preset, 'name': 'Persistent'}).status_code == 201
    with TestClient(create_app(Settings(data_dir=tmp_path, mock_mode=False))) as client:
        client.post('/api/auth/login', json={'username': 'admin', 'password': 'mapping-editor-password'})
        assert client.get('/api/mapping-presets?plugin_id=generic.osc').json()[0]['name'] == 'Persistent'
