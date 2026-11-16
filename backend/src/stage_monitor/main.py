import asyncio
import hmac
import json
import logging
import sqlite3
from contextlib import asynccontextmanager, suppress
from anyio import CancelScope

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from jsonschema import ValidationError as SchemaError
from pydantic import AwareDatetime, Field, SecretStr
from starlette.websockets import WebSocketDisconnect

from .alerts.api import register as register_alerts
from .alerts.service import AlertService
from .auth import AuthService, Login, UserCreate, UserUpdate, can_access, can_command, public_user
from .backups import BackupService, apply_pending, register as register_backups
from .chat.api import register as register_chat
from .chat.service import ChatService
from .contracts import CommandRequest, Device, DeviceCreate, DeviceEndpoint, Model, new_id, now
from .credentials import CredentialUnavailable
from .device_manager import Conflict, DeviceManager
from .discovery import DiscoveryService, register as register_discovery
from .diagnostics import Diagnostics, register as register_diagnostics
from .mapping_presets import register as register_mapping_presets
from .events import EventBus
from .frontend_themes import register as register_frontend_themes
from .frontend_languages import register as register_frontend_languages
from .health import register as register_health
from .journal import Journal, redact, register as register_journal
from .media.service import MediaService, register as register_media
from .plugin_manager import PluginManager
from .presets import PresetService, register as register_presets
from .runtime import PluginFailure, PluginSupervisor
from .settings import Settings
from .shows import ShowService, register as register_shows
from .storage import Storage
from .topology import register as register_topology
from .weather.models import WeatherConfig
from .weather.service import WeatherService, WeatherUnavailable

log = logging.getLogger(__name__)


class InstanceLock:
    """One owner of devices and in-memory bus per SQLite data directory."""
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.file = (directory / ".runtime.lock").open("a+b")
        try:
            self.file.seek(0)
            if not self.file.read(1):
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            import os
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError("Another backend already owns this data directory; use one worker") from exc

    def close(self):
        self.file.close()


class Subscription(Model):
    type: str = "subscribe"
    csrf_token: str
    device_ids: list[str] | None = Field(default=None, max_length=500)
    include_metrics: bool = True


class DeviceInput(DeviceCreate):
    password: SecretStr | None = Field(default=None, exclude=True, min_length=1, max_length=1024,
                                       json_schema_extra={'writeOnly': True})


