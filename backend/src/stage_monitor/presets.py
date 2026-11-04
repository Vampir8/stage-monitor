"""Immutable presets and reviewed, durable restore jobs using logical plugin commands."""

import asyncio
import hashlib
import json
from datetime import timedelta
from typing import Literal

from anyio import CancelScope
from fastapi import Depends, HTTPException, Query
from jsonschema import Draft202012Validator, ValidationError
from pydantic import Field, JsonValue, field_validator

from .auth import can_access, can_command, public_user
from .contracts import CommandRequest, Device, DeviceCreate, Model, new_id, now
from .device_manager import Conflict
from .topology import validate_graph


class Capture(Model):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["parameters", "configuration"] = "parameters"
    device_ids: list[str] = Field(min_length=1, max_length=100)
    overrides: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("Name cannot be blank")
        return value.strip()


class Selection(Model):
    device_ids: list[str] = Field(min_length=1, max_length=100)


class Restore(Model):
    plan_id: str


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def configuration(device):
    # Credential references are deliberately preserved from the current device on restore.
    return device.model_dump(mode="json", exclude={"id", "config_revision", "credential_ref"})


def differences(before, after, path=""):
    result = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            result.extend(differences(before.get(key), after.get(key), f"{path}.{key}".strip(".")))
    elif digest(before) != digest(after):
        result.append({"parameter": path, "current": before, "desired": after})
    return result


