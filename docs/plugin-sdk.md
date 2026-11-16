# Device plugin SDK

Stage Monitor plugins adapt one manufacturer's protocol to a common device model. They do not implement users, HTTP routes, database tables, permission checks or their own configuration windows.

The executable contract is [`sdk.py`](../backend/src/stage_monitor/sdk.py); the data models are in [`contracts.py`](../backend/src/stage_monitor/contracts.py). These files take precedence over this guide. The current plugin API is **1.2**; the loader accepts major version **1**. Declare the lowest minor version whose features you actually use.

## Start with a working adapter

[`examples/plugins/http_sensor`](../examples/plugins/http_sensor) is a complete, bounded HTTP adapter. It reads a real endpoint, publishes temperature/humidity and optionally sets an output with readback confirmation. It never generates sample device data.

1. Install the project and development dependencies as described in the root README.
2. Copy the example folder below a separate plugin root:

   ```text
   local-plugins/
     my_sensor/
       manifest.yaml
       plugin.py
       locales/
         en.json
         pl.json
   ```

3. Change `plugin_id`, manufacturer, model, telemetry and configuration to match your hardware. `entrypoint: plugin:HTTPSensorPlugin` resolves `plugin.py` inside this folder. Packaged modules can instead use `my_package.module:MyPlugin` if that module is importable in worker processes.
4. Create a local YAML configuration, outside version control:

   ```yaml
   data_dir: data
   mock_mode: false
   plugin_dirs:
     - ../local-plugins
   ```

   Paths in `plugin_dirs` and `data_dir` are relative to the backend process's working directory, not the YAML file. Use absolute paths if you launch from multiple directories. Point `STAGE_CONFIG` to this YAML file, then restart the backend. Discovery happens at startup; hot code reload is not implemented.

5. Sign in as an administrator, open **Devices → Add device**, choose the plugin and configure its endpoint. Enable monitoring only when ready to connect to hardware.
6. Check **Devices → Details → Diagnostics / service**, `/api/plugins`, `/api/devices/{id}/state`, and the backend worker logs.

Plugin IDs must be unique. Use a stable namespace such as `acme.environment_sensor`; avoid renaming an ID after devices have been saved. Plugin versions describe adapter releases, not the physical device firmware.

## Manifest

Every installed plugin needs `manifest.yaml`:

```yaml
plugin_id: acme.environment_sensor
plugin_version: 0.1.0
plugin_api_version: '1.2'
entrypoint: plugin:EnvironmentSensorPlugin
manufacturer: Acme
model: Environmental sensor
supported_protocols: [http, https]
communication_mode: polling
capabilities:
  - {id: environment.monitoring, writable: false}
commands: []
telemetry:
  - {key: environment.temperature, title: Temperature, value_type: number, unit: '°C'}
configuration_schema:
  type: object
  additionalProperties: false
  x-default-port: 80
  properties:
    status_path: {type: string, title: Status endpoint, default: /api/status}
dashboard:
  collapsed: [metric:environment.temperature]
  expanded: [network:host, metric:environment.temperature, history:environment.temperature]
```

JSON Schema validates `custom_config` and command parameters. Add required fields, type constraints, bounded lengths, enums and ranges. **Schema `default` values are annotations:** merge or read defaults in your adapter; the backend does not automatically write them into saved configuration.

Configuration titles are English source labels. An optional `x-credential-label` exposes the shared password/token input. The stored configuration contains only an opaque `credential_ref`; use `await context.get_password()` to retrieve the device's secret. Windows Credential Manager is the current storage implementation. Never add `password`, `secret`, `token`, `api_key` or private keys to `custom_config`, dashboard metadata, stream URLs or snapshot bindings.

Use `x-list-options: true` on a string property to offer choices from the endpoint entered in the device editor. Handle `inspect(action="configuration:<property>")` by returning up to 100 objects containing a string `value` and an optional string `label`. The administrator-only lookup runs in a temporary worker before `connect()` and `start()`. Use only read operations and close any temporary connections within the inspection. Manual entry remains available. The optional `x-default-category` sets the initial group for new devices.

The core validates major API compatibility, entrypoint shape, schemas, command uniqueness and snapshot bindings before activating an adapter. A bad plugin is reported through `/api/plugins` and does not stop unrelated devices.

## Lifecycle and isolation

The parent process reads manifests but imports manufacturer code only inside a spawned worker. Each device instance has its own worker and bounded command queue. Process isolation provides fault containment; it is **not an OS security sandbox**. Install trusted plugins only.

Normal startup is `construct → describe → connect → start`. Polling/hybrid modes then receive `poll()` calls at the device's configured interval; all modes receive periodic health checks. Shutdown calls `stop → cancel context tasks → disconnect`; cleanup may be attempted again after a failure. Make cleanup safe to repeat.

