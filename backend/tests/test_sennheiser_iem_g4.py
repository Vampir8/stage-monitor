import asyncio
from pathlib import Path

import pytest
import yaml

from stage_monitor.contracts import Device, DeviceEndpoint, PluginDescriptor
from stage_monitor.plugins.sennheiser_iem_g4.plugin import IEMG4Plugin, parse_frames
from stage_monitor.sdk import PluginContext


def descriptor():
    path=Path(__file__).parents[1]/'src/stage_monitor/plugins/sennheiser_iem_g4/manifest.yaml'
    return PluginDescriptor.model_validate(yaml.safe_load(path.read_text(encoding='utf-8')))


def test_udp_stereo_monitoring_out_of_order_and_timeout():
    async def run():
        class DeviceSimulator(asyncio.DatagramProtocol):
            def connection_made(self, transport): self.transport=transport;self.requests=[];self.silent=False
            def datagram_received(self, data, address):
                self.requests.append(data)
                if self.silent:return
                replies={b'FirmwareRevision\r':b'FirmwareRevision 1.8.0\r',b'Mode\r':b'Mode 1\r',
                         b'Name\r':b'Name Stage 1\r',b'Frequency\r':b'Frequency 550025 0 0\r',
                         b'RfConfig\r':b'RfConfig 516000 558000 25\r',b'Sensitivity\r':b'Sensitivity -21\r',
                         b'Mute\r':b'Mute 0\r'}
                if data==b'Push 0 100 1\r':
                    # Config may arrive before stereo meters; never synthesize AF zeroes.
                    self.transport.sendto(b'Config 234\rMsg OK\r',address)
                    self.transport.sendto(b'Af 15 25 40 38 5\rStates 0 0\r',address)
                else:self.transport.sendto(replies[data],address)
        sock,sim=await asyncio.get_running_loop().create_datagram_endpoint(DeviceSimulator,local_addr=('127.0.0.1',0))
        device=Device(name='G4',plugin_id='sennheiser.iem_g4',category='iem',
                      endpoint=DeviceEndpoint(transport='udp',host='127.0.0.1',port=sock.get_extra_info('sockname')[1]),custom_config={'request_timeout':0.2})
        plugin=IEMG4Plugin(device,PluginContext(lambda s:None),descriptor())
        try:
            await plugin.connect();await plugin.start();state=await plugin.poll()
            assert state.identity.firmware=='1.8.0' and state.parameters['frequency_mhz']==550.025
            assert [(m.key,m.channel,m.value) for m in state.metrics]==[('audio.level','1',15),('audio.level','2',25),('audio.peak','1',40),('audio.peak','2',38)]
            assert not any(m.key.startswith(('rf.','battery.')) for m in state.metrics)
            assert (await plugin.health_check()).reachable and plugin.get_commands()==[]
            with pytest.raises(PermissionError):await plugin.execute_command('mute',{'enabled':True})
            assert b'Mute 1\r' not in sim.requests
            before=plugin.observed
            sim.silent=True
            with pytest.raises(TimeoutError):await plugin.poll()
            assert plugin.observed==before and not (await plugin.health_check()).reachable
        finally:await plugin.disconnect();sock.close()
    asyncio.run(run())


def test_frames_and_invalid_meters_are_rejected():
    assert parse_frames(b'Name FOH 1\rAf 10 20 30 40 0\rConfig 5\r')['AF']=='10 20 30 40 0'
    with pytest.raises(UnicodeDecodeError):parse_frames(b'Name \xff\r')
    device=Device(name='G4',plugin_id='sennheiser.iem_g4',category='iem',endpoint=DeviceEndpoint(transport='udp',host='127.0.0.1'))
    plugin=IEMG4Plugin(device,PluginContext(lambda s:None),descriptor())
    with pytest.raises(ValueError):plugin._accept({'AF':'101 20 30 40','Frequency':'550025 0 0','Mode':'1'})
    assert plugin.observed is None and plugin.metrics==[]
