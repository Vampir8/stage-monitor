# OBSBOT Tail Air

Monitor camera position/zoom/focus/tracking and optionally control PTZ, HOME, tracking and presets.

- Plugin ID: `obsbot.tail_air`
- Adapter version: `0.3.1`
- Plugin API: `1.2`
- Communication: `polling`
- Endpoint transports: `udp`
- Default endpoint port: `52381`
- [Manifest/source](../../backend/src/stage_monitor/plugins/obsbot_tail_air)

## Setup and behavior

Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.

VISCA over IP uses UDP. Control is off by default. Completion/readback matters: ACK alone does not prove motion completed. The shared UI uses bounded relative PTZ steps, not a continuous joystick implementation. Optional camera portal telemetry reads battery and temperatures; that portal schema is firmware-dependent and undocumented. REC, arbitrary exposure/WB/microphone control are not implemented by this adapter. Video uses the separate media service; source metadata is not a video transport.

## Telemetry

| Key | Label | Type | Unit |
| --- | --- | --- | --- |
| `camera.pan_deg` | camera.pan_deg | number | deg |
| `camera.tilt_deg` | camera.tilt_deg | number | deg |
| `camera.zoom_ratio` | camera.zoom_ratio | number | x |
| `camera.focus_mode` | camera.focus_mode | string | — |
| `camera.tracking_mode` | camera.tracking_mode | string | — |
| `battery.percent` | Battery | number | % |
| `battery.temperature` | Battery temp. | number | degC |
| `camera.lens_temperature` | Lens temp. | number | degC |

Metrics can be channelized. Missing/stale/invalid values are not invented zeros. Optional data depends on device/firmware and the selected communication mode.

## Commands

- `recall_preset` — capability `camera.presets`; parameters: `preset_number` (integer).
- `save_preset` — capability `camera.presets`; parameters: `preset_number` (integer).
- `set_tracking` — capability `camera.tracking`; parameters: `mode` (string).
- `set_position` — capability `camera.position`; parameters: `pan_deg` (number), `tilt_deg` (number).
- `set_zoom` — capability `camera.zoom`; parameters: `zoom_ratio` (number).
- `set_focus_mode` — capability `camera.focus`; parameters: `mode` (string).
- `home` — capability `camera.position`; parameters: none.

Available commands may be narrowed by configuration. All requests still pass role/device-scope checks. Transport acceptance is not automatically confirmed execution.

## Custom configuration

| Key | Label | Type | Default |
| --- | --- | --- | --- |
| `video_fps` | Video preview — FPS limit | integer | 15 |
| `enable_control` | Enable camera control | boolean | false |
| `enable_portal_telemetry` | Read battery and temperature from portal | boolean | true |
| `portal_port` | Camera portal HTTP port | integer | 80 |
| `request_timeout` | Read timeout (s) | number | 1 |
| `video_sources` | Additional video sources (NDI discovered automatically) | array | "not set" |

The manifest contains authoritative ranges, enums, required fields and defaults. Device name, category, endpoint, enabled, tags, polling and stale timeout are common fields, not custom config.

## Verification and limitations

VISCA/portal protocol tests are included. Local development verified telemetry on physical Tail Air hardware; this is not certification of every command/firmware combination. Verify movement and preset writes deliberately on your rig.

See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.
