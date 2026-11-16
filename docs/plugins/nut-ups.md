# Generic NUT UPS

Monitor a UPS exposed by Network UPS Tools (NUT), including a Legrand Keor PDU connected to a Dragino running NUT.

- Plugin ID: `nut.ups`
- Adapter version: `0.1.0`
- Plugin API: `1.2`
- Communication: `polling`
- Endpoint transports: `tcp`
- Default endpoint port: `3493`
- [Manifest/source](../../backend/src/stage_monitor/plugins/nut_ups)

## Setup and behavior

Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.

Point the device at the upsd host, typically on TCP port 3493. In the device editor, click **Load options from server** and select the UPS by its NUT name, or enter the name manually. An empty name works when the server exposes a single UPS. Add one Stage Monitor device per UPS. Standard NUT status codes remain visible (OL means line power, OB means on battery and LB means low battery). To add alarms, configure parameter rules for `ups.on_battery` and `ups.low_battery`, with operator **equals** and value **true**. The adapter sends only LIST requests.

## Telemetry

| Key | Label | Type | Unit |
| --- | --- | --- | --- |
| `ups.status` | UPS status | string | — |
| `battery.percent` | Battery charge | number | % |
| `battery.runtime` | Battery runtime | number | s |
| `battery.voltage` | Battery voltage | number | V |
| `ups.load` | UPS load | number | % |
| `power.active` | Active power | number | W |
| `power.apparent` | Apparent power | number | VA |
| `ups.temperature` | UPS temperature | number | °C |
| `input.voltage` | Input voltage | number | V |
| `input.frequency` | Input frequency | number | Hz |
| `output.voltage` | Output voltage | number | V |
| `output.frequency` | Output frequency | number | Hz |

Metrics can be channelized. Missing/stale/invalid values are not invented zeros. Optional data depends on device/firmware and the selected communication mode.

## Commands

Read-only adapter; no device write commands are declared.

Available commands may be narrowed by configuration. All requests still pass role/device-scope checks. Transport acceptance is not automatically confirmed execution.

## Custom configuration

| Key | Label | Type | Default |
| --- | --- | --- | --- |
| `ups_name` | UPS name | string | "not set" |
| `request_timeout` | Response timeout (s) | number | 2 |

The manifest contains authoritative ranges, enums, required fields and defaults. Device name, category, endpoint, enabled, tags, polling and stale timeout are common fields, not custom config.

## Verification and limitations

A local NUT protocol fixture exercises UPS selection, multiple devices, an alert rule and stale telemetry. Live telemetry was verified with a Legrand Keor PDU 800 over USB through a Dragino MS14N-S running NUT 2.8.1. Metric values are reported by the NUT driver and may need independent verification. See the [NUT wire protocol](https://networkupstools.org/historic/v2.8.1/docs/developer-guide.chunked/net-protocol.html).

See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.
