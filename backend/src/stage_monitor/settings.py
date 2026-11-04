import os
from pathlib import Path

from pydantic import Field

from .contracts import Model


class Settings(Model):
    data_dir: Path = Field(default_factory=lambda: Path(os.getenv("STAGE_DATA_DIR", "data")))
    plugin_dirs: list[Path] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8080"])
    session_seconds: int = 28800
    secure_cookies: bool = False
    mock_mode: bool = False
    weather_refresh_seconds: float = Field(default=300, ge=10, le=3600)
    history_sample_seconds: float = Field(default=5, ge=0)
    alert_retention_days: int = Field(default=90, ge=1)
    chat_retention_days: int = Field(default=90, ge=1)
    history_retention_days: int = Field(default=14, ge=1)
    timeline_retention_days: int = Field(default=90, ge=1)
    history_overrides: dict[str, float] = Field(default_factory=dict)
    rpc_timeout_seconds: float = Field(default=5, gt=0)
    health_interval_seconds: float = Field(default=5, ge=0.1)
    reconnect_base_seconds: float = Field(default=1, gt=0)
    reconnect_max_seconds: float = Field(default=60, gt=0)
    bootstrap_username: str = "admin"
    bootstrap_password: str | None = Field(
        default_factory=lambda: os.getenv("STAGE_ADMIN_PASSWORD"), exclude=True
    )

    @classmethod
    def from_environment(cls):
        import yaml
        path = os.getenv("STAGE_CONFIG")
        values = yaml.safe_load(Path(path).read_text(encoding="utf-8")) if path else {}
        values = values or {}
        if "STAGE_DATA_DIR" in os.environ:
            values["data_dir"] = os.environ["STAGE_DATA_DIR"]
        return cls.model_validate(values)
