# MikroTik CRS326 / RouterOS

Read RouterOS resources, identity, network traffic, Ethernet/SFP+ status and port diagnostics.

- Plugin ID: `mikrotik.crs326`
- Adapter version: `0.1.0`
- Plugin API: `1.2`
- Communication: `polling`
- Endpoint transports: `http`, `https`
- Default endpoint port: `80`
- [Manifest/source](../../backend/src/stage_monitor/plugins/mikrotik_crs326)

## Setup and behavior

Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.

Requires RouterOS REST API and an authorized account; not SwOS and not SNMP. Use HTTPS with a trusted certificate/optional CA file. Plain HTTP requires explicit trusted-network opt-in and carries credentials without encryption. Set the actual REST service port; the manifest default is 80. The password can be saved in Windows Credential Manager or referenced by the configured environment variable. The adapter is read-only: no reboot, PoE, VLAN or port configuration commands.

## Telemetry

| Key | Label | Type | Unit |
| --- | --- | --- | --- |
| `system.cpu_percent` | CPU | number | % |
| `system.memory_free` | Free memory | number | B |
| `system.memory_total` | Total memory | number | B |
| `system.uptime` | Uptime | string | — |
| `temperature` | Temperature | number | degC |
| `network.ports_total` | Ports | number | — |
| `network.ports_up` | Ports UP | number | — |
| `network.ports_summary` | Ports UP | string | — |
| `network.sfp1_status` | SFP+ 1 | string | — |
| `network.sfp2_status` | SFP+ 2 | string | — |
| `network.rx_bps` | Total RX | number | bit/s |
| `network.tx_bps` | Total TX | number | bit/s |
| `network.port_up` | Port link | boolean | — |
| `network.port_disabled` | Port disabled | boolean | — |
| `network.port_rate` | Port speed | string | — |
| `network.port_rx_bps` | Port RX | number | bit/s |
| `network.port_tx_bps` | Port TX | number | bit/s |
| `network.port_rx_errors` | RX errors | number | — |
| `network.port_tx_errors` | TX errors | number | — |
| `network.port_link_downs` | Link drops | number | — |

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

HTTP fixture tests are included and local development used RouterOS hardware. Check API availability, account permissions and field differences on the deployed firmware.

See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.
