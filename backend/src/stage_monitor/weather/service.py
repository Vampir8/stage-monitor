import asyncio
import logging
from contextlib import suppress
from datetime import timedelta

import httpx

from ..contracts import now
from ..device_manager import Conflict
from .models import WeatherAlert, WeatherConfig, WeatherSnapshot
from .providers import OpenMeteoProvider

log = logging.getLogger(__name__)


class WeatherUnavailable(Exception):
    pass


class WeatherService:
    def __init__(self, storage, bus, interval=300, client=None):
        self.storage, self.bus, self.interval = storage, bus, interval
        self.client = client or httpx.AsyncClient(timeout=5, follow_redirects=False)
        self.providers = {"open_meteo": OpenMeteoProvider(self.client)}
        self.config = WeatherConfig()
        self.snapshot = None
        self.next_refresh = 0
        self.lock = asyncio.Lock()
        self.task = None
        self.show_service = None

    async def start(self):
        rows = await self.storage.rows("SELECT value FROM metadata WHERE key='weather_config'")
        if rows:
            self.config = WeatherConfig.model_validate_json(rows[0][0])
        self.task = asyncio.create_task(self.run(), name="weather-service")

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        await self.client.aclose()

    async def configure(self, config, actor_id):
        if self.show_service is not None:
            return await self.show_service.configure_weather(config, actor_id)
        async with self.lock:
            if config.revision != self.config.revision:
                raise Conflict("Weather configuration changed; reload before saving")
            previous = self.config
            updated = config.model_copy(update={"revision": previous.revision + 1})
            async with self.storage.lock:
                try:
                    await self.storage.db.execute(
                        "INSERT INTO metadata VALUES ('weather_config',?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (updated.model_dump_json(),))
                    await self.storage._audit(actor_id, "weather.configure", "active_event", {
                        "previous": previous.model_dump(mode="json"), "new": updated.model_dump(mode="json"),
                    })
                    await self.storage.db.commit()
                except BaseException:
                    await self.storage.db.rollback()
                    raise
            self.config = updated
            self.snapshot = None
            self.next_refresh = 0
            self.bus.publish(self.bus.event("weather.update", {"config_revision": updated.revision}))
            return updated

    async def get(self):
        async with self.lock:
            clock = asyncio.get_running_loop().time()
            if clock >= self.next_refresh:
                self.next_refresh = clock + self.interval
                try:
                    current, hourly = await self.providers[self.config.provider].fetch(self.config)
                    stamp = now()
                    self.snapshot = WeatherSnapshot(
                        config=self.config, fetched_at=stamp, simulation=False,
                        source_url="https://open-meteo.com/" if self.config.provider == "open_meteo" else None,
                        current=current, hourly=hourly,
                        stale=(stamp - current.time).total_seconds() > 1800,
                        alerts=self.alerts([current, *hourly]),
                    )
                except Exception:
                    log.exception("Weather provider failed")
                    if self.snapshot:
                        self.snapshot = self.snapshot.model_copy(update={
                            "stale": True, "last_error": 'Weather source unavailable; showing cached data',
                        })
                if self.snapshot:
                    self.bus.publish(self.bus.event("weather.update", self.snapshot.model_dump(mode="json")))
            if self.snapshot is None:
                raise WeatherUnavailable('Weather source unavailable; no data for this location')
            result = self.snapshot.model_copy(deep=True)
            if (now() - result.fetched_at).total_seconds() > max(self.interval * 2, 1800):
                result.stale = True
            return result

    def alerts(self, samples):
        result = []
        rules = [
            ("gusts", lambda s: s.gusts is not None and s.gusts >= self.config.gust_threshold,
             "warning", f"Wind gusts ≥ {self.config.gust_threshold:g} m/s"),
            ("rain", lambda s: s.precipitation is not None and s.precipitation >= self.config.rain_threshold,
             "warning", f"Rain ≥ {self.config.rain_threshold:g} mm in the model period"),
            ("storm", lambda s: s.weather_code in {95, 96, 99}, "critical", 'The weather model indicates a thunderstorm'),
        ]
        for key, predicate, severity, message in rules:
            matches = [s for s in samples if predicate(s) and s.time + timedelta(hours=1) > now()]
            if matches:
                result.append(WeatherAlert(id=f"{self.config.revision}:{key}", severity=severity,
                                           message=message, starts_at=min(s.time for s in matches),
                                           ends_at=max(s.time for s in matches) + timedelta(hours=1)))
        return result

    async def run(self):
        while True:
            try:
                await self.get()
            except Exception:
                log.warning("Weather refresh unavailable; device monitoring continues")
            await asyncio.sleep(min(self.interval, 10))
