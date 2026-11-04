"""Versioned contracts shared by the core, API and isolated plugin workers."""
from datetime import datetime, timezone
from enum import StrEnum
from ipaddress import IPv4Address, ip_network
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class DeviceStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"
    DEGRADED = "degraded"


class ConnectionStatus(StrEnum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class DeviceEndpoint(Model):
    transport: str
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    serial_port: str | None = None


class DeviceDependency(Model):
    device_id: str = Field(min_length=1, max_length=120)
    port: str = Field(default="", max_length=120)
    metric_key: str | None = Field(default=None, min_length=1, max_length=120)
    channel: str | None = Field(default=None, max_length=120)


class DashboardLayout(Model):
    collapsed: list[str] = Field(default_factory=list, max_length=64)
    expanded: list[str] = Field(default_factory=list, max_length=64)
    history_minutes: int = Field(default=10, ge=1, le=10)

    @field_validator('collapsed', 'expanded')
    @classmethod
    def validate_features(cls, values):
        if len(values) != len(set(values)) or any(not value or len(value) > 160 for value in values):
            raise ValueError('Dashboard feature IDs must be unique and bounded')
        return values


class DeviceCreate(Model):
    name: str = Field(min_length=1, max_length=120)
    plugin_id: str
    category: str = Field(min_length=1, max_length=80)
    endpoint: DeviceEndpoint | None = None
    enabled: bool = True
    tags: list[str] = Field(default_factory=list, max_length=50)
    location: str = Field(default="", max_length=120)
    rack: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=10000)
    dependencies: list[DeviceDependency] = Field(default_factory=list, max_length=16)
    poll_interval: float = Field(default=2, ge=0.1, le=3600)
    stale_after: float = Field(default=10, ge=0.2, le=86400)
    custom_config: dict[str, JsonValue] = Field(default_factory=dict)
    dashboard_layout: DashboardLayout | None = None
    credential_ref: str | None = None
    site_id: str | None = None
    show_ids: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values):
        result, seen = [], set()
        for value in values:
            value = value.strip()
            if not value or len(value) > 80:
                raise ValueError("Tags must contain 1 to 80 characters")
            if value.casefold() not in seen:
                result.append(value)
                seen.add(value.casefold())
        return result

    @model_validator(mode="after")
    def validate_intervals(self):
        if self.stale_after <= self.poll_interval:
            raise ValueError("stale_after must exceed poll_interval")
        return self


class Device(DeviceCreate):
    id: str = Field(default_factory=new_id)
    config_revision: int = Field(default=1, ge=1)


class DeviceError(Model):
    code: str
    message: str
    occurred_at: AwareDatetime = Field(default_factory=now)
    retryable: bool = True


class DeviceHealth(Model):
    last_seen: AwareDatetime | None = None
    last_error: DeviceError | None = None
    reconnect_attempts: int = 0
    next_retry_at: AwareDatetime | None = None
    latency_ms: float | None = None


class DeviceIdentity(Model):
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None
    asset_id: str | None = None


class DeviceNetwork(Model):
    host: str | None = None
    port: int | None = None
    mac: str | None = None
    transport: str | None = None


class Metric(Model):
    device_id: str
    key: str = Field(min_length=1, max_length=120)
    channel: str | None = None
    value: float | int | bool | str | None
    unit: str | None = None
    observed_at: AwareDatetime = Field(default_factory=now)
    received_at: AwareDatetime = Field(default_factory=now)
    quality: Literal["good", "stale", "invalid", "unknown"] = "good"
    labels: dict[str, str] = Field(default_factory=dict)


class DeviceState(Model):
    device_id: str
    status: DeviceStatus = DeviceStatus.OFFLINE
    connection_status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    revision: int = 0
    observed_at: AwareDatetime | None = None
    updated_at: AwareDatetime = Field(default_factory=now)
    identity: DeviceIdentity = Field(default_factory=DeviceIdentity)
    network: DeviceNetwork = Field(default_factory=DeviceNetwork)
    health: DeviceHealth = Field(default_factory=DeviceHealth)
    metrics: list[Metric] = Field(default_factory=list)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    extra: dict[str, JsonValue] = Field(default_factory=dict)


class Capability(Model):
    id: str
    version: str = "1.0"
    readable: bool = True
    writable: bool = False
    parameters_schema: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Command(Model):
    name: str
    capability_id: str
    parameters_schema: dict[str, JsonValue]
    timeout_seconds: float = Field(default=5, gt=0, le=60)
    idempotent: bool = False
    confirmation_required: bool = False
    snapshot_parameters: dict[str, str] = Field(default_factory=dict)


class CommandRequest(Model):
    command: str
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)
    expected_state_revision: int | None = Field(default=None, ge=0)
    expected_config_revision: int | None = Field(default=None, ge=1)
    expected_parameters: dict[str, JsonValue] | None = None


class CommandResult(Model):
    command_id: str = Field(default_factory=new_id)
    device_id: str
    actor_id: str
    request: CommandRequest
    status: Literal[
        "queued", "running", "succeeded", "failed", "timed_out", "unknown", "cancelled"
    ] = "queued"
    requested_at: AwareDatetime = Field(default_factory=now)
    completed_at: AwareDatetime | None = None
    confirmed: bool = False
    result: dict[str, JsonValue] = Field(default_factory=dict)
    error: DeviceError | None = None


class PluginCommandResult(Model):
    confirmed: bool = False
    result: dict[str, JsonValue] = Field(default_factory=dict)


class HealthCheckResult(Model):
    reachable: bool
    degraded: bool = False
    error: DeviceError | None = None


class Event(Model):
    id: str = Field(default_factory=new_id)
    type: str
    schema_version: int = 1
    occurred_at: AwareDatetime = Field(default_factory=now)
    received_at: AwareDatetime = Field(default_factory=now)
    source: str
    device_id: str | None = None
    actor_id: str | None = None
    correlation_id: str | None = None
    stream_id: str
    sequence: int = 0
    payload: dict[str, JsonValue]


class PluginDescriptor(Model):
    plugin_id: str
    plugin_version: str
    plugin_api_version: str
    entrypoint: str
    manufacturer: str
    model: str
    supported_protocols: list[str]
    communication_mode: Literal["polling", "push", "hybrid"] = "polling"
    capabilities: list[Capability]
    commands: list[Command]
    telemetry: list[dict[str, JsonValue]]
    configuration_schema: dict[str, JsonValue]
    dashboard: dict[str, JsonValue] = Field(default_factory=dict)
    shared_udp_port: int | None = Field(default=None, ge=1, le=65535)
    shared_udp_match_host: bool = False
    discovery: "DiscoverySupport | None" = None


class DiscoverySupport(Model):
    methods: list[str] = Field(min_length=1, max_length=8)
    simulation: bool = False


class DiscoveryScope(Model):
    subnet: str
    timeout_seconds: float = Field(default=10, ge=2, le=30)
    max_results: int = Field(default=50, ge=1, le=100)

    @field_validator("subnet")
    @classmethod
    def subnet_scope(cls, value):
        network = ip_network(value, strict=False)
        private = [ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
        if network.version != 4 or network.num_addresses > 256 or not any(network.subnet_of(p) for p in private):
            raise ValueError("Use a private IPv4 subnet with at most 256 addresses")
        return str(network)


class DiscoveryCandidate(Model):
    host: IPv4Address
    port: int | None = Field(default=None, ge=1, le=65535)
    transport: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    manufacturer: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=120)
    serial: str | None = Field(default=None, max_length=120)
    custom_config: dict[str, JsonValue] = Field(default_factory=dict)


PluginDescriptor.model_rebuild()
