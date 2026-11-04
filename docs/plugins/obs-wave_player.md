# OBS Wave Player

Integrate a separate Wave Player application's API for playback, library selection and live audio meters.

- Plugin ID: `obs.wave_player`
- Adapter version: `1.1.0`
- Plugin API: `1.2`
- Communication: `hybrid`
- Endpoint transports: `http`
- Default endpoint port: `8765`
- [Manifest/source](../../backend/src/stage_monitor/plugins/obs_wave_player)

## Setup and behavior

Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.

The player itself is not bundled. Configure its HTTP API endpoint and connection key using the shared credential input. Commands operate playback, seek, volume, mode and group/track selection. RMS/peak meters require the player's real audio meter/SSE API; a volume setting is not signal level. The plugin cannot manufacture meters when the external player does not publish them.

## Telemetry

| Key | Label | Type | Unit |
| --- | --- | --- | --- |
| `audio.level` | RMS | number | dBFS |
| `audio.peak` | Peak | number | dBFS |
| `player.bpm` | Tempo | number | BPM |

Metrics can be channelized. Missing/stale/invalid values are not invented zeros. Optional data depends on device/firmware and the selected communication mode.

## Commands

- `play` — capability `audio.playback`; parameters: none.
- `pause` — capability `audio.playback`; parameters: none.
- `stop` — capability `audio.playback`; parameters: none.
- `previous` — capability `audio.playback`; parameters: none.
- `next` — capability `audio.playback`; parameters: none.
- `seek` — capability `audio.playback`; parameters: `value` (number).
- `volume` — capability `audio.playback`; parameters: `value` (number).
- `mode` — capability `audio.playback`; parameters: `value` (string).
- `select_group` — capability `audio.playback`; parameters: `value` (string).
- `select_track` — capability `audio.playback`; parameters: `value` (string).

Available commands may be narrowed by configuration. All requests still pass role/device-scope checks. Transport acceptance is not automatically confirmed execution.

## Custom configuration

| Key | Label | Type | Default |
| --- | --- | --- | --- |
| — | No additional custom fields | — | — |

Credential input: **OBS Wave Player connection key (Windows vault)**. Stored outside custom config in the backend account's credential vault; never embed it in a URL.

The manifest contains authoritative ranges, enums, required fields and defaults. Device name, category, endpoint, enabled, tags, polling and stale timeout are common fields, not custom config.

## Verification and limitations

HTTP/API and meter-stream tests are included. Match the external player's API version and validate control/state/meter behavior together before use.

See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.
