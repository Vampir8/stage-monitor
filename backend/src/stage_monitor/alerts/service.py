import asyncio
import json
import logging
from contextlib import suppress

from ..contracts import now
from ..device_manager import Conflict
from .models import Alert, Rule

log = logging.getLogger(__name__)


class AlertService:
    """Reconcile authoritative state; device plugins never depend on this service."""

    def __init__(self, storage, bus, manager):
        self.storage, self.bus, self.manager = storage, bus, manager
        self.rules = {}
        self.open_alerts = {}
        self.pending = {}
        self.lock = asyncio.Lock()
        self.task = None
        self.last_error = None

    async def start(self):
        self.rules = {
            r.id: r
            for r in [
                Rule.model_validate_json(row["data"])
                for row in await self.storage.rows("SELECT data FROM alert_rules")
            ]
        }
        self.open_alerts = {
            (a.rule_id, a.device_id, a.channel or ""): a
            for a in [
                Alert.model_validate_json(row["data"])
                for row in await self.storage.rows(
                    "SELECT data FROM alerts WHERE status!='RESOLVED'"
                )
            ]
        }
        if not await self.storage.rows("SELECT 1 FROM metadata WHERE key='alerts_seeded'"):
            defaults = [
                Rule(
                    id="builtin.offline",
                    name='Device offline',
                    source="status",
                    key="status",
                    operator="eq",
                    threshold="offline",
                    severity="critical",
                    hold_seconds=10,
                ),
                Rule(
                    id="builtin.error",
                    name='Device error',
                    source="status",
                    key="status",
                    operator="eq",
                    threshold="error",
                    severity="critical",
                    hold_seconds=5,
                ),
                Rule(
                    id="builtin.battery",
                    name='Low battery',
                    key="battery.percent",
                    threshold=20,
                    hysteresis=5,
                ),
            ]
            for rule in defaults:
                if rule.id not in self.rules:
                    await self.configure(rule, "system")
            await self.storage.write("INSERT INTO metadata VALUES ('alerts_seeded','true')")
        self.task = asyncio.create_task(self.run(), name="alert-reconciliation")

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def run(self):
        while True:
            try:
                await self.evaluate()
                self.last_error = None
            except Exception:
                self.last_error = "Alert evaluation failed"
                log.exception("Alert evaluation failed; retrying")
            await asyncio.sleep(0.5)

    async def configure(self, rule, actor):
        async with self.lock:
            old = self.rules.get(rule.id)
            if old and rule.revision != old.revision:
                raise Conflict("Rule changed; reload before editing")
            saved = rule.model_copy(update={"revision": old.revision + 1 if old else 1})
            async with self.storage.lock:
                try:
                    await self.storage.db.execute(
                        "INSERT INTO alert_rules VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                        (saved.id, saved.model_dump_json()),
                    )
                    await self.storage._audit(
                        actor,
                        "alert.rule.configure",
                        saved.id,
                        {
                            "previous": old.model_dump(mode="json") if old else None,
                            "new": saved.model_dump(mode="json"),
                        },
                    )
                    await self.storage.db.commit()
                except BaseException:
                    await self.storage.db.rollback()
                    raise
            self.rules[saved.id] = saved
            # Restart reconciliation also handles a crash between configuration and resolution.
            for key, alert in list(self.open_alerts.items()):
                if key[0] == saved.id:
                    await self.resolve(
                        key, alert, "rule_changed" if saved.enabled else "rule_disabled"
                    )
            self.pending = {k: v for k, v in self.pending.items() if k[0] != saved.id}
            return saved

    async def persist(self, alert, action, actor="system", comment="", actor_name=None):
        event = self.bus.event(
            "alert." + action,
            {"alert": alert.model_dump(mode="json")},
            device_id=alert.device_id,
            actor_id=actor,
        )
        entry = {
            "action": action,
            "actor_id": actor,
            "actor_name": actor_name or actor,
            "resolution_reason": alert.resolution_reason,
            "comment": comment,
            "status": alert.status,
            "revision": alert.revision,
        }
        async with self.storage.lock:
            try:
                await self.storage.db.execute(
                    "INSERT INTO alerts VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "status=excluded.status,data=excluded.data",
                    (
                        alert.id,
                        alert.rule_id,
                        alert.device_id,
                        alert.channel or "",
                        alert.status,
                        alert.opened_at.isoformat(),
                        alert.model_dump_json(),
                    ),
                )
                await self.storage.db.execute(
                    "INSERT INTO alert_history(alert_id,occurred_at,data) VALUES (?,?,?)",
                    (alert.id, now().isoformat(), json.dumps(entry)),
                )
                await self.storage.db.execute(
                    "INSERT INTO outbox(id,data) VALUES (?,?)", (event.id, event.model_dump_json())
                )
                await self.storage._audit(actor, "alert." + action, alert.id, entry)
                await self.storage.db.commit()
            except BaseException:
                await self.storage.db.rollback()
                raise

    async def resolve(self, key, alert, reason):
        updated = alert.model_copy(
            update={
                "status": "RESOLVED",
                "resolved_at": now(),
                "resolution_reason": reason,
                "revision": alert.revision + 1,
            }
        )
        await self.persist(updated, "resolved")
        self.open_alerts.pop(key, None)
        self.pending.pop(key, None)

    async def acknowledge(self, alert_id, revision, actor, comment, actor_name=None):
        async with self.lock:
            match = next(((k, a) for k, a in self.open_alerts.items() if a.id == alert_id), None)
            if not match:
                raise Conflict("Alert already resolved")
            key, alert = match
            if alert.revision != revision or alert.status != "ACTIVE":
                raise Conflict("Alert changed; reload before acknowledging")
            updated = alert.model_copy(
                update={
                    "status": "ACKNOWLEDGED",
                    "acknowledged_at": now(),
                    "acknowledged_by": actor,
                    "revision": alert.revision + 1,
                }
            )
            await self.persist(updated, "acknowledged", actor, comment, actor_name)
            self.open_alerts[key] = updated
            return updated

    @staticmethod
    def matches(rule, device):
        return (
            (not rule.device_ids or device.id in rule.device_ids)
            and (not rule.categories or device.category in rule.categories)
            and (not rule.tags or set(rule.tags).issubset(device.tags))
        )

    @staticmethod
    def breached(rule, value, active=False):
        if not isinstance(value, (int, float, str, bool)):
            return None
        threshold = rule.threshold
        if rule.operator in {"lt", "le", "gt", "ge"}:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            if active:
                threshold += rule.hysteresis if rule.operator in {"lt", "le"} else -rule.hysteresis
            return {
                "lt": value < threshold,
                "le": value <= threshold,
                "gt": value > threshold,
                "ge": value >= threshold,
            }[rule.operator]
        # Avoid treating False as the numeric value 0.
        equal = type(value) is type(threshold) and value == threshold
        if (
            not isinstance(value, bool)
            and not isinstance(threshold, bool)
            and isinstance(value, (int, float))
            and isinstance(threshold, (int, float))
        ):
            equal = value == threshold
        return equal if rule.operator == "eq" else not equal

    async def evaluate(self, clock=None):
        clock = asyncio.get_running_loop().time() if clock is None else clock
        async with self.lock:
            for key, alert in list(self.open_alerts.items()):
                rule, device = self.rules.get(key[0]), self.manager.devices.get(key[1])
                reason = None
                if not rule or not rule.enabled:
                    reason = "rule_disabled"
                elif rule.revision != alert.rule_revision:
                    reason = "rule_changed"
                elif not device or not device.enabled:
                    reason = "device_disabled_or_removed"
                elif not getattr(self.manager, 'monitored', lambda d: d.enabled)(device):
                    reason = "outside_active_show"
                elif not self.matches(rule, device):
                    reason = "target_changed"
                if reason:
                    await self.resolve(key, alert, reason)
            seen = set()
            for rule in list(self.rules.values()):
                if not rule.enabled:
                    continue
                for device in list(self.manager.devices.values()):
                    if not getattr(self.manager, 'monitored', lambda d: d.enabled)(device) or not self.matches(rule, device):
                        continue
                    state = self.manager.states.get(device.id)
                    if not state:
                        continue
                    samples = []
                    if rule.source == "status":
                        if rule.key == "status":
                            samples = [(None, str(state.status), None)]
                        elif rule.key == "connection_status":
                            samples = [(None, str(state.connection_status), None)]
                    elif state.status in {"online", "degraded"}:
                        if rule.source == "parameter" and rule.key in state.parameters:
                            if (
                                state.health.last_seen
                                and (now() - state.health.last_seen).total_seconds()
                                <= device.stale_after
                            ):
                                samples = [(None, state.parameters[rule.key], None)]
                        elif rule.source == "metric":
                            samples = [
                                (m.channel, m.value, m.unit)
                                for m in state.metrics
                                if m.key == rule.key
                                and m.quality == "good"
                                and m.value is not None
                                and 0
                                <= (now() - m.observed_at).total_seconds()
                                <= device.stale_after
                                and (rule.channel is None or m.channel == rule.channel)
                            ]
                    for channel, value, unit in samples:
                        key = (rule.id, device.id, channel or "")
                        seen.add(key)
                        alert = self.open_alerts.get(key)
                        breach = self.breached(rule, value, bool(alert))
                        if breach is None:
                            self.pending.pop(key, None)
                            continue
                        desired = (
                            "open"
                            if breach and not alert
                            else "clear"
                            if not breach and alert
                            else None
                        )
                        if not desired:
                            self.pending.pop(key, None)
                            continue
                        if key not in self.pending or self.pending[key][0] != desired:
                            self.pending[key] = (desired, clock)
                        delay = rule.hold_seconds if desired == "open" else rule.clear_seconds
                        if clock - self.pending[key][1] < delay:
                            continue
                        if desired == "clear":
                            await self.resolve(key, alert, "condition_cleared")
                        else:
                            created = Alert(
                                rule_id=rule.id,
                                rule_revision=rule.revision,
                                rule_name=rule.name,
                                device_id=device.id,
                                device_name=device.name,
                                key=rule.key,
                                channel=channel,
                                severity=rule.severity,
                                value=value,
                                unit=unit,
                            )
                            await self.persist(created, "created")
                            self.open_alerts[key] = created
                            self.pending.pop(key, None)
            # Unknown/stale readings interrupt both debounce timers; they never clear an incident.
            self.pending = {k: v for k, v in self.pending.items() if k in seen}
