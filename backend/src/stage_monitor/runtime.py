"""JSON RPC over local pipes. Vendor code is imported only in spawned workers."""
import asyncio
import importlib
import json
import logging
import multiprocessing
import signal
import sys
from contextlib import suppress

from .contracts import Device, DeviceState, DiscoveryCandidate, DiscoveryScope, PluginDescriptor, new_id
from .sdk import BaseDevicePlugin, PluginContext
from .credentials import WindowsCredentialStore, reference

MAX_MESSAGE_BYTES = 1024 * 1024
log = logging.getLogger(__name__)


def encode(message):
    data = json.dumps(message, allow_nan=False).encode("utf-8")
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError("IPC message exceeds size limit")
    return data


def worker_main(connection, device_data, descriptor_data, directory, udp_relay=None):
    # This target is importable on Windows and does not inherit the parent's event loop.
    # The console broadcasts Ctrl+C to children too. Only the backend should
    # handle it; workers stop through the existing bounded shutdown protocol.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(worker_loop(connection, device_data, descriptor_data, directory, udp_relay))
    except (EOFError, BrokenPipeError, OSError):
        pass
    except Exception:
        log.exception("Plugin worker failed")
    finally:
        connection.close()


async def worker_loop(connection, device_data, descriptor_data, directory, udp_relay=None):
    send_lock = asyncio.Lock()

    async def send(message):
        async with send_lock:
            await asyncio.to_thread(connection.send_bytes, encode(message))

    descriptor = PluginDescriptor.model_validate(descriptor_data)
    device = Device.model_validate(device_data)

    async def publish(state):
        state = DeviceState.model_validate(state)
        if state.device_id != device.id:
            raise ValueError("Plugin may only publish its own device")
        await send({"kind": "update", "data": state.model_dump(mode="json")})

    plugin = None
    heartbeat = None
    async def trace(direction,payload,sensitive):
        if direction not in {'TX','RX'}:
            return
        text=json.dumps(payload,ensure_ascii=True,default=str) if not isinstance(payload,str) else payload
        truncated=len(text)>512
        await send({'kind':'trace','data':{'direction':direction,'payload':'[ukryte]' if sensitive or (truncated and isinstance(payload,dict)) else payload if isinstance(payload,dict) else text[:512],
            'sensitive':bool(sensitive),'truncated':truncated}})
    def read_password():
        if device.credential_ref == reference(device.id):
            return WindowsCredentialStore().read(device.id)
        return None
    context = PluginContext(publish,trace,read_password,udp_relay)
    try:
        sys.path.insert(0, directory)
        module_name, class_name = descriptor.entrypoint.split(":", 1)
        plugin_class = getattr(importlib.import_module(module_name), class_name)
        if not isinstance(plugin_class, type) or not issubclass(plugin_class, BaseDevicePlugin):
            raise TypeError("Entrypoint must implement BaseDevicePlugin")
        plugin = plugin_class(device, context, descriptor)

        async def pulse():
            while True:
                if context.failure:
                    # Do not keep a broken push reader looking healthy.
                    connection.close()
                    return
                await send({"kind": "heartbeat"})
                await asyncio.sleep(1)

        heartbeat = asyncio.create_task(pulse())
        operations = {
            "connect": plugin.connect, "disconnect": plugin.disconnect,
            "start": plugin.start, "stop": plugin.stop, "poll": plugin.poll,
            "health_check": plugin.health_check, "get_state": plugin.get_state,
            "get_status": plugin.get_status, "get_metrics": plugin.get_metrics,
            "execute_command": plugin.execute_command,
            "inspect": plugin.inspect,
        }
        while True:
            message = json.loads(await asyncio.to_thread(connection.recv_bytes, MAX_MESSAGE_BYTES))
            request_id = message["id"]
            try:
                if message["operation"] == "shutdown":
                    await plugin.stop()
                    await context.close()
                    await plugin.disconnect()
                    await send({"kind": "response", "id": request_id, "data": None})
                    return
                if message["operation"] == "describe":
                    result = {
                        "capabilities": [c.model_dump(mode="json") for c in plugin.get_capabilities()],
                        "commands": [c.model_dump(mode="json") for c in plugin.get_commands()],
                    }
                else:
                    operation = operations[message["operation"]]
                    result = await operation(**message.get("parameters", {}))
                    if hasattr(result, "model_dump"):
                        result = result.model_dump(mode="json")
                    elif isinstance(result, list):
                        result = [v.model_dump(mode="json") if hasattr(v, "model_dump") else v for v in result]
                await send({"kind": "response", "id": request_id, "data": result})
            except Exception as exc:
                log.exception("Plugin operation failed: %s", message.get("operation"))
                await send({"kind": "response", "id": request_id,
                            "error": {"code": type(exc).__name__, "message": "Plugin operation failed"}})
    finally:
        if heartbeat:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat
        if plugin:
            for operation in (plugin.stop, plugin.disconnect):
                with suppress(Exception):
                    await asyncio.wait_for(operation(), timeout=1)
        with suppress(Exception):
            await asyncio.wait_for(context.close(), timeout=1)


class PluginFailure(Exception):
    pass


