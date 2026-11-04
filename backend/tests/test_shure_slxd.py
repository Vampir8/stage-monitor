import asyncio
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from stage_monitor.contracts import Device, DeviceEndpoint, PluginDescriptor, now
from stage_monitor.plugins.shure_slxd.plugin import SLXDPlugin
from stage_monitor.plugins.shure_slxd.protocol import parse_frame, sample
from stage_monitor.sdk import PluginContext


def descriptor():
    path = Path(__file__).parents[1] / "src/stage_monitor/plugins/shure_slxd/manifest.yaml"
    return PluginDescriptor.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_slxd_frames_and_documented_scales():
    assert parse_frame(b"< REP 1 FREQUENCY 0578350 >") == ("REP", 1, "FREQUENCY", "0578350")
    assert parse_frame(b"< REP MODEL {SLXD4                          } >") == (
        "REP",
        None,
        "MODEL",
        "SLXD4",
    )
    assert parse_frame(b"< REP 1 CHAN_NAME {Lead > Vocal} >")[3] == "Lead > Vocal"
    assert sample("102 090 086") == (-18, -30, -34)
    for value in ["AX 086 040", "121 090 086", "102 nope 086", "102 090", "-1 000 000"]:
        assert sample(value) is None
    for frame in [b"< SAMPLE ALL 102 102 086 >", b"< REP 0 FREQUENCY 550000 >", b"\xff"]:
        assert parse_frame(frame) is None
    assert "battery.percent" not in {m["key"] for m in descriptor().telemetry}
    assert {c.name for c in descriptor().commands} == {"set_frequency", "set_gain"}


def test_slxd_sentinels_foreign_channel_and_stale_data():
    async def run():
        context = PluginContext(lambda state: None)
        plugin = SLXDPlugin(
            Device(
                name="SLXD4",
                plugin_id="shure.slxd",
                category="microphone",
                endpoint=DeviceEndpoint(transport="tcp", host="127.0.0.1"),
            ),
            context,
            descriptor(),
        )
        assert plugin.get_commands() == []
        with pytest.raises(PermissionError):
            await plugin.execute_command("set_gain", {"gain_db": 0})
        assert not plugin._accept("REP", 2, "FREQUENCY", "0550000")
        assert plugin.parameters == {}
        for sentinel, meaning in [(65533, "warning"), (65534, "calculating"), (65535, "unknown")]:
            assert plugin._accept("REP", 1, "TX_BATT_MINS", str(sentinel))
            assert plugin.metrics["battery.runtime_minutes"].value is None
            assert plugin.vendor["battery_runtime_state"] == meaning
        plugin._accept("REP", 1, "TX_BATT_BARS", "255")
        assert plugin.metrics["battery.bars"].quality == "unknown"
        plugin._accept("REP", 1, "TX_BATT_BARS", "006")
        assert plugin.metrics["battery.bars"].quality == "invalid"
        plugin._accept("REP", 1, "TX_MODEL", "SLXD2")
        plugin._accept("REP", 1, "TX_BATT_MINS", "00125")
        plugin._accept("REP", 1, "TX_BATT_BARS", "004")
        assert plugin.metrics["battery.runtime_minutes"].value == 125
        plugin._accept("REP", 1, "TX_MODEL", "UNKNOWN")
        assert all(m.value is None for k, m in plugin.metrics.items() if k.startswith("battery."))
        plugin._accept("SAMPLE", 1, "ALL", "102 090 086")
        plugin.metrics["audio.level"].observed_at = now() - timedelta(seconds=60)
        state = await plugin.get_state()
        assert next(m for m in state.metrics if m.key == "audio.level").quality == "stale"
        assert plugin.metrics["audio.level"].quality == "good"  # Getters do not mutate the cache.
        await context.close()

    asyncio.run(run())


