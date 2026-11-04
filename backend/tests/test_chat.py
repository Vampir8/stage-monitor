import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from stage_monitor.alerts.models import Alert
from stage_monitor.contracts import now
from stage_monitor.main import create_app
from stage_monitor.settings import Settings

PASSWORD = "chat-test-password-2026"


def login(client, username):
    data = client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).json()
    return data["user"], {
        "Cookie": "stage_session=" + client.cookies.get("stage_session"),
        "X-CSRF-Token": data["csrf_token"],
    }


@pytest.fixture
def chat_app(tmp_path):
    settings = Settings(data_dir=tmp_path, bootstrap_password=PASSWORD, mock_mode=False)
    app = create_app(settings)
    with TestClient(app) as client:
        admin, auth = login(client, "admin")
        users = {}
        for name, roles, devices in [
            ("alice", ["viewer"], ["visible-device"]),
            ("bob", ["operator"], []),
        ]:
            response = client.post(
                "/api/users",
                headers=auth,
                json={
                    "username": name,
                    "password": PASSWORD,
                    "roles": roles,
                    "device_ids": devices,
                },
            )
            assert response.status_code == 201
            users[name] = response.json()
        alice, alice_auth = login(client, "alice")
        bob, bob_auth = login(client, "bob")
        yield app, client, settings, (admin, auth), (alice, alice_auth), (bob, bob_auth)


def post_message(client, room, auth, text, key):
    return client.post(
        f"/api/chat/rooms/{room}/messages",
        headers=auth,
        json={"text": text, "client_message_id": key},
    )


def test_channels_dm_privacy_idempotency_history_and_restart(chat_app):
    app, client, settings, (_, admin), (alice, alice_auth), (bob, bob_auth) = chat_app
    assert client.get("/api/chat/rooms", headers={"Cookie": ""}).status_code == 401
    assert len(client.get("/api/chat/rooms", headers=alice_auth).json()) == 4
    assert (
        client.post("/api/chat/rooms", headers=alice_auth, json={"name": "crew"}).status_code == 403
    )
    assert (
        client.post("/api/chat/rooms", headers=bob_auth, json={"name": "crew"}).status_code == 201
    )
    assert (
        client.post("/api/chat/rooms", headers=bob_auth, json={"name": "CREW"}).status_code == 409
    )
    assert post_message(client, "channel.alerts", bob_auth, "manual", "readonly").status_code == 403
    direct = client.post(
        "/api/chat/rooms", headers=alice_auth, json={"kind": "direct", "other_user_id": bob["id"]}
    ).json()["id"]
    assert (
        client.post(
            "/api/chat/rooms",
            headers=bob_auth,
            json={"kind": "direct", "other_user_id": alice["id"]},
        ).json()["id"]
        == direct
    )
    assert direct not in [r["id"] for r in client.get("/api/chat/rooms", headers=admin).json()]
    for path in ("messages", "read", "typing"):
        response = (
            client.get(f"/api/chat/rooms/{direct}/{path}", headers=admin)
            if path == "messages"
            else client.post(
                f"/api/chat/rooms/{direct}/{path}",
                headers=admin,
                json={"message_id": 1} if path == "read" else {"typing": True},
            )
        )
        assert response.status_code == 404
    first = post_message(client, direct, alice_auth, "<script>alert(1)</script>", "one").json()
    again = post_message(client, direct, alice_auth, "<script>alert(1)</script>", "one").json()
    assert again["id"] == first["id"]
    assert post_message(client, direct, alice_auth, "changed", "one").status_code == 409
    second = post_message(client, direct, alice_auth, "Stage ready", "two").json()
    rooms = client.get("/api/chat/rooms", headers=bob_auth).json()
    assert next(r for r in rooms if r["id"] == direct)["unread"] == 2
    assert (
        next(
            r for r in client.get("/api/chat/rooms", headers=alice_auth).json() if r["id"] == direct
        )["unread"]
        == 0
    )
    page = client.get(f"/api/chat/rooms/{direct}/messages?limit=1", headers=bob_auth).json()
    assert page["has_more"] and page["items"][0]["id"] == second["id"]
    previous = client.get(
        f"/api/chat/rooms/{direct}/messages?before={second['id']}", headers=bob_auth
    ).json()
    assert [m["id"] for m in previous["items"]] == [first["id"]]
    assert (
        client.post(
            f"/api/chat/rooms/{direct}/read", headers=bob_auth, json={"message_id": second["id"]}
        ).status_code
        == 200
    )
    client.post(
        f"/api/chat/rooms/{direct}/read", headers=bob_auth, json={"message_id": first["id"]}
    )
    assert (
        next(
            r for r in client.get("/api/chat/rooms", headers=bob_auth).json() if r["id"] == direct
        )["unread"]
        == 0
    )


