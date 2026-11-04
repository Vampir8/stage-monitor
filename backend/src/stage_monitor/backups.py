"""Reviewed configuration recovery applied atomically before any device workers start."""

import hashlib
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Literal

import aiosqlite
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import AwareDatetime, Field, field_validator
from jsonschema import ValidationError as SchemaError
from starlette.background import BackgroundTask

from .alerts.models import Rule
from .auth import ROLES, public_user
from .contracts import Command, Device, DeviceCreate, Model, JsonValue, new_id, now
from .plugin_manager import PluginManager
from .shows import Show
from .topology import validate_graph
from .weather.models import WeatherConfig

MAX_BYTES = 5 * 1024 * 1024


class Account(Model):
    id: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    password_hash: str = Field(pattern=r"^scrypt\$[0-9a-f]{32}\$[0-9a-f]{128}$")
    roles: list[str] = Field(min_length=1, max_length=6)
    device_ids: list[str] | None = None
    enabled: bool = True

    @field_validator("roles")
    @classmethod
    def known_roles(cls, value):
        if not set(value) <= ROLES:
            raise ValueError("Unknown role")
        return value


class PluginSetting(Model):
    plugin_id: str
    version: str
    enabled: bool


class SavedEntry(Model):
    device_id: str = Field(min_length=1, max_length=120)
    device_name: str = Field(min_length=1, max_length=120)
    plugin_id: str
    plugin_version: str
    config_revision: int = Field(ge=1)
    configuration: DeviceCreate | None = None
    parameters: dict[str, JsonValue] | None = None
    commands: list[Command] | None = None


class SavedSnapshot(Model):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["parameters", "configuration"]
    created_at: AwareDatetime
    created_by: str = Field(min_length=1, max_length=120)
    entries: list[SavedEntry] = Field(min_length=1, max_length=100)


class Bundle(Model):
    format: Literal["stage-monitor-config"] = "stage-monitor-config"
    version: Literal[1] = 1
    exported_at: AwareDatetime
    devices: list[Device] = Field(max_length=1000)
    plugins: list[PluginSetting] = Field(max_length=100)
    rules: list[Rule] = Field(max_length=1000)
    shows: list[Show] = Field(max_length=100)
    active_show_id: str | None = None
    weather: WeatherConfig
    snapshots: list[dict] = Field(max_length=2000)
    users: list[Account] = Field(max_length=1000)


def fingerprint(bundle):
    data = bundle.model_dump(mode="json", exclude={"exported_at"})
    for key in ["devices", "rules", "shows", "snapshots", "users"]:
        data[key] = sorted(data[key], key=lambda row: row["id"])
    data["plugins"] = sorted(data["plugins"], key=lambda row: row["plugin_id"])
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def export_locked(storage, plugins):
    async def rows(sql):
        async with storage.db.execute(sql) as cursor:
            return await cursor.fetchall()

    meta = {
        row["key"]: json.loads(row["value"])
        for row in await rows(
            "SELECT * FROM metadata WHERE key IN ('weather_config','show_profiles')"
        )
    }
    shows = meta.get("show_profiles", {"profiles": [], "active_id": None})
    return Bundle(
        exported_at=now(),
        devices=[
            Device.model_validate_json(r["data"]) for r in await rows("SELECT * FROM devices")
        ],
        plugins=[
            PluginSetting(
                plugin_id=key, version=entry["descriptor"].plugin_version, enabled=entry["enabled"]
            )
            for key, entry in plugins.entries.items()
            if entry["descriptor"] and not entry["error"]
        ],
        rules=[
            Rule.model_validate_json(r["data"]) for r in await rows("SELECT * FROM alert_rules")
        ],
        shows=[Show.model_validate(p) for p in shows["profiles"]],
        active_show_id=shows["active_id"],
        weather=WeatherConfig.model_validate(meta.get("weather_config", {})),
        snapshots=[json.loads(r["data"]) for r in await rows("SELECT data FROM snapshots")],
        users=[
            Account(**public_user(r), password_hash=r["password_hash"])
            for r in await rows("SELECT * FROM users")
        ],
    )


