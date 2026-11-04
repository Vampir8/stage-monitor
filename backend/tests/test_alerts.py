import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from stage_monitor.alerts.models import Rule
from stage_monitor.alerts.service import AlertService
from stage_monitor.contracts import Device, DeviceState, Metric
from stage_monitor.device_manager import Conflict
from stage_monitor.events import EventBus
from stage_monitor.main import create_app
from stage_monitor.settings import Settings
from stage_monitor.storage import Storage

PASSWORD = "alerts-test-password-2026"


def test_debounce_hysteresis_unknown_channels_ack_and_restart(tmp_path):
    async def exercise():
        storage = Storage(tmp_path / "alerts.sqlite3")
        await storage.open()
        device = Device(
            name="Receiver", plugin_id="mock.rf", category="rf", poll_interval=1, stale_after=30
        )
        state = DeviceState(
            device_id=device.id,
            status="online",
            metrics=[
                Metric(device_id=device.id, key="battery.percent", channel="1", value=10),
                Metric(device_id=device.id, key="battery.percent", channel="2", value=15),
            ],
        )
        manager = SimpleNamespace(devices={device.id: device}, states={device.id: state})
        service = AlertService(storage, EventBus(), manager)
        rule = await service.configure(
            Rule(name="Battery", hysteresis=5, hold_seconds=2, clear_seconds=2), "admin"
        )
        try:
            await service.evaluate(0)
            await service.evaluate(1)
            assert not service.open_alerts
            await service.evaluate(2)
            await service.evaluate(3)
            assert len(service.open_alerts) == 2
            first = next(iter(service.open_alerts.values()))
            ack = await service.acknowledge(
                first.id, first.revision, "operator", "Bateria do wymiany"
            )
            assert ack.status == "ACKNOWLEDGED"
            with pytest.raises(Conflict):
                await service.acknowledge(first.id, first.revision, "operator", "")
            for m in state.metrics:
                m.value = 22
            await service.evaluate(4)
            await service.evaluate(10)
            assert len(service.open_alerts) == 2  # hysteresis holds up to 25
            for m in state.metrics:
                m.value = 30
            await service.evaluate(11)
            for m in state.metrics:
                m.quality = "stale"
            await service.evaluate(15)
            assert len(service.open_alerts) == 2  # unknown does not resolve
            state.metrics = []
            await service.evaluate(20)
            assert len(service.open_alerts) == 2
            # Restore persisted ACK and rules without treating startup as recovery.
            restored = AlertService(storage, EventBus(), manager)
            await storage.write("INSERT INTO metadata VALUES ('alerts_seeded','true')")
            await restored.start()
            await restored.close()
            assert (
                next(a for a in restored.open_alerts.values() if a.id == first.id).status
                == "ACKNOWLEDGED"
            )
            state.metrics = [
                Metric(device_id=device.id, key="battery.percent", channel=c, value=30)
                for c in ("1", "2")
            ]
            await restored.evaluate(30)
            await restored.evaluate(32)
            assert not restored.open_alerts
            rows = await storage.rows(
                "SELECT data FROM alert_history WHERE alert_id=?", (first.id,)
            )
            assert len(rows) == 3
            assert "Bateria do wymiany" in rows[1]["data"]
            for m in state.metrics:
                m.value = 0
            await restored.evaluate(40)
            await restored.evaluate(42)
            assert len(restored.open_alerts) == 2
            await restored.configure(rule.model_copy(update={"enabled": False}), "admin")
            assert not restored.open_alerts
        finally:
            await storage.close()

    asyncio.run(exercise())


def test_alert_transaction_rolls_back_and_offline_is_independent(tmp_path):
    async def exercise():
        storage = Storage(tmp_path / "alerts.sqlite3")
        await storage.open()
        device = Device(name="Switch", plugin_id="mock.network", category="network")
        manager = SimpleNamespace(
            devices={device.id: device}, states={device.id: DeviceState(device_id=device.id)}
        )
        service = AlertService(storage, EventBus(), manager)
        await service.configure(
            Rule(
                name="Offline",
                source="status",
                key="status",
                operator="eq",
                threshold="offline",
                hold_seconds=0,
                clear_seconds=0,
            ),
            "admin",
        )
        try:
            await storage.write(
                "CREATE TRIGGER fail_alert_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'failure'); END"
            )
            with pytest.raises(sqlite3.IntegrityError):
                await service.evaluate(0)
            assert await storage.rows("SELECT * FROM alerts") == []
            assert await storage.rows("SELECT * FROM alert_history") == []
            assert not service.open_alerts
            await storage.write("DROP TRIGGER fail_alert_outbox")
            await service.evaluate(1)
            assert len(service.open_alerts) == 1
            device.enabled = False
            await service.evaluate(2)
            assert not service.open_alerts
        finally:
            await storage.close()

    asyncio.run(exercise())


