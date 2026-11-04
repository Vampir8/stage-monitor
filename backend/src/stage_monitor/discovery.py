"""Bounded discovery jobs, isolated adapters and explicit administrator adoption."""

import asyncio
import json
from contextlib import suppress
from ipaddress import ip_network

from anyio import CancelScope
from fastapi import Depends, HTTPException
from jsonschema import ValidationError as SchemaError
from pydantic import Field, field_validator

from .contracts import (
    Device,
    DeviceEndpoint,
    DiscoveryCandidate,
    DiscoveryScope,
    Model,
    new_id,
    now,
)
from .device_manager import Conflict
from .runtime import discover_plugin


class Scan(DiscoveryScope):
    plugin_ids: list[str] = Field(min_length=1, max_length=8)

    @field_validator("plugin_ids")
    @classmethod
    def unique_plugins(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Select each adapter once")
        return values


class Adopt(Model):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Name cannot be blank")
        return value.strip() if value is not None else None


class DiscoveryService:
    def __init__(self, storage, plugins, manager, authorize):
        self.storage, self.plugins, self.manager, self.authorize = (
            storage,
            plugins,
            manager,
            authorize,
        )
        self.jobs, self.tasks = {}, {}
        self.lock = asyncio.Lock()
        self.last_start = 0.0
        self.last_error = None

    async def start(self):
        rows = await self.storage.rows("SELECT value FROM metadata WHERE key='discovery_jobs'")
        self.jobs = json.loads(rows[0][0]) if rows else {}
        changed = False
        for job in self.jobs.values():
            if job["status"] == "running":
                job["status"] = "interrupted"
                job["finished_at"] = now().isoformat()
                for adapter in job["adapters"]:
                    if adapter["status"] in {"queued", "running"}:
                        adapter.update(
                            status="interrupted", error="Backend restarted; start a new scan"
                        )
                changed = True
        if changed:
            await self.persist()

    async def close(self):
        for task in tuple(self.tasks.values()):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks.values()), return_exceptions=True)

    async def persist(self, actor=None, action=None, data=None):
        async with self.storage.lock:
            try:
                await self.storage.db.execute(
                    "INSERT INTO metadata VALUES ('discovery_jobs',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps(self.jobs),),
                )
                if action:
                    await self.storage._audit(actor, action, data["job_id"], data)
                await self.storage.db.commit()
            except BaseException:
                await self.storage.db.rollback()
                self.last_error = "Discovery persistence failed"
                raise

    def catalog(self):
        return [
            {
                "plugin_id": key,
                "manufacturer": entry["descriptor"].manufacturer,
                "model": entry["descriptor"].model,
                "plugin_version": entry["descriptor"].plugin_version,
                **entry["descriptor"].discovery.model_dump(),
            }
            for key, entry in self.plugins.entries.items()
            if entry["enabled"]
            and not entry["error"]
            and entry["descriptor"]
            and entry["descriptor"].discovery
        ]

    def get(self, key):
        if key not in self.jobs:
            raise HTTPException(404, "Discovery job not found")
        return self.jobs[key]

    def existing(self, candidate):
        endpoint = DeviceEndpoint(
            transport=candidate["transport"], host=candidate["host"], port=candidate["port"]
        )
        return next(
            (
                d
                for d in self.manager.devices.values()
                if d.plugin_id == candidate["plugin_id"] and d.endpoint == endpoint
            ),
            None,
        )

    def view(self, job):
        result = json.loads(json.dumps(job))
        for candidate in result["candidates"]:
            existing = self.existing(candidate)
            candidate["existing_device_id"] = existing.id if existing else None
        return result

    async def scan(self, request, user):
        async with self.lock:
            clock = asyncio.get_running_loop().time()
            if self.tasks:
                raise HTTPException(409, "A discovery job is already running")
            if clock - self.last_start < 5:
                raise HTTPException(429, "Wait five seconds before starting another scan")
            entries = []
            for key in request.plugin_ids:
                try:
                    entry = self.plugins.get(key)
                except ValueError:
                    raise HTTPException(422, "Discovery adapter unavailable") from None
                if not entry["enabled"] or not entry["descriptor"].discovery:
                    raise HTTPException(422, "Discovery is not supported by this adapter")
                entries.append((key, entry))
            job = {
                "id": new_id(),
                "actor_id": user["id"],
                "created_at": now().isoformat(),
                "finished_at": None,
                "status": "running",
                "scope": request.model_dump(mode="json", exclude={"plugin_ids"}),
                "candidates": [],
                "adapters": [
                    {
                        "plugin_id": key,
                        "plugin_version": entry["descriptor"].plugin_version,
                        "simulation": entry["descriptor"].discovery.simulation,
                        "status": "queued",
                        "error": None,
                        "rejected": 0,
                    }
                    for key, entry in entries
                ],
            }
            while len(self.jobs) >= 20:
                self.jobs.pop(next(iter(self.jobs)))
            self.jobs[job["id"]] = job
            with CancelScope(shield=True):
                try:
                    await self.persist(
                        user["id"],
                        "discovery.start",
                        {
                            "job_id": job["id"],
                            "subnet": request.subnet,
                            "plugin_ids": request.plugin_ids,
                        },
                    )
                except Exception:
                    self.jobs.pop(job["id"], None)
                    raise
                self.last_start = clock
                task = asyncio.create_task(self.run(job, entries), name="discovery-" + job["id"])
                self.tasks[job["id"]] = task
                task.add_done_callback(lambda _: self.tasks.pop(job["id"], None))
            return self.view(job)

    async def run(self, job, entries):
        semaphore = asyncio.Semaphore(2)

        async def adapter(index, key, entry):
            row = job["adapters"][index]
            async with semaphore:
                try:
                    await self.authorize(job["actor_id"])
                    if not entry["enabled"]:
                        raise ValueError("Plugin disabled")
                    row["status"] = "running"
                    scope = DiscoveryScope.model_validate(job["scope"])
                    response = await discover_plugin(entry, scope)
                    await self.authorize(job["actor_id"])
                    if not entry["enabled"]:
                        raise ValueError("Plugin disabled")
                    seen = {
                        (c["plugin_id"], c["transport"], c["host"], c["port"])
                        for c in job["candidates"]
                    }
                    network = ip_network(scope.subnet)
                    for raw in response:
                        try:
                            value = DiscoveryCandidate.model_validate(raw)
                            if value.transport != "mock" and value.port is None:
                                raise ValueError("LAN candidates require a port")
                            if value.host not in network or (
                                network.prefixlen < 31
                                and value.host
                                in {network.network_address, network.broadcast_address}
                            ):
                                raise ValueError("Candidate outside host scope")
                            configuration = Device(
                                name=value.name,
                                plugin_id=key,
                                category=value.category,
                                endpoint=DeviceEndpoint(
                                    transport=value.transport, host=str(value.host), port=value.port
                                ),
                                custom_config=value.custom_config,
                            )
                            self.plugins.validate_device(configuration)
                            identity = (key, value.transport, str(value.host), value.port)
                            if identity in seen:
                                continue
                            seen.add(identity)
                            if len(job["candidates"]) >= scope.max_results:
                                row["rejected"] += 1
                                continue
                            job["candidates"].append(
                                {
                                    **value.model_dump(mode="json"),
                                    "id": new_id(),
                                    "plugin_id": key,
                                    "plugin_version": row["plugin_version"],
                                    "simulation": row["simulation"],
                                    "adopted_device_id": None,
                                }
                            )
                        except (ValueError, SchemaError):
                            row["rejected"] += 1
                    row["status"] = "completed"
                except asyncio.CancelledError:
                    row["status"] = "cancelled"
                    raise
                except Exception:
                    row.update(
                        status="failed", error="Adapter failed, timed out or authorization changed"
                    )

        try:
            await asyncio.gather(
                *(adapter(i, key, entry) for i, (key, entry) in enumerate(entries))
            )
            statuses = {r["status"] for r in job["adapters"]}
            job["status"] = (
                "completed"
                if statuses == {"completed"}
                else "partial"
                if "completed" in statuses
                else "failed"
            )
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            for row in job["adapters"]:
                if row["status"] in {"running", "queued"}:
                    row["status"] = "cancelled"
        except Exception:
            job["status"] = "failed"
            self.last_error = "Discovery job failed"
        finally:
            job["finished_at"] = now().isoformat()
            with CancelScope(shield=True):
                try:
                    await self.persist(
                        job["actor_id"],
                        "discovery.result",
                        {
                            "job_id": job["id"],
                            "status": job["status"],
                            "count": len(job["candidates"]),
                        },
                    )
                except Exception:
                    self.last_error = "Discovery result could not be persisted"

    async def cancel(self, key, user):
        self.get(key)
        task = self.tasks.get(key)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            job = self.get(key)
            if job["status"] == "running":
                job["status"] = "cancelled"
                job["finished_at"] = now().isoformat()
                for row in job["adapters"]:
                    if row["status"] in {"running", "queued"}:
                        row["status"] = "cancelled"
            await self.persist(user["id"], "discovery.cancel", {"job_id": key})
        return self.view(self.get(key))

    async def adopt(self, key, candidate_id, request, user):
        async with self.lock:
            job = self.get(key)
            if job["status"] not in {"completed", "partial"}:
                raise HTTPException(409, "Scan must finish before adding devices")
            candidate = next((c for c in job["candidates"] if c["id"] == candidate_id), None)
            if not candidate:
                raise HTTPException(404, "Discovery candidate not found")
            await self.authorize(user["id"])
            try:
                entry = self.plugins.get(candidate["plugin_id"])
                if (
                    not entry["enabled"]
                    or entry["descriptor"].plugin_version != candidate["plugin_version"]
                ):
                    raise ValueError("Plugin changed")
            except ValueError:
                raise HTTPException(409, "Adapter changed; scan again") from None
            device = self.existing(candidate)
            created = False
            if not device:
                device = Device(
                    name=(request.name or candidate["name"]).strip(),
                    plugin_id=candidate["plugin_id"],
                    category=candidate["category"],
                    endpoint=DeviceEndpoint(
                        transport=candidate["transport"],
                        host=candidate["host"],
                        port=candidate["port"],
                    ),
                    enabled=request.enabled,
                    custom_config=candidate["custom_config"],
                )
                try:
                    await self.manager.save_device(
                        device, user["id"], reject_duplicate_endpoint=True
                    )
                    created = True
                except Conflict:
                    device = self.existing(candidate)
                    if not device:
                        raise
            candidate["adopted_device_id"] = device.id
            await self.persist(
                user["id"],
                "discovery.adopt",
                {"job_id": key, "device_id": device.id, "created": created},
            )
            return {"device": device, "created": created}


def register(app, admin):
    @app.get("/api/discovery")
    async def catalog(user=Depends(admin)):
        return {
            "adapters": app.state.discovery.catalog(),
            "jobs": [
                app.state.discovery.view(j)
                for j in reversed(list(app.state.discovery.jobs.values()))
            ],
            "status": "error" if app.state.discovery.last_error else "ok",
        }

    @app.post("/api/discovery/jobs", status_code=202)
    async def scan(data: Scan, user=Depends(admin)):
        return await app.state.discovery.scan(data, user)

    @app.get("/api/discovery/jobs/{job_id}")
    async def job(job_id: str, user=Depends(admin)):
        return app.state.discovery.view(app.state.discovery.get(job_id))

    @app.post("/api/discovery/jobs/{job_id}/cancel")
    async def cancel(job_id: str, user=Depends(admin)):
        return await app.state.discovery.cancel(job_id, user)

    @app.post("/api/discovery/jobs/{job_id}/candidates/{candidate_id}/adopt")
    async def adopt(job_id: str, candidate_id: str, data: Adopt, user=Depends(admin)):
        return await app.state.discovery.adopt(job_id, candidate_id, data, user)
