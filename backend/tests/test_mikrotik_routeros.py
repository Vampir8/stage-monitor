import asyncio
import json
from pathlib import Path

import httpx
import pytest
import yaml

from stage_monitor.contracts import Device,DeviceEndpoint,PluginDescriptor
from stage_monitor.plugins.mikrotik_crs326 import plugin as shared
from stage_monitor.plugins.mikrotik_routeros.plugin import RouterOSPlugin
from stage_monitor.sdk import PluginContext
from stage_monitor.transports import HTTPTransport


@pytest.mark.parametrize('board,types', [('Metal 52 ac',['ether','wlan']),('RB5009UG+S+',['ether','ether']),('cAP ax',['wifi'])])
def test_generic_models_missing_health_and_physical_traffic(monkeypatch,board,types):
    async def run():
        descriptor=PluginDescriptor.model_validate(yaml.safe_load((Path(shared.__file__).parents[1]/'mikrotik_routeros/manifest.yaml').read_text(encoding='utf-8')))
        device=Device(name='Fixture',plugin_id=descriptor.plugin_id,category='network',
                      endpoint=DeviceEndpoint(transport='http',host='127.0.0.1',port=80),custom_config={'allow_plain_http':True})
        monkeypatch.delenv('STAGE_CRS_PASSWORD',raising=False)
        rows=[{'.id':f'*{i+1}','type':kind,'name':f'{kind}{i+1}','default-name':f'{kind}{i+1}',
               'running':'true','disabled':'false','rx-byte':'100','tx-byte':'50'} for i,kind in enumerate(types)]
        # Virtual radios/bridges must never double count physical traffic.
        rows.extend([{'.id':'*A','type':'bridge','name':'bridge1','rx-byte':'99999'},
                     {'.id':'*B','type':'wifi','name':'guest','master-interface':'wifi1','rx-byte':'99999'}])
        monitor_calls=[]
        def response(request):
            assert request.headers['authorization'].startswith('Basic ')
            if request.url.path=='/rest/system/resource':return httpx.Response(200,json={'board-name':board,'version':'7.20','cpu-load':'3','uptime':'1h','free-memory':'1000','total-memory':'2000'})
            if request.url.path=='/rest/system/identity':return httpx.Response(200,json={'name':'Fixture'})
            if request.url.path=='/rest/interface/print':return httpx.Response(200,json=rows)
            if request.url.path=='/rest/interface/ethernet/monitor':
                monitor_calls.append(json.loads(request.content)['numbers'])
                return httpx.Response(404,json={'error':404})
            return httpx.Response(404,json={'error':404})
        monkeypatch.setattr(shared,'HTTPTransport',lambda *args,**kw:HTTPTransport(*args,client_transport=httpx.MockTransport(response),**kw))
        plugin=RouterOSPlugin(device,PluginContext(lambda s:None,credential_reader=lambda:'fixture-secret'),descriptor)
        ticks=iter([100.,102.]);plugin.clock=lambda:next(ticks)
        await plugin.connect()
        try:
            state=await plugin.poll()
            keys={m.key for m in state.metrics}
            assert state.identity.model==board
            assert 'temperature' not in keys and not any(k.startswith('network.sfp') for k in keys)
            assert not any(m.channel for m in state.metrics)
            assert 'ports' not in state.extra
            assert next(m.value for m in state.metrics if m.key=='network.ports_summary')==f'{len(types)}/{len(types)}'
            for row in rows[:len(types)]:row['rx-byte']='300';row['tx-byte']='150'
            state=await plugin.poll()
            assert next(m.value for m in state.metrics if m.key=='network.rx_bps')==len(types)*800
            assert next(m.value for m in state.metrics if m.key=='network.tx_bps')==len(types)*400
            expected=','.join(f'*{i+1}' for i,t in enumerate(types) if t=='ether')
            assert monitor_calls==[expected,expected] if expected else not monitor_calls
            assert (await plugin.health_check()).reachable
        finally:await plugin.disconnect()
    asyncio.run(run())