def test_direct_room_messages_and_reads_survive_restart(tmp_path):
    settings = Settings(data_dir=tmp_path, bootstrap_password=PASSWORD, mock_mode=False)
    with TestClient(create_app(settings)) as client:
        _, admin = login(client, "admin")
        bob = client.post(
            "/api/users", headers=admin, json={"username": "bob", "password": PASSWORD}
        ).json()
        direct = client.post(
            "/api/chat/rooms", headers=admin, json={"kind": "direct", "other_user_id": bob["id"]}
        ).json()["id"]
        saved = post_message(client, direct, admin, "Check stage", "restart").json()
        _, auth = login(client, "bob")
        client.post(
            f"/api/chat/rooms/{direct}/read", headers=auth, json={"message_id": saved["id"]}
        )
    with TestClient(create_app(settings)) as client:
        _, auth = login(client, "bob")
        room = next(
            r for r in client.get("/api/chat/rooms", headers=auth).json() if r["id"] == direct
        )
        assert room["unread"] == 0
        assert (
            client.get(f"/api/chat/rooms/{direct}/messages", headers=auth).json()["items"][0][
                "text"
            ]
            == "Check stage"
        )


def test_websocket_private_messages_typing_and_multiple_presence_connections(chat_app):
    app, client, _, (_, admin), (alice, alice_auth), (bob, bob_auth) = chat_app
    direct = client.post(
        "/api/chat/rooms", headers=alice_auth, json={"kind": "direct", "other_user_id": bob["id"]}
    ).json()["id"]
    with client.websocket_connect("/ws", headers=alice_auth) as alice_ws:
        alice_ws.send_json(
            {"type": "subscribe", "csrf_token": alice_auth["X-CSRF-Token"], "device_ids": []}
        )
        assert alice_ws.receive_json()["type"] == "snapshot"
        with client.websocket_connect("/ws", headers=admin) as admin_ws:
            admin_ws.send_json(
                {"type": "subscribe", "csrf_token": admin["X-CSRF-Token"], "device_ids": []}
            )
            assert admin_ws.receive_json()["type"] == "snapshot"
            assert alice_ws.receive_json()["type"] == "chat.presence"
            post_message(client, direct, alice_auth, "Private stage note", "private-ws")
            public = post_message(
                client, "channel.general", bob_auth, "Public marker", "public-ws"
            ).json()
            client.portal.call(app.state.bus.deliver_outbox, app.state.storage)
            # FIFO public marker flushes the stream: a leaked DM would appear first even for admin.
            event = admin_ws.receive_json()
            assert (
                event["type"] == "chat.message"
                and event["payload"]["message"]["id"] == public["id"]
            )
            assert alice_ws.receive_json()["payload"]["message"]["text"] == "Private stage note"
            assert alice_ws.receive_json()["payload"]["message"]["id"] == public["id"]
            client.post(
                f"/api/chat/rooms/{direct}/typing", headers=alice_auth, json={"typing": True}
            )
            client.post(
                "/api/chat/rooms/channel.general/typing", headers=bob_auth, json={"typing": True}
            )
            assert admin_ws.receive_json()["payload"]["room_id"] == "channel.general"
            assert alice_ws.receive_json()["payload"]["room_id"] == direct
            assert alice_ws.receive_json()["payload"]["room_id"] == "channel.general"
        with client.websocket_connect("/ws", headers=alice_auth) as second:
            second.send_json(
                {"type": "subscribe", "csrf_token": alice_auth["X-CSRF-Token"], "device_ids": []}
            )
            assert second.receive_json()["type"] == "snapshot"
        assert next(
            u for u in client.get("/api/chat/users", headers=admin).json() if u["id"] == alice["id"]
        )["online"]
    roster = client.get("/api/chat/users", headers=admin).json()
    user = next(u for u in roster if u["id"] == alice["id"])
    assert not user["online"] and user["last_seen"]