def test_alert_api_scope_roles_csrf_conflicts_and_websocket(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, bootstrap_password=PASSWORD, mock_mode=False))
    with TestClient(app) as client:
        assert client.get("/api/alerts").status_code == 401
        session = client.post(
            "/api/auth/login", json={"username": "admin", "password": PASSWORD}
        ).json()
        client.headers["X-CSRF-Token"] = session["csrf_token"]
        device = Device(name="Test receiver", plugin_id="mock.rf", category="rf")
        app.state.manager.devices[device.id] = device
        app.state.manager.states[device.id] = DeviceState(
            device_id=device.id,
            status="online",
            metrics=[Metric(device_id=device.id, key="battery.percent", value=0)],
        )
        payload = {
            "name": "Test battery",
            "key": "battery.percent",
            "threshold": 1,
            "hold_seconds": 0,
            "clear_seconds": 0,
            "device_ids": [device.id],
        }
        rule = client.post("/api/alert-rules", json=payload).json()
        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "type": "subscribe",
                    "csrf_token": session["csrf_token"],
                    "device_ids": [device.id],
                }
            )
            assert ws.receive_json()["type"] == "snapshot"
            client.portal.call(app.state.alerts.evaluate)
            client.portal.call(app.state.bus.deliver_outbox, app.state.storage)
            assert ws.receive_json()["type"] == "alert.created"
        alert = next(a for a in client.get("/api/alerts").json() if a["rule_id"] == rule["id"])
        assert client.get("/api/alerts/summary").json()["counts"]["ACTIVE"] >= 1
        for username, roles, ids in [
            ("viewer", ["viewer"], [device.id]),
            ("hidden", ["operator"], []),
            ("operator", ["operator"], [device.id]),
        ]:
            assert (
                client.post(
                    "/api/users",
                    json={
                        "username": username,
                        "password": PASSWORD,
                        "roles": roles,
                        "device_ids": ids,
                    },
                ).status_code
                == 201
            )
        viewer = client.post(
            "/api/auth/login", json={"username": "viewer", "password": PASSWORD}
        ).json()
        client.headers["X-CSRF-Token"] = viewer["csrf_token"]
        assert client.get("/api/alert-rules").status_code == 403
        assert (
            client.post(f"/api/alerts/{alert['id']}/ack", json={"revision": 1}).status_code == 403
        )
        hidden = client.post(
            "/api/auth/login", json={"username": "hidden", "password": PASSWORD}
        ).json()
        client.headers["X-CSRF-Token"] = hidden["csrf_token"]
        assert client.get("/api/alerts").json() == []
        assert client.get(f"/api/alerts/{alert['id']}/history").status_code == 404
        assert client.get("/api/alerts/summary").json()["counts"]["ACTIVE"] == 0
        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {"type": "subscribe", "csrf_token": hidden["csrf_token"], "device_ids": []}
            )
            assert ws.receive_json()["states"] == []
            client.portal.call(
                app.state.bus.publish,
                app.state.bus.event("alert.created", {"alert": alert}, device_id=device.id),
            )
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"

        op = client.post(
            "/api/auth/login", json={"username": "operator", "password": PASSWORD}
        ).json()
        client.headers["X-CSRF-Token"] = "wrong"
        assert (
            client.post(f"/api/alerts/{alert['id']}/ack", json={"revision": 1}).status_code == 403
        )
        client.headers["X-CSRF-Token"] = op["csrf_token"]
        assert (
            client.post(
                f"/api/alerts/{alert['id']}/ack", json={"revision": 1, "comment": "Sprawdzam rack"}
            ).status_code
            == 200
        )
        assert (
            client.post(f"/api/alerts/{alert['id']}/ack", json={"revision": 1}).status_code == 409
        )
        assert (
            client.get(f"/api/alerts/{alert['id']}/history").json()[-1]["comment"]
            == "Sprawdzam rack"
        )
        session = client.post(
            "/api/auth/login", json={"username": "admin", "password": PASSWORD}
        ).json()
        client.headers["X-CSRF-Token"] = session["csrf_token"]
        updated = {k: v for k, v in rule.items() if k != "id"}
        updated["enabled"] = False
        assert client.put("/api/alert-rules/" + rule["id"], json=updated).status_code == 200
        assert client.put("/api/alert-rules/" + rule["id"], json=updated).status_code == 409
        assert client.get(f"/api/alerts/{alert['id']}").json()["status"] == "RESOLVED"


def test_scalar_validation_and_retention_preserve_open_alerts(tmp_path):
    from datetime import timedelta
    from pydantic import ValidationError
    from stage_monitor.alerts.models import Alert
    from stage_monitor.contracts import now

    with pytest.raises(ValidationError):
        Rule(name="invalid", threshold=True, operator="gt")
    assert AlertService.breached(Rule(name="port", operator="eq", threshold=False), False)
    assert not AlertService.breached(Rule(name="port", operator="eq", threshold=False), 0)
    assert AlertService.breached(Rule(name="nested", operator="ne", threshold="up"), {}) is None

    async def exercise():
        storage = Storage(tmp_path / "retention.sqlite3")
        await storage.open()
        service = AlertService(storage, EventBus(), SimpleNamespace(devices={}, states={}))
        try:
            old = now() - timedelta(days=100)
            opened = Alert(
                rule_id="r",
                rule_revision=1,
                rule_name="Old",
                device_id="d",
                device_name="D",
                key="k",
                severity="warning",
                opened_at=old,
            )
            resolved = opened.model_copy(
                update={"id": "resolved", "status": "RESOLVED", "resolved_at": old}
            )
            await service.persist(opened, "created")
            await service.persist(resolved, "resolved")
            await storage.retain_alerts(90)
            rows = await storage.rows("SELECT id FROM alerts")
            assert [r["id"] for r in rows] == [opened.id]
            assert len(await storage.rows("SELECT * FROM alert_history")) == 1
        finally:
            await storage.close()

    asyncio.run(exercise())
