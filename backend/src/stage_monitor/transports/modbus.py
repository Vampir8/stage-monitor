import asyncio

from .base import BaseTransport


class ModbusTransport(BaseTransport):
    """Modbus TCP registers only; register maps/endianness/scales belong to adapters."""

    def __init__(self, host, port=502, *, device_id=1, **kwargs):
        super().__init__(**kwargs)
        if not host or not 1 <= port <= 65535 or not 1 <= device_id <= 247:
            raise ValueError("Invalid Modbus TCP endpoint/unit")
        self.host, self.port, self.device_id = host, port, device_id
        self.client = None
        self.lock = asyncio.Lock()

    @property
    def connected(self):
        return self.client is not None and self.client.connected

    async def connect(self):
        if self.connected:
            return
        try:
            from pymodbus.client import AsyncModbusTcpClient
        except ImportError:
            raise RuntimeError("Install stage-monitor-backend[modbus] for Modbus devices") from None
        self.client = AsyncModbusTcpClient(
            self.host, port=self.port, timeout=self.timeout, retries=0, reconnect_delay=0
        )
        try:
            async with asyncio.timeout(self.timeout):
                if not await self.client.connect():
                    raise ConnectionError("Modbus connection failed")
        except BaseException:
            await self.disconnect()
            raise

    async def disconnect(self):
        client, self.client = self.client, None
        if client:
            client.close()

    def validate_range(self, address, count, maximum):
        if not isinstance(address, int) or not isinstance(count, int):
            raise ValueError("Register address/count must be integers")
        if not 0 <= address <= 65535 or not 1 <= count <= maximum or address + count > 65536:
            raise ValueError("Invalid Modbus register range")

    async def _call(self, method, address, **parameters):
        try:
            async with asyncio.timeout(self.timeout):
                async with self.lock:
                    if not self.connected:
                        raise ConnectionError("Modbus disconnected")
                    await self.record("TX", b"request")
                    reply = await getattr(self.client, method)(
                        address, device_id=self.device_id, **parameters
                    )
                    if reply.isError():
                        raise ConnectionError("Modbus exception response")
                    await self.record("RX", b"response")
                    return reply
        except BaseException:
            await self.disconnect()
            raise

    async def read_registers(self, address, count, *, input_registers=False):
        self.validate_range(address, count, 125)
        method = "read_input_registers" if input_registers else "read_holding_registers"
        reply = await self._call(method, address, count=count)
        if len(reply.registers) != count:
            await self.disconnect()
            raise ConnectionError("Modbus register count mismatch")
        return list(reply.registers)

    async def write_registers(self, address, values):
        self.validate_range(address, len(values), 123)
        if any(
            isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 65535 for v in values
        ):
            raise ValueError("Registers must be uint16 integers")
        reply = await self._call("write_registers", address, values=list(values))
        if reply.address != address or reply.count != len(values):
            await self.disconnect()
            raise ConnectionError("Modbus write acknowledgment mismatch")
        return True
