from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx

from ..contracts import now
from .models import Conditions, WeatherConfig


class WeatherProvider(Protocol):
    async def fetch(self, config: WeatherConfig) -> tuple[Conditions, list[Conditions]]: ...




class OpenMeteoProvider:
    fields = {
        "temperature": "temperature_2m", "feels_like": "apparent_temperature",
        "humidity": "relative_humidity_2m", "pressure": "pressure_msl",
        "wind": "wind_speed_10m", "gusts": "wind_gusts_10m",
        "wind_direction": "wind_direction_10m", "precipitation": "precipitation",
        "weather_code": "weather_code",
    }

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def fetch(self, config):
        variables = ",".join(self.fields.values())
        response = await self.client.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": config.latitude, "longitude": config.longitude,
            "current": variables, "hourly": variables, "forecast_hours": 24,
            "timezone": "GMT", "timeformat": "unixtime", "wind_speed_unit": "ms",
            "temperature_unit": "celsius", "precipitation_unit": "mm",
        })
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise ValueError("Weather response too large")
        data = response.json()

        def sample(section, index=None):
            def value(key):
                result = section.get(key)
                if index is not None:
                    return result[index] if isinstance(result, list) and index < len(result) else None
                return result
            stamp = value("time")
            if not isinstance(stamp, (float, int)) or isinstance(stamp, bool):
                raise ValueError("Missing weather timestamp")
            return Conditions(time=datetime.fromtimestamp(stamp, timezone.utc),
                              **{key: value(field) for key, field in self.fields.items()})

        current = sample(data["current"])
        hourly = data["hourly"]
        times = hourly["time"]
        if not isinstance(times, list) or not times:
            raise ValueError("Missing hourly forecast")
        return current, [sample(hourly, i) for i in range(min(len(times), 24))]
