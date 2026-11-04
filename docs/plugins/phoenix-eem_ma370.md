# Phoenix Contact EEM-MA370

Read three-phase voltage/current, power, frequency, power factor and energy over Modbus TCP.

- Plugin ID: `phoenix.eem_ma370`
- Adapter version: `0.1.0`
- Plugin API: `1.2`
- Communication: `polling`
- Endpoint transports: `modbus`
- Default endpoint port: `502`
- [Manifest/source](../../backend/src/stage_monitor/plugins/phoenix_eem_ma370)

## Setup and behavior

Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.

Read-only FC03 operations use two bounded blocks: 0x8000/30 registers and 0x8100/8 registers, with float32 pairs. Confirm Unit ID and low_first/high_first word order on real hardware. Measurements are primary values returned by the meter; CT/VT ratios are not multiplied again. API/history use base units, while the UI may display kW/kWh. No CT/VT, counter-reset or output commands are supported. No synthetic meter data is included in the public build.

## Telemetry

| Key | Label | Type | Unit |
| --- | --- | --- | --- |
| `power.frequency` | Frequency | number | Hz |
| `power.active` | Active power | number | W |
| `power.energy_import` | Imported energy | number | Wh |
| `power.current` | Current | number | A |
| `power.voltage` | Voltage | number | V |
| `power.factor` | Power factor | number | — |
| `power.voltage_ll` | Line-to-line voltage | number | V |
| `power.current_neutral` | Neutral current | number | A |
| `power.reactive` | Reactive power | number | var |
| `power.apparent` | Apparent power | number | VA |
| `power.energy_export` | Exported energy | number | Wh |

Metrics can be channelized. Missing/stale/invalid values are not invented zeros. Optional data depends on device/firmware and the selected communication mode.

## Commands

Read-only adapter; no device write commands are declared.

Available commands may be narrowed by configuration. All requests still pass role/device-scope checks. Transport acceptance is not automatically confirmed execution.

## Custom configuration

| Key | Label | Type | Default |
| --- | --- | --- | --- |
| `unit_id` | Modbus Unit ID | integer | 1 |
| `word_order` | Float32 word order | string | "low_first" |
| `request_timeout` | Response timeout (s) | number | 2 |

The manifest contains authoritative ranges, enums, required fields and defaults. Device name, category, endpoint, enabled, tags, polling and stale timeout are common fields, not custom config.

## Verification and limitations

Register mapping and TCP/float/bounds behavior were tested against protocol fixtures. A physical EEM-MA370 was not available during development; hardware/firmware and configured ratios must be verified before operational use.

See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.
