import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import yaml

from stage_monitor.contracts import Device, DeviceEndpoint, PluginDescriptor
from stage_monitor.plugins.openwrt.plugin import OpenWrtPlugin, UbusError
from stage_monitor.sdk import PluginContext


class UbusHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        assert self.path == "/ubus"
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        session, service, method, args = request["params"]
        self.server.calls.append((service, method))
        operation = (service, method)
        if operation == ("session", "login"):
            assert session == "0" * 32 and args == {"username": "monitor", "password": "test"}
            self.server.session = f"{len(self.server.calls):032x}"
            answer = {"result": [0, {"ubus_rpc_session": self.server.session}]}
        elif session != self.server.session:
            answer = {"error": {"code": -32002, "message": "Access denied"}}
        elif operation == ("session", "destroy"):
            answer = {"result": [0]}
        elif operation == ("system", "board"):
            answer = {"result": [0, {
                "model": "Test router", "kernel": "6.6", "release": {"description": "OpenWrt test"},
            }]}
        elif operation == ("system", "info"):
            answer = {"result": [0, {
                "uptime": self.server.uptime, "memory": {"free": 1048576, "total": 8388608},
                "load": [32768, 0, 65536],
            }]}
        elif operation == ("network.device", "status"):
            if self.server.fail_network:
                answer = {"result": [6]}
            else:
                port = {"up": True, "carrier": True, "statistics": {
                    "rx_bytes": self.server.rx_bytes, "tx_bytes": 1000,
                    "rx_errors": 0, "tx_errors": 1, "rx_dropped": 2,
                }}
                answer = {"result": [0, {"eth0": port, "lo": port, "br-lan": port}]}
        else:
            assert operation == ("file", "read")
            assert args == {"path": "/sys/class/thermal/thermal_zone0/temp"}
            answer = {"result": [0, {"data": "42500\n"}]} if self.server.thermal else {"result": [4]}
        payload = json.dumps({"jsonrpc": "2.0", "id": request["id"], **answer}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def test_openwrt_snapshot_session_expiry_rates_and_missing_sensor():
    with ThreadingHTTPServer(("127.0.0.1", 0), UbusHandler) as server:
        server.calls, server.uptime, server.rx_bytes = [], 7200, 1000
        server.thermal, server.fail_network = True, False
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        async def exercise():
            path = Path(__file__).parents[1] / "src/stage_monitor/plugins/openwrt/manifest.yaml"
            manifest = PluginDescriptor.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            device = Device(name="OpenWrt test", plugin_id=manifest.plugin_id, category="network",
                endpoint=DeviceEndpoint(transport="http", host="127.0.0.1", port=server.server_port),
                custom_config={
                    "allow_plain_http": True, "username": "monitor",
                    "temperature_path": "/sys/class/thermal/thermal_zone0/temp",
                })
            plugin = OpenWrtPlugin(device, PluginContext(None, credential_reader=lambda: "test"), manifest)
            moments = iter((10, 12, 14, 16))
            plugin.clock = lambda: next(moments)
            try:
                await plugin.connect()
                first = await plugin.poll()
                assert first.identity.firmware == "OpenWrt test"
                assert first.extra["ports"] == [{
                    "name": "eth0", "up": True, "disabled": False, "rx_errors": 0,
                    "tx_errors": 1, "rx_dropped": 2,
                }]
                assert {m.key: m.value for m in first.metrics}["temperature"] == 42.5

                server.session, server.uptime, server.rx_bytes = "f" * 32, 7202, 1200
                second = await plugin.poll()
                assert second.extra["ports"][0]["rx_bps"] == 800
                assert second.extra["ports"][0]["tx_bps"] == 0
                assert server.calls.count(("session", "login")) == 2
                assert {m.key: m.value for m in second.metrics}["system.load_1m"] == 0.5

                server.uptime, server.rx_bytes, server.thermal = 10, 1500, False
                third = await plugin.poll()
                assert "rx_bps" not in third.extra["ports"][0]
                assert "temperature" not in {m.key for m in third.metrics}

                server.uptime, server.rx_bytes = 12, 100
                reset = await plugin.poll()
                assert "rx_bps" not in reset.extra["ports"][0]
                assert reset.extra["ports"][0]["tx_bps"] == 0

                server.fail_network = True
                with pytest.raises(UbusError):
                    await plugin.poll()
                state = await plugin.get_state()
                assert state.status == "offline"
                assert all(m.quality == "stale" for m in state.metrics)
                assert plugin.get_commands() == []
            finally:
                await plugin.disconnect()
            assert server.calls[-1] == ("session", "destroy")

        try:
            asyncio.run(exercise())
        finally:
            server.shutdown()
            thread.join()