def validate_bundle(bundle, plugins):
    for key in ["devices", "rules", "shows", "snapshots", "users"]:
        ids = [row["id"] if isinstance(row, dict) else row.id for row in getattr(bundle, key)]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate IDs in " + key)
    if len({u.username for u in bundle.users}) != len(bundle.users):
        raise ValueError("Duplicate usernames")
    devices = {d.id: d for d in bundle.devices}
    validate_graph(devices)
    for d in devices.values():
        plugins.validate_device(d)
    ids = set(devices)
    if bundle.active_show_id is not None and bundle.active_show_id not in {
        s.id for s in bundle.shows
    }:
        raise ValueError("Active show is missing")
    if len({p.plugin_id for p in bundle.plugins}) != len(bundle.plugins):
        raise ValueError("Duplicate plugin settings")
    for p in bundle.plugins:
        entry = plugins.get(p.plugin_id)
        if p.version != entry["descriptor"].plugin_version:
            raise ValueError("Plugin version changed: " + p.plugin_id)
    for saved in bundle.snapshots:
        parsed = SavedSnapshot.model_validate(saved)
        if len({entry.device_id for entry in parsed.entries}) != len(parsed.entries):
            raise ValueError("Duplicate snapshot devices")
        required = {"id", "name", "kind", "created_at", "created_by", "entries"}
        if set(saved) != required or saved["kind"] not in {"parameters", "configuration"}:
            raise ValueError("Invalid snapshot")
        if (
            not isinstance(saved["id"], str)
            or not 1 <= len(saved["id"]) <= 120
            or not isinstance(saved["name"], str)
            or not 1 <= len(saved["name"]) <= 120
        ):
            raise ValueError("Invalid snapshot identity")
        if not isinstance(saved["entries"], list) or not 1 <= len(saved["entries"]) <= 100:
            raise ValueError("Invalid snapshot entries")
        for entry in saved["entries"]:
            for key in ["device_id", "device_name", "plugin_id", "plugin_version"]:
                if not isinstance(entry.get(key), str):
                    raise ValueError("Invalid snapshot device")
            if saved["kind"] == "configuration":
                DeviceCreate.model_validate(entry["configuration"])
            else:
                if not isinstance(entry.get("parameters"), dict):
                    raise ValueError("Invalid snapshot parameters")
                for command in entry["commands"]:
                    Command.model_validate(command)


class BackupService:
    def __init__(self, storage, plugins, app=None):
        self.storage, self.plugins, self.app = storage, plugins, app
        self.pending = False
        self.plans = {}

    async def export(self):
        async with self.storage.lock:
            return await export_locked(self.storage, self.plugins)

    async def preview(self, bundle, user):
        validate_bundle(bundle, self.plugins)
        current = await self.export()
        changes = {}
        for key in ["devices", "rules", "shows", "snapshots"]:
            before = {
                row.id if hasattr(row, "id") else row["id"]: row.model_dump(mode="json")
                if hasattr(row, "model_dump")
                else row
                for row in getattr(current, key)
            }
            after = {
                row.id if hasattr(row, "id") else row["id"]: row.model_dump(mode="json")
                if hasattr(row, "model_dump")
                else row
                for row in getattr(bundle, key)
            }
            changes[key] = {
                "add": [
                    {"id": i, "name": after[i].get("name", i)} for i in after.keys() - before.keys()
                ],
                "remove": [
                    {"id": i, "name": before[i].get("name", i)}
                    for i in before.keys() - after.keys()
                ],
                "update": [
                    {"id": i, "name": after[i].get("name", i)}
                    for i in before.keys() & after.keys()
                    if before[i] != after[i]
                ],
            }
        plan = {
            "id": new_id(),
            "owner": user["id"],
            "expires_at": (now() + timedelta(minutes=5)).isoformat(),
            "baseline": fingerprint(current),
            "bundle": bundle.model_dump(mode="json"),
            "changes": changes,
            "users": [
                {
                    "username": u.username,
                    "roles": u.roles,
                    "enabled": u.enabled,
                    "preserve_current_admin": u.username == user["username"],
                }
                for u in bundle.users
            ],
            "weather_changed": current.weather != bundle.weather,
            "plugins": [p.model_dump() for p in bundle.plugins],
            "active_show_id": bundle.active_show_id,
        }
        while len(self.plans) >= 5:
            self.plans.pop(next(iter(self.plans)))
        self.plans[plan["id"]] = plan
        return {k: v for k, v in plan.items() if k not in {"bundle", "baseline", "owner"}}

    async def stage(self, plan_id, user):
        plan = self.plans.get(plan_id)
        if not plan or plan["owner"] != user["id"] or now().isoformat() > plan["expires_at"]:
            raise HTTPException(409, "Restore preview expired")
        if self.app.state.presets.tasks or self.app.state.discovery.tasks:
            raise HTTPException(409, "Finish active restore or discovery jobs first")
        async with self.storage.lock:
            current = await export_locked(self.storage, self.plugins)
            if fingerprint(current) != plan["baseline"] or self.pending:
                raise HTTPException(409, "Configuration changed or restore already scheduled")
            pending = {"plan": plan, "status": "pending", "scheduled_at": now().isoformat()}
            try:
                await self.storage.db.execute(
                    "INSERT INTO metadata VALUES ('backup.pending',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps(pending),),
                )
                await self.storage._audit(
                    user["id"], "backup.schedule", plan_id, {"changes": plan["changes"]}
                )
                await self.storage.db.commit()
                self.pending = True
            except BaseException:
                await self.storage.db.rollback()
                raise
        return {"status": "pending", "restart_required": True}

    async def cancel(self, user):
        async with self.storage.lock:
            try:
                await self.storage.db.execute("DELETE FROM metadata WHERE key='backup.pending'")
                await self.storage._audit(user["id"], "backup.cancel", "system", {})
                await self.storage.db.commit()
                self.pending = False
            except BaseException:
                await self.storage.db.rollback()
                raise
        return {"status": "cancelled"}


