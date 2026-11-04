# MikroTik RouterOS 7 — basic adapter

Provide a broader RouterOS identity/resource/traffic/link summary without assuming CRS326 port topology.

- Plugin ID: `mikrotik.routeros`
- Adapter version: `0.1.0`
- Plugin API: `1.2`
- Communication: `polling`
- Endpoint transports: `http`, `https`
- Default endpoint port: `80`
- [Manifest/source](../../backend/src/stage_monitor/plugins/mikrotik_routeros)

## Setup and behavior

Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.

Uses authenticated RouterOS REST, not SwOS or SNMP. HTTPS is preferred; plain HTTP needs explicit opt-in. Supported data depends on RouterOS version and board capabilities. Missing resource/health fields remain missing; the generic adapter does not imply full CRS326-style inventory or configuration support. No write/reboot commands are declared.

## Telemetry

| Key | Label | Type | Unit |
| --- | --- | --- | --- |
| `system.cpu_percent` | CPU | number | % |
| `system.uptime` | Uptime | string | — |
| `temperature` | Temperature | number | degC |
| `system.memory_free` | Free memory | number | B |
| `system.memory_total` | Total memory | number | B |
| `network.ports_summary` | Interfaces UP | string | — |
| `network.ports_total` | Interfaces | number | — |
| `network.ports_up` | Interfaces UP | number | — |
| `network.sfp1_status` | SFP 1 | string | — |
| `network.sfp2_status` | SFP 2 | string | — |
| `network.rx_bps` | Total RX | number | bit/s |
| `network.tx_bps` | Total TX | number | bit/s |

Metrics can be channelized. Missing/stale/invalid values are not invented zeros. Optional data depends on device/firmware and the selected communication mode.

## Commands

Read-only adapter; no device write commands are declared.

Available commands may be narrowed by configuration. All requests still pass role/device-scope checks. Transport acceptance is not automatically confirmed execution.

## Custom configuration

| Key | Label | Type | Default |
| --- | --- | --- | --- |
| `username` | RouterOS username | string | "admin" |
| `password_env` | Password environment variable | string | "STAGE_CRS_PASSWORD" |
| `allow_plain_http` | Allow HTTP on a trusted network (unencrypted password) | boolean | false |
| `ca_file` | HTTPS CA file (optional) | string | "not set" |

Credential input: **RouterOS password**. Stored outside custom config in the backend account's credential vault; never embed it in a URL.

The manifest contains authoritative ranges, enums, required fields and defaults. Device name, category, endpoint, enabled, tags, polling and stale timeout are common fields, not custom config.

## Verification and limitations

Protocol fixture tests cover the basic contract. Validate each additional MikroTik model and firmware separately.

See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.
