# Obsidian NETRON EN12

Monitor the DMX gateway, configured universes and exposed input/output/port state.

- Plugin ID: `netron.en12`
- Adapter version: `0.2.0`
- Plugin API: `1.2`
- Communication: `polling`
- Endpoint transports: `http`, `udp`
- Default endpoint port: `80`
- [Manifest/source](../../backend/src/stage_monitor/plugins/netron_en12)

## Setup and behavior

Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.

HTTP polling reads the gateway web data; UDP mode uses Art-Net information. A configured universe or ArtPoll response is not a measurement of live DMX values or packet throughput. Port/state details differ between modes. The adapter does not send DMX levels, reboot/configure the gateway, or switch power. Showtec NET-8/3 is not advertised as verified support.

## Telemetry

| Key | Label | Type | Unit |
| --- | --- | --- | --- |
| `gateway.reported_ports` | Reported ports | number | — |
| `gateway.active_outputs` | Active DMX outputs | number | — |
| `gateway.port_errors` | Port errors | number | — |
| `gateway.firmware_revision` | Art-Net firmware revision | number | — |
| `gateway.configured_outputs` | Configured outputs | number | — |
| `gateway.uptime` | Uptime | string | — |
| `dmx.mode` | Port mode | string | — |
| `dmx.configured_framerate_hz` | Configured DMX frequency | number | Hz |
| `dmx.rdm_enabled` | RDM enabled | boolean | — |
| `dmx.input_universe` | Input universe (from 0) | number | — |
| `dmx.output_universe` | Output universe (from 0) | number | — |
| `dmx.input_active` | Input data | boolean | — |
| `dmx.output_active` | Output data | boolean | — |
| `dmx.input_error` | Input error | boolean | — |
| `dmx.output_short` | Output short circuit | boolean | — |
| `dmx.merging` | Merge active | boolean | — |
| `dmx.input_protocol` | Input protocol | string | — |
| `dmx.output_protocol` | Output protocol | string | — |

Metrics can be channelized. Missing/stale/invalid values are not invented zeros. Optional data depends on device/firmware and the selected communication mode.

## Commands

Read-only adapter; no device write commands are declared.

Available commands may be narrowed by configuration. All requests still pass role/device-scope checks. Transport acceptance is not automatically confirmed execution.

## Custom configuration

| Key | Label | Type | Default |
| --- | --- | --- | --- |
| `response_window` | ArtPoll response window (s) | number | 3 |

The manifest contains authoritative ranges, enums, required fields and defaults. Device name, category, endpoint, enabled, tags, polling and stale timeout are common fields, not custom config.

## Verification and limitations

HTTP/Art-Net tests are included; local development also used NETRON hardware. Verify the deployed firmware and compare available fields in the chosen communication mode.

See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.
