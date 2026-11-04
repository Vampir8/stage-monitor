from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, field_validator

from ..contracts import Model


class WeatherConfig(Model):
    event_name: str = Field(default='Demo show', min_length=1, max_length=120)
    location_name: str = Field(default='Warsaw — example location', min_length=1, max_length=120)
    latitude: float = Field(default=52.2297, ge=-90, le=90)
    longitude: float = Field(default=21.0122, ge=-180, le=180)
    timezone: str = "Europe/Warsaw"
    provider: Literal["open_meteo"] = "open_meteo"
    gust_threshold: float = Field(default=15, gt=0, le=100)
    rain_threshold: float = Field(default=5, gt=0, le=1000)
    revision: int = Field(default=1, ge=1)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Unknown IANA timezone") from None
        return value


class Conditions(Model):
    time: AwareDatetime
    temperature: float | None = None
    feels_like: float | None = None
    humidity: float | None = Field(default=None, ge=0, le=100)
    pressure: float | None = None
    wind: float | None = Field(default=None, ge=0)
    gusts: float | None = Field(default=None, ge=0)
    wind_direction: float | None = Field(default=None, ge=0, le=360)
    precipitation: float | None = Field(default=None, ge=0)
    weather_code: int | None = None


class WeatherAlert(Model):
    id: str
    severity: Literal["warning", "critical"]
    message: str
    source: Literal["local_rule"] = "local_rule"
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class WeatherSnapshot(Model):
    config: WeatherConfig
    fetched_at: AwareDatetime
    simulation: bool
    stale: bool = False
    last_error: str | None = None
    source_url: str | None = None
    official_warnings_available: bool = False
    units: dict[str, str] = Field(default_factory=lambda: {
        "temperature": "°C", "feels_like": "°C", "humidity": "%", "pressure": "hPa",
        "wind": "m/s", "gusts": "m/s", "wind_direction": "°", "precipitation": "mm",
    })
    current: Conditions
    hourly: list[Conditions]
    alerts: list[WeatherAlert] = Field(default_factory=list)
