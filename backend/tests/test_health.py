from datetime import timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from stage_monitor.contracts import Device, DeviceHealth, DeviceState, Metric, now
from stage_monitor.health import snapshot
from stage_monitor.main import create_app
from stage_monitor.settings import Settings


def state(devices, states, alarms=()):
    return SimpleNamespace(
        manager=SimpleNamespace(devices={d.id: d for d in devices}, states={s.device_id: s for s in states},
                                supervisors={}, queues={}),
        alerts=SimpleNamespace(open_alerts={str(i): a for i, a in enumerate(alarms)}, last_error=None),
        chat=SimpleNamespace(last_error=None), storage=SimpleNamespace(last_error=None),
        plugins=SimpleNamespace(list=lambda: []), weather=SimpleNamespace(snapshot=None, interval=300),
    )


def device(id, **kwargs):
    return Device(id=id, name=id, plugin_id="test", category="future-category", **kwargs)


ADMIN = {"roles": ["admin"], "device_ids": None}


def test_health_scope_disabled_and_acknowledged_critical():
    clock = now()
    devices = [device("visible"), device("private"), device("disabled", enabled=False)]
    states = [DeviceState(device_id="visible", status="online", connection_status="connected",
                          health=DeviceHealth(last_seen=clock)),
              DeviceState(device_id="private", status="error"), DeviceState(device_id="disabled")]
    alarm = SimpleNamespace(device_id="private", status="ACKNOWLEDGED", severity="critical")
    source = state(devices, states, [alarm])
    scoped = snapshot(source, {"roles": ["viewer"], "device_ids": ["visible", "disabled"]}, True, clock)
    assert scoped.fleet == "ok" and scoped.counts["offline"] == 0
    assert scoped.counts["disabled"] == 1
    assert scoped.alert_counts["critical"] == 0 and scoped.diagnostics is None
    assert {d.device_id for d in scoped.devices} == {"visible", "disabled"}
    assert scoped.categories[0].devices == 1 and scoped.categories[0].category == "future-category"
    source.manager.states["private"].status = "online"
    source.manager.states["private"].health.last_seen = clock
    all_data = snapshot(source, ADMIN, True, clock)
    assert all_data.fleet == "error"
    assert all_data.alert_counts["ACKNOWLEDGED"] == 1
    assert all_data.diagnostics is not None


def test_health_freshness_future_metrics_missing_contact_and_connecting():
    clock = now()
    row = DeviceState(device_id="a", status="online", connection_status="connected",
                      health=DeviceHealth(last_seen=clock), metrics=[
                          Metric(device_id="a", key="fresh", value=1, observed_at=clock),
                          Metric(device_id="a", key="old", value=2, observed_at=clock-timedelta(seconds=11)),
                          Metric(device_id="a", key="future", value=3, observed_at=clock+timedelta(seconds=1)),
                          Metric(device_id="a", key="bad", value=4, quality="invalid"),
                      ])
    source = state([device("a")], [row])
    result = snapshot(source, ADMIN, False, clock)
    assert result.fleet == "degraded"
    assert result.devices[0].telemetry == {"fresh": 1, "stale": 2, "invalid": 1, "unknown": 0}
    row.metrics = []
    row.health.last_seen = None
    assert snapshot(source, ADMIN, False, clock).fleet == "unknown"
    row.connection_status = "connecting"
    result = snapshot(source, ADMIN, False, clock)
    assert result.counts["connecting"] == 1 and result.counts["online"] == 0
    assert result.fleet == "unknown"
    source.manager.devices["a"].enabled = False
    assert snapshot(source, ADMIN, False, clock).fleet == "unknown"


def test_health_api_auth_runtime_errors_and_cached_weather(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, bootstrap_password="health-test-password", mock_mode=False))
    with TestClient(app) as client:
        assert client.get("/api/system/health").status_code == 401
        assert client.post("/api/auth/login", json={"username":"admin", "password":"health-test-password"}).status_code == 200
        result = client.get("/api/system/health")
        assert result.status_code == 200 and result.json()["fleet"] == "unknown"
        app.state.chat.last_error = "internal error"
        result = client.get("/api/system/health").json()
        assert result["backend"] == "error" and result["modules"]["chat"] == "error"
        app.state.weather.snapshot = SimpleNamespace(stale=False, last_error=None, fetched_at=now()-timedelta(seconds=601))
        result = client.get("/api/system/health").json()
        assert result["modules"]["weather"] == "degraded"
        app.state.weather.snapshot = None
