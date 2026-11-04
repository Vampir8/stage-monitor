import asyncio

from .base import BaseTransport, TransportLimitError


class DatagramReader(asyncio.DatagramProtocol):
    def __init__(self, owner):
        self.owner = owner

    def connection_made(self, transport):
        self.socket = transport

    def datagram_received(self, data, addr):
        owner = self.owner
        if owner.socket is not self.socket:
            return
        if len(data) > owner.max_bytes or owner.queue.full():
            owner.fail(TransportLimitError("UDP frame or receive queue too large"))
        elif not owner.error:
            owner.queue.put_nowait(data)

    def error_received(self, exc):
        if self.owner.socket is self.socket:
            self.owner.fail(ConnectionError("UDP socket error"))

    def connection_lost(self, exc):
        if self.owner.socket is self.socket:
            self.owner.fail(ConnectionError("UDP disconnected"))


class UDPTransport(BaseTransport):
    def __init__(self, host, port, *, queue_size=32, local_port=None, **kwargs):
        super().__init__(**kwargs)
        if not host or not 1 <= port <= 65535 or not 1 <= queue_size <= 1024:
            raise ValueError("Invalid UDP endpoint/queue")
        if self.max_bytes > 65507:
            self.max_bytes = 65507
        self.host, self.port = host, port
        if local_port is not None and (type(local_port) is not int or not 0 <= local_port <= 65535):
            raise ValueError('Invalid UDP local port')
        self.local_port = local_port
        self.queue = asyncio.Queue(queue_size)
        self.socket, self.error = None, None

    @property
    def connected(self):
        return self.socket is not None and not self.socket.is_closing() and not self.error

    def fail(self, error):
        self.error = error
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(None)
        if self.socket:
            self.socket.close()

    async def connect(self):
        if self.connected:
            return
        await self.disconnect()
        self.error = None
        self.queue = asyncio.Queue(self.queue.maxsize)
        async with asyncio.timeout(self.timeout):
            options = {'remote_addr': (self.host, self.port)}
            if self.local_port is not None:
                options['local_addr'] = ('0.0.0.0', self.local_port)
            self.socket, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: DatagramReader(self), **options)

    async def disconnect(self):
        socket, self.socket = self.socket, None
        self.fail(ConnectionError("UDP disconnected"))
        if socket:
            socket.close()
            # Deliver connection_lost before this instance can be connected again.
            await asyncio.sleep(0)

    async def write(self, data):
        self.validate_payload(data)
        if not self.connected:
            raise ConnectionError("UDP disconnected")
        self.socket.sendto(data)
        await self.record("TX", data)

    async def receive(self, *, timeout=None, disconnect_on_timeout=True):
        if not self.connected:
            raise self.error or ConnectionError("UDP disconnected")
        try:
            async with asyncio.timeout(self.timeout if timeout is None else timeout):
                data = await self.queue.get()
                if data is None:
                    raise self.error
                await self.record("RX", data)
                return data
        except TimeoutError:
            if disconnect_on_timeout:
                await self.disconnect()
            raise
        except BaseException:
            await self.disconnect()
            raise
