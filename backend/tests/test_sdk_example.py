import asyncio
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from stage_monitor.contracts import Device, DeviceEndpoint, PluginDescriptor
from stage_monitor.sdk import PluginContext

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/plugins/http_sensor"


def load_example():
    spec = importlib.util.spec_from_file_location("sdk_http_sensor_example", EXAMPLE / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    descriptor = PluginDescriptor.model_validate(yaml.safe_load(
        (EXAMPLE / "manifest.yaml").read_text(encoding="utf-8")))
    return module.HTTPSensorPlugin, descriptor


def test_sdk_example_real_http_readback_quality_and_cleanup():
    class Endpoint(BaseHTTPRequestHandler):
        status = {"temperature": 22.4, "humidity": 48, "output": False}
        writes = []
        apply_write = True

        def log_message(self, *args):
            pass

        def send_body(self, data):
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            assert self.path == "/api/status"
            self.send_body(self.status)

        def do_PUT(self):
            assert self.path == "/api/output"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.writes.append(body)
            if self.apply_write:
                self.status["output"] = body["enabled"]
            self.send_body({"accepted": True})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def run():
        plugin_class, descriptor = load_example()
        Draft202012Validator.check_schema(descriptor.configuration_schema)
        Draft202012Validator(descriptor.configuration_schema).validate({"enable_control": True})
        device = Device(name="SDK test endpoint", plugin_id=descriptor.plugin_id,
            category="environment", endpoint=DeviceEndpoint(transport="http", host="127.0.0.1",
            port=server.server_port), custom_config={"enable_control": True})

        async def publish(state):
            raise AssertionError("Polling example should return its state")

        context = PluginContext(publish)
        plugin = plugin_class(device, context, descriptor)
        await plugin.connect()
        await plugin.start()
        state = await plugin.get_state()
        assert state.status == "online"
        assert state.metrics[0].value == 22.4
        assert (await plugin.health_check()).reachable
        assert plugin.get_commands()[0].name == "set_output"
        assert (await plugin.execute_command("set_output", {"enabled": True})).confirmed
        assert Endpoint.writes == [{"enabled": True}]
        Endpoint.apply_write = False
        assert not (await plugin.execute_command("set_output", {"enabled": False})).confirmed
        Endpoint.status["temperature"] = "bad"
        Endpoint.status.pop("humidity")
        state = await plugin.poll()
        assert state.status == "degraded"
        assert [m.quality for m in state.metrics] == ["invalid", "unknown"]
        assert all(m.value is None for m in state.metrics)
        with pytest.raises(ValueError):
            await plugin.execute_command("set_output", {"enabled": 1})
        plugin.control_enabled = False
        assert plugin.get_commands() == []
        with pytest.raises(PermissionError):
            await plugin.execute_command("set_output", {"enabled": False})
        await plugin.stop()
        await plugin.disconnect()
        await plugin.disconnect()
        assert not (await plugin.health_check()).reachable
        await context.close()

    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
