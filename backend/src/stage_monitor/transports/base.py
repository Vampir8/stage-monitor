from abc import ABC, abstractmethod


class TransportLimitError(ConnectionError):
    """A peer exceeded the configured frame/body/queue limit."""


class BaseTransport(ABC):
    def __init__(self, *, timeout=3.0, max_bytes=65536, trace=None):
        if not 0 < timeout <= 60 or not 1 <= max_bytes <= 1024 * 1024:
            raise ValueError("Invalid transport limits")
        self.timeout, self.max_bytes, self.trace = timeout, max_bytes, trace

    async def record(self, direction, data):
        if self.trace:
            # Raw I/O is sensitive by default; adapters may emit classified summaries separately.
            await self.trace(direction, {"bytes": len(data)}, sensitive=True)

    def validate_payload(self, data):
        if not isinstance(data, bytes) or not 0 < len(data) <= self.max_bytes:
            raise ValueError("Payload must be nonempty bounded bytes")

    @property
    @abstractmethod
    def connected(self): ...

    @abstractmethod
    async def connect(self): ...

    @abstractmethod
    async def disconnect(self): ...

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()
