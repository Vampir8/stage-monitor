# Shure QLX-D / QLXD4

Monitor receiver RF/audio/battery information and optionally configure frequency and gain.

- Plugin ID: `shure.qlxd`
- Adapter version: `0.1.0`
- Plugin API: `1.2`
- Communication: `hybrid`
- Endpoint transports: `tcp`
- Default endpoint port: `2202`
- [Manifest/source](../../backend/src/stage_monitor/plugins/shure_qlxd)

## Setup and behavior

Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.

Uses the receiver's TCP control interface. Enable control explicitly; configure receiver band limits before changing frequency. Frequency/gain commands are not a generic mute API. QLX-D support does not imply verified ULX-D compatibility. Audio meters indicate measured signal; receiver battery availability depends on the transmitter and battery type.

## Telemetry

| Key | Label | Type | Unit |
| --- | --- | --- | --- |
| `rf.level` | rf.level | number | raw |
| `audio.level` | audio.level | number | raw |
| `battery.percent` | battery.percent | number | % |
| `battery.bars` | battery.bars | number | bars |
| `battery.runtime_minutes` | battery.runtime_minutes | number | min |
| `battery.temperature` | battery.temperature | number | degC |

Metrics can be channelized. Missing/stale/invalid values are not invented zeros. Optional data depends on device/firmware and the selected communication mode.

## Commands

- `set_frequency` — capability `rf.frequency`; parameters: `frequency_mhz` (number).
- `set_gain` — capability `audio.gain`; parameters: `gain_db` (integer).

Available commands may be narrowed by configuration. All requests still pass role/device-scope checks. Transport acceptance is not automatically confirmed execution.

## Custom configuration

| Key | Label | Type | Default |
| --- | --- | --- | --- |
| `meter_interval_ms` | Meter interval (ms) | integer | 250 |
| `request_timeout` | Response timeout (s) | number | 1 |
| `enable_control` | Enable frequency and gain control | boolean | false |
| `min_frequency_mhz` | Receiver band lower limit (MHz) | number | "not set" |
| `max_frequency_mhz` | Receiver band upper limit (MHz) | number | "not set" |

The manifest contains authoritative ranges, enums, required fields and defaults. Device name, category, endpoint, enabled, tags, polling and stale timeout are common fields, not custom config.

## Verification and limitations

Protocol parser/stream fixture tests are included. Compare the selected receiver/firmware and transmitter measurements before show use.

See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.
