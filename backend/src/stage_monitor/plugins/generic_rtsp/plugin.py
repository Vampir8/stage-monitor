import asyncio
from contextlib import suppress

from stage_monitor.contracts import (
    ConnectionStatus, DeviceIdentity, DeviceNetwork, DeviceState, DeviceStatus,
    HealthCheckResult, Metric, now,
)
from stage_monitor.sdk import BaseDevicePlugin


class GenericRTSPPlugin(BaseDevicePlugin):
    """Port reachability only. Decoding and stream authentication belong to media workers."""
    def __init__(self, *args):
        super().__init__(*args)
        if not self.device.endpoint or not self.device.endpoint.host:
            raise ValueError('RTSP requires host')
        self.state = DeviceState(device_id=self.device.id)

    async def connect(self):
        await self.poll()

    async def disconnect(self):
        self.state.status = DeviceStatus.OFFLINE

    async def start(self):
        pass

    async def stop(self):
        pass

    async def get_status(self):
        return self.state.status

    async def get_state(self):
        return self.state

    async def get_metrics(self):
        return self.state.metrics

    async def execute_command(self, command, parameters):
        raise ValueError('Generic RTSP is read-only')

    async def poll(self):
        endpoint = self.device.endpoint
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(endpoint.host, endpoint.port or 554), 2)
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()
        self.state = DeviceState(
            device_id=self.device.id, status=DeviceStatus.ONLINE,
            connection_status=ConnectionStatus.CONNECTED, observed_at=now(),
            identity=DeviceIdentity(manufacturer='Generic', model='RTSP Camera'),
            network=DeviceNetwork(host=endpoint.host, port=endpoint.port or 554, transport='tcp'),
            metrics=[Metric(device_id=self.device.id, key='camera.rtsp_reachable', value=True)],
            extra={'video_health': 'not_checked', 'monitoring': 'TCP reachability only'})
        return self.state

    async def health_check(self):
        await self.poll()
        return HealthCheckResult(reachable=True)
