import asyncio
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.protocol import State

from .base import BaseTransport


class _DirectConnect(connect):
    def process_redirect(self, error):
        return error


class WebSocketTransport(BaseTransport):
    """Bounded, protocol-neutral WebSocket I/O; never follows redirects."""

    def __init__(self, url, **kwargs):
        super().__init__(**kwargs)
        parts = urlsplit(url)
        if parts.scheme not in {'ws', 'wss'} or not parts.hostname or parts.username or parts.password:
            raise ValueError('WebSocket requires a URL without embedded credentials')
        self.url, self.socket = url, None

    @property
    def connected(self):
        return self.socket is not None and self.socket.state == State.OPEN

    async def connect(self):
        if not self.connected:
            self.socket = await _DirectConnect(self.url, proxy=None, open_timeout=self.timeout,
                                        close_timeout=1, max_size=self.max_bytes, max_queue=2)

    async def receive(self):
        if not self.connected:
            raise ConnectionError('WebSocket closed')
        async with asyncio.timeout(self.timeout):
            data = await self.socket.recv()
            await self.record('RX', data.encode() if isinstance(data, str) else data)
            return data

    async def disconnect(self):
        socket, self.socket = self.socket, None
        if socket:
            await socket.close()