async def apply_pending(storage, settings):
    rows = await storage.rows("SELECT value FROM metadata WHERE key='backup.pending'")
    if not rows:
        return
    pending = json.loads(rows[0][0])
    plugins = PluginManager(storage, settings.plugin_dirs)
    await plugins.discover()
    plan = pending["plan"]
    try:
        bundle = Bundle.model_validate(plan["bundle"])
        validate_bundle(bundle, plugins)
        async with storage.lock:
            before = await export_locked(storage, plugins)
            actor = next(
                (
                    u
                    for u in before.users
                    if u.id == plan["owner"] and u.enabled and "admin" in u.roles
                ),
                None,
            )
            if not actor or fingerprint(before) != plan["baseline"]:
                raise ValueError("Configuration or authorization changed before restart")
            users = {u.username: u for u in before.users}
            for imported in bundle.users:
                if imported.username == actor.username:
                    continue
                existing = users.get(imported.username)
                identity = existing.id if existing else imported.id
                if any(
                    u.id == identity and u.username != imported.username for u in users.values()
                ):
                    raise ValueError("Account identity collision")
                users[imported.username] = imported.model_copy(update={"id": identity})
            try:
                await storage.db.execute(
                    "INSERT INTO metadata VALUES ('backup.recovery',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (before.model_dump_json(),),
                )
                for table in ["devices", "alert_rules", "snapshots", "plugin_settings"]:
                    await storage.db.execute("DELETE FROM " + table)
                for key in ["mock_seeded", "alerts_seeded"]:
                    await storage.db.execute(
                        "INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, "true"),
                    )
                old = {d.id: d for d in before.devices}
                for d in bundle.devices:
                    updated = d.model_copy(
                        update={
                            "config_revision": max(
                                d.config_revision, old[d.id].config_revision if d.id in old else 0
                            )
                            + 1
                        }
                    )
                    await storage.db.execute(
                        "INSERT INTO devices VALUES (?,?)", (d.id, updated.model_dump_json())
                    )
                old_rules = {r.id: r for r in before.rules}
                for rule in bundle.rules:
                    updated = rule.model_copy(
                        update={
                            "revision": max(
                                rule.revision,
                                old_rules[rule.id].revision if rule.id in old_rules else 0,
                            )
                            + 1
                        }
                    )
                    await storage.db.execute(
                        "INSERT INTO alert_rules VALUES (?,?)", (rule.id, updated.model_dump_json())
                    )
                for saved in bundle.snapshots:
                    await storage.db.execute(
                        "INSERT INTO snapshots VALUES (?,?)", (saved["id"], json.dumps(saved))
                    )
                for p in bundle.plugins:
                    await storage.db.execute(
                        "INSERT INTO plugin_settings VALUES (?,?)", (p.plugin_id, int(p.enabled))
                    )
                weather = bundle.weather.model_copy(
                    update={"revision": max(bundle.weather.revision, before.weather.revision) + 1}
                )
                old_shows = {s.id: s for s in before.shows}
                profiles = [
                    s.model_copy(
                        update={
                            "revision": max(
                                s.revision, old_shows[s.id].revision if s.id in old_shows else 0
                            )
                            + 1
                        }
                    ).model_dump(mode="json")
                    for s in bundle.shows
                ]
                for p in profiles:
                    if p["id"] == bundle.active_show_id:
                        p["weather"] = weather.model_dump(mode="json")
                async with storage.db.execute(
                    "SELECT value FROM metadata WHERE key='show_profiles'"
                ) as cursor:
                    row = await cursor.fetchone()
                revision = json.loads(row[0])["revision"] + 1 if row else 1
                for key, value in [
                    ("weather_config", weather.model_dump(mode="json")),
                    (
                        "show_profiles",
                        {
                            "profiles": profiles,
                            "active_id": bundle.active_show_id,
                            "revision": revision,
                        },
                    ),
                ]:
                    await storage.db.execute(
                        "INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value)),
                    )
                await storage.db.execute("DELETE FROM sessions")
                for u in users.values():
                    await storage.db.execute(
                        "INSERT INTO users(id,username,password_hash,roles,device_ids,enabled) VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET password_hash=excluded.password_hash,roles=excluded.roles,device_ids=excluded.device_ids,enabled=excluded.enabled",
                        (
                            u.id,
                            u.username,
                            u.password_hash,
                            json.dumps(u.roles),
                            json.dumps(u.device_ids) if u.device_ids is not None else None,
                            int(u.enabled),
                        ),
                    )
                await storage.db.execute("DELETE FROM metadata WHERE key='backup.pending'")
                await storage.db.execute(
                    "INSERT INTO metadata VALUES ('backup.result',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps({"status": "completed", "time": now().isoformat()}),),
                )
                await storage._audit(
                    actor.id, "backup.restore", plan["id"], {"changes": plan["changes"]}
                )
                await storage.db.commit()
            except BaseException:
                await storage.db.rollback()
                raise
    except Exception:
        async with storage.lock:
            try:
                await storage.db.execute("DELETE FROM metadata WHERE key='backup.pending'")
                await storage.db.execute(
                    "INSERT INTO metadata VALUES ('backup.result',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (
                        json.dumps(
                            {
                                "status": "failed",
                                "time": now().isoformat(),
                                "error": "Backup validation or transaction failed; previous configuration preserved",
                            }
                        ),
                    ),
                )
                await storage._audit(plan["owner"], "backup.failed", plan["id"], {})
                await storage.db.commit()
            except BaseException:
                await storage.db.rollback()
                raise