@pytest.mark.parametrize("model,channel", [("SLXD4", 1), ("SLXD4D", 2)])
def test_slxd_real_tcp_metering_commands_and_cleanup(model, channel):
    async def run():
        requests = []
        writers = set()
        drop_gain = False

        async def endpoint(reader, writer):
            writers.add(writer)

            async def send(text):
                data = text.encode("ascii")
                writer.write(data[:7])
                await writer.drain()
                await asyncio.sleep(0.001)
                writer.write(data[7:])
                await writer.drain()

            try:
                while True:
                    request = (await reader.readuntil(b">")).decode().strip("<> ").split()
                    requests.append(request)
                    if request == ["GET", "MODEL"]:
                        await send(f"< REP MODEL {{{model:32}}} >")
                    elif request == ["GET", "FW_VER"]:
                        await send("< REP FW_VER {2.0.15.2} >")
                    elif request[:2] == ["GET", str(channel)]:
                        key = request[2]
                        values = {
                            "FREQUENCY": "0578350",
                            "TX_MODEL": "SLXD2",
                            "TX_BATT_BARS": "004",
                            "TX_BATT_MINS": "00125",
                            "AUDIO_GAIN": "030",
                            "CHAN_NAME": "{Lead > Vocal}",
                        }
                        if key in values:
                            await send(f"< REP {channel} {key} {values[key]} >")
                    elif request[:2] == ["SET", str(channel)]:
                        key, value = request[2:]
                        if key == "AUDIO_GAIN" and drop_gain:
                            continue
                        if key == "AUDIO_GAIN":
                            # A different channel's reply must never acknowledge this command.
                            await send(f"< REP {3 - channel} AUDIO_GAIN 060 >")
                        await send(
                            f"< REP {channel} {key} {int(value):07d} >"
                            if key == "FREQUENCY"
                            else f"< REP {channel} {key} {value} >"
                        )
                        if key == "METER_RATE" and int(value):
                            await send(f"< SAMPLE {channel} ALL 102 090 086 >")
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writers.discard(writer)
                writer.close()
                with suppress(ConnectionError):
                    await writer.wait_closed()

        server = await asyncio.start_server(endpoint, "127.0.0.1", 0)
        updates = []

        async def publish(state):
            updates.append(state)

        context = PluginContext(publish)
        plugin = SLXDPlugin(
            Device(
                name=model,
                plugin_id="shure.slxd",
                category="microphone",
                endpoint=DeviceEndpoint(
                    transport="tcp", host="127.0.0.1", port=server.sockets[0].getsockname()[1]
                ),
                custom_config={
                    "channel": channel,
                    "enable_control": True,
                    "min_frequency_mhz": 470,
                    "max_frequency_mhz": 608,
                    "request_timeout": 0.2,
                },
            ),
            context,
            descriptor(),
        )
        try:
            await plugin.connect()
            await plugin.start()
            state = await plugin.poll()
            assert state.status == "online" and state.identity.model == model
            assert state.parameters["channel_name"] == "Lead > Vocal"
            metrics = {m.key: m for m in state.metrics}
            assert metrics["audio.level"].value == -30 and metrics["audio.peak"].value == -18
            assert metrics["rf.level"].value == -34 and metrics["rf.level"].unit == "dBm"
            assert {m.channel for m in state.metrics} == {str(channel)}
            assert (
                await plugin.execute_command("set_frequency", {"frequency_mhz": 550.025})
            ).confirmed
            assert plugin.parameters["frequency_mhz"] == 550.025
            results = await asyncio.gather(
                plugin.execute_command("set_gain", {"gain_db": -3}), plugin.health_check()
            )
            assert results[0].confirmed and results[1].reachable
            assert plugin.parameters["gain_db"] == -3
            with pytest.raises(ValueError):
                await plugin.execute_command("set_frequency", {"frequency_mhz": 700})
            with pytest.raises(ValueError):
                await plugin.execute_command("set_gain", {"gain_db": True})
            assert updates
            await plugin.stop()
            await asyncio.sleep(0.02)
            assert ["SET", str(channel), "METER_RATE", "00000"] in requests
            # Restart the subscription and verify timeout invalidates the TCP session.
            await plugin.connect()
            await plugin.start()
            drop_gain = True
            with pytest.raises(TimeoutError):
                await plugin.execute_command("set_gain", {"gain_db": 0})
            assert not plugin.transport.connected
        finally:
            await plugin.stop()
            await plugin.disconnect()
            await context.close()
            for writer in list(writers):
                writer.close()
            server.close()
            await server.wait_closed()

    asyncio.run(run())
