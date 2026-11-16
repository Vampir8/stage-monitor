"""Read-only OpenWrt telemetry over the uhttpd ubus JSON-RPC endpoint."""

import asyncio
import json
import math
import re
import ssl
import time
from contextlib import suppress

from stage_monitor.contracts import (
    ConnectionStatus, DeviceIdentity, DeviceNetwork, DeviceState, DeviceStatus,
    HealthCheckResult, Metric, now,
)
from stage_monitor.sdk import BaseDevicePlugin
from stage_monitor.transports import HTTPTransport

ANONYMOUS = "0" * 32
SESSION = re.compile(r"[0-9a-fA-F]{32}")
PHYSICAL = re.compile(r"(?:eth\d+|lan\d*|wan\d*|wlan\d+|ath\d+|phy\d+-[^.]+|sfp[-\w]*|en[\w-]+)")
THERMAL_FILE = re.compile(
    r"/sys/class/(?:thermal/thermal_zone\d+/temp|hwmon/hwmon\d+/temp\d+_input)"
)
COUNTERS = {
    "rx_errors": "network.port_rx_errors",
    "tx_errors": "network.port_tx_errors",
    "rx_dropped": "network.port_rx_dropped",
    "tx_dropped": "network.port_tx_dropped",
}


class UbusError(ConnectionError):
    def __init__(self, service, method, code):
        self.code = code if type(code) is int else None
        super().__init__(f"OpenWrt {service}.{method} failed (code {self.code})")


def counter(value):
    return value if type(value) is int and 0 <= value < 2**64 else None


def text(value):
    if isinstance(value, str):
        return value.strip()[:120] or None
    return None


def pretty_uptime(seconds):
    if seconds is None:
        return None
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return " ".join(f"{n}{label}" for n, label in (
        (days, "d"), (hours, "h"), (minutes, "m"), (seconds, "s"),
    ) if n) or "0s"


