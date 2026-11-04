# Generic RTSP camera

Describe a camera RTSP source and open browser preview through the media service.

- Plugin ID: `generic.rtsp`
- Adapter version: `1.1.0`
- Plugin API: `1.2`
- Communication: `polling`
- Endpoint transports: `tcp`
- Default endpoint port: `554`
- [Manifest/source](../../backend/src/stage_monitor/plugins/generic_rtsp)

## Setup and behavior

Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.

Configure the camera endpoint, RTSP path and optional credentials. The manifest's TCP endpoint describes the source host/port; the browser receives WebRTC, not RTSP directly. This is not ONVIF, PTZ, camera inventory or arbitrary camera configuration. A reachable port does not prove a decodable stream. Install the optional video dependencies and check the camera codec/stream permissions.

## Telemetry

| Key | Label | Type | Unit |
| --- | --- | --- | --- |
| `camera.rtsp_reachable` | RTSP port | boolean | — |

Metrics can be channelized. Missing/stale/invalid values are not invented zeros. Optional data depends on device/firmware and the selected communication mode.

## Commands

Read-only adapter; no device write commands are declared.

Available commands may be narrowed by configuration. All requests still pass role/device-scope checks. Transport acceptance is not automatically confirmed execution.

## Custom configuration

| Key | Label | Type | Default |
| --- | --- | --- | --- |
| `video_fps` | Video preview — FPS limit | integer | 15 |
| `rtsp_path` | RTSP path (e.g. /Streaming/Channels/101) | string | "/" |
| `username` | RTSP username (empty for unauthenticated streams) | string | "" |

Credential input: **RTSP password (Windows vault)**. Stored outside custom config in the backend account's credential vault; never embed it in a URL.

The manifest contains authoritative ranges, enums, required fields and defaults. Device name, category, endpoint, enabled, tags, polling and stale timeout are common fields, not custom config.

## Verification and limitations

Media tests include bounded preview/session behavior and a loopback RTSP fixture. Camera-specific stream paths/codecs must be verified individually.

See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.