| Method | Responsibility |
| --- | --- |
| `connect()` | Open the transport and establish or verify the device session. Do not equate a local socket or HTTP pool with remote reachability. |
| `disconnect()` | Release transport/session resources; tolerate partial initialization. |
| `start()` | Start subscriptions/readers needed for push/hybrid operation. |
| `stop()` | Stop subscriptions and release any held controls while the transport is still usable. |
| `poll()` | Perform a bounded refresh and return `DeviceState`. Do not start a second polling scheduler. |
| `get_status()` | Return the standard device status. |
| `get_state()` | Return current state without pretending a cached value is a new measurement. |
| `get_metrics()` | Return the current metric list. |
| `execute_command(command, parameters)` | Validate the logical operation, translate it to the vendor protocol and report its confirmation accurately. |
| `health_check()` | Return `HealthCheckResult`; evaluate remote contact/freshness, not just worker survival. |

`get_capabilities()` and `get_commands()` have manifest-based defaults. An instance may return fewer declared items when a feature is disabled. It cannot invent commands or change their declared schemas/timeouts.

Do not block the event loop with vendor SDKs, sleeps or serial reads. Use async transports, bounded `asyncio.timeout()` operations or `asyncio.to_thread()` for unavoidable synchronous calls. The manager handles failed workers, exponential backoff and jitter. Do not add an unbounded reconnect loop inside `connect()` or `poll()`.

## Push and hybrid adapters

Set `communication_mode: push` or `hybrid`. A push adapter must still publish data often enough to meet `stale_after`, or perform a genuine bounded freshness probe. A worker heartbeat is not device telemetry.

```python
async def start(self):
    self.reader = self.context.create_task(self.read_updates(), name="device-reader")

async def read_updates(self):
    while True:
        frame = await self.transport.receive()
        state = self.parse_state(frame)  # Validate and timestamp an actual response.
        await self.context.publish_update(state)

async def stop(self):
    if self.reader:
        self.reader.cancel()
        await asyncio.gather(self.reader, return_exceptions=True)
        self.reader = None
```

Register long-lived tasks with `context.create_task()`. A failed registered task is noticed by the worker supervisor; unmanaged tasks can silently die while the device still appears healthy. Keep transport access serialized where the protocol requires one outstanding request or shares a push reader with command replies.

## State, timestamps and metrics

`DeviceState` contains `device_id`, `status`, `connection_status`, `identity`, `network`, `health`, `metrics`, `parameters`, `extra` and timestamps. Core-managed state revisions, reconnect counters and retry scheduling must not be treated as vendor protocol fields.

- Device status is `online`, `offline`, `error` or `degraded`. Connecting is represented separately by `connection_status: connecting`; there is no `DeviceStatus.CONNECTING` member.
- Metrics use a stable semantic `key`, optional `channel`, a scalar value or `None`, a unit, UTC observation/receipt timestamps and `quality`.
- Quality is `good`, `unknown`, `stale` or `invalid`. Missing data is `None`, not fabricated zero. NaN and infinity must never cross JSON IPC.
- Use `observed_at` for the actual sample time. Do not stamp cached values as fresh every time a getter is called. Set `health.last_seen` only after genuine device contact.
- Use channels for repeated logical measurements, such as `L1`, `L2`, `L3` or receiver channels; keep units consistent across channels and API history.
- `parameters` contains actual readable settings, such as `mute` or `output_enabled`. `extra` is for bounded vendor-specific metadata, not video frames, raw packet dumps or secrets.

All emitted states and metrics must refer to the same device instance. Publish copies if a background task may mutate the cached state while RPC serialization is in progress. The IPC limit is currently 1 MiB per message; practical telemetry payloads should be much smaller.

## Logical commands and permissions

Declare commands and their capability domain in the manifest. The frontend sends logical names and parameters, never raw manufacturer packets. Core admission performs role/device scope checks, schema validation, revision checks, idempotency tracking and queueing before the plugin runs.

The example's `set_output` demonstrates a boolean command with optional control and readback. A successful write, UDP send or protocol ACK is **not automatically confirmation**. Return `PluginCommandResult(confirmed=True)` only when protocol completion or a suitable readback proves the requested result. Use `confirmed=False` for an uncertain/unacknowledged result, with a small explanatory `result` object. Do not optimistically update an actual-state metric merely because a command was sent.

Use capability prefixes deliberately: `audio.*`, `rf.*`, `camera.*`, `network.*`, etc. Current role policy is implemented in `auth.py`; adding a new domain does not automatically grant a specialist role permission. `configuration.*` commands are administrator-only. The example's `environment.output` is available to admin/operator under the current policy. Viewer users cannot control devices.