class Schedule(Model):
    plan_id: str


def register(app, admin):
    @app.get("/api/backups/status")
    async def status(user=Depends(admin)):
        result = await app.state.storage.rows(
            "SELECT value FROM metadata WHERE key='backup.result'"
        )
        recovery = await app.state.storage.rows(
            "SELECT key FROM metadata WHERE key='backup.recovery'"
        )
        return {
            "pending": app.state.backup.pending,
            "last_result": json.loads(result[0][0]) if result else None,
            "recovery_available": bool(recovery),
        }

    @app.get("/api/backups/config")
    async def export(user=Depends(admin)):
        bundle = await app.state.backup.export()
        await app.state.storage.audit(
            user["id"], "backup.export", "system", {"kind": "configuration"}
        )
        return JSONResponse(
            bundle.model_dump(mode="json"),
            headers={
                "Content-Disposition": 'attachment; filename="stage-monitor-config.json"',
                "Cache-Control": "no-store",
            },
        )

    @app.get("/api/backups/recovery")
    async def recovery(user=Depends(admin)):
        rows = await app.state.storage.rows(
            "SELECT value FROM metadata WHERE key='backup.recovery'"
        )
        if not rows:
            raise HTTPException(404, "Recovery backup missing")
        return JSONResponse(
            json.loads(rows[0][0]),
            headers={
                "Content-Disposition": 'attachment; filename="stage-monitor-recovery.json"',
                "Cache-Control": "no-store",
            },
        )

    @app.get("/api/backups/database")
    async def database(user=Depends(admin)):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as handle:
            path = Path(handle.name)
        try:
            async with aiosqlite.connect(path) as target:
                async with app.state.storage.lock:
                    await app.state.storage.db.backup(target)
                await target.execute("DELETE FROM sessions")
                await target.execute("DELETE FROM metadata WHERE key='backup.pending'")
                await target.commit()
            await app.state.storage.audit(
                user["id"], "backup.export", "system", {"kind": "database"}
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return FileResponse(
            path,
            filename="stage-monitor.sqlite3",
            media_type="application/vnd.sqlite3",
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.post("/api/backups/preview")
    async def preview(request: Request, user=Depends(admin)):
        buffer = bytearray()
        async for chunk in request.stream():
            buffer.extend(chunk)
            if len(buffer) > MAX_BYTES:
                raise HTTPException(413, "Backup exceeds 5 MiB")
        try:
            bundle = Bundle.model_validate_json(buffer)
            return await app.state.backup.preview(bundle, user)
        except (ValueError, KeyError, TypeError, SchemaError):
            raise HTTPException(
                422, "Invalid backup or incompatible plugin configuration"
            ) from None

    @app.post("/api/backups/schedule")
    async def schedule(data: Schedule, user=Depends(admin)):
        return await app.state.backup.stage(data.plan_id, user)

    @app.post("/api/backups/cancel")
    async def cancel(user=Depends(admin)):
        return await app.state.backup.cancel(user)
