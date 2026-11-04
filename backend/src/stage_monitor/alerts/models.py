from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from ..contracts import Model, new_id, now


class RuleInput(Model):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    severity: Literal["info", "warning", "critical"] = "warning"
    source: Literal["metric", "status", "parameter"] = "metric"
    key: str = Field(default="battery.percent", min_length=1, max_length=120)
    device_ids: list[str] = Field(default_factory=list, max_length=500)
    categories: list[str] = Field(default_factory=list, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=100)
    channel: str | None = Field(default=None, max_length=120)
    operator: Literal["lt", "le", "gt", "ge", "eq", "ne"] = "lt"
    threshold: float | int | str | bool = 20
    hysteresis: float = Field(default=0, ge=0, le=1000000)
    hold_seconds: float = Field(default=3, ge=0, le=3600)
    clear_seconds: float = Field(default=3, ge=0, le=3600)

    @model_validator(mode="after")
    def check_condition(self):
        if self.operator in {"lt", "le", "gt", "ge"} and (
            isinstance(self.threshold, bool) or not isinstance(self.threshold, (float, int))
        ):
            raise ValueError("Numeric comparisons require a numeric threshold")
        if self.hysteresis and self.operator in {"eq", "ne"}:
            raise ValueError("Hysteresis applies only to numeric comparisons")
        return self


class Rule(RuleInput):
    id: str = Field(default_factory=new_id)
    revision: int = Field(default=1, ge=1)


class RuleUpdate(RuleInput):
    revision: int = Field(ge=1)


class Acknowledge(Model):
    revision: int = Field(ge=1)
    comment: str = Field(default="", max_length=2000)


class Alert(Model):
    id: str = Field(default_factory=new_id)
    rule_id: str
    rule_revision: int
    rule_name: str
    device_id: str
    device_name: str
    key: str
    channel: str | None = None
    severity: Literal["info", "warning", "critical"]
    status: Literal["ACTIVE", "ACKNOWLEDGED", "RESOLVED"] = "ACTIVE"
    value: float | str | bool | None = None
    unit: str | None = None
    opened_at: AwareDatetime = Field(default_factory=now)
    acknowledged_at: AwareDatetime | None = None
    acknowledged_by: str | None = None
    resolved_at: AwareDatetime | None = None
    resolution_reason: str | None = None
    revision: int = 1
