import asyncio
import json
import logging
from collections import defaultdict, deque
from contextlib import suppress
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException

from ..auth import can_access
from ..contracts import new_id, now

log = logging.getLogger(__name__)


class ChatService:
    def __init__(self, storage, bus):
        self.storage, self.bus = storage, bus
        self.rooms = {}
        self.members = defaultdict(set)
        self.connections = defaultdict(set)
        self.last_seen = {}
        self.typing = {}
        self.typing_updates = {}
        self.rate = defaultdict(deque)
        self.lock = asyncio.Lock()
        self.task = None
        self.last_error = None

    async def start(self):
        for name in ("general", "audio", "network", "alerts"):
            await self.storage.write(
                "INSERT OR IGNORE INTO chat_rooms VALUES (?,?,?,?,?)",
                ("channel." + name, "channel", name, "system", now().isoformat()),
            )
        self.rooms = {r["id"]: dict(r) for r in await self.storage.rows("SELECT * FROM chat_rooms")}
        for row in await self.storage.rows("SELECT * FROM chat_members"):
            self.members[row["room_id"]].add(row["user_id"])
        # Keep the legacy channel/history for rollback; new alarms use the alert UI
        # and optional transient notifications instead of a second persisted history.

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    def room_visible(self, room_id, user):
        room = self.rooms.get(room_id)
        return bool(room and (room["kind"] == "channel" or user["id"] in self.members[room_id]))

    def require_room(self, room_id, user):
        if not self.room_visible(room_id, user):
            raise HTTPException(404, "Room not found")
        return self.rooms[room_id]

    def event_visible(self, event, user):
        if event.type == "chat.presence":
            return event.payload.get("user_id") != user["id"]
        if event.type == "chat.read":
            return event.payload.get("user_id") == user["id"]
        if event.type == "chat.room":
            return self.room_visible(event.payload.get("room_id"), user)
        if not self.room_visible(event.payload.get("room_id"), user):
            return False
        message = event.payload.get("message", {})
        return not message.get("device_id") or can_access(user, message["device_id"])

    @staticmethod
    def message_scope(user):
        if "admin" in user["roles"] or user["device_ids"] is None:
            return "1=1", []
        ids = user["device_ids"]
        return (
            ("(device_id IS NULL OR device_id IN (" + ",".join("?" for _ in ids) + "))", list(ids))
            if ids
            else ("device_id IS NULL", [])
        )

    async def create_room(self, data, user):
        async with self.lock:
            if data.kind == "channel":
                if not set(user["roles"]) & {"operator", "audio", "video", "network", "admin"}:
                    raise HTTPException(403, "Operator role required to create channels")
                name = data.name.strip().lower()
                if not name:
                    raise HTTPException(422, "Channel name cannot be blank")
                if any(r["kind"] == "channel" and r["name"] == name for r in self.rooms.values()):
                    raise HTTPException(409, "Channel already exists")
                room_id, participants = new_id(), []
            else:
                if data.other_user_id == user["id"]:
                    raise HTTPException(422, "Choose another user")
                rows = await self.storage.rows(
                    "SELECT id FROM users WHERE id=? AND enabled=1", (data.other_user_id,)
                )
                if not rows:
                    raise HTTPException(404, "Recipient not found")
                participants = sorted([user["id"], data.other_user_id])
                room_id = str(uuid5(NAMESPACE_URL, "stage-monitor-chat:" + ":".join(participants)))
                if room_id in self.rooms:
                    return self.rooms[room_id]
                name = None
            room = dict(
                id=room_id,
                kind=data.kind,
                name=name,
                created_by=user["id"],
                created_at=now().isoformat(),
            )
            async with self.storage.lock:
                try:
                    await self.storage.db.execute(
                        "INSERT INTO chat_rooms VALUES (?,?,?,?,?)", tuple(room.values())
                    )
                    await self.storage.db.executemany(
                        "INSERT INTO chat_members VALUES (?,?)",
                        [(room_id, uid) for uid in participants],
                    )
                    await self.storage._audit(
                        user["id"], "chat.room.create", room_id, {"kind": data.kind}
                    )
                    await self.storage.db.commit()
                except BaseException:
                    await self.storage.db.rollback()
                    raise
            self.rooms[room_id] = room
            self.members[room_id].update(participants)
            self.bus.publish(self.bus.event("chat.room", {"room_id": room_id}))
            return room

    async def messages(self, room_id, user, before=None, after=None, limit=50):
        self.require_room(room_id, user)
        scope, params = self.message_scope(user)
        sql = "SELECT * FROM chat_messages WHERE room_id=? AND " + scope
        params = [room_id, *params]
        if before:
            sql += " AND id<?"
            params.append(before)
        if after is not None:
            sql += " AND id>?"
            params.append(after)
        sql += " ORDER BY id " + ("ASC" if after is not None else "DESC") + " LIMIT ?"
        rows = await self.storage.rows(sql, (*params, limit + 1))
        page = [dict(r) for r in rows[:limit]]
        if after is None:
            page.reverse()
        return {"items": page, "has_more": len(rows) > limit}

    async def list_rooms(self, user):
        scope, args = self.message_scope(user)
        results = []
        for room in list(self.rooms.values()):
            if not self.room_visible(room["id"], user):
                continue
            counts = await self.storage.rows(
                "SELECT COUNT(*) AS count FROM chat_messages WHERE room_id=? AND "
                + scope
                + " AND (sender_id IS NULL OR sender_id!=?) AND id>COALESCE((SELECT message_id FROM chat_reads WHERE room_id=? AND user_id=?),0)",
                (room["id"], *args, user["id"], room["id"], user["id"]),
            )
            latest = (await self.messages(room["id"], user, limit=1))["items"]
            results.append(
                {
                    **room,
                    "members": sorted(self.members[room["id"]]),
                    "unread": counts[0]["count"],
                    "last_message": latest[-1] if latest else None,
                    "read_only": room["id"] == "channel.alerts",
                }
            )
        return results

    async def send(self, room_id, data, user):
        self.require_room(room_id, user)
        if room_id == "channel.alerts":
            raise HTTPException(403, "System alerts channel is read-only")
        async with self.lock:
            existing = await self.storage.rows(
                "SELECT * FROM chat_messages WHERE room_id=? AND sender_id=? AND client_message_id=?",
                (room_id, user["id"], data.client_message_id),
            )
            if existing:
                if existing[0]["text"] != data.text.strip():
                    raise HTTPException(409, "Message ID already used for different content")
                return dict(existing[0])
            clock = asyncio.get_running_loop().time()
            samples = self.rate[user["id"]]
            while samples and samples[0] < clock - 10:
                samples.popleft()
            if len(samples) >= 20:
                raise HTTPException(429, "Chat rate limit exceeded")
            samples.append(clock)
            return await self.store_message(
                room_id, data.text.strip(), user["id"], user["username"], data.client_message_id
            )

    async def store_message(
        self,
        room_id,
        text,
        sender=None,
        name="System",
        client_id=None,
        source_id=None,
        device_id=None,
        bridge_cursor=None,
        created_at=None,
    ):
        async with self.storage.lock:
            try:
                cursor = await self.storage.db.execute(
                    "INSERT INTO chat_messages(room_id,sender_id,sender_name,text,created_at,client_message_id,source_id,device_id) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        room_id,
                        sender,
                        name,
                        text,
                        created_at or now().isoformat(),
                        client_id,
                        source_id,
                        device_id,
                    ),
                )
                async with self.storage.db.execute(
                    "SELECT * FROM chat_messages WHERE id=?", (cursor.lastrowid,)
                ) as query:
                    message = dict(await query.fetchone())
                event = self.bus.event(
                    "chat.message", {"room_id": room_id, "message": message}, actor_id=sender
                )
                await self.storage.db.execute(
                    "INSERT INTO outbox(id,data) VALUES (?,?)", (event.id, event.model_dump_json())
                )
                if bridge_cursor is not None:
                    await self.storage.db.execute(
                        "INSERT INTO metadata VALUES ('chat_alert_cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (str(bridge_cursor),),
                    )
                await self.storage.db.commit()
                return message
            except BaseException:
                await self.storage.db.rollback()
                raise

    async def mark_read(self, room_id, message_id, user):
        self.require_room(room_id, user)
        scope, args = self.message_scope(user)
        rows = await self.storage.rows(
            "SELECT id FROM chat_messages WHERE room_id=? AND id=? AND " + scope,
            (room_id, message_id, *args),
        )
        if not rows:
            raise HTTPException(404, "Message not found")
        await self.storage.write(
            "INSERT INTO chat_reads VALUES (?,?,?) ON CONFLICT(room_id,user_id) DO UPDATE SET message_id=MAX(message_id,excluded.message_id)",
            (room_id, user["id"], message_id),
        )
        self.bus.publish(self.bus.event("chat.read", {"room_id": room_id, "user_id": user["id"]}))

    def set_typing(self, room_id, user, active):
        self.require_room(room_id, user)
        if room_id == "channel.alerts":
            raise HTTPException(403, "System alerts channel is read-only")
        key, clock = (room_id, user["id"]), asyncio.get_running_loop().time()
        previous = self.typing.get(key, 0)
        if not active and previous <= clock:
            self.typing.pop(key, None)
            return
        if active and clock - self.typing_updates.get(user["id"], -100) < 1:
            return
        if active:
            self.typing_updates[user["id"]] = clock
        self.typing[key] = clock + 5 if active else 0
        self.bus.publish(
            self.bus.event(
                "chat.typing",
                {
                    "room_id": room_id,
                    "user_id": user["id"],
                    "username": user["username"],
                    "typing": active,
                    "expires_at": (now().timestamp() + 5) if active else 0,
                },
            )
        )

    async def connect(self, user, connection_id):
        was_online = bool(self.connections[user["id"]])
        self.connections[user["id"]].add(connection_id)
        if not was_online:
            self.bus.publish(
                self.bus.event("chat.presence", {"user_id": user["id"], "online": True})
            )

    async def disconnect(self, user_id, connection_id):
        self.connections[user_id].discard(connection_id)
        if not self.connections[user_id]:
            self.last_seen[user_id] = now().isoformat()
            for key in list(self.typing):
                if key[1] == user_id:
                    self.typing.pop(key)
                    self.bus.publish(
                        self.bus.event(
                            "chat.typing",
                            {
                                "room_id": key[0],
                                "user_id": user_id,
                                "typing": False,
                                "expires_at": 0,
                            },
                        )
                    )
            self.bus.publish(self.bus.event("chat.presence", {"user_id": user_id, "online": False}))
            await self.storage.write(
                "INSERT INTO chat_presence VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen",
                (user_id, self.last_seen[user_id]),
            )

    async def users(self):
        rows = await self.storage.rows(
            "SELECT u.id,u.username,u.roles,p.last_seen FROM users u LEFT JOIN chat_presence p ON p.user_id=u.id WHERE u.enabled=1 ORDER BY u.username"
        )
        return [
            {
                "id": r["id"],
                "username": r["username"],
                "roles": json.loads(r["roles"]),
                "online": bool(self.connections[r["id"]]),
                "last_seen": self.last_seen.get(r["id"], r["last_seen"]),
            }
            for r in rows
        ]

    async def project_alerts(self):
        async with self.lock:
            await self._project_alerts()

    async def _project_alerts(self):
        cursor = await self.storage.rows("SELECT value FROM metadata WHERE key='chat_alert_cursor'")
        last = int(cursor[0]["value"]) if cursor else 0
        rows = await self.storage.rows(
            "SELECT h.id,h.occurred_at,h.data AS history,a.data AS alert FROM alert_history h JOIN alerts a ON a.id=h.alert_id WHERE h.id>? ORDER BY h.id LIMIT 100",
            (last,),
        )
        labels = {"created": "ALARM", "acknowledged": 'ACKNOWLEDGED', "resolved": 'RESOLVED'}
        for row in rows:
            history, alert = json.loads(row["history"]), json.loads(row["alert"])
            text = f"{labels.get(history['action'], history['action'])}: {alert['device_name']} — {alert['rule_name']}"
            if alert.get("channel"):
                text += f" · channel {alert['channel']}"
            # Device scope is checked on every read and WS send, never copied into a public channel unrestricted.
            await self.store_message(
                "channel.alerts",
                text,
                source_id="alert-history:" + str(row["id"]),
                device_id=alert["device_id"],
                bridge_cursor=row["id"],
                created_at=row["occurred_at"],
            )

    async def bridge(self):
        subscriber = self.bus.subscribe(lambda event: event.type.startswith("alert."))
        try:
            while True:
                try:
                    await self.project_alerts()
                    self.last_error = None
                except Exception:
                    self.last_error = "Chat alert bridge failed"
                    log.exception("Chat alert bridge failed; retrying")
                if subscriber.overflow.is_set():
                    self.bus.unsubscribe(subscriber)
                    subscriber = self.bus.subscribe(lambda event: event.type.startswith("alert."))
                with suppress(TimeoutError):
                    await asyncio.wait_for(subscriber.queue.get(), 1)
        finally:
            self.bus.unsubscribe(subscriber)
