# Shure SLX-D / SLXD4 / SLXD4D

Monitor RF RSSI, audio RMS/peak, transmitter battery bars/runtime and receiver settings; optionally change frequency and gain.

- Plugin ID: `shure.slxd`
- Adapter version: `0.1.0`
- Plugin API: `1.2`
- Communication: `hybrid`
- Endpoint transports: `tcp`
- Default endpoint port: `2202`
- [Manifest/source](../../backend/src/stage_monitor/plugins/shure_slxd)

## Setup and behavior

Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.

Enable Advanced Settings → Controller Access on the receiver and use TCP port 2202. Select channel 1 for SLXD4; SLXD4D allows channel 1 or 2 (one device instance per selected channel). Use one external controller per receiver; two independent channel instances/connections on an SLXD4D have not been validated. Meter subscription is required even when frequency/gain control is disabled. Audio RMS/peak and RF RSSI use the documented raw-minus-120 dBFS/dBm scales. Battery bars are 0–5, not a percentage; runtime depends on the transmitter/battery and sentinel values remain unknown. Control is off by default and requires the actual receiver band limits. No mute, temperature, SLX-D+ support or discovery is implemented. See the official [Shure user guide](https://www.shure.com/en-US/docs/guide/SLXD) and [command reference](https://www.shure.com/en-US/docs/commandstrings/SLXD).

## Telemetry

| Key | Label | Type | Unit |
| --- | --- | --- | --- |
| `rf.level` | RF level | number | dBm |
| `rf.rssi` | RSSI | number | dBm |
| `audio.level` | Audio level | number | dBFS |
| `audio.rms` | Audio RMS | number | dBFS |
| `audio.peak` | Audio peak | number | dBFS |
| `battery.bars` | Battery bars | number | bars |
| `battery.runtime_minutes` | Battery runtime | number | min |

Metrics can be channelized. Missing/stale/invalid values are not invented zeros. Optional data depends on device/firmware and the selected communication mode.

## Commands

- `set_frequency` — capability `rf.frequency`; parameters: `frequency_mhz` (number).
- `set_gain` — capability `audio.gain`; parameters: `gain_db` (integer).

Available commands may be narrowed by configuration. All requests still pass role/device-scope checks. Transport acceptance is not automatically confirmed execution.

## Custom configuration

| Key | Label | Type | Default |
| --- | --- | --- | --- |
| `channel` | Receiver channel | integer | 1 |
| `meter_interval_ms` | Meter interval (ms) | integer | 250 |
| `request_timeout` | Response timeout (s) | number | 1 |
| `enable_control` | Enable frequency and gain control | boolean | false |
| `min_frequency_mhz` | Receiver band lower limit (MHz) | number | "not set" |
| `max_frequency_mhz` | Receiver band upper limit (MHz) | number | "not set" |

The manifest contains authoritative ranges, enums, required fields and defaults. Device name, category, endpoint, enabled, tags, polling and stale timeout are common fields, not custom config.

## Verification and limitations

Parser and real loopback TCP tests cover fragmented frames, selected channels, documented meter scales, battery sentinels, confirmed frequency/gain replies, timeouts and cleanup. No physical SLXD4 or SLXD4D was connected during development; verify telemetry and control against your receiver/firmware before show use.

See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.
