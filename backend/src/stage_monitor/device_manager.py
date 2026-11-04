import asyncio
import json
import logging
import random
from contextlib import suppress
from datetime import timedelta, timezone

from jsonschema import Draft202012Validator

from .contracts import (
    Capability, Command, CommandResult, ConnectionStatus, Device, DeviceError, DeviceNetwork,
    DeviceState, DeviceStatus, HealthCheckResult, PluginCommandResult, now,
)
from .runtime import PluginFailure, PluginSupervisor
from .topology import validate_graph
from .credentials import WindowsCredentialStore, reference

log = logging.getLogger(__name__)


class Conflict(Exception):
    pass


class DeviceManager:
    def __init__(self, storage, plugins, bus, settings):
        self.storage, self.plugins, self.bus, self.settings = storage, plugins, bus, settings
        self.devices = {}
        self.states = {}
        self.descriptions = {}
        self.supervisors = {}
        self.runners = {}
        self.command_workers = {}
        self.queues = {}
        self.config_lock = asyncio.Lock()
        self.admission_lock = asyncio.Lock()
        self.shutting_down = False
        self.command_authorizer = None
        self.diagnostics = None
        self.show_device_ids = None
        self.credentials = WindowsCredentialStore()
        self.udp_relays = {}
        self.udp_relay_lock = asyncio.Lock()

    async def start(self):
        await self.storage.recover_commands()
        devices = await self.storage.devices()
        rows = await self.storage.rows("SELECT value FROM metadata WHERE key='show_profiles'")
        if rows:
            saved = json.loads(rows[0][0])
            if saved.get('active_id'):
                profile = next(p for p in saved['profiles'] if p['id'] == saved['active_id'])
                self.show_device_ids = set(profile['device_ids'])
        for device in devices:
            self.devices[device.id] = device
            self.states[device.id] = self.initial_state(device)
            self.launch(device)

    def initial_state(self, device):
        network = DeviceNetwork()
        if device.endpoint:
            network = DeviceNetwork(host=device.endpoint.host, port=device.endpoint.port,
                                    transport=device.endpoint.transport)
        return DeviceState(device_id=device.id, network=network, extra=self.monitoring_info(device))

    def monitored(self, device):
        return device.enabled and (self.show_device_ids is None or device.id in self.show_device_ids
                                   or device.category.casefold() == 'power')

    def monitoring_info(self, device):
        return {'monitoring_active': self.monitored(device),
                'monitoring_reason': 'manual_pause' if not device.enabled else
                'active_show' if not self.monitored(device) else None}

    async def set_show_scope(self, device_ids):
        """Called under config_lock; preserve inventory configuration and manual pauses."""
        async with self.admission_lock:
            self.show_device_ids = set(device_ids) if device_ids is not None else None
            stopped = []
            for device in list(self.devices.values()):
                if not self.active(device):
                    self.states[device.id].extra.update(self.monitoring_info(device))
                    stopped.append(device)
            await asyncio.gather(*(self.stop_device(device.id) for device in stopped))
            for device in list(self.devices.values()):
                if self.active(device) and device.id not in self.runners:
                    revision = self.states[device.id].revision
                    self.states[device.id] = self.initial_state(device)
                    self.states[device.id].revision = revision
                    self.launch(device)

    def active(self, device):
        entry = self.plugins.entries.get(device.plugin_id)
        return self.monitored(device) and entry and entry["enabled"] and not entry["error"]

    def launch(self, device):
        self.queues[device.id] = asyncio.Queue(maxsize=32)
        if self.active(device):
            self.runners[device.id] = asyncio.create_task(self.run_device(device), name=f"device-{device.id}")
            self.command_workers[device.id] = asyncio.create_task(self.run_commands(device.id))

    def publish_state(self, state):
        previous = self.states[state.device_id]
        state.revision = previous.revision + 1
        state.updated_at = now()
        state.extra.update(self.monitoring_info(self.devices[state.device_id]))
        self.states[state.device_id] = state
        self.bus.publish(self.bus.event("device.state", state.model_dump(mode="json"), state.device_id))
        if (previous.connection_status != state.connection_status
                or previous.health.reconnect_attempts != state.health.reconnect_attempts):
            self.bus.publish(self.bus.event("device.connection", {
                "connection_status": state.connection_status,
                "reconnect_attempts": state.health.reconnect_attempts,
                "next_retry_at": state.health.next_retry_at.isoformat() if state.health.next_retry_at else None,
            }, state.device_id))
        if previous.status != state.status:
            event_type = {
                DeviceStatus.ONLINE: "device.online", DeviceStatus.OFFLINE: "device.offline",
                DeviceStatus.ERROR: "device.error", DeviceStatus.DEGRADED: "device.warning",
            }[state.status]
            self.bus.publish(self.bus.event(event_type, {
                "previous": previous.status, "status": state.status,
                "error": state.health.last_error.model_dump(mode="json") if state.health.last_error else None,
            }, state.device_id))

    def mark(self, device_id, status, connection, error=None, attempt=None, next_retry=None):
        state = self.states[device_id].model_copy(deep=True)
        state.status, state.connection_status = status, connection
        if error:
            state.health.last_error = error
        if attempt is not None:
            state.health.reconnect_attempts = attempt
        state.health.next_retry_at = next_retry
        if status in {DeviceStatus.OFFLINE, DeviceStatus.ERROR}:
            for metric in state.metrics:
                metric.quality = "stale"
        self.publish_state(state)

    async def accept_update(self, device_id, state):
        if state.device_id != device_id or any(m.device_id != device_id for m in state.metrics):
            raise PluginFailure("Plugin published another device's state")
        timestamp = now()
        if state.observed_at is None or state.observed_at > timestamp + timedelta(seconds=5):
            raise PluginFailure("Invalid observation timestamp")
        previous = self.states[device_id]
        state.observed_at = state.observed_at.astimezone(timezone.utc)
        latency = state.health.latency_ms
        state.health = previous.health.model_copy(deep=True)
        if latency is not None:
            state.health.latency_ms = latency
        state.health.last_seen = timestamp
        state.health.next_retry_at = None
        state.connection_status = ConnectionStatus.CONNECTED
        reported_mac=state.network.mac
        state.network = previous.network.model_copy(deep=True)
        if reported_mac:
            state.network.mac=reported_mac
        for metric in state.metrics:
            metric.observed_at = metric.observed_at.astimezone(timezone.utc)
            metric.received_at = timestamp
            if (metric.quality == "good"
                    and (timestamp - metric.observed_at).total_seconds() > self.devices[device_id].stale_after):
                metric.quality = "stale"
        if state.status == DeviceStatus.ONLINE and any(m.quality == "stale" for m in state.metrics):
            state.status = DeviceStatus.DEGRADED
        self.publish_state(state)
        for metric in state.metrics:
            self.bus.publish(self.bus.event("device.metric", metric.model_dump(mode="json"), device_id))
        try:
            await self.storage.sample(state.metrics, self.settings.history_sample_seconds,
                                      self.settings.history_overrides)
            self.storage.last_error = None
        except Exception:
            self.storage.last_error = "History write failed"
            log.exception("History write failed device=%s", device_id)

    async def run_device(self, device):
        attempts = 0
        while True:
            supervisor = None
            relay = None
            try:
                entry = self.plugins.validate_device(device)
                relay_config = None
                if entry['descriptor'].shared_udp_port is not None:
                    from .transports.udp_relay import UDPRelay
                    port = entry['descriptor'].shared_udp_port
                    async with self.udp_relay_lock:
                        relay = self.udp_relays.get(port)
                        if relay is None:
                            relay = UDPRelay(port)
                            await relay.start()
                            self.udp_relays[port] = relay
                    endpoint = device.endpoint
                    if not endpoint or endpoint.transport != 'udp' or not endpoint.host:
                        raise ValueError('Shared UDP transport requires a UDP endpoint')
                    relay_config = await relay.register(device.id, endpoint.host, endpoint.port or port,
                        match_host=entry['descriptor'].shared_udp_match_host)
                self.mark(device.id, DeviceStatus.OFFLINE, ConnectionStatus.CONNECTING, attempt=attempts)
                supervisor = PluginSupervisor(
                    device, entry, self.settings.rpc_timeout_seconds,
                    lambda state: self.accept_update(device.id, state),
                    self.diagnostics,
                    udp_relay=relay_config,
                )
                self.supervisors[device.id] = supervisor
                description = await supervisor.start()
                capabilities = [Capability.model_validate(c) for c in description["capabilities"]]
                commands = [Command.model_validate(c) for c in description["commands"]]
                # A worker can narrow manifest permissions, never invent a new command/capability.
                manifest = entry["descriptor"]
                if not {c.id for c in capabilities} <= {c.id for c in manifest.capabilities}:
                    raise PluginFailure("Undeclared capability")
                manifest_commands = {c.name: c for c in manifest.commands}
                if any(c.name not in manifest_commands or c != manifest_commands[c.name] for c in commands):
                    raise PluginFailure("Undeclared command")
                self.descriptions[device.id] = {"capabilities": capabilities, "commands": commands}
                self.mark(device.id, DeviceStatus.OFFLINE, ConnectionStatus.CONNECTED)
                loop = asyncio.get_running_loop()
                stable_since = loop.time()
                last_health = 0.0
                next_poll = 0.0
                mode = manifest.communication_mode
                while True:
                    supervisor.check()
                    if mode in {"polling", "hybrid"} and loop.time() >= next_poll:
                        started = loop.time()
                        state = DeviceState.model_validate(await supervisor.rpc("poll"))
                        state.health.latency_ms = (loop.time() - started) * 1000
                        await self.accept_update(device.id, state)
                        next_poll = loop.time() + device.poll_interval
                    if loop.time() - last_health >= self.settings.health_interval_seconds:
                        health = HealthCheckResult.model_validate(await supervisor.rpc("health_check"))
                        if not health.reachable:
                            raise PluginFailure("Device health check failed")
                        if health.degraded and self.states[device.id].status == DeviceStatus.ONLINE:
                            self.mark(device.id, DeviceStatus.DEGRADED, ConnectionStatus.CONNECTED,
                                      error=health.error)
                        last_health = loop.time()
                    if loop.time() - stable_since >= 30 and attempts:
                        attempts = 0
                        self.states[device.id].health.reconnect_attempts = 0
                    last_seen = self.states[device.id].health.last_seen
                    if last_seen and (now() - last_seen).total_seconds() > device.stale_after:
                        raise PluginFailure("Telemetry stale")
                    if not last_seen and loop.time() - stable_since > device.stale_after:
                        raise PluginFailure("No telemetry received")
                    poll_delay = max(0.01, next_poll - loop.time()) if mode != "push" else 1
                    health_delay = max(0.01, last_health + self.settings.health_interval_seconds - loop.time())
                    await asyncio.sleep(min(poll_delay, health_delay, 1))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempts += 1
                delay = min(self.settings.reconnect_max_seconds,
                            self.settings.reconnect_base_seconds * 2 ** min(attempts - 1, 16))
                delay = min(self.settings.reconnect_max_seconds, delay * random.uniform(0.8, 1.2))
                status = DeviceStatus.OFFLINE if isinstance(exc, (TimeoutError, ConnectionError)) else DeviceStatus.ERROR
                if isinstance(exc, PluginFailure) and str(exc) in {
                    "ConnectionError", "Worker disconnected", "Worker unavailable",
                    "Worker heartbeat expired", "Device health check failed", "Telemetry stale",
                    "No telemetry received",
                }:
                    status = DeviceStatus.OFFLINE
                self.mark(device.id, status, ConnectionStatus.DISCONNECTED,
                          error=DeviceError(code=type(exc).__name__, message="Device runtime failed"),
                          attempt=attempts, next_retry=now() + timedelta(seconds=delay))
                log.warning("Device runtime failed device=%s attempt=%s reason=%s",
                            device.id, attempts, type(exc).__name__)
                if supervisor:
                    await supervisor.close()
                    supervisor = None
                self.supervisors.pop(device.id, None)
                await asyncio.sleep(delay)
            finally:
                if supervisor:
                    await supervisor.close()
                self.supervisors.pop(device.id, None)
                if relay: relay.unregister(device.id)

    def commands(self, device_id):
        return self.descriptions.get(device_id, {}).get("commands", [])

    def check_expectations(self, device_id, request):
        if (request.expected_config_revision is not None
                and request.expected_config_revision != self.devices[device_id].config_revision):
            raise Conflict("Device configuration changed")
        if request.expected_parameters is not None:
            current = self.states[device_id].parameters
            if any(key not in current or json.dumps(current[key], sort_keys=True) != json.dumps(value, sort_keys=True)
                   for key, value in request.expected_parameters.items()):
                raise Conflict("Device parameters changed")

    async def submit(self, device_id, actor_id, request):
        async with self.admission_lock:
            if self.shutting_down:
                raise Conflict("Backend is stopping")
            if request.idempotency_key:
                existing = await self.storage.find_command(device_id, actor_id, request.idempotency_key)
                if existing:
                    if existing.request != request:
                        raise Conflict("Idempotency key already used for another request")
                    return existing
            device = self.devices[device_id]
            if not self.active(device) or device_id not in self.supervisors:
                raise Conflict("Device is unavailable")
            if self.states[device_id].status not in {DeviceStatus.ONLINE, DeviceStatus.DEGRADED}:
                raise Conflict("Device is unavailable")
            command = next((c for c in self.commands(device_id) if c.name == request.command), None)
            if not command:
                raise ValueError("Unsupported command")
            Draft202012Validator(command.parameters_schema).validate(request.parameters)
            self.check_expectations(device_id, request)
            if (request.expected_state_revision is not None
                    and request.expected_state_revision != self.states[device_id].revision):
                raise Conflict("State revision has changed")
            queue = self.queues[device_id]
            if queue.full():
                raise Conflict("Command queue is full")
            result = CommandResult(device_id=device_id, actor_id=actor_id, request=request)
            await self.storage.save_command(result, initial=True,
                                             previous_parameters=self.states[device_id].parameters,
                                             desired_parameters={key:request.parameters[arg] for key,arg in command.snapshot_parameters.items()})
            queue.put_nowait((result, command, self.supervisors[device_id]))
            return result

    async def finish(self, result):
        result.completed_at = now()
        event_type = "device.command.completed" if result.status == "succeeded" else "device.command.failed"
        event = self.bus.event(event_type, result.model_dump(mode="json"), result.device_id,
                               result.actor_id, result.command_id)
        await self.storage.save_command(result, event)

    async def run_commands(self, device_id):
        queue = self.queues[device_id]
        while True:
            result, command, supervisor = await queue.get()
            dispatched = False
            try:
                if not self.active(self.devices[device_id]):
                    raise Conflict('Device monitoring is paused')
                if supervisor is not self.supervisors.get(device_id):
                    raise PluginFailure("Connection changed before execution")
                supervisor.check()
                self.check_expectations(device_id, result.request)
                if self.command_authorizer:
                    await self.command_authorizer(result.actor_id, device_id, command.capability_id)
                if (result.request.expected_state_revision is not None
                        and result.request.expected_state_revision != self.states[device_id].revision):
                    raise Conflict("State revision changed before execution")
                result.status = "running"
                await self.storage.save_command(result)
                dispatched = True
                response = PluginCommandResult.model_validate(await supervisor.rpc(
                    "execute_command", {"command": result.request.command,
                                        "parameters": result.request.parameters},
                    timeout=command.timeout_seconds,
                ))
                result.status = "succeeded" if response.confirmed else "unknown"
                result.confirmed, result.result = response.confirmed, response.result
            except asyncio.CancelledError:
                result.status = "unknown" if dispatched else "cancelled"
                await self.finish(result)
                raise
            except Exception as exc:
                result.status = "unknown" if dispatched else "failed"
                result.error = DeviceError(code=type(exc).__name__, message="Command execution failed",
                                           retryable=not dispatched)
            finally:
                queue.task_done()
            await self.finish(result)

    async def stop_device(self, device_id):
        for tasks in (self.command_workers, self.runners):
            task = tasks.pop(device_id, None)
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        queue = self.queues.pop(device_id, None)
        if queue:
            while not queue.empty():
                result, _, _ = queue.get_nowait()
                result.status = "cancelled"
                await self.finish(result)
                queue.task_done()
        self.descriptions.pop(device_id, None)
        if device_id in self.states:
            self.mark(device_id, DeviceStatus.OFFLINE, ConnectionStatus.DISCONNECTED)

    async def save_device(self, device, actor_id, previous=None, reject_duplicate_endpoint=False, password=None):
        async with self.config_lock, self.admission_lock:
            if reject_duplicate_endpoint and device.endpoint and any(
                d.id != device.id and d.plugin_id == device.plugin_id and d.endpoint == device.endpoint
                for d in self.devices.values()
            ):
                raise Conflict("Device endpoint already exists")
            if previous and self.devices[device.id].config_revision != previous.config_revision:
                raise Conflict("Device configuration changed; reload before saving")
            validate_graph({**self.devices, device.id: device})
            self.plugins.validate_device(device)
            prior_secret = None
            if password is not None:
                descriptor = self.plugins.get(device.plugin_id)['descriptor']
                if not descriptor.configuration_schema.get('x-credential-label'):
                    raise ValueError('Plugin does not accept a saved password')
                prior_secret = await asyncio.to_thread(self.credentials.read, device.id)
                await asyncio.to_thread(self.credentials.write, device.id, password.get_secret_value())
                device.credential_ref = reference(device.id)
            try:
                await self.storage.save_device(device, actor_id, previous)
            except BaseException:
                if password is not None:
                    if prior_secret is None:
                        await asyncio.to_thread(self.credentials.delete, device.id)
                    else:
                        await asyncio.to_thread(self.credentials.write, device.id, prior_secret)
                raise
            descriptive = {"notes", "location", "rack", "tags", "dependencies", "config_revision", "dashboard_layout"}
            if password is None and previous and previous.model_dump(exclude=descriptive) == device.model_dump(exclude=descriptive):
                self.devices[device.id] = device
                return
            if previous:
                await self.stop_device(device.id)
            self.devices[device.id] = device
            if device.id not in self.states:
                self.states[device.id] = self.initial_state(device)
            else:
                fresh = self.initial_state(device)
                fresh.revision = self.states[device.id].revision
                self.states[device.id] = fresh
            self.launch(device)

    async def delete_device(self, device_id, actor_id,expected_revision=None):
        async with self.config_lock, self.admission_lock:
            if device_id not in self.devices or (expected_revision is not None and str(self.devices[device_id].config_revision)!=expected_revision.strip('"')):
                raise Conflict('Device changed or was already removed')
            if any(link.device_id == device_id for d in self.devices.values() for link in d.dependencies):
                raise Conflict("Device is referenced by dependencies; remove links before deleting")
            await self.storage.delete_device(device_id, actor_id, self.devices[device_id])
            await self.stop_device(device_id)
            self.devices.pop(device_id)
            self.states.pop(device_id)
            if self.diagnostics:
                self.diagnostics.clear(device_id)

    async def toggle_plugin(self, plugin_id, enabled):
        async with self.config_lock, self.admission_lock:
            await self.plugins.set_enabled(plugin_id, enabled)
            for device in list(self.devices.values()):
                if device.plugin_id == plugin_id:
                    await self.stop_device(device.id)
                    self.launch(device)

    async def close(self):
        self.shutting_down = True
        for device_id in list(self.devices):
            await self.stop_device(device_id)
        for relay in self.udp_relays.values(): relay.close()
        self.udp_relays.clear()
