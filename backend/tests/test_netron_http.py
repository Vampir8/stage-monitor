import asyncio
import copy

import httpx
import pytest

from stage_monitor.contracts import Device, DeviceEndpoint
from stage_monitor.plugins.netron_en12.http_reader import HTTPReader


def test_http_configuration_is_not_live_dmx_and_failed_poll_marks_stale():
    async def run():
        device = Device(name='EN12', plugin_id='netron.en12', category='gateway',
                        endpoint=DeviceEndpoint(transport='http', host='127.0.0.1', port=80))
        reader = HTTPReader(device)
        row = dict(ptMode=2, ptProtocol=0, ptUniverse=0, ptFramerate=6,
                   ptRDM=1, ptMergeMode=0)
        data = {'/Setting.json':dict(DeviceType='NETRON EN12', UniverseMode=0),
                '/index.json':dict(FirmwareVer='V2.9.2', OnTime='468h'),
                '/IP.json':{}, '/DMXPorts.json':[copy.copy(row) for _ in range(12)]}
        requests = []
        def respond(request):
            assert request.method == 'GET' and request.headers['connection'] == 'close'
            requests.append(request.url.path)
            return httpx.Response(200, json=data[request.url.path])
        reader.transport.client_transport = httpx.MockTransport(respond)
        await reader.transport.connect()
        try:
            state = await reader.poll()
            assert state.status.value == 'online'
            assert state.extra['identity_verified']
            assert state.extra['ports'][0]['universe_display'] == 1
            metrics = {m.key:m for m in state.metrics if m.channel is None}
            assert metrics['gateway.configured_outputs'].value == 12
            assert metrics['gateway.active_outputs'].value is None
            assert metrics['gateway.active_outputs'].quality == 'unknown'
            observed = state.observed_at
            data['/DMXPorts.json'][0]['ptRDM'] = True
            with pytest.raises(ValueError):
                await reader.poll()
            stale = await reader.state()
            assert stale.status.value == 'offline' and stale.observed_at == observed
            assert next(m for m in stale.metrics if m.key == 'gateway.configured_outputs').quality == 'stale'
            data['/DMXPorts.json'][0]['ptRDM'] = 1
            data['/DMXPorts.json'] = data['/DMXPorts.json'][:4]
            assert (await reader.poll()).status.value == 'degraded'
            data['/Setting.json']['DeviceType'] = 'OTHER'
            with pytest.raises(ValueError, match='not NETRON'):
                await reader.poll()
            assert all(p in data for p in requests)
        finally:
            await reader.transport.disconnect()
    asyncio.run(run())


def test_transient_http_timeout_retries_once_and_persistent_failure_is_stale():
    async def run():
        device = Device(name='EN12', plugin_id='netron.en12', category='gateway',
                        endpoint=DeviceEndpoint(transport='http', host='127.0.0.1', port=80))
        reader = HTTPReader(device)
        row = dict(ptMode=2, ptProtocol=0, ptUniverse=0, ptFramerate=6, ptRDM=0, ptMergeMode=0)
        data = {'/Setting.json': dict(DeviceType='NETRON EN12', UniverseMode=0),
                '/index.json': {}, '/IP.json': {}, '/DMXPorts.json': [row] * 12}
        failures = 1
        attempts = 0

        def respond(request):
            nonlocal failures, attempts
            if request.url.path == '/index.json':
                attempts += 1
                if failures:
                    failures -= 1
                    raise httpx.ReadTimeout('temporary timeout', request=request)
            return httpx.Response(200, json=data[request.url.path])

        reader.transport.client_transport = httpx.MockTransport(respond)
        await reader.transport.connect()
        try:
            state = await reader.poll()
            assert state.status.value == 'online' and attempts == 2
            failures, attempts = 10, 0
            with pytest.raises(httpx.ReadTimeout):
                await reader.poll()
            assert attempts == 2
            stale = await reader.state()
            assert stale.status.value == 'offline' and stale.observed_at == state.observed_at
            assert next(m for m in stale.metrics if m.key == 'gateway.configured_outputs').quality == 'stale'
        finally:
            await reader.transport.disconnect()
    asyncio.run(run())
