"""SDK has no FastAPI, storage or manufacturer dependencies."""
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Awaitable, Callable

from .contracts import (
    Capability, Command, Device, DeviceState, DeviceStatus, HealthCheckResult,
    JsonValue, Metric, PluginCommandResult, PluginDescriptor,
    DiscoveryCandidate, DiscoveryScope,
)


class PluginContext:
    def __init__(self, publish_update: Callable[[DeviceState], Awaitable[None]], trace_sink=None, credential_reader=None, udp_relay=None):
        self.publish_update = publish_update
        self.logger = logging.getLogger("stage_monitor.plugin")
        self.tasks: set[asyncio.Task] = set()
        self.failure: BaseException | None = None
        self.trace_sink = trace_sink
        self.credential_reader = credential_reader
        self.udp_relay = udp_relay

    def udp_transport(self, host, port, **options):
        from .transports.udp import UDPTransport
        from .transports.udp_relay import RelayedUDPTransport
        return RelayedUDPTransport(self.udp_relay, **options) if self.udp_relay else UDPTransport(host, port, **options)

    async def get_password(self):
        return await asyncio.to_thread(self.credential_reader) if self.credential_reader else None

    async def trace(self,direction,payload,sensitive=True):
        """Adapters mark only known credential-free protocol data as non-sensitive."""
        if self.trace_sink:
            await self.trace_sink(direction,payload,sensitive)

    def create_task(self, coroutine, name=None):
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.add(task)

        def completed(done):
            self.tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                self.failure = done.exception()

        task.add_done_callback(completed)
        return task

    async def close(self):
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class BaseDevicePlugin(ABC):
    def __init__(self, device: Device, context: PluginContext, descriptor: PluginDescriptor):
        self.device = device
        self.context = context
        self.descriptor = descriptor

    def get_capabilities(self) -> list[Capability]:
        return self.descriptor.capabilities

    def get_commands(self) -> list[Command]:
        return self.descriptor.commands

    async def inspect(self, action='read', seconds=30, cursor=0):
        """Optional, administrator-only, bounded live inspection; no device commands."""
        return {'supported': False}

    @classmethod
    async def discover(cls, scope: DiscoveryScope) -> list[DiscoveryCandidate]:
        """Optional, bounded discovery without creating or connecting a device instance."""
        return []

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def get_status(self) -> DeviceStatus: ...

    @abstractmethod
    async def get_state(self) -> DeviceState: ...

    @abstractmethod
    async def get_metrics(self) -> list[Metric]: ...

    @abstractmethod
    async def execute_command(
        self, command: str, parameters: dict[str, JsonValue]
    ) -> PluginCommandResult: ...

    @abstractmethod
    async def poll(self) -> DeviceState: ...

    @abstractmethod
    async def health_check(self) -> HealthCheckResult: ...


class DiscoverablePlugin(ABC):
    @classmethod
    @abstractmethod
    async def discover(cls, scope: DiscoveryScope) -> list[DiscoveryCandidate]: ...


class SubscribablePlugin(ABC):
    @abstractmethod
    async def subscribe(self, topics: list[str]) -> None: ...
