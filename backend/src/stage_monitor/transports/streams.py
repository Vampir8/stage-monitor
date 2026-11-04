import asyncio
from contextlib import suppress

from .base import BaseTransport, TransportLimitError


class StreamTransport(BaseTransport):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.reader = self.writer = None
        self.read_lock, self.write_lock = asyncio.Lock(), asyncio.Lock()

    @property
    def connected(self):
        return self.writer is not None and not self.writer.is_closing()

    async def disconnect(self):
        writer, self.writer, self.reader = self.writer, None, None
        if writer:
            writer.close()
            with suppress(Exception):
                async with asyncio.timeout(min(self.timeout, 1)):
                    await writer.wait_closed()

    async def write(self, data):
        self.validate_payload(data)
        try:
            async with asyncio.timeout(self.timeout):
                async with self.write_lock:
                    if not self.connected:
                        raise ConnectionError("Transport disconnected")
                    self.writer.write(data)
                    await self.writer.drain()
                    await self.record("TX", data)
        except BaseException:
            await self.disconnect()
            raise

    async def _read(self, operation):
        try:
            async with asyncio.timeout(self.timeout):
                async with self.read_lock:
                    if not self.connected:
                        raise ConnectionError("Transport disconnected")
                    data = await operation(self.reader)
                    if len(data) > self.max_bytes:
                        raise TransportLimitError("Stream frame too large")
                    await self.record("RX", data)
                    return data
        except asyncio.LimitOverrunError:
            await self.disconnect()
            raise TransportLimitError("Stream frame too large") from None
        except asyncio.IncompleteReadError:
            await self.disconnect()
            raise ConnectionError("Peer closed incomplete frame") from None
        except BaseException:
            await self.disconnect()
            raise

    async def read_until(self, separator=b"\n"):
        if not isinstance(separator, bytes) or not 0 < len(separator) <= self.max_bytes:
            raise ValueError("Invalid separator")
        return await self._read(lambda reader: reader.readuntil(separator))

    async def read_exactly(self, size):
        if not 0 < size <= self.max_bytes:
            raise ValueError("Invalid read size")
        return await self._read(lambda reader: reader.readexactly(size))


class TCPTransport(StreamTransport):
    def __init__(self, host, port, **kwargs):
        super().__init__(**kwargs)
        if not host or not 1 <= port <= 65535:
            raise ValueError("TCP requires host and port")
        self.host, self.port = host, port

    async def connect(self):
        if not self.connected:
            async with asyncio.timeout(self.timeout):
                self.reader, self.writer = await asyncio.open_connection(
                    self.host, self.port, limit=self.max_bytes
                )


class SerialTransport(StreamTransport):
    def __init__(self, port, *, baudrate=9600, bytesize=8, parity="N", stopbits=1, **kwargs):
        super().__init__(**kwargs)
        if not port or baudrate <= 0 or bytesize not in {5, 6, 7, 8}:
            raise ValueError("Invalid serial configuration")
        if parity not in {"N", "E", "O", "M", "S"} or stopbits not in {1, 1.5, 2}:
            raise ValueError("Invalid serial framing")
        self.options = dict(
            url=port, baudrate=baudrate, bytesize=bytesize, parity=parity, stopbits=stopbits
        )

    async def connect(self):
        if self.connected:
            return
        try:
            import serial_asyncio
        except ImportError:
            raise RuntimeError("Install stage-monitor-backend[serial] for serial devices") from None
        async with asyncio.timeout(self.timeout):
            self.reader, self.writer = await serial_asyncio.open_serial_connection(
                limit=self.max_bytes, **self.options
            )