def test_alert_bridge_replay_scope_atomicity_and_retention(chat_app):
    app, client, _, (_, admin), (alice, alice_auth), (_, bob_auth) = chat_app
    assert app.state.chat.task is None  # Legacy history remains readable; no automatic duplication.
    alert = Alert(
        rule_id="test-rule",
        rule_revision=1,
        rule_name="Power alarm",
        device_id="visible-device",
        device_name="Rack power",
        key="power.voltage",
        severity="critical",
    )
    hidden = alert.model_copy(
        update={"id": "hidden-alert", "device_id": "hidden-device", "device_name": "Private rack"}
    )
    client.portal.call(app.state.alerts.persist, alert, "created")
    client.portal.call(app.state.alerts.persist, hidden, "created")
    client.portal.call(app.state.bus.deliver_outbox, app.state.storage)
    client.portal.call(app.state.chat.project_alerts)
    client.portal.call(app.state.chat.project_alerts)
    assert (
        len(client.get("/api/chat/rooms/channel.alerts/messages", headers=admin).json()["items"])
        == 2
    )
    assert (
        len(
            client.get("/api/chat/rooms/channel.alerts/messages", headers=alice_auth).json()[
                "items"
            ]
        )
        == 1
    )
    assert (
        client.get("/api/chat/rooms/channel.alerts/messages", headers=bob_auth).json()["items"]
        == []
    )
    assert (
        next(
            r
            for r in client.get("/api/chat/rooms", headers=bob_auth).json()
            if r["id"] == "channel.alerts"
        )["unread"]
        == 0
    )
    message_id = client.get("/api/chat/rooms/channel.alerts/messages", headers=admin).json()[
        "items"
    ][-1]["id"]
    assert (
        client.post(
            "/api/chat/rooms/channel.alerts/read", headers=bob_auth, json={"message_id": message_id}
        ).status_code
        == 404
    )
    with client.websocket_connect("/ws", headers=bob_auth) as ws:
        ws.send_json(
            {"type": "subscribe", "csrf_token": bob_auth["X-CSRF-Token"], "device_ids": []}
        )
        assert ws.receive_json()["type"] == "snapshot"
        # A visible public marker must arrive ahead of neither restricted system message.
        saved_public = post_message(
            client, "channel.general", alice_auth, "Scope marker", "scope-marker"
        ).json()
        client.portal.call(app.state.bus.deliver_outbox, app.state.storage)
        assert ws.receive_json()["payload"]["message"]["id"] == saved_public["id"]
    client.portal.call(
        app.state.storage.write, "DELETE FROM chat_messages WHERE room_id='channel.general'"
    )
    client.portal.call(
        app.state.storage.write,
        "CREATE TRIGGER fail_chat_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'failure'); END",
    )
    with pytest.raises(sqlite3.IntegrityError):
        post_message(client, "channel.general", alice_auth, "Must rollback", "atomic")
    assert (
        client.get("/api/chat/rooms/channel.general/messages", headers=admin).json()["items"] == []
    )
    client.portal.call(app.state.storage.write, "DROP TRIGGER fail_chat_outbox")
    saved = post_message(client, "channel.general", alice_auth, "Persisted", "atomic").json()
    old = (now() - timedelta(days=100)).isoformat()
    client.portal.call(
        app.state.storage.write,
        "UPDATE chat_messages SET created_at=? WHERE id=?",
        (old, saved["id"]),
    )
    client.portal.call(app.state.storage.retain_chat, 90)
    assert (
        client.get("/api/chat/rooms/channel.general/messages", headers=admin).json()["items"] == []
    )


def test_chat_rate_limit_csrf_and_no_device_command_from_text(chat_app):
    app, client, _, (_, admin), (_, alice_auth), _ = chat_app
    wrong = {**alice_auth, "X-CSRF-Token": "wrong"}
    assert post_message(client, "channel.general", wrong, "Denied", "bad-csrf").status_code == 403
    assert post_message(client, "channel.general", alice_auth, "   ", "blank").status_code == 422
    for i in range(20):
        assert (
            post_message(
                client, "channel.general", alice_auth, "/mute receiver", str(i)
            ).status_code
            == 201
        )
    assert (
        post_message(client, "channel.general", alice_auth, "Over limit", "over").status_code == 429
    )
    assert client.portal.call(app.state.storage.rows, "SELECT * FROM commands") == []
    assert client.get("/api/chat/users", headers=admin).json()[0].keys() == {
        "id",
        "username",
        "roles",
        "online",
        "last_seen",
    }