def discovery_worker(connection, descriptor_data, directory, scope_data):
    """One-shot worker; vendor imports and discovery never run in the core process."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        descriptor = PluginDescriptor.model_validate(descriptor_data)
        scope = DiscoveryScope.model_validate(scope_data)
        sys.path.insert(0, directory)
        module_name, class_name = descriptor.entrypoint.split(":", 1)
        plugin_class = getattr(importlib.import_module(module_name), class_name)
        if not isinstance(plugin_class, type) or not issubclass(plugin_class, BaseDevicePlugin):
            raise TypeError("Invalid discovery plugin")
        result = asyncio.run(plugin_class.discover(scope))
        if not isinstance(result, list) or len(result) > scope.max_results:
            raise ValueError("Discovery result limit exceeded")
        candidates = [DiscoveryCandidate.model_validate(c).model_dump(mode="json") for c in result]
        connection.send_bytes(encode({"candidates":candidates}))
    except Exception:
        with suppress(Exception):
            connection.send_bytes(encode({"error":"Discovery adapter failed"}))
    finally:
        connection.close()


async def discover_plugin(entry, scope):
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=discovery_worker, args=(child, entry["descriptor"].model_dump(mode="json"),
        str(entry["directory"]), scope.model_dump(mode="json")),daemon=True,name="discovery-"+entry["descriptor"].plugin_id)
    try:
        process.start()
        child.close()
        async with asyncio.timeout(scope.timeout_seconds):
            while not await asyncio.to_thread(parent.poll, 0.1):
                if not process.is_alive():
                    raise PluginFailure("Discovery worker exited")
            response = json.loads(await asyncio.to_thread(parent.recv_bytes, MAX_MESSAGE_BYTES))
            if "error" in response:
                raise PluginFailure(response["error"])
            return response["candidates"]
    finally:
        if process.pid:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 2)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 2)
        parent.close()
        child.close()


class PluginSupervisor:
    def __init__(self, device, entry, timeout, update_callback,diagnostics=None,udp_relay=None):
        self.device = device
        self.entry = entry
        self.timeout = timeout
        self.update_callback = update_callback
        self.process = None
        self.connection = None
        self.reader = None
        self.pending = {}
        self.lock = asyncio.Lock()
        self.last_heartbeat = 0.0
        self.failed = None
        self.closing = False
        self.diagnostics = diagnostics
        self.udp_relay = udp_relay

    async def start(self):
        ctx = multiprocessing.get_context("spawn")
        parent, child = ctx.Pipe(duplex=True)
        self.connection = parent
        self.process = ctx.Process(
            target=worker_main,
            args=(child, self.device.model_dump(mode="json"),
                  self.entry["descriptor"].model_dump(mode="json"), str(self.entry["directory"]), self.udp_relay),
            name=f"device-{self.device.id}", daemon=True,
        )
        self.process.start()
        child.close()
        self.last_heartbeat = asyncio.get_running_loop().time()
        self.reader = asyncio.create_task(self._read(), name=f"reader-{self.device.id}")
        description = await self.rpc("describe")
        await self.rpc("connect")
        await self.rpc("start")
        return description

    async def _read(self):
        try:
            while True:
                message = json.loads(await asyncio.to_thread(self.connection.recv_bytes, MAX_MESSAGE_BYTES))
                kind = message.get("kind")
                if kind == "heartbeat":
                    self.last_heartbeat = asyncio.get_running_loop().time()
                elif kind == "response":
                    future = self.pending.get(message.get("id"))
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(PluginFailure(message["error"]["code"]))
                        else:
                            future.set_result(message.get("data"))
                elif kind == "update":
                    await self.update_callback(DeviceState.model_validate(message["data"]))
                elif kind == 'trace':
                    if self.diagnostics:
                        self.diagnostics.trace(self.device.id,message.get('data',{}))
                else:
                    raise PluginFailure("Invalid IPC message")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.failed = type(exc).__name__
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(PluginFailure("Worker disconnected"))
            if not self.closing:
                log.warning("Worker disconnected device=%s reason=%s", self.device.id, self.failed)

    def check(self):
        if self.failed or not self.process or not self.process.is_alive():
            raise PluginFailure("Worker unavailable")
        if asyncio.get_running_loop().time() - self.last_heartbeat > max(3, self.timeout * 2):
            raise PluginFailure("Worker heartbeat expired")

    async def rpc(self, operation, parameters=None, timeout=None):
        async with self.lock:
            self.check()
            request_id = new_id()
            future = asyncio.get_running_loop().create_future()
            self.pending[request_id] = future
            started=asyncio.get_running_loop().time()
            error=None
            try:
                async with asyncio.timeout(timeout or self.timeout):
                    await asyncio.to_thread(self.connection.send_bytes, encode({
                        "id": request_id, "operation": operation, "parameters": parameters or {},
                    }))
                    return await future
            except TimeoutError:
                error='Timeout'
                self.failed = "RPC timeout"
                raise
            except Exception as exc:
                error=type(exc).__name__
                raise
            finally:
                if self.diagnostics:
                    self.diagnostics.rpc(self.device.id,operation,(asyncio.get_running_loop().time()-started)*1000,error)
                self.pending.pop(request_id, None)
                if not future.done():
                    future.cancel()

    async def close(self):
        self.closing = True
        if self.process and self.process.is_alive() and not self.failed:
            with suppress(Exception):
                await self.rpc("shutdown", timeout=1)
                await asyncio.to_thread(self.process.join, 1)
        if self.process and self.process.is_alive():
            self.process.terminate()
        if self.process:
            await asyncio.to_thread(self.process.join, 2)
            if self.process.is_alive():
                self.process.kill()
                await asyncio.to_thread(self.process.join, 2)
        if self.connection:
            self.connection.close()
        if self.reader:
            with suppress(Exception):
                await asyncio.wait_for(self.reader, 2)
        for future in self.pending.values():
            if not future.done():
                future.set_exception(PluginFailure("Worker stopped"))