For snapshots, `snapshot_parameters` maps **state parameter key → command argument**, for example `{output_enabled: enabled}`. A snapshot command must be idempotent and have complete, unambiguous parameter bindings. Credentials and guessed state must not be restorable parameters. Multi-device restoration can be partial; there is no distributed transaction or guaranteed rollback.

## Transports

Use the reusable transport layer for I/O, framing/size/time bounds and cleanup. Keep the manufacturer's parser and command encoder inside the plugin.

| Transport | Use |
| --- | --- |
| `TCPTransport` | Async TCP streams, bounded reads/writes. |
| `UDPTransport` / `UDPDuplexTransport` | Datagram requests or separate local feedback ports. |
| `HTTPTransport` | Bounded HTTP(S) responses tied to the configured host; no automatic redirects or disabled TLS verification. |
| `SerialTransport` | Optional serial dependencies; RS232/RS422 adapters own framing/baud/parity semantics. |
| `ModbusTransport` | Optional Modbus TCP dependency; register map, endianness, scaling and meanings belong to the plugin. |

For protocols requiring one fixed local UDP port shared by multiple devices, declare `shared_udp_port` and obtain the transport through `context.udp_transport(...)`. The parent owns the shared socket and relays datagrams. `shared_udp_match_host` is an explicit host-only routing opt-in; it is not a broadcast permission. See the StageLinQ adapter for a real example.

Protocol library dependencies must be documented and installed in the backend environment. Do not import optional vendor packages at module import time if that would break unrelated device types.

## Shared dashboard and translations

Basic features are declarative: `network:host`, `metric:<key>`, `parameter:<key>`, `history:<key>`, `meter:<key>` and `command:<name>`. Operators select collapsed/expanded features in the shared device panel. Existing dedicated widgets are recognized frontend types, not arbitrary executable plugin views. A new widget type needs a reviewed shared frontend renderer; a manifest cannot load arbitrary JavaScript or open its own window.

Numeric telemetry can use history; the currently built-in generic vertical meter selector is specifically wired to `rf.level` and `audio.level`. Do not assume any `meter:<key>` creates a generic meter renderer. The OSC mapping editor supports explicitly configured numeric meters.

Maintain separate `locales/en.json` and `locales/pl.json` inside each plugin. Dictionary keys are English source labels; values are translated text. IDs, addresses, enum values and saved configuration are never translated. See [Languages](languages.md). The backend serves validated plugin dictionaries automatically, including external plugins.

Video belongs to the media layer. Camera plugins may expose source metadata; NDI/RTSP frames never travel through the telemetry WebSocket.

## Optional discovery and diagnostics

Implement `@classmethod async discover(scope)` only when the device has a safe, bounded discovery mechanism. Declare `discovery: {methods: [your_method]}` in the manifest. Discovery runs in a disposable worker, returns `DiscoveryCandidate` objects and proposes devices; it must not create database records or control hardware. Honor the supplied timeout/subnet/result limits and deduplicate results. The API currently accepts private IPv4 scopes up to /24. The shipped hardware adapters do not advertise automatic discovery yet; StageLinQ's connection handshake is not the same as the explicit discovery API.

Optional `inspect(action, seconds, cursor)` supports administrator diagnostics. Keep capture opt-in, time-limited, size/rate-limited and secret-free. `context.trace(direction, payload, sensitive=True)` marks raw I/O sensitive by default; only explicitly classified credential-free summaries should use `sensitive=False`. Never log authentication headers, passwords, stream tokens or private payloads.

## Tests and contribution checklist

Run the example's tests with the backend virtual environment:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_sdk_example.py -q
```

Tests use a loopback HTTP fixture and do not register a simulator plugin. For a new adapter, cover parsing real protocol frames, partial/malformed responses, bounds, missing data, timestamps, timeouts, concurrent push/command I/O, cleanup, optional control, exact command encoding and truthful acknowledgement. Verify a clean worker restart and multi-device operation. Record physical hardware/firmware verification separately from tests against protocol fixtures.

Before submitting a pull request:

1. Keep vendor logic out of the core; declare only implemented telemetry/commands.
2. Add a schema, explicit limits, stable units/channels and English labels.
3. Include per-plugin language files and documentation under `docs/plugins/`.
4. Document ports, authentication, optional dependencies and unverified behavior.
5. Include focused tests without production simulator plugins or real credentials.
6. Test collapsed/expanded cards in the existing themes; do not change typography or header geometry when expanding.
7. Preserve uncertainty and freshness; do not claim hardware verification from fixture tests alone.
