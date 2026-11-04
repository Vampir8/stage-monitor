import asyncio

import pytest

from stage_monitor.transports.udp_relay import UDPRelay, RelayedUDPTransport


def test_fixed_port_routes_four_peers_and_revokes_tokens():
    async def run():
        class Peer(asyncio.DatagramProtocol):
            def __init__(self, label): self.label=label
            def connection_made(self, transport): self.transport=transport
            def datagram_received(self, data, address):self.transport.sendto(self.label+data,address)
        relay=UDPRelay(0,bind_host='127.0.0.1');await relay.start()
        peers=[];clients=[]
        try:
            for i in range(4):
                wire,_=await asyncio.get_running_loop().create_datagram_endpoint(lambda i=i:Peer(str(i).encode()),local_addr=('127.0.0.1',0))
                peers.append(wire)
                registration=await relay.register(str(i),'127.0.0.1',wire.get_extra_info('sockname')[1])
                client=RelayedUDPTransport(registration,timeout=.15,max_bytes=4096);await client.connect();clients.append(client)
            for round in range(3):
                await asyncio.gather(*(c.write(str(round).encode()) for c in clients))
                replies=await asyncio.gather(*(c.receive() for c in clients))
                assert replies==[str(i).encode()+str(round).encode() for i in range(4)]
            with pytest.raises(ValueError):await relay.register('duplicate','127.0.0.1',peers[0].get_extra_info('sockname')[1])
            relay.unregister('0');await clients[0].write(b'revoked')
            with pytest.raises(TimeoutError):await clients[0].receive()
            await clients[1].write(b'alive');assert await clients[1].receive()==b'1alive'
        finally:
            for c in clients:await c.disconnect()
            for p in peers:p.close()
            relay.close()
    asyncio.run(run())


def test_explicit_host_match_accepts_variable_source_port_and_revokes():
    async def run():
        loop = asyncio.get_running_loop()
        reply, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol,
                                                       local_addr=('127.0.0.1',0))
        class Peer(asyncio.DatagramProtocol):
            def datagram_received(self, data, address):
                reply.sendto(b'variable:'+data, address)
        wire, _ = await loop.create_datagram_endpoint(Peer,local_addr=('127.0.0.1',0))
        relay = UDPRelay(0,bind_host='127.0.0.1')
        await relay.start()
        client = None
        try:
            config = await relay.register('denon','127.0.0.1',wire.get_extra_info('sockname')[1],match_host=True)
            with pytest.raises(ValueError):
                await relay.register('duplicate','127.0.0.1',1,match_host=True)
            client = RelayedUDPTransport(config,timeout=.15,max_bytes=4096)
            await client.connect()
            await client.write(b'discovery')
            assert await client.receive() == b'variable:discovery'
            relay.unregister('denon')
            await client.write(b'after revoke')
            with pytest.raises(TimeoutError):
                await client.receive()
        finally:
            if client:
                await client.disconnect()
            relay.close()
            wire.close()
            reply.close()
    asyncio.run(run())
