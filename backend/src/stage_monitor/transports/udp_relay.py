"""One fixed UDP receive port shared by isolated workers, routed by remote address.

No vendor protocol parsing. Each worker receives a random route token and can
send only to its registered endpoint. Loopback client queues remain bounded.
"""
import asyncio
import secrets
import socket

from .udp import UDPTransport


class WireProtocol(asyncio.DatagramProtocol):
    def __init__(self, relay): self.relay = relay
    def datagram_received(self, data, address):
        route = self.relay.remotes.get(address) or self.relay.remotes.get((address[0], None))
        if route and route['client'] and len(data) <= 4096:
            self.relay.local.sendto(data, route['client'])


class LocalProtocol(asyncio.DatagramProtocol):
    def __init__(self, relay): self.relay = relay
    def datagram_received(self, data, address):
        if address[0] != '127.0.0.1' or len(data) > 4129 or len(data) < 34:
            return
        try: token = data[:32].decode('ascii')
        except UnicodeDecodeError: return
        route = self.relay.tokens.get(token)
        if route and data[32:33] == b':':
            route['client'] = address
            self.relay.wire.sendto(data[33:], route['remote'])


class UDPRelay:
    def __init__(self, port, *, bind_host='0.0.0.0'):
        self.port, self.bind_host = port, bind_host
        self.tokens, self.remotes = {}, {}
        self.wire = self.local = None

    async def start(self):
        loop = asyncio.get_running_loop()
        self.wire, _ = await loop.create_datagram_endpoint(lambda: WireProtocol(self), local_addr=(self.bind_host, self.port))
        try:
            self.local, _ = await loop.create_datagram_endpoint(lambda: LocalProtocol(self), local_addr=('127.0.0.1', 0))
        except BaseException:
            self.wire.close(); self.wire=None; raise

    async def register(self, owner, host, port, *, match_host=False):
        resolved = await asyncio.get_running_loop().getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM)
        remote = resolved[0][4]
        key = (remote[0], None) if match_host else remote
        existing = self.remotes.get(key)
        if existing and existing['owner'] != owner:
            raise ValueError('Shared UDP endpoint already monitored by another device')
        self.unregister(owner)
        token = secrets.token_hex(16)
        route = {'owner':owner, 'remote':remote, 'key':key, 'client':None}
        self.tokens[token] = route; self.remotes[key] = route
        return {'port':self.local.get_extra_info('sockname')[1], 'token':token}

    def unregister(self, owner):
        for token, route in list(self.tokens.items()):
            if route['owner'] == owner:
                self.tokens.pop(token); self.remotes.pop(route['key'], None)

    def close(self):
        for transport in (self.wire, self.local):
            if transport: transport.close()
        self.wire=self.local=None; self.tokens.clear(); self.remotes.clear()


class RelayedUDPTransport(UDPTransport):
    def __init__(self, relay, **options):
        self.route_token = relay['token'].encode('ascii')
        super().__init__('127.0.0.1', relay['port'], **options)

    async def write(self, data):
        self.validate_payload(data)
        if not self.connected: raise ConnectionError('UDP relay disconnected')
        self.socket.sendto(self.route_token+b':'+data)
        await self.record('TX', data)
