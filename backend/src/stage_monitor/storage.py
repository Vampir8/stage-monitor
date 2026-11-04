import asyncio
import json
from datetime import timedelta

import aiosqlite

from .contracts import CommandResult, Device, Event, Metric, new_id, now


class Storage:
    def __init__(self, path):
        self.path = path
        self.lock = asyncio.Lock()
        self.db = None
        self.sample_times: dict[tuple[str, str, str], float] = {}
        self.last_error: str | None = None

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        async with self.db.execute("PRAGMA user_version") as cursor:
            version = (await cursor.fetchone())[0]
        if version > 4:
            raise RuntimeError("Database schema is newer than this backend")
        await self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS plugin_settings (id TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
                roles TEXT NOT NULL, device_ids TEXT, enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                csrf TEXT NOT NULL, expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS commands (
                id TEXT PRIMARY KEY, device_id TEXT NOT NULL, actor_id TEXT NOT NULL,
                idempotency_key TEXT, data TEXT NOT NULL,
                UNIQUE(device_id, actor_id, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
                actor_id TEXT NOT NULL, action TEXT NOT NULL, target_id TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox (
                id TEXT PRIMARY KEY, data TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL,
                metric_key TEXT NOT NULL, channel TEXT NOT NULL, observed_at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS metrics_lookup ON metrics(device_id, metric_key, channel, observed_at);
            CREATE TABLE IF NOT EXISTS alert_rules (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, device_id TEXT NOT NULL,
                channel TEXT NOT NULL, status TEXT NOT NULL, opened_at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS alert_open_unique
                ON alerts(rule_id,device_id,channel) WHERE status != 'RESOLVED';
            CREATE INDEX IF NOT EXISTS alerts_listing ON alerts(status,opened_at);
            CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id TEXT NOT NULL REFERENCES alerts(id),
                occurred_at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_rooms (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT,
                created_by TEXT, created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS chat_channel_name ON chat_rooms(name) WHERE kind='channel';
            CREATE TABLE IF NOT EXISTS chat_members (
                room_id TEXT NOT NULL REFERENCES chat_rooms(id), user_id TEXT NOT NULL REFERENCES users(id),
                PRIMARY KEY(room_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, room_id TEXT NOT NULL REFERENCES chat_rooms(id),
                sender_id TEXT, sender_name TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL,
                client_message_id TEXT, source_id TEXT UNIQUE, device_id TEXT,
                UNIQUE(room_id,sender_id,client_message_id)
            );
            CREATE INDEX IF NOT EXISTS chat_history ON chat_messages(room_id,id);
            CREATE TABLE IF NOT EXISTS chat_reads (
                room_id TEXT NOT NULL REFERENCES chat_rooms(id), user_id TEXT NOT NULL REFERENCES users(id),
                message_id INTEGER NOT NULL, PRIMARY KEY(room_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS chat_presence (
                user_id TEXT PRIMARY KEY REFERENCES users(id), last_seen TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mapping_presets (
                id TEXT PRIMARY KEY, plugin_id TEXT NOT NULL, name TEXT NOT NULL COLLATE NOCASE,
                data TEXT NOT NULL, UNIQUE(plugin_id,name)
            );
            CREATE TABLE IF NOT EXISTS restore_plans (id TEXT PRIMARY KEY,actor_id TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS restore_jobs (id TEXT PRIMARY KEY,actor_id TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS timeline (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,
                occurred_at TEXT NOT NULL,type TEXT NOT NULL,device_id TEXT,actor_id TEXT,
                visibility TEXT NOT NULL DEFAULT 'scoped',data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS timeline_lookup ON timeline(device_id,sequence);
            CREATE INDEX IF NOT EXISTS timeline_time ON timeline(occurred_at);
            PRAGMA user_version=4;
        """)
        await self.db.commit()

    async def close(self):
        if self.db:
            await self.db.close()

    async def rows(self, sql, params=()):
        async with self.lock:
            async with self.db.execute(sql, params) as cursor:
                return await cursor.fetchall()

    async def write(self, sql, params=()):
        async with self.lock:
            try:
                await self.db.execute(sql, params)
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def devices(self):
        return [Device.model_validate_json(r["data"]) for r in await self.rows("SELECT data FROM devices")]

    async def save_device(self, device: Device, actor_id: str | None = None, previous=None):
        async with self.lock:
            try:
                await self.db.execute(
                    "INSERT INTO devices VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                    (device.id, device.model_dump_json()),
                )
                if actor_id:
                    await self._audit(actor_id, "device.configure", device.id, {
                        "previous": previous.model_dump(mode="json") if previous else None,
                        "new": device.model_dump(mode="json"),
                    })
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def delete_device(self, device_id, actor_id, previous):
        async with self.lock:
            try:
                await self.db.execute("DELETE FROM devices WHERE id=?", (device_id,))
                await self._audit(actor_id, "device.delete", device_id, previous.model_dump(mode="json"))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def _audit(self, actor_id, action, target_id, data):
        stamp = now().isoformat()
        await self.db.execute(
            "INSERT INTO audit(occurred_at,actor_id,action,target_id,data) VALUES (?,?,?,?,?)",
            (stamp, actor_id, action, target_id, json.dumps(data)),
        )
        if not action.startswith(("device.command", "alert.")):
            device_id = target_id if action.startswith("device.") else None
            new = data.get("new") or {}
            previous = data.get("previous") or {}
            label = data.get("name") or (new.get("name") if isinstance(new,dict) else None) or (previous.get("name") if isinstance(previous,dict) else None)
            await self.db.execute(
                "INSERT INTO timeline(id,occurred_at,type,device_id,actor_id,visibility,data) VALUES (?,?,?,?,?,?,?)",
                (new_id(), stamp, "audit."+action, device_id, actor_id,
                 "scoped" if device_id else "admin", json.dumps({"action":action,"target_id":target_id,"device_name":label if device_id else None})),
            )

    async def audit(self,actor_id,action,target_id,data):
        async with self.lock:
            try:
                await self._audit(actor_id,action,target_id,data)
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def save_command(self, result: CommandResult, event: Event | None = None,
                           initial=False, previous_parameters=None, desired_parameters=None):
        async with self.lock:
            try:
                await self.db.execute(
                    "INSERT INTO commands VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                    (result.command_id, result.device_id, result.actor_id,
                     result.request.idempotency_key, result.model_dump_json()),
                )
                if initial:
                    await self._audit(result.actor_id, "device.command", result.device_id,
                                      {"command": result.model_dump(mode="json"),
                                       "previous_parameters": previous_parameters,
                                       "desired_parameters": desired_parameters or {}})
                if event:
                    await self.db.execute("INSERT INTO outbox(id,data) VALUES (?,?)",
                                          (event.id, event.model_dump_json()))
                    async with self.db.execute("SELECT data FROM audit WHERE action='device.command' AND json_extract(data,'$.command.command_id')=? LIMIT 1",(result.command_id,)) as cursor:
                        previous_audit = await cursor.fetchone()
                    prior = json.loads(previous_audit[0]) if previous_audit else {}
                    desired = prior.get("desired_parameters",{})
                    completed = {**result.model_dump(mode="json"),"previous_parameters":prior.get("previous_parameters",{}),
                                 "desired_parameters":desired,
                                 "confirmed_parameters":{k:result.result.get(k,v) for k,v in desired.items()} if result.confirmed else None}
                    await self._audit(result.actor_id, "device.command.result", result.device_id,completed)
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def get_command(self, command_id):
        rows = await self.rows("SELECT data FROM commands WHERE id=?", (command_id,))
        return CommandResult.model_validate_json(rows[0]["data"]) if rows else None

    async def find_command(self, device_id, actor_id, key):
        rows = await self.rows(
            "SELECT data FROM commands WHERE device_id=? AND actor_id=? AND idempotency_key=?",
            (device_id, actor_id, key),
        )
        return CommandResult.model_validate_json(rows[0]["data"]) if rows else None

    async def recover_commands(self):
        for row in await self.rows("SELECT data FROM commands"):
            result = CommandResult.model_validate_json(row["data"])
            if result.status in {"queued", "running"}:
                result.status = "cancelled" if result.status == "queued" else "unknown"
                result.completed_at = now()
                event = Event(type="device.command.failed",source="core.recovery",device_id=result.device_id,
                              actor_id=result.actor_id,correlation_id=result.command_id,stream_id="recovery:"+new_id(),
                              payload=result.model_dump(mode="json"))
                await self.save_command(result,event)

    async def sample(self, metrics: list[Metric], default_interval, overrides):
        clock = asyncio.get_running_loop().time()
        selected = []
        for metric in metrics:
            if metric.quality != "good" or metric.value is None:
                continue
            key = (metric.device_id, metric.key, metric.channel or "")
            if clock - self.sample_times.get(key, float("-inf")) >= overrides.get(metric.key, default_interval):
                selected.append((key, metric))
        if not selected:
            return
        async with self.lock:
            try:
                await self.db.executemany(
                    "INSERT INTO metrics(device_id,metric_key,channel,observed_at,data) VALUES (?,?,?,?,?)",
                    [(m.device_id, m.key, m.channel or "", m.observed_at.isoformat(), m.model_dump_json())
                     for _, m in selected],
                )
                await self.db.commit()
                for key, _ in selected:
                    self.sample_times[key] = clock
            except BaseException:
                await self.db.rollback()
                raise

    async def retain(self, days):
        cutoff = (now() - timedelta(days=days)).isoformat()
        await self.write("DELETE FROM metrics WHERE observed_at < ?", (cutoff,))
        await self.write("DELETE FROM sessions WHERE expires_at < ?", (now().isoformat(),))
        await self.write("DELETE FROM outbox WHERE delivered=1")

    async def retain_alerts(self, days):
        cutoff = (now() - timedelta(days=days)).isoformat()
        # Preserve open incidents regardless of age. Resolved history is removed atomically.
        condition = "status='RESOLVED' AND json_extract(data,'$.resolved_at') < ?"
        async with self.lock:
            try:
                await self.db.execute("DELETE FROM alert_history WHERE alert_id IN (SELECT id FROM alerts WHERE " + condition + ")", (cutoff,))
                await self.db.execute("DELETE FROM alerts WHERE " + condition, (cutoff,))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def retain_chat(self, days):
        cutoff = (now() - timedelta(days=days)).isoformat()
        await self.write('DELETE FROM chat_messages WHERE created_at < ?', (cutoff,))

    async def history(self, device_id, metric, channel, since, until, cursor, limit):
        sql = "SELECT id,data FROM metrics WHERE device_id=? AND metric_key=? AND id>?"
        params = [device_id, metric, cursor]
        if channel is not None:
            sql += " AND channel=?"
            params.append(channel)
        if since:
            sql += " AND observed_at>=?"
            params.append(since.isoformat())
        if until:
            sql += " AND observed_at<=?"
            params.append(until.isoformat())
        sql += " ORDER BY id LIMIT ?"
        params.append(limit + 1)
        rows = await self.rows(sql, params)
        page = rows[:limit]
        return {
            "items": [json.loads(r["data"]) for r in page],
            "next_cursor": page[-1]["id"] if len(rows) > limit else None,
        }