class ConfigurationOption(Model):
    value: str = Field(min_length=1, max_length=160)
    label: str = Field(default="", max_length=160)


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_environment()
    option_lookup_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        instance_lock = InstanceLock(settings.data_dir)
        storage = Storage(settings.data_dir / "stage_monitor.sqlite3")
        manager = None
        maintenance = None
        weather = None
        alerts = None
        chat = None
        presets = None
        journal = None
        discovery = None
        media = None
        app.state.ready = False
        try:
            await storage.open()
            await apply_pending(storage,settings)
            auth = AuthService(storage, settings)
            await auth.bootstrap()
            plugins = PluginManager(storage, settings.plugin_dirs)
            await plugins.discover()
            bus = EventBus()
            manager = DeviceManager(storage, plugins, bus, settings)
            app.state.storage, app.state.auth = storage, auth
            app.state.plugins, app.state.bus, app.state.manager = plugins, bus, manager
            app.state.backup=BackupService(storage,plugins,app)
            app.state.diagnostics=Diagnostics()
            manager.diagnostics=app.state.diagnostics
            journal = Journal(storage, bus, manager)
            app.state.journal = journal
            presets = PresetService(storage, bus, manager)
            app.state.presets = presets

            async def authorize_command(actor_id, device_id, capability):
                user = await presets.live_user(actor_id)
                if not can_command(user, device_id, capability):
                    raise PermissionError("Command permission revoked")

            manager.command_authorizer = authorize_command
            await manager.start()
            await presets.start()
            async def authorize_discovery(actor_id):
                try:
                    user = await presets.live_user(actor_id)
                except PermissionError:
                    raise HTTPException(403,"Discovery authorization revoked") from None
                if "admin" not in user["roles"]:
                    raise HTTPException(403,"Admin role required")
            discovery = DiscoveryService(storage,plugins,manager,authorize_discovery)
            app.state.discovery = discovery
            await discovery.start()
            weather = WeatherService(storage, bus, settings.weather_refresh_seconds)
            app.state.weather = weather
            await weather.start()
            shows = ShowService(storage, bus, manager, weather)
            app.state.shows = shows
            await shows.start()
            alerts = AlertService(storage, bus, manager)
            app.state.alerts = alerts
            await alerts.start()
            chat = ChatService(storage, bus)
            app.state.chat = chat
            await chat.start()
            media = MediaService(app)
            app.state.media = media

            async def maintain():
                last_retention = 0.0
                while True:
                    try:
                        await bus.deliver_outbox(storage)
                        await journal.flush()
                        clock = asyncio.get_running_loop().time()
                        if clock - last_retention > 3600:
                            await storage.retain(settings.history_retention_days)
                            await storage.retain_alerts(settings.alert_retention_days)
                            await storage.retain_chat(settings.chat_retention_days)
                            await journal.retain(settings.timeline_retention_days)
                            last_retention = clock
                    except Exception:
                        storage.last_error = "Storage maintenance failed"
                        log.exception("Storage maintenance failed")
                    await asyncio.sleep(0.1)

            maintenance = asyncio.create_task(maintain(), name="storage-maintenance")
            app.state.ready = True
            yield
        finally:
            app.state.ready = False
            try:
                if media:
                    await media.close()
                if alerts:
                    await alerts.close()
                if chat:
                    await chat.close()
                if presets:
                    await presets.close()
                if discovery:
                    await discovery.close()
                if manager:
                    await manager.close()
            finally:
                if weather:
                    await weather.close()
                if maintenance:
                    maintenance.cancel()
                    with suppress(asyncio.CancelledError):
                        await maintenance
                try:
                    if journal:
                        with suppress(Exception):
                            await journal.flush()
                    await storage.close()
                finally:
                    instance_lock.close()

    app = FastAPI(title="Stage Monitor", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins,
                       allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
                       allow_headers=["Content-Type", "X-CSRF-Token", "If-Match"])

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(CredentialUnavailable)
    async def credential_error(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={'detail': 'Could not save the password in Windows Credential Manager. Configuration was not saved.'})

    def origin_allowed(origin, request):
        scheme = "https" if request.url.scheme in {"https", "wss"} else "http"
        same_origin = f"{scheme}://{request.url.netloc}"
        return not origin or origin == same_origin or origin in settings.allowed_origins

    def check_origin(request):
        origin = request.headers.get("origin")
        if not origin_allowed(origin, request):
            raise HTTPException(403, "Origin not allowed")

    async def current_user(request: Request):
        session = await request.app.state.auth.session(request.cookies.get("stage_session"))
        if not session:
            raise HTTPException(401, "Authentication required")
        user, csrf = session
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            check_origin(request)
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf):
                raise HTTPException(403, "Invalid CSRF token")
            if request.app.state.backup.pending and request.url.path not in {'/api/backups/cancel','/api/auth/logout'}:
                raise HTTPException(409,'Configuration restore scheduled; restart backend or cancel restore')
        return user

    async def admin(user=Depends(current_user)):
        if "admin" not in user["roles"]:
            raise HTTPException(403, "Admin role required")
        return user

    def visible_device(device_id, user):
        device = app.state.manager.devices.get(device_id)
        if not device or not can_access(user, device_id):
            raise HTTPException(404, "Device not found")
        return device

    register_alerts(app, current_user, admin)
    register_chat(app, current_user)
    register_health(app, current_user, settings)
    register_shows(app, current_user, admin)
    register_presets(app, current_user)
    register_journal(app, current_user, admin)
    register_topology(app, current_user)
    register_discovery(app, admin)
    register_diagnostics(app,current_user,admin)
    register_mapping_presets(app,admin)
    register_backups(app,admin)
    register_media(app, current_user)

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        if not getattr(app.state, "ready", False):
            raise HTTPException(503, "Not ready")
        try:
            await app.state.storage.rows("SELECT 1")
        except Exception:
            raise HTTPException(503, "Storage unavailable") from None
        return {"status": "ready"}

    @app.post("/api/auth/login")
    async def login(data: Login, request: Request, response: Response):
        check_origin(request)
        try:
            token, csrf, user = await app.state.auth.login(data, request.client.host if request.client else "local")
        except PermissionError as exc:
            raise HTTPException(429 if str(exc) == "rate_limited" else 401, str(exc)) from None
        response.set_cookie("stage_session", token, httponly=True, secure=settings.secure_cookies,
                            samesite="lax", max_age=settings.session_seconds, path="/")
        response.headers["Cache-Control"] = "no-store"
        return {"user": user, "csrf_token": csrf}

    @app.get("/api/auth/me")
    async def me(request: Request, response: Response, user=Depends(current_user)):
        _, csrf = await app.state.auth.session(request.cookies.get("stage_session"))
        response.headers["Cache-Control"] = "no-store"
        return {"user": user, "csrf_token": csrf}

    @app.post("/api/auth/logout", status_code=204)
    async def logout(request: Request, response: Response, user=Depends(current_user)):
        await app.state.auth.logout(request.cookies["stage_session"])
        response.delete_cookie("stage_session", path="/")

    @app.get("/api/users")
    async def users(user=Depends(admin)):
        return [public_user(row) for row in await app.state.storage.rows("SELECT * FROM users")]

    @app.post("/api/users", status_code=201)
    async def add_user(data: UserCreate, user=Depends(admin)):
        try:
            return await app.state.auth.create_user(data, user["id"])
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(422, "Invalid roles or duplicate username") from exc

    @app.patch("/api/users/{user_id}")
    async def update_user(user_id: str, data: UserUpdate, user=Depends(admin)):
        try:
            return await app.state.auth.update_user(user_id, data, user["id"])
        except KeyError:
            raise HTTPException(404, "User not found") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/devices")
    async def devices(tag: str | None = Query(None, max_length=80), rack: str | None = Query(None, max_length=120), user=Depends(current_user)):
        return [d.model_copy(update={"dependencies": [link for link in d.dependencies if can_access(user, link.device_id)]}) for d in app.state.manager.devices.values() if can_access(user, d.id)
                and (tag is None or tag.casefold() in {t.casefold() for t in d.tags})
                and (rack is None or rack.casefold() == d.rack.casefold())]

    @app.post("/api/devices", status_code=201)
    async def add_device(data: DeviceInput, user=Depends(admin)):
        device = Device(**data.model_dump())
        try:
            await app.state.manager.save_device(device, user["id"], password=data.password)
        except (ValueError, SchemaError):
            raise HTTPException(422, "Invalid plugin configuration") from None
        return device

    @app.get("/api/devices/{device_id}")
    async def device(device_id: str, user=Depends(current_user)):
        device = visible_device(device_id, user)
        return device.model_copy(update={"dependencies": [link for link in device.dependencies if can_access(user, link.device_id)]})

    @app.put("/api/devices/{device_id}")
    async def replace_device(device_id: str, data: DeviceInput, request: Request, user=Depends(admin)):
        previous = visible_device(device_id, user)
        expected = request.headers.get("if-match")
        if expected is not None and expected.strip('"') != str(previous.config_revision):
            raise Conflict("Device configuration changed; reload before saving")
        device = Device(**data.model_dump(), id=device_id, config_revision=previous.config_revision + 1)
        try:
            await app.state.manager.save_device(device, user["id"], previous, password=data.password)
        except (ValueError, SchemaError):
            raise HTTPException(422, "Invalid plugin configuration") from None
        return device

    @app.delete("/api/devices/{device_id}", status_code=204)
    async def delete_device(device_id: str, request:Request,user=Depends(admin)):
        visible_device(device_id, user)
        await app.state.manager.delete_device(device_id, user["id"],request.headers.get('if-match'))

    @app.get("/api/devices/{device_id}/state")
    async def state(device_id: str, user=Depends(current_user)):
        visible_device(device_id, user)
        return app.state.manager.states[device_id]

    @app.get("/api/devices/{device_id}/capabilities")
    async def capabilities(device_id: str, user=Depends(current_user)):
        visible_device(device_id, user)
        return app.state.manager.descriptions.get(device_id, {}).get("capabilities", [])

    @app.get("/api/devices/{device_id}/commands")
    async def commands(device_id: str, user=Depends(current_user)):
        visible_device(device_id, user)
        return [{**command.model_dump(mode="json"),
                 "can_execute": can_command(user, device_id, command.capability_id)}
                for command in app.state.manager.commands(device_id)]

    @app.post("/api/devices/{device_id}/commands", status_code=202)
    async def command(device_id: str, data: CommandRequest, user=Depends(current_user)):
        visible_device(device_id, user)
        spec = next((c for c in app.state.manager.commands(device_id) if c.name == data.command), None)
        if not spec:
            raise HTTPException(422, "Unsupported command or capabilities not loaded")
        if not can_command(user, device_id, spec.capability_id):
            raise HTTPException(403, "Command permission denied")
        try:
            return await app.state.manager.submit(device_id, user["id"], data)
        except (ValueError, SchemaError):
            raise HTTPException(422, "Invalid command parameters") from None

    @app.get("/api/commands/{command_id}")
    async def command_result(command_id: str, user=Depends(current_user)):
        result = await app.state.storage.get_command(command_id)
        if not result or not can_access(user, result.device_id):
            raise HTTPException(404, "Command not found")
        return result

    @app.get("/api/devices/{device_id}/history/{metric:path}")
    async def history(device_id: str, metric: str, channel: str | None = None,
                      since: AwareDatetime | None = None, until: AwareDatetime | None = None,
                      cursor: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=1000),
                      user=Depends(current_user)):
        visible_device(device_id, user)
        if since and until and since > until:
            raise HTTPException(422, "Invalid time range")
        from datetime import timezone
        return await app.state.storage.history(
            device_id, metric, channel,
            since.astimezone(timezone.utc) if since else None,
            until.astimezone(timezone.utc) if until else None, cursor, limit,
        )

    @app.get("/api/plugins")
    async def plugins(user=Depends(current_user)):
        return app.state.plugins.list()

    @app.post("/api/plugins/{plugin_id}/configuration/{field}", response_model=list[ConfigurationOption])
    async def configuration_options(plugin_id: str, field: str, endpoint: DeviceEndpoint,
                                    user=Depends(admin)):
        try:
            entry = app.state.plugins.get(plugin_id)
        except ValueError:
            raise HTTPException(404, "Plugin unavailable") from None
        descriptor = entry["descriptor"]
        property_schema = descriptor.configuration_schema.get("properties", {}).get(field)
        if not entry["enabled"] or not isinstance(property_schema, dict) or not property_schema.get("x-list-options"):
            raise HTTPException(404, "Configuration options unavailable")
        if (endpoint.transport not in descriptor.supported_protocols or not endpoint.host
                or len(endpoint.host) > 253):
            raise HTTPException(422, "Invalid device endpoint")
        if option_lookup_lock.locked():
            raise HTTPException(429, "Another device lookup is running")

        async with option_lookup_lock:
            device = Device(name=descriptor.model, category="unassigned", plugin_id=plugin_id,
                            endpoint=endpoint)
            supervisor = PluginSupervisor(device, entry, 5, None)
            try:
                await supervisor.start(initialize=False)
                options = await supervisor.rpc("inspect", {"action": f"configuration:{field}"})
                if not isinstance(options, list) or len(options) > 100:
                    raise ValueError("Invalid configuration options")
                return [ConfigurationOption.model_validate(option) for option in options]
            except TimeoutError:
                raise HTTPException(504, "Device lookup timed out") from None
            except (PluginFailure, OSError, ValueError):
                raise HTTPException(502, "Could not load device options") from None
            finally:
                await supervisor.close()

    @app.post("/api/plugins/{plugin_id}/{action}")
    async def toggle_plugin(plugin_id: str, action: str, user=Depends(admin)):
        if action not in {"enable", "disable"}:
            raise HTTPException(404, "Unknown action")
        try:
            await app.state.manager.toggle_plugin(plugin_id, action == "enable")
        except ValueError:
            raise HTTPException(404, "Plugin unavailable") from None
        return {"plugin_id": plugin_id, "enabled": action == "enable"}

    @app.get("/api/system/status")
    async def system_status(user=Depends(current_user)):
        manager = app.state.manager
        visible = [d for d in manager.devices.values() if can_access(user, d.id)]
        result = {
            "status": "degraded" if app.state.storage.last_error or app.state.alerts.last_error or app.state.chat.last_error else "ok",
            "time": now(), "mock_mode": settings.mock_mode,"restore_pending":app.state.backup.pending,
            "alerts": {status: sum(a.status == status and can_access(user, a.device_id)
                                   for a in app.state.alerts.open_alerts.values())
                       for status in ["ACTIVE", "ACKNOWLEDGED"]},
            "alert_engine": "error" if app.state.alerts.last_error else "ok",
            "chat": "error" if app.state.chat.last_error else "ok",
            "devices": {status: sum(manager.monitored(d) and manager.states[d.id].status == status for d in visible)
                        for status in ["online", "offline", "error", "degraded"]},
        }
        if "admin" in user["roles"]:
            result.update({
                "storage": {"status": "error" if app.state.storage.last_error else "ok",
                            "last_error": app.state.storage.last_error},
                "workers": [{"device_id": device_id, "pid": s.process.pid,
                             "alive": s.process.is_alive(), "last_error": s.failed}
                            for device_id, s in manager.supervisors.items()],
                "queues": {device_id: queue.qsize() for device_id, queue in manager.queues.items()},
                "plugin_errors": [p for p in app.state.plugins.list() if p["load_error"]],
            })
        return result

    @app.get("/api/audit")
    async def audit(cursor: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=1000),
                    user=Depends(admin)):
        rows = await app.state.storage.rows("SELECT * FROM audit WHERE id>? ORDER BY id LIMIT ?", (cursor, limit))
        return [{**dict(row), "data": redact(json.loads(row["data"]))} for row in rows]

    @app.get("/api/weather/config")
    async def weather_config(user=Depends(current_user)):
        return app.state.weather.config

    @app.put("/api/weather/config")
    async def configure_weather(data: WeatherConfig, user=Depends(admin)):
        return await app.state.weather.configure(data, user["id"])

    async def weather_data():
        try:
            return await app.state.weather.get()
        except WeatherUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @app.get("/api/weather/current")
    async def weather_current(user=Depends(current_user)):
        data = (await weather_data()).model_dump(mode="json")
        return {key: value for key, value in data.items() if key not in {"hourly", "alerts"}}

    @app.get("/api/weather")
    async def weather_snapshot(user=Depends(current_user)):
        return await weather_data()

    @app.get("/api/weather/forecast")
    async def weather_forecast(user=Depends(current_user)):
        data = (await weather_data()).model_dump(mode="json")
        return {key: value for key, value in data.items() if key not in {"current", "alerts"}}

    @app.get("/api/weather/alerts")
    async def weather_alerts(user=Depends(current_user)):
        data = (await weather_data()).model_dump(mode="json")
        return {key: value for key, value in data.items() if key not in {"current", "hourly"}}

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        origin = ws.headers.get("origin")
        session = await app.state.auth.session(ws.cookies.get("stage_session"))
        if not session or not origin_allowed(origin, ws):
            await ws.close(code=1008)
            return
        user, csrf = session
        await ws.accept()
        subscriber = None
        connection_id = None
        connection_user_id = user['id']
        tasks = []
        try:
            message = await asyncio.wait_for(ws.receive_json(), 5)
            subscription = Subscription.model_validate(message)
            if subscription.type != "subscribe" or not hmac.compare_digest(subscription.csrf_token, csrf):
                await ws.close(code=1008)
                return
            requested = set(subscription.device_ids) if subscription.device_ids is not None else None

            def allowed(device_id):
                return can_access(user, device_id) and (requested is None or device_id in requested)

            if requested and any(not can_access(user, d) or d not in app.state.manager.devices for d in requested):
                await ws.close(code=1008)
                return
            # Register first, then take a snapshot with no await between reads.
            def event_allowed(event):
                # State snapshots already contain metrics. Dashboards can avoid
                # duplicate per-metric bursts from devices with many channels.
                if event.type == 'device.metric' and not subscription.include_metrics:
                    return False
                # Chat privacy takes precedence over device scope and administrator diagnostics.
                if event.type.startswith('chat.'):
                    return app.state.chat.event_visible(event, user)
                return (
                (event.device_id is not None and allowed(event.device_id))
                or (event.device_id is None and event.type in {"weather.update", "show.changed"})
                or (event.device_id is None and "admin" in user["roles"])
                )

            # Multi-channel devices publish hundreds of metrics in one sample.
            # Allow a bounded burst while keeping slow-consumer resynchronization.
            subscriber = app.state.bus.subscribe(event_allowed, queue_size=1024)
            snapshots = [s.model_dump(mode="json") for d, s in app.state.manager.states.items() if allowed(d)]
            await asyncio.wait_for(ws.send_json({"type": "snapshot", "schema_version": 1,
                                                "epoch": app.state.bus.epoch, "states": snapshots}), 5)
            connection_id = new_id()
            await app.state.chat.connect(user, connection_id)
            last_ping = asyncio.get_running_loop().time()
            incoming = asyncio.create_task(ws.receive_json())
            outgoing = asyncio.create_task(subscriber.queue.get())
            overflow = asyncio.create_task(subscriber.overflow.wait())
            tasks = [incoming, outgoing, overflow]
            while True:
                done, _ = await asyncio.wait(tasks, timeout=1, return_when=asyncio.FIRST_COMPLETED)
                session = await app.state.auth.session(ws.cookies.get("stage_session"))
                if not session:
                    await ws.close(code=1008)
                    return
                user, _ = session
                if asyncio.get_running_loop().time() - last_ping > 70:
                    await ws.close(code=1001, reason="Heartbeat timeout")
                    return
                if overflow in done:
                    await ws.close(code=1013, reason="Slow consumer; reconnect and resynchronize")
                    return
                if incoming in done:
                    data = incoming.result()
                    if not isinstance(data, dict) or data.get("type") != "ping":
                        await ws.close(code=1008, reason="Only ping is accepted after subscription")
                        return
                    await ws.send_json({"type": "pong"})
                    last_ping = asyncio.get_running_loop().time()
                    incoming = asyncio.create_task(ws.receive_json())
                    tasks = [incoming, outgoing, overflow]
                if outgoing in done:
                    event = outgoing.result()
                    if event_allowed(event):
                        await asyncio.wait_for(ws.send_json(event.model_dump(mode="json")), 5)
                    outgoing = asyncio.create_task(subscriber.queue.get())
                tasks = [incoming, outgoing, overflow]
        except WebSocketDisconnect:
            pass
        except (TimeoutError, ValueError, TypeError):
            with suppress(RuntimeError):
                await ws.close(code=1008)
        finally:
            # Finish presence persistence and task cleanup even when the socket scope is cancelled.
            with CancelScope(shield=True):
                if subscriber:
                    app.state.bus.unsubscribe(subscriber)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if connection_id:
                    try:
                        await app.state.chat.disconnect(connection_user_id, connection_id)
                    except Exception:
                        log.exception('Chat presence cleanup failed')

    # Explicit asset allowlist: never expose backend sources, configuration or SQLite.
    from pathlib import Path
    from fastapi.responses import FileResponse
    frontend = Path(__file__).resolve().parents[3]
    register_frontend_themes(app, frontend)
    register_frontend_languages(app, frontend)

    @app.get("/", include_in_schema=False)
    async def dashboard():
        if not (frontend / "index.html").is_file():
            raise HTTPException(404, "Frontend not installed")
        return FileResponse(frontend / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/{asset}", include_in_schema=False)
    async def frontend_asset(asset: str):
        if asset not in {"i18n.js", "bootstrap.js", "CascadiaCode.ttf", "app.js", "themes.js", "styles.css", "live.js", "live.css", "devices-ui.js", "weather-ui.js", "alerts-ui.js", "chat-ui.js", "health-ui.js", "shows-ui.js", "history-ui.js", "presets-ui.js", "device-tools.js", "discovery-ui.js", "service-ui.js", "media-ui.js", "osc-ui.js", "mapping-editor.js", "meter-ui.js"} or not (frontend / asset).is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(frontend / asset, media_type="font/ttf" if asset == "CascadiaCode.ttf" else None,
                            headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
