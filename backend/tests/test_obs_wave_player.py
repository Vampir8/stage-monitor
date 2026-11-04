import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from stage_monitor.contracts import Device, DeviceEndpoint, PluginDescriptor
from stage_monitor.plugins.obs_wave_player.plugin import WavePlayerPlugin
from stage_monitor.sdk import PluginContext


def test_wave_levels_real_missing_invalid_stale_and_silence():
    async def run():
        path = Path(__file__).parents[1] / 'src/stage_monitor/plugins/obs_wave_player/manifest.yaml'
        manifest = PluginDescriptor.model_validate(yaml.safe_load(path.read_text(encoding='utf-8')))
        device = Device(name='Wave', plugin_id='obs.wave_player', category='audio',
                        endpoint=DeviceEndpoint(transport='http', host='127.0.0.1', port=8765))
        async def publish(state):
            pass
        plugin = WavePlayerPlugin(device, PluginContext(publish), manifest)
        data = dict(apiVersion=1, position=1, remaining=9, duration=10, volume=.9,
                    bpm=128, age=10, connected=True, playing=True)
        levels = {}

        async def request(*args):
            return SimpleNamespace(json=lambda: levels if args[1].endswith('/meters') else data)
        plugin.transport.request = request

        async def meters():
            return [m for m in (await plugin.poll()).metrics if m.key == 'audio.level']
        assert all(m.value is None and m.quality == 'stale' for m in await meters())
        levels.update(version=1,unit='dBFS',position='post-fader',valid=True,age=100,
            channels=[{'channel':'L','peakDb':-12,'rmsDb':-18}, {'channel':'R','peakDb':-6,'rmsDb':-12}])
        await plugin.apply_meter_frame(levels)
        assert [(m.channel,m.value,m.quality) for m in await meters()] == [('L',-18,'good'),('R',-12,'good')]
        levels['channels'] = [{'channel':channel,'peakDb':-60,'rmsDb':-60} for channel in ['L','R']]
        await plugin.apply_meter_frame(levels)
        assert all(m.value == -60 and m.quality == 'good' for m in await meters())
        plugin.meter_received -= 3
        assert all(m.value is None and m.quality == 'stale' for m in await meters())
        levels['age'] = 0
        levels['channels'] = [{'channel':'L','peakDb':float('nan')}, {'channel':'R','peakDb':False}]
        with pytest.raises(ValueError):
            await plugin.apply_meter_frame(levels)
        assert 'meter:audio.level' in manifest.dashboard['collapsed']
    asyncio.run(run())