class PresetService:
    def __init__(self, storage, bus, manager):
        self.storage, self.bus, self.manager = storage, bus, manager
        self.lock = asyncio.Lock()
        self.tasks = set()
        self.last_error = None

    async def start(self):
        for row in await self.storage.rows("SELECT data FROM restore_jobs"):
            job = json.loads(row[0])
            if job["status"] == "running":
                job["status"] = "unknown"
                for item in job["items"]:
                    if item["status"] in {"queued", "running"}:
                        item.update(
                            status="unknown",
                            message="Backend restarted; inspect device state before retrying",
                        )
                await self.save_job(job)

    async def close(self):
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def live_user(self, actor_id):
        rows = await self.storage.rows("SELECT * FROM users WHERE id=? AND enabled=1", (actor_id,))
        if not rows:
            raise PermissionError("User no longer authorized")
        return public_user(rows[0])

    def specs(self, device_id):
        return [
            c for c in self.manager.commands(device_id) if c.snapshot_parameters and c.idempotent
        ]

    def version(self, device):
        return self.manager.plugins.get(device.plugin_id)["descriptor"].plugin_version

    def available(self, device):
        state = self.manager.states.get(device.id)
        return (
            device.enabled
            and self.manager.active(device)
            and state is not None
            and state.status in {"online", "degraded"}
            and state.connection_status == "connected"
            and state.health.last_seen is not None
            and 0 <= (now() - state.health.last_seen).total_seconds() <= device.stale_after
        )

    async def get(self, snapshot_id, user):
        rows = await self.storage.rows("SELECT data FROM snapshots WHERE id=?", (snapshot_id,))
        if not rows:
            raise HTTPException(404, "Snapshot not found")
        saved = json.loads(rows[0][0])
        if saved["kind"] == "configuration" and "admin" not in user["roles"]:
            raise HTTPException(403, "Configuration snapshots require admin")
        saved["entries"] = [e for e in saved["entries"] if can_access(user, e["device_id"])]
        if not saved["entries"]:
            raise HTTPException(404, "Snapshot not found")
        return saved

    async def capture(self, data, user):
        if not set(user["roles"]) & {"admin", "operator", "audio", "video", "network"}:
            raise HTTPException(403, "Read-only role")
        if data.kind == "configuration" and "admin" not in user["roles"]:
            raise HTTPException(403, "Configuration snapshots require admin")
        if not set(data.overrides) <= set(data.device_ids) or (
            data.kind == "configuration" and data.overrides
        ):
            raise HTTPException(422, "Overrides apply only to selected device parameters")
        entries = []
        async with self.manager.config_lock:
            for device_id in dict.fromkeys(data.device_ids):
                device = self.manager.devices.get(device_id)
                if not device or not can_access(user, device_id):
                    raise HTTPException(404, "Device not found")
                try:
                    version = self.version(device)
                except ValueError:
                    raise HTTPException(422, "Plugin unavailable") from None
                entry = {
                    "device_id": device_id,
                    "device_name": device.name,
                    "plugin_id": device.plugin_id,
                    "plugin_version": version,
                    "config_revision": device.config_revision,
                }
                if data.kind == "configuration":
                    entry["configuration"] = configuration(device)
                else:
                    if not self.available(device):
                        raise Conflict("Capture requires fresh device state")
                    state, specs = self.manager.states[device_id], self.specs(device_id)
                    keys = {key for c in specs for key in c.snapshot_parameters}
                    if not keys or not keys <= state.parameters.keys():
                        raise HTTPException(422, "Plugin does not expose restorable parameters")
                    override = data.overrides.get(device_id, {})
                    if not set(override) <= keys:
                        raise HTTPException(422, "Parameter is not declared restorable")
                    entry["parameters"] = {key: state.parameters[key] for key in keys} | override
                    entry["commands"] = [c.model_dump(mode="json") for c in specs]
                    try:
                        for spec in specs:
                            args = {
                                argument: entry["parameters"][key]
                                for key, argument in spec.snapshot_parameters.items()
                            }
                            Draft202012Validator(spec.parameters_schema).validate(args)
                    except ValidationError:
                        raise HTTPException(422, "Invalid preset values") from None
                entries.append(entry)
        saved = {
            "id": new_id(),
            "name": data.name,
            "kind": data.kind,
            "created_at": now().isoformat(),
            "created_by": user["id"],
            "entries": entries,
        }
        async with self.storage.lock:
            try:
                await self.storage.db.execute(
                    "INSERT INTO snapshots VALUES (?,?)", (saved["id"], json.dumps(saved))
                )
                await self.storage._audit(
                    user["id"],
                    "snapshot.create",
                    saved["id"],
                    {"name": saved["name"], "kind": saved["kind"], "device_ids": data.device_ids},
                )
                await self.storage.db.commit()
            except BaseException:
                await self.storage.db.rollback()
                raise
        return saved

    def review(self, saved, selected, user):
        entries = {e["device_id"]: e for e in saved["entries"]}
        if not set(selected) <= entries.keys():
            raise HTTPException(404, "Snapshot device not found")
        result = []
        for device_id in dict.fromkeys(selected):
            entry, device = entries[device_id], self.manager.devices.get(device_id)
            row = {
                "device_id": device_id,
                "device_name": entry["device_name"],
                "status": "blocked",
                "message": "",
                "changes": [],
                "commands": [],
                "command_ids": [],
            }
            result.append(row)
            if not can_access(user, device_id):
                raise HTTPException(403, "Device permission denied")
            if not device:
                row["message"] = "Device removed"
                continue
            try:
                if (
                    device.plugin_id != entry["plugin_id"]
                    or self.version(device) != entry["plugin_version"]
                ):
                    row["message"] = "Plugin version or model changed"
                    continue
            except ValueError:
                row["message"] = "Plugin unavailable"
                continue
            row["config_revision"] = device.config_revision
            if saved["kind"] == "configuration":
                if "admin" not in user["roles"]:
                    row["message"] = "Admin role required"
                    continue
                current = configuration(device)
                desired = {**current, **entry["configuration"]}
                try:
                    candidate = Device(**desired, id=device_id, credential_ref=device.credential_ref)
                    validate_graph({**self.manager.devices, device_id: candidate})
                except ValueError:
                    row["message"] = "Snapshot dependencies are missing or cyclic"
                    continue
                row["changes"] = differences(current, desired)
                row["fingerprint"] = digest(current)
            else:
                if not self.available(device):
                    row["message"] = "Device offline or telemetry stale"
                    continue
                specs = self.specs(device_id)
                if digest([c.model_dump(mode="json") for c in specs]) != digest(entry["commands"]):
                    row["message"] = "Restore command declarations changed"
                    continue
                if any(not can_command(user, device_id, c.capability_id) for c in specs):
                    row["message"] = "Command permission denied"
                    continue
                current = self.manager.states[device_id].parameters
                desired = entry["parameters"]
                if not set(desired) <= current.keys():
                    row["message"] = "Restorable parameter missing"
                    continue
                row["changes"] = differences({key: current[key] for key in desired}, desired)
                row["fingerprint"] = digest(
                    {
                        "config_revision": device.config_revision,
                        "parameters": {key: current[key] for key in desired},
                    }
                )
                for spec in specs:
                    keys = spec.snapshot_parameters
                    if any(digest(current[key]) != digest(desired[key]) for key in keys):
                        row["commands"].append(
                            {
                                "command": spec.name,
                                "capability_id": spec.capability_id,
                                "parameters": {arg: desired[key] for key, arg in keys.items()},
                                "expected_parameters": {key: current[key] for key in keys},
                            }
                        )
            row["status"] = "ready" if row["changes"] else "noop"
        return result

    async def preview(self, snapshot_id, data, user):
        saved = await self.get(snapshot_id, user)
        async with self.manager.config_lock:
            items = self.review(saved, data.device_ids, user)
        plan = {
            "id": new_id(),
            "snapshot_id": snapshot_id,
            "actor_id": user["id"],
            "kind": saved["kind"],
            "expires_at": (now() + timedelta(minutes=5)).isoformat(),
            "items": items,
        }
        await self.storage.write(
            "INSERT INTO restore_plans VALUES (?,?,?)", (plan["id"], user["id"], json.dumps(plan))
        )
        return plan

    async def save_job(self, job, initial=False):
        async with self.storage.lock:
            try:
                await self.storage.db.execute(
                    "INSERT INTO restore_jobs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                    (job["id"], job["actor_id"], json.dumps(job)),
                )
                if initial or job["status"] != "running":
                    await self.storage._audit(
                        job["actor_id"],
                        "snapshot.restore" if initial else "snapshot.restore.result",
                        job["snapshot_id"],
                        {"job_id": job["id"], "status": job["status"], "items": job["items"]},
                    )
                await self.storage.db.commit()
            except BaseException:
                await self.storage.db.rollback()
                raise

    async def restore(self, snapshot_id, plan_id, user):
        async with self.lock:
            existing = await self.storage.rows(
                "SELECT data FROM restore_jobs WHERE id=? AND actor_id=?", (plan_id, user["id"])
            )
            if existing:
                job = json.loads(existing[0][0])
                if job["snapshot_id"] != snapshot_id:
                    raise HTTPException(404, "Restore not found")
                return self.visible_job(job, user)
            rows = await self.storage.rows(
                "SELECT data FROM restore_plans WHERE id=? AND actor_id=?", (plan_id, user["id"])
            )
            if not rows:
                raise HTTPException(404, "Preview not found")
            plan = json.loads(rows[0][0])
            if plan["snapshot_id"] != snapshot_id or plan["expires_at"] < now().isoformat():
                raise Conflict("Preview expired; review again")
            saved = await self.get(snapshot_id, user)
            fresh = self.review(saved, [r["device_id"] for r in plan["items"]], user)
            if digest(fresh) != digest(plan["items"]):
                raise Conflict("Devices changed since preview; review again")
            if not any(r["status"] in {"ready", "noop"} for r in fresh):
                raise Conflict("No restorable devices")
            if len(self.tasks) >= 2:
                raise Conflict("Restore queue busy")
            job = {
                "id": plan_id,
                "snapshot_id": snapshot_id,
                "actor_id": user["id"],
                "kind": saved["kind"],
                "created_at": now().isoformat(),
                "status": "running",
                "items": fresh,
            }
            for row in job["items"]:
                if row["status"] == "ready":
                    row["status"] = "queued"
            with CancelScope(shield=True):
                await self.save_job(job, initial=True)
                task = asyncio.create_task(self.run(job, saved), name="restore-" + plan_id)
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
            return self.visible_job(job, user)

    def visible_job(self, job, user):
        if job["kind"] == "configuration" and "admin" not in user["roles"]:
            raise HTTPException(403, "Admin role required")
        return {**job, "items": [r for r in job["items"] if can_access(user, r["device_id"])]}

    async def run(self, job, saved):
        try:
            for item in job["items"]:
                if item["status"] != "queued":
                    continue
                device_id = item["device_id"]
                try:
                    user = await self.live_user(job["actor_id"])
                    checked = self.review(saved, [device_id], user)[0]
                    if (
                        checked["status"] not in {"ready", "noop"}
                        or checked.get("fingerprint") != item["fingerprint"]
                    ):
                        raise Conflict("Device changed or permission revoked since review")
                    item["status"] = "running"
                    await self.save_job(job)
                    if job["kind"] == "configuration":
                        old = self.manager.devices[device_id]
                        if (
                            old.config_revision != item["config_revision"]
                            or digest(configuration(old)) != item["fingerprint"]
                        ):
                            raise Conflict("Configuration changed since review")
                        entry = next(e for e in saved["entries"] if e["device_id"] == device_id)
                        data = DeviceCreate(
                            **{**configuration(old), **entry["configuration"]}, credential_ref=old.credential_ref
                        )
                        updated = Device(
                            **data.model_dump(),
                            id=device_id,
                            config_revision=old.config_revision + 1,
                        )
                        await self.manager.save_device(updated, user["id"], old)
                        item["status"] = "succeeded"
                    else:
                        for index, operation in enumerate(item["commands"]):
                            user = await self.live_user(job["actor_id"])
                            if not can_command(user, device_id, operation["capability_id"]):
                                raise PermissionError("Command permission revoked")
                            request = CommandRequest(
                                command=operation["command"],
                                parameters=operation["parameters"],
                                idempotency_key=f"restore:{job['id']}:{device_id}:{index}",
                                expected_config_revision=item["config_revision"],
                                expected_parameters=operation["expected_parameters"],
                            )
                            result = await self.manager.submit(device_id, user["id"], request)
                            item["command_ids"].append(result.command_id)
                            await self.save_job(job)
                            while result.status in {"queued", "running"}:
                                await asyncio.sleep(0.1)
                                result = await self.storage.get_command(result.command_id)
                            operation.update(
                                status=result.status,
                                confirmed=result.confirmed,
                                command_id=result.command_id,
                            )
                            if result.status != "succeeded" or not result.confirmed:
                                item.update(
                                    status="unknown"
                                    if result.status in {"unknown", "timed_out"}
                                    else "partial"
                                    if any(c.get("status") == "succeeded" for c in item["commands"])
                                    else "failed",
                                    message="Command outcome uncertain; inspect device"
                                    if result.status == "unknown"
                                    else "Command failed",
                                )
                                break
                        else:
                            item["status"] = "succeeded"
                except (
                    Conflict,
                    ValueError,
                    ValidationError,
                    PermissionError,
                    HTTPException,
                    KeyError,
                ):
                    item.update(
                        status="partial"
                        if any(c.get("status") == "succeeded" for c in item["commands"])
                        else "failed",
                        message="Device unavailable, changed or permission revoked; inspect current state",
                    )
                await self.save_job(job)
            statuses = {r["status"] for r in job["items"]}
            job["status"] = (
                "unknown"
                if "unknown" in statuses
                else "completed"
                if statuses <= {"succeeded", "noop"}
                else "partial"
                if statuses & {"succeeded", "noop", "partial"}
                else "failed"
            )
        except BaseException as exc:
            job["status"] = "unknown"
            for item in job["items"]:
                if item["status"] in {"running", "queued"}:
                    item.update(
                        status="unknown", message="Restore interrupted; inspect device state"
                    )
            if not isinstance(exc, asyncio.CancelledError):
                self.last_error = "Restore persistence or execution failed"
        finally:
            with CancelScope(shield=True):
                try:
                    await self.save_job(job)
                except Exception:
                    self.last_error = "Restore result could not be persisted"


