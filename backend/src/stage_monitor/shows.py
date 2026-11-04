"""Persistent show profiles, independent of device protocols and permissions."""
import asyncio
import json

from anyio import CancelScope
from fastapi import Depends, HTTPException
from pydantic import Field, field_validator

from .auth import can_access
from .contracts import Model, new_id
from .device_manager import Conflict
from .weather.models import WeatherConfig


class ShowInput(Model):
    name: str = Field(min_length=1, max_length=120)
    device_ids: list[str] = Field(default_factory=list, max_length=500)
    weather: WeatherConfig = Field(default_factory=WeatherConfig)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Show name cannot be blank")
        return value.strip()

    @field_validator("device_ids")
    @classmethod
    def unique_devices(cls, value):
        return list(dict.fromkeys(value))


class Show(ShowInput):
    id: str = Field(default_factory=new_id)
    revision: int = Field(default=1, ge=1)


class ShowUpdate(ShowInput):
    revision: int = Field(ge=1)


class Activation(Model):
    show_id: str | None = None
    expected_revision: int = Field(ge=0)
    profile_revision: int | None = Field(default=None, ge=1)


class ShowService:
    def __init__(self, storage, bus, manager, weather):
        self.storage, self.bus, self.manager, self.weather = storage, bus, manager, weather
        self.lock = asyncio.Lock()
        self.profiles = {}
        self.active_id = None
        self.revision = 0

    async def start(self):
        rows = await self.storage.rows("SELECT value FROM metadata WHERE key='show_profiles'")
        if rows:
            saved = json.loads(rows[0][0])
            self.profiles = {p["id"]: Show.model_validate(p) for p in saved["profiles"]}
            self.active_id, self.revision = saved["active_id"], saved["revision"]
        self.weather.show_service = self

    def get(self, show_id):
        profile = self.profiles.get(show_id)
        if profile is None:
            raise HTTPException(404, "Show profile not found")
        return profile

    def visible(self, profile, user):
        return profile.model_copy(update={"device_ids": [d for d in profile.device_ids if can_access(user, d)]})

    def active(self, user):
        profile = self.profiles.get(self.active_id)
        return {"revision": self.revision, "profile": self.visible(profile, user) if profile else None}

    def validate_devices(self, profile):
        if any(d not in self.manager.devices for d in profile.device_ids):
            raise HTTPException(422, "Show references a missing device; reload the inventory")

    async def persist(self, profiles, active_id, actor_id, action, target_id, previous, updated, weather=None):
        revision = self.revision + 1
        saved = json.dumps({"profiles": [p.model_dump(mode="json") for p in profiles.values()],
                            "active_id": active_id, "revision": revision})
        event = self.bus.event("show.changed", {"active_id": active_id, "revision": revision}, actor_id=actor_id)
        # Cancellation must not leave a committed profile with old in-memory weather/selection.
        with CancelScope(shield=True):
            async with self.storage.lock:
                try:
                    await self.storage.db.execute(
                        "INSERT INTO metadata VALUES ('show_profiles',?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (saved,))
                    if weather is not None:
                        await self.storage.db.execute(
                            "INSERT INTO metadata VALUES ('weather_config',?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (weather.model_dump_json(),))
                    await self.storage._audit(actor_id, action, target_id, {"previous": previous, "new": updated})
                    await self.storage.db.execute("INSERT INTO outbox(id,data) VALUES (?,?)", (event.id, event.model_dump_json()))
                    await self.storage.db.commit()
                except BaseException:
                    await self.storage.db.rollback()
                    raise
            self.profiles, self.active_id, self.revision = profiles, active_id, revision
            profile = profiles.get(active_id)
            scope = set(profile.device_ids) if profile else None
            if scope != self.manager.show_device_ids:
                await self.manager.set_show_scope(scope)
            if weather is not None:
                self.weather.config = weather
                self.weather.snapshot = None
                self.weather.next_refresh = 0
                self.bus.publish(self.bus.event("weather.update", {"config_revision": weather.revision}))

    async def save(self, data, actor_id, show_id=None):
        async with self.lock, self.manager.config_lock, self.weather.lock:
            previous = self.get(show_id) if show_id else None
            if previous and data.revision != previous.revision:
                raise Conflict("Show profile changed; reload before saving")
            if not previous and len(self.profiles) >= 100:
                raise Conflict("Show profile limit reached")
            config = data.weather.model_copy(update={"event_name": data.name})
            profile = Show(name=data.name, device_ids=data.device_ids, weather=config,
                           id=show_id or new_id(), revision=previous.revision + 1 if previous else 1)
            self.validate_devices(profile)
            profiles = {**self.profiles, profile.id: profile}
            weather = None
            if profile.id == self.active_id:
                weather = config.model_copy(update={"revision": self.weather.config.revision + 1})
            await self.persist(profiles, self.active_id, actor_id, "show.configure", profile.id,
                               previous.model_dump(mode="json") if previous else None,
                               profile.model_dump(mode="json"), weather)
            return profile

    async def activate(self, data, actor_id):
        async with self.lock, self.manager.config_lock, self.weather.lock:
            if data.expected_revision != self.revision:
                raise Conflict("Active show changed; reload before switching")
            profile = self.get(data.show_id) if data.show_id else None
            if profile and data.profile_revision != profile.revision:
                raise Conflict("Show profile changed; reload before switching")
            if profile:
                self.validate_devices(profile)
            weather = profile.weather.model_copy(update={"revision": self.weather.config.revision + 1}) if profile else None
            before = {"active_id": self.active_id, "weather": self.weather.config.model_dump(mode="json")}
            after = {"active_id": data.show_id, "weather": weather.model_dump(mode="json") if weather else None}
            await self.persist(dict(self.profiles), data.show_id, actor_id, "show.activate", data.show_id or "inventory", before, after, weather)

    async def configure_weather(self, config, actor_id):
        """Existing weather editor updates the active profile in the same transaction."""
        async with self.lock, self.weather.lock:
            if config.revision != self.weather.config.revision:
                raise Conflict("Weather configuration changed; reload before saving")
            weather = config.model_copy(update={"revision": self.weather.config.revision + 1})
            profiles = dict(self.profiles)
            profile = profiles.get(self.active_id)
            if profile:
                if not config.event_name.strip():
                    raise HTTPException(422, "Show name cannot be blank")
                profiles[profile.id] = profile.model_copy(update={
                    "name": config.event_name.strip(), "weather": config, "revision": profile.revision + 1,
                })
            await self.persist(profiles, self.active_id, actor_id, "weather.configure", self.active_id or "inventory",
                               self.weather.config.model_dump(mode="json"), weather.model_dump(mode="json"), weather)
            return weather


def register(app, current_user, admin):
    @app.get("/api/shows/active")
    async def active_show(user=Depends(current_user)):
        return app.state.shows.active(user)

    @app.post("/api/shows/active")
    async def activate_show(data: Activation, user=Depends(admin)):
        await app.state.shows.activate(data, user["id"])
        return app.state.shows.active(user)

    @app.get("/api/shows")
    async def shows(user=Depends(current_user)):
        return [app.state.shows.visible(p, user) for p in app.state.shows.profiles.values()]

    @app.post("/api/shows", status_code=201, response_model=Show)
    async def create_show(data: ShowInput, user=Depends(admin)):
        return await app.state.shows.save(data, user["id"])

    @app.get("/api/shows/{show_id}", response_model=Show)
    async def show(show_id: str, user=Depends(current_user)):
        return app.state.shows.visible(app.state.shows.get(show_id), user)

    @app.put("/api/shows/{show_id}", response_model=Show)
    async def update_show(show_id: str, data: ShowUpdate, user=Depends(admin)):
        return await app.state.shows.save(data, user["id"], show_id)
