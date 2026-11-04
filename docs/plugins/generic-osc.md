# Generic OSC UDP

Map device-defined OSC feedback and logical controls with a shared visual editor.

- Plugin ID: `generic.osc`
- Adapter version: `0.2.0`
- Plugin API: `1.2`
- Communication: `polling`
- Endpoint transports: `udp`
- Default endpoint port: `9000`
- [Manifest/source](../../backend/src/stage_monitor/plugins/generic_osc)

## Setup and behavior

Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.

Supports statuses, trigger buttons, momentary holds/releases, feedback-based ON/OFF toggles, numeric sliders and vertical/horizontal meters. Configure remote endpoint and local feedback port separately. Typed OSC arguments are bounded; immediate bundles are supported, future-timed execution is not. UDP sends are not device acknowledgements. Toggles require fresh feedback; momentary leases auto-release on lost control contact. Administrator inspection/learning is opt-in and bounded. Mapping presets can be saved or imported/exported as JSON without copying endpoint credentials.

## Telemetry

| Key | Label | Type | Unit |
| --- | --- | --- | --- |

Metrics can be channelized. Missing/stale/invalid values are not invented zeros. Optional data depends on device/firmware and the selected communication mode.

## Commands

- `send_osc` — capability `osc.control`; parameters: `button_id` (string), `action` (string), `value` (number).
- `test_osc` — capability `configuration.osc_test`; parameters: `control` (schema), `action` (string), `value` (number).

Available commands may be narrowed by configuration. All requests still pass role/device-scope checks. Transport acceptance is not automatically confirmed execution.

## Custom configuration

| Key | Label | Type | Default |
| --- | --- | --- | --- |
| `receive_port` | Status receive UDP port | integer | 9001 |
| `bind_host` | Receiver bind address | string | "0.0.0.0" |
| `query_interval` | OSC query interval (s) | number | 2 |
| `statuses` | OSC statuses | array | see manifest |
| `buttons` | OSC buttons | array | see manifest |

The manifest contains authoritative ranges, enums, required fields and defaults. Device name, category, endpoint, enabled, tags, polling and stale timeout are common fields, not custom config.

## Verification and limitations

Codec, real UDP feedback, control leases, bounds, presets and frontend mapping tests are included. Verify exact addresses/types/ranges and feedback semantics against your target device's protocol.

See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.