def register(app, current_user):
    @app.get("/api/snapshots/catalog")
    async def catalog(user=Depends(current_user)):
        result = []
        for device in app.state.manager.devices.values():
            if not can_access(user, device.id):
                continue
            specs = app.state.presets.specs(device.id)
            keys = {key for spec in specs for key in spec.snapshot_parameters}
            state = app.state.manager.states[device.id]
            result.append(
                {
                    "device_id": device.id,
                    "name": device.name,
                    "category": device.category,
                    "capturable": bool(keys)
                    and keys <= state.parameters.keys()
                    and app.state.presets.available(device),
                    "parameters": {
                        key: state.parameters[key] for key in keys if key in state.parameters
                    },
                    "fields": {
                        key: spec.parameters_schema.get("properties", {}).get(argument, {})
                        for spec in specs
                        for key, argument in spec.snapshot_parameters.items()
                    },
                }
            )
        return result

    @app.get("/api/snapshots")
    async def snapshots(limit: int = Query(100, ge=1, le=200), user=Depends(current_user)):
        clauses, params = [], []
        if "admin" not in user["roles"]:
            clauses.append("json_extract(data,'$.kind')='parameters'")
            scope = user.get("device_ids")
            if scope is not None:
                clauses.append(
                    "EXISTS (SELECT 1 FROM json_each(snapshots.data,'$.entries') entry "
                    "WHERE json_extract(entry.value,'$.device_id') IN "
                    "(SELECT value FROM json_each(?)))"
                )
                params.append(json.dumps(scope))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = await app.state.storage.rows(
            "SELECT data FROM snapshots" + where + " ORDER BY rowid DESC LIMIT ?",
            (*params, limit),
        )
        result = []
        for row in rows:
            data = json.loads(row[0])
            if data["kind"] == "configuration" and "admin" not in user["roles"]:
                continue
            data["entries"] = [e for e in data["entries"] if can_access(user, e["device_id"])]
            if data["entries"]:
                result.append(data)
            if len(result) >= limit:
                break
        return result

    @app.post("/api/snapshots", status_code=201)
    async def capture(data: Capture, user=Depends(current_user)):
        return await app.state.presets.capture(data, user)

    @app.get("/api/snapshots/{snapshot_id}")
    async def snapshot(snapshot_id: str, user=Depends(current_user)):
        return await app.state.presets.get(snapshot_id, user)

    @app.post("/api/snapshots/{snapshot_id}/preview")
    async def preview(snapshot_id: str, data: Selection, user=Depends(current_user)):
        return await app.state.presets.preview(snapshot_id, data, user)

    @app.post("/api/snapshots/{snapshot_id}/restore", status_code=202)
    async def restore(snapshot_id: str, data: Restore, user=Depends(current_user)):
        return await app.state.presets.restore(snapshot_id, data.plan_id, user)

    @app.get("/api/restores")
    async def restores(user=Depends(current_user)):
        rows = await app.state.storage.rows(
            "SELECT data FROM restore_jobs WHERE actor_id=? ORDER BY rowid DESC LIMIT 50",
            (user["id"],),
        )
        return [
            app.state.presets.visible_job(json.loads(row[0]), user)
            for row in rows
            if json.loads(row[0])["kind"] != "configuration" or "admin" in user["roles"]
        ]

    @app.get("/api/restores/{job_id}")
    async def restore_result(job_id: str, user=Depends(current_user)):
        rows = await app.state.storage.rows(
            "SELECT data FROM restore_jobs WHERE id=? AND actor_id=?", (job_id, user["id"])
        )
        if not rows:
            raise HTTPException(404, "Restore not found")
        return app.state.presets.visible_job(json.loads(rows[0][0]), user)
