# Sennheiser SR IEM G4

Read IEM transmitter identity, status and exposed RF/audio/settings information.

- Plugin ID: `sennheiser.iem_g4`
- Adapter version: `0.1.0`
- Plugin API: `1.2`
- Communication: `polling`
- Endpoint transports: `udp`
- Default endpoint port: `53212`
- [Manifest/source](../../backend/src/stage_monitor/plugins/sennheiser_iem_g4)

## Setup and behavior

Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.

UDP monitoring is read-only. The adapter does not remotely control an IEM bodypack or invent its battery/RF reception data. Allow device replies through the local firewall and configure a reachable transmitter address. Available fields depend on the device response and firmware.

## Telemetry

| Key | Label | Type | Unit |
| --- | --- | --- | --- |
| `audio.level` | AF | number | % |
| `audio.peak` | AF peak | number | % |

Metrics can be channelized. Missing/stale/invalid values are not invented zeros. Optional data depends on device/firmware and the selected communication mode.

## Commands

Read-only adapter; no device write commands are declared.

Available commands may be narrowed by configuration. All requests still pass role/device-scope checks. Transport acceptance is not automatically confirmed execution.

## Custom configuration

| Key | Label | Type | Default |
| --- | --- | --- | --- |
| `request_timeout` | Response timeout (s) | number | 2 |

The manifest contains authoritative ranges, enums, required fields and defaults. Device name, category, endpoint, enabled, tags, polling and stale timeout are common fields, not custom config.

## Verification and limitations

Protocol tests are included. Verify the exact SR IEM G4 hardware and network reply behavior in the deployment.

See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.
