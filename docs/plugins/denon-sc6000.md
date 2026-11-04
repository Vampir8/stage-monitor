# Denon DJ SC6000

Read StageLinQ track metadata and deck state independently for layers A/B.

- Plugin ID: `denon.sc6000`
- Adapter version: `0.1.0`
- Plugin API: `1.2`
- Communication: `hybrid`
- Endpoint transports: `udp`
- Default endpoint port: `51337`
- [Manifest/source](../../backend/src/stage_monitor/plugins/denon_sc6000)

## Setup and behavior

Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.

Read-only StageLinQ discovery/Directory/StateMap session; the device endpoint uses UDP 51337 and negotiates TCP services. The shared UDP relay supports multiple players. This is a consumer of state, not transport/tempo/sync/library control. No audio stream, waveform, playhead/remaining time, cover art, BeatInfo or inferred audio VU is implemented. Track/BPM/playing availability depends on Engine OS and exposed StateMap fields. StageLinQ facts come from community protocol observations, not an official Denon SDK.

## Telemetry

| Key | Label | Type | Unit |
| --- | --- | --- | --- |
| `player.bpm` | Tempo | number | BPM |
| `player.playing` | Playing | boolean | — |
| `player.title` | Track | string | — |
| `player.artist` | Artist | string | — |
| `player.loaded` | Track loaded | boolean | — |
| `player.key_index` | Key index | number | — |
| `player.sync` | Synchronizing | boolean | — |
| `player.master` | Master | boolean | — |
| `player.loop` | Loop | boolean | — |

Metrics can be channelized. Missing/stale/invalid values are not invented zeros. Optional data depends on device/firmware and the selected communication mode.

## Commands

Read-only adapter; no device write commands are declared.

Available commands may be narrowed by configuration. All requests still pass role/device-scope checks. Transport acceptance is not automatically confirmed execution.

## Custom configuration

| Key | Label | Type | Default |
| --- | --- | --- | --- |
| `layers` | Monitored layers | string | "both" |
| `update_interval_ms` | Minimum update interval (ms) | integer | 250 |

The manifest contains authoritative ranges, enums, required fields and defaults. Device name, category, endpoint, enabled, tags, polling and stale timeout are common fields, not custom config.

## Verification and limitations

Tests exercise loopback UDP/TCP handshakes, fragmentation, both layers and cleanup. Physical SC6000/latest Engine OS compatibility is not yet verified. Compare track metadata and state changes on hardware.

See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.
