"""UDP peer with a separate local feedback port and source-IP filtering."""
import asyncio
import socket

from .udp import DatagramReader, UDPTransport


class PeerReader(DatagramReader):
    def datagram_received(self, data, addr):
        if addr[0] == self.owner.remote[0]:
            super().datagram_received(data, addr)


class UDPDuplexTransport(UDPTransport):
    def __init__(self, host, port, *, bind_host='0.0.0.0', **kwargs):
        super().__init__(host, port, **kwargs)
        self.bind_host = bind_host
        self.remote = None

    async def connect(self):
        if self.connected:
            return
        await self.disconnect()
        self.error = None
        self.queue = asyncio.Queue(self.queue.maxsize)
        loop = asyncio.get_running_loop()
        async with asyncio.timeout(self.timeout):
            addresses = await loop.getaddrinfo(self.host, self.port, family=socket.AF_INET, type=socket.SOCK_DGRAM)
            self.remote = addresses[0][4]
            self.socket, _ = await loop.create_datagram_endpoint(
                lambda: PeerReader(self), local_addr=(self.bind_host, self.local_port or 0))

    async def write(self, data):
        self.validate_payload(data)
        if not self.connected:
            raise ConnectionError('UDP disconnected')
        self.socket.sendto(data, self.remote)
        await self.record('TX', data)
