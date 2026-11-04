"""Durable timeline projection and administrator audit queries."""

import asyncio
import json
from collections import deque
from datetime import timedelta, timezone

from fastapi import Depends, HTTPException, Query
from pydantic import AwareDatetime

from .auth import can_access
from .contracts import new_id, now
from .presets import differences

SECRET_KEYS = {
    "password",
    "password_hash",
    "token",
    "token_hash",
    "csrf",
    "csrf_token",
    "secret",
    "api_key",
    "private_key",
    "credential_ref",
    "authorization",
    "cookie",
}


def redact(value):
    if isinstance(value, dict):
        return {k: "[ukryte]" if k.lower() in SECRET_KEYS else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class Journal:
    def __init__(self, storage, bus, manager):
        self.storage, self.bus, self.manager = storage, bus, manager
        self.pending = deque()
        self.lock = asyncio.Lock()
        self.last_error = None
        self.dropped = 0
        self.bus.recorder = self.capture
        self.bus.durable_recorder = self.record_durable

    def capture(self, event):
        record = self.project(event)
        if record is None:
            return
        if len(self.pending) >= 2048:
            self.dropped += 1
            self.last_error = "Timeline queue overflow; some runtime transitions omitted"
            return
        self.pending.append(record)

    def project(self, event):
        if event.type not in {
            "device.online",
            "device.offline",
            "device.warning",
            "device.error",
            "device.connection",
            "device.command.completed",
            "device.command.failed",
            "alert.created",
            "alert.acknowledged",
            "alert.resolved",
            "show.changed",
            "weather.update",
        }:
            return
        data = event.payload
        if event.type.startswith("device.command."):
            data = {
                "command_id": data.get("command_id"),
                "command": data.get("request", {}).get("command"),
                "status": data.get("status"),
                "confirmed": data.get("confirmed"),
                "error": data.get("error"),
            }
        elif event.type == "weather.update":
            data = {
                "config_revision": data.get(
                    "config_revision", data.get("config", {}).get("revision")
                ),
                "stale": data.get("stale"),
                "simulation": data.get("simulation"),
                "last_error": data.get("last_error"),
            }
        elif event.type.startswith("alert."):
            alert = data.get("alert", data)
            data = {
                k: alert.get(k)
                for k in ("id", "rule_name", "status", "severity", "channel", "device_name")
            }
        device = self.manager.devices.get(event.device_id)
        if device:
            data = {**data, "device_name": device.name}
        return (
            event.id,
            event.occurred_at.astimezone(timezone.utc).isoformat(),
            event.type,
            event.device_id,
            event.actor_id,
            "scoped",
            json.dumps(redact(data)),
        )

    async def record_durable(self, event):
        record = self.project(event)
        if record:
            await self.storage.write(
                "INSERT OR IGNORE INTO timeline(id,occurred_at,type,device_id,actor_id,visibility,data) VALUES (?,?,?,?,?,?,?)",
                record,
            )

    async def flush(self):
        async with self.lock:
            records = list(self.pending)[:100]
            omitted = self.dropped
            if omitted:
                records.append(
                    (
                        new_id(),
                        now().isoformat(),
                        "system.timeline.gap",
                        None,
                        None,
                        "admin",
                        json.dumps({"omitted": omitted}),
                    )
                )
            if not records:
                return
            async with self.storage.lock:
                try:
                    await self.storage.db.executemany(
                        "INSERT OR IGNORE INTO timeline(id,occurred_at,type,device_id,actor_id,visibility,data) VALUES (?,?,?,?,?,?,?)",
                        records,
                    )
                    await self.storage.db.commit()
                except BaseException:
                    await self.storage.db.rollback()
                    self.last_error = "Timeline storage unavailable"
                    raise
            for _ in range(min(100, len(records) - (1 if omitted else 0))):
                self.pending.popleft()
            self.dropped -= omitted
            self.last_error = None

    async def retain(self, days):
        cutoff = (now() - timedelta(days=days)).isoformat()
        await self.storage.write("DELETE FROM timeline WHERE occurred_at<?", (cutoff,))
        await self.storage.write(
            "DELETE FROM restore_plans WHERE json_extract(data,'$.expires_at')<? AND id NOT IN (SELECT id FROM restore_jobs)",
            (now().isoformat(),),
        )


def validate_dates(since, until):
    if since and until and since > until:
        raise HTTPException(422, "Invalid date range")


def filters(clauses, params, alias, since, until, type_value, type_column):
    if since:
        clauses.append(f"{alias}.occurred_at>=?")
        params.append(since.astimezone(timezone.utc).isoformat())
    if until:
        clauses.append(f"{alias}.occurred_at<=?")
        params.append(until.astimezone(timezone.utc).isoformat())
    if type_value:
        clauses.append(f"{alias}.{type_column}=?")
        params.append(type_value)


def register(app, current_user, admin):
    @app.get("/api/timeline")
    async def timeline(
        before: int | None = Query(None, ge=1),
        after: int | None = Query(None, ge=0),
        limit: int = Query(50, ge=1, le=200),
        device_id: str | None = None,
        event_type: str | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        user=Depends(current_user),
    ):
        validate_dates(since, until)
        if before is not None and after is not None:
            raise HTTPException(422, "Choose before or after")
        clauses, args = [], []
        if "admin" not in user["roles"]:
            clauses.append("t.visibility!='admin'")
            if user["device_ids"] is not None:
                ids = user["device_ids"]
                clauses.append(
                    "(t.device_id IS NULL OR t.device_id IN (" + ",".join("?" for _ in ids) + "))"
                    if ids
                    else "t.device_id IS NULL"
                )
                args.extend(ids)
        if device_id:
            if not can_access(user, device_id):
                raise HTTPException(404, "Device not found")
            clauses.append("t.device_id=?")
            args.append(device_id)
        if before is not None:
            scope = " AND ".join(clauses) or "1=1"
            anchor = await app.state.storage.rows(
                f"SELECT t.occurred_at FROM timeline t WHERE {scope} AND t.sequence=?",
                [*args, before],
            )
            if not anchor:
                raise HTTPException(404, "Timeline cursor expired or inaccessible")
            stamp = anchor[0][0]
            clauses.append("(t.occurred_at<? OR (t.occurred_at=? AND t.sequence<?))")
            args.extend([stamp, stamp, before])
        if after is not None:
            clauses.append("t.sequence>?")
            args.append(after)
        filters(clauses, args, "t", since, until, event_type, "type")
        where = " AND ".join(clauses) or "1=1"
        order = "t.sequence ASC" if after is not None else "t.occurred_at DESC,t.sequence DESC"
        rows = await app.state.storage.rows(
            f"SELECT t.*,u.username AS actor_name FROM timeline t LEFT JOIN users u ON u.id=t.actor_id WHERE {where} ORDER BY {order} LIMIT ?",
            [*args, limit + 1],
        )
        items = [{**dict(r), "data": redact(json.loads(r["data"]))} for r in rows[:limit]]
        return {
            "items": items,
            "next_cursor": items[-1]["sequence"] if len(rows) > limit else None,
            "status": "error"
            if app.state.journal.last_error or app.state.bus.recording_error
            else "ok",
        }

    @app.get("/api/audit/log")
    async def audit_log(
        before: int | None = Query(None, ge=1),
        limit: int = Query(50, ge=1, le=200),
        action: str | None = None,
        actor_id: str | None = None,
        target_id: str | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        user=Depends(admin),
    ):
        validate_dates(since, until)
        clauses, args = [], []
        if before is not None:
            clauses.append("a.id<?")
            args.append(before)
        for key, value in (("actor_id", actor_id), ("target_id", target_id)):
            if value:
                clauses.append(f"a.{key}=?")
                args.append(value)
        filters(clauses, args, "a", since, until, action, "action")
        where = " AND ".join(clauses) or "1=1"
        rows = await app.state.storage.rows(
            f"SELECT a.*,u.username AS actor_name FROM audit a LEFT JOIN users u ON u.id=a.actor_id WHERE {where} ORDER BY a.id DESC LIMIT ?",
            [*args, limit + 1],
        )
        items = []
        for row in rows[:limit]:
            raw = json.loads(row["data"])
            data = redact(raw)
            changes = (
                differences(data.get("previous"), data.get("new"))
                if "previous" in data or "new" in data
                else []
            )
            if row["action"] == "device.command":
                desired = data.get("desired_parameters", {})
                changes = differences(
                    {k: data.get("previous_parameters", {}).get(k) for k in desired}, desired
                )
            if row["action"] == "device.command.result":
                after = data.get("desired_parameters") or data.get("confirmed_parameters") or {}
                changes = differences(
                    {k: data.get("previous_parameters", {}).get(k) for k in after}, after
                )
            for change in differences(raw.get("previous"), raw.get("new")):
                if change["parameter"].split(".")[-1].lower() in SECRET_KEYS:
                    changes.append(
                        {
                            "parameter": change["parameter"],
                            "current": "[ukryte]",
                            "desired": "[ukryte]",
                        }
                    )
            items.append({**dict(row), "data": data, "changes": changes})
        return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}
