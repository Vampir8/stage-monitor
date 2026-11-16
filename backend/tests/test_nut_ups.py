from socketserver import StreamRequestHandler, ThreadingTCPServer
from threading import Thread
from time import monotonic, sleep

from fastapi.testclient import TestClient

from stage_monitor.main import create_app
from stage_monitor.settings import Settings

PASSWORD = "nut-ups-test-password-2026"


def eventually(check, timeout=15):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        result = check()
        if result:
            return result
        sleep(0.05)
    raise AssertionError(f"NUT condition not satisfied within {timeout}s")


class UpsdHandler(StreamRequestHandler):
    def handle(self):
        command = self.rfile.readline().decode().strip()
        self.server.commands.append(command)
        if command == "LIST UPS":
            reply = (
                'BEGIN LIST UPS\nUPS keor "Rack UPS"\nUPS backup "Backup UPS"\nEND LIST UPS\n'
            )
        elif command == "LIST VAR keor" and self.server.status != "STALE":
            measured = self.server.measured_watts
            measured_row = f'VAR keor ups.realpower "{measured}"\n' if measured is not None else ""
            reply = (
                'BEGIN LIST VAR keor\nVAR keor ups.mfr "Legrand"\n'
                'VAR keor ups.model "Keor PDU"\nVAR keor battery.charge "85"\n'
                'VAR keor battery.runtime "NaN"\nVAR keor ups.load "17"\n'
                'VAR keor ups.realpower.nominal "480"\n'
                f'{measured_row}'
                'VAR keor ups.vendorid "1cb0"\nVAR keor ups.productid "0038"\n'
                'VAR keor driver.version.data "Legrand HID 0.2"\n'
                f'VAR keor battery.voltage.nominal "{self.server.nominal}"\n'
                f'VAR keor battery.voltage "{self.server.battery_voltage}"\n'
                'VAR keor output.voltage "22"\n'
                'VAR keor input.voltage "227"\n'
                f'VAR keor ups.status "{self.server.status}"\nEND LIST VAR keor\n'
            )
        elif command == "LIST VAR keor":
            reply = "ERR DATA-STALE\n"
        elif command == "LIST VAR backup":
            reply = 'BEGIN LIST VAR backup\nVAR backup ups.status "OL"\nEND LIST VAR backup\n'
        else:
            reply = "ERR UNKNOWN-COMMAND\n"
        self.wfile.write(reply.encode())


def test_nut_ups_options_monitoring_alert_rules_and_stale_data(tmp_path):
    app = create_app(Settings(
        data_dir=tmp_path, bootstrap_password=PASSWORD, mock_mode=False,
        reconnect_base_seconds=0.2, reconnect_max_seconds=1,
    ))
    with ThreadingTCPServer(("127.0.0.1", 0), UpsdHandler) as server:
        server.status, server.commands, server.nominal = "OL", [], "1.2"
        server.battery_voltage = "1.4"
        server.measured_watts = None
        listener = Thread(target=server.serve_forever, daemon=True)
        listener.start()
        endpoint = {"transport": "tcp", "host": "127.0.0.1", "port": server.server_address[1]}
        options_url = "/api/plugins/nut.ups/configuration/ups_name"
        try:
            with TestClient(app) as client:
                assert client.post(options_url, json=endpoint).status_code == 401
                login = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
                assert login.status_code == 200, login.text
                client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
                options = client.post(options_url, json=endpoint)
                assert options.status_code == 200, options.text
                assert options.json() == [
                    {"value": "keor", "label": "Rack UPS"},
                    {"value": "backup", "label": "Backup UPS"},
                ]

                def add_ups(name):
                    response = client.post("/api/devices", json={
                        "name": name, "category": "power", "plugin_id": "nut.ups",
                        "endpoint": endpoint, "custom_config": {"ups_name": name},
                        "poll_interval": 0.15, "stale_after": 3,
                    })
                    assert response.status_code == 201, response.text
                    return response.json()["id"]

                def state(device_id):
                    return client.get(f"/api/devices/{device_id}/state").json()

                keor, backup = add_ups("keor"), add_ups("backup")
                eventually(lambda: state(keor)["status"] == state(backup)["status"] == "online")
                current = state(keor)
                assert current["identity"]["manufacturer"] == "Legrand"
                assert {m["key"]: m["value"] for m in current["metrics"]} == {
                    "ups.status": "OL", "battery.percent": 85, "ups.load": 17,
                    "battery.voltage": 14, "output.voltage": 220, "input.voltage": 227,
                    "ups.estimated_realpower": 82,
                }
                watts = next(m for m in current["metrics"] if m["key"] == "ups.estimated_realpower")
                assert watts["unit"] == "W"
                output = next(m for m in current["metrics"] if m["key"] == "output.voltage")
                assert output["labels"] == {"nut_raw_value": "22", "nut_scale_factor": "10"}
                assert output["quality"] == "unknown"
                assert [m["key"] for m in state(backup)["metrics"]] == ["ups.status"]
                assert client.get(f"/api/devices/{keor}/commands").json() == []

                response = client.post("/api/alert-rules", json={
                    "name": "UPS on battery", "source": "parameter", "key": "ups.on_battery",
                    "operator": "eq", "threshold": True, "device_ids": [keor], "hold_seconds": 0,
                })
                assert response.status_code == 201, response.text
                server.status = "OB LB"
                eventually(lambda: state(keor)["parameters"].get("ups.low_battery"))
                eventually(lambda: client.get("/api/alerts").json())
                assert state(keor)["status"] == "online"

                server.nominal = "12"
                server.battery_voltage = "13.5"
                eventually(lambda: any(
                    metric["key"] == "battery.voltage" and metric["value"] == 13.5
                    for metric in state(keor)["metrics"]
                ))
                assert next(
                    metric for metric in state(keor)["metrics"] if metric["key"] == "output.voltage"
                )["quality"] == "unknown"
                server.measured_watts = 79

                def measured_watts():
                    metrics = {m["key"]: m["value"] for m in state(keor)["metrics"]}
                    return metrics if metrics.get("power.active") == 79 else None

                assert "ups.estimated_realpower" not in eventually(measured_watts)
                server.status = "STALE"
                eventually(lambda: state(keor)["status"] in {"error", "offline"})
                assert all(m["quality"] == "stale" for m in state(keor)["metrics"])
                assert state(backup)["status"] == "online"
                assert set(server.commands) == {"LIST UPS", "LIST VAR keor", "LIST VAR backup"}
        finally:
            server.shutdown()
            listener.join()