class OpenWrtPlugin(BaseDevicePlugin):
    def __init__(self, *args):
        super().__init__(*args)
        self.transport = None
        self.session = ANONYMOUS
        self.board = {}
        self.previous = {}
        self.last_sample_time = self.last_uptime = None
        self.clock = time.monotonic
        self.online = False
        self.sequence = 0
        self.state = DeviceState(device_id=self.device.id)

    async def rpc(self, service, method, params, session):
        self.sequence += 1
        request_id = self.sequence
        payload = {
            "jsonrpc": "2.0", "id": request_id, "method": "call",
            "params": [session, service, method, params],
        }
        response = await self.transport.request("POST", "/ubus", content=json.dumps(payload).encode())
        reply = response.json()
        if not isinstance(reply, dict) or reply.get("id") != request_id:
            raise ValueError("Invalid OpenWrt JSON-RPC response")
        if isinstance(reply.get("error"), dict):
            raise UbusError(service, method, reply["error"].get("code"))
        result = reply.get("result")
        if not isinstance(result, list) or not result or type(result[0]) is not int:
            raise ValueError("Invalid OpenWrt ubus result")
        if result[0] != 0:
            raise UbusError(service, method, result[0])
        data = result[1] if len(result) > 1 else {}
        if not isinstance(data, dict):
            raise ValueError("Invalid OpenWrt ubus payload")
        return data

    async def login(self):
        password = await self.context.get_password()
        if password is None:
            raise ValueError("An OpenWrt password must be saved in the device settings")
        reply = await self.rpc("session", "login", {
            "username": self.device.custom_config.get("username", "root"),
            "password": password,
        }, ANONYMOUS)
        session = reply.get("ubus_rpc_session")
        if not isinstance(session, str) or not SESSION.fullmatch(session) or session == ANONYMOUS:
            raise ValueError("OpenWrt did not provide a valid RPC session")
        self.session = session

    async def connect(self):
        endpoint, config = self.device.endpoint, self.device.custom_config
        if not endpoint or not endpoint.host or endpoint.transport not in {"http", "https"}:
            raise ValueError("OpenWrt requires an HTTP or HTTPS endpoint")
        if endpoint.transport == "http" and not config.get("allow_plain_http", False):
            raise ValueError("Plain HTTP requires explicit configuration")
        default_port = 443 if endpoint.transport == "https" else 80
        port = endpoint.port or default_port
        host = f"[{endpoint.host}]" if ":" in endpoint.host else endpoint.host
        self.network = DeviceNetwork(host=endpoint.host, port=port, transport=endpoint.transport)
        verify = ssl.create_default_context(cafile=config["ca_file"]) if config.get("ca_file") else True
        self.transport = HTTPTransport(
            f"{endpoint.transport}://{host}:{port}", timeout=2, max_bytes=262144, verify=verify,
            headers={"Content-Type": "application/json"}, trace=self.context.trace,
        )
        await self.transport.connect()
        await self.login()
        self.board = await self.rpc("system", "board", {}, self.session)
        if not text(self.board.get("model")) and not text(self.board.get("board_name")):
            raise ValueError("OpenWrt returned no board identity")

    async def disconnect(self):
        self.online = False
        self.previous.clear()
        self.last_sample_time = self.last_uptime = None
        if self.transport and self.transport.connected:
            if self.session != ANONYMOUS:
                with suppress(Exception):
                    await self.rpc("session", "destroy", {}, self.session)
            await self.transport.disconnect()
        self.session = ANONYMOUS

    async def start(self):
        await self.poll()

    async def stop(self):
        pass

    async def read_snapshot(self):
        try:
            info = await self.rpc("system", "info", {}, self.session)
        except UbusError as error:
            if error.code not in {6, -32002}:
                raise
            await self.login()
            info = await self.rpc("system", "info", {}, self.session)
        ports = await self.rpc("network.device", "status", {}, self.session)
        return info, ports

    async def temperature(self):
        path = self.device.custom_config.get("temperature_path")
        if not path:
            return None
        if not THERMAL_FILE.fullmatch(path):
            raise ValueError("Invalid OpenWrt temperature path")
        try:
            reply = await self.rpc("file", "read", {"path": path}, self.session)
        except UbusError as error:
            if error.code in {4, 6, -32000, -32002}:
                return None
            raise
        raw = reply.get("data")
        if not isinstance(raw, str) or not re.fullmatch(r"-?\d{1,6}", raw.strip()):
            return None
        degrees = int(raw.strip()) / 1000
        return degrees if -100 <= degrees <= 200 else None

    async def poll(self):
        self.online = False
        async with asyncio.timeout(4.4):
            info, interfaces = await self.read_snapshot()
            temperature = await self.temperature()
        timestamp = now()
        sample_time = self.clock()
        uptime = counter(info.get("uptime"))
        if uptime is None:
            raise ValueError("Invalid OpenWrt uptime")
        rebooted = self.last_uptime is not None and uptime < self.last_uptime
        interval = sample_time - self.last_sample_time if self.last_sample_time is not None else 0
        metrics = []

        def add(key, value, unit=None, channel=None):
            if value is not None:
                metrics.append(Metric(
                    device_id=self.device.id, key=key, value=value, unit=unit,
                    channel=channel, observed_at=timestamp,
                ))

        add("system.uptime", pretty_uptime(uptime))
        add("system.uptime_seconds", uptime, "s")
        memory = info.get("memory")
        if isinstance(memory, dict):
            for field in ("free", "total"):
                value = counter(memory.get(field))
                if value is not None:
                    add(f"system.memory_{field}_mib", value / (1024**2), "MiB")
        load = info.get("load")
        if isinstance(load, list):
            for period, value in zip((1, 5, 15), load):
                if counter(value) is not None:
                    add(f"system.load_{period}m", value / 65536)
        add("temperature", temperature, "degC")
        release = self.board.get("release")
        version = None
        if isinstance(release, dict):
            version = text(release.get("description")) or text(release.get("version"))
        add("system.firmware", version)
        add("system.kernel", text(self.board.get("kernel")))

        configured = self.device.custom_config.get("interfaces") or []
        names = configured or sorted(name for name in interfaces if PHYSICAL.fullmatch(name))
        previous = {} if rebooted else self.previous
        ports, next_counters = [], {}
        for name in names[:64]:
            interface = interfaces.get(name)
            if not isinstance(interface, dict) or interface.get("present") is False:
                continue
            admin_up = interface.get("up")
            carrier = interface.get("carrier")
            link = False if admin_up is False else carrier if type(carrier) is bool else None
            port = {"name": name, "up": link, "disabled": admin_up is False}
            add("network.port_up", link, channel=name)
            stats = interface.get("statistics")
            if isinstance(stats, dict):
                next_counters[name] = {}
                for direction in ("rx", "tx"):
                    value = counter(stats.get(f"{direction}_bytes"))
                    old = previous.get(name, {}).get(direction)
                    next_counters[name][direction] = value
                    if value is not None and old is not None and value >= old and interval > 0:
                        rate = (value - old) * 8 / interval
                        if math.isfinite(rate):
                            port[f"{direction}_bps"] = rate
                            add(f"network.port_{direction}_bps", rate, "bit/s", name)
                for field, key in COUNTERS.items():
                    value = counter(stats.get(field))
                    if value is not None:
                        port[field] = value
                        add(key, value, channel=name)
            ports.append(port)
        self.previous, self.last_sample_time, self.last_uptime = next_counters, sample_time, uptime
        if ports:
            up = sum(port["up"] is True for port in ports)
            add("network.ports_summary", f"{up}/{len(ports)}")
            add("network.ports_up", up)
            add("network.ports_total", len(ports))
        self.state = DeviceState(
            device_id=self.device.id, status=DeviceStatus.ONLINE,
            connection_status=ConnectionStatus.CONNECTED, observed_at=timestamp,
            identity=DeviceIdentity(
                manufacturer="OpenWrt",
                model=text(self.board.get("model")) or text(self.board.get("board_name")),
                firmware=version,
            ),
            network=self.network, metrics=metrics, extra={"ports": ports},
        )
        self.online = True
        return self.state

    async def get_state(self):
        observed = self.state.observed_at
        if self.online and observed and (now() - observed).total_seconds() < self.device.stale_after:
            return self.state
        return self.state.model_copy(update={
            "status": DeviceStatus.OFFLINE, "connection_status": ConnectionStatus.DISCONNECTED,
            "metrics": [item.model_copy(update={"quality": "stale"}) for item in self.state.metrics],
        })

    async def get_status(self):
        return (await self.get_state()).status

    async def get_metrics(self):
        return (await self.get_state()).metrics

    async def health_check(self):
        return HealthCheckResult(reachable=await self.get_status() == DeviceStatus.ONLINE)

    async def execute_command(self, command, parameters):
        raise PermissionError("OpenWrt monitoring is read-only")
