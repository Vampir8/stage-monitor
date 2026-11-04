# REST and WebSocket API

The authoritative schemas are generated at `/docs` and `/openapi.json` by the running build. This is an overview, not a second static copy of every request model.

## Sessions and commands

`POST /api/auth/login` accepts username/password, sets the HttpOnly session cookie and returns a CSRF token. Preserve the cookie and send `X-CSRF-Token` for writes. `GET /api/auth/me` checks the session; `POST /api/auth/logout` ends it. Origin/CORS validation applies. Swagger does not automatically add the CSRF header to later writes.

Roles are viewer/operator/audio/video/network/admin. Device scopes and capability checks are enforced by the backend; hiding a UI button is not authorization.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/devices` | Scoped device list |
| `GET /api/devices/{id}` | Saved configuration |
| `GET /api/devices/{id}/state` | Standard state and data quality |
| `GET /api/devices/{id}/capabilities` | Effective capabilities |
| `GET /api/devices/{id}/commands` | Commands and permission information |
| `POST /api/devices/{id}/commands` | Admit a logical command |
| `GET /api/devices/{id}/history/{metric}` | Time/channel-filtered sampled history |
| `GET /api/plugins` | Manifests, versions, enablement, load errors |
| `GET /api/system/status` | Backend status |
| `GET /api/system/health` | Scoped health dashboard |

Device configuration writes use `If-Match` with the current revision. Conflicts require a fresh read. Administrators add/edit/disable/delete through the corresponding routes.

Example command body:

```json
{
  "command": "set_frequency",
  "parameters": {"frequency_mhz": 550.0},
  "idempotency_key": "your-unique-operation-id",
  "expected_config_revision": 3
}
```

Read the device's schema first; `set_frequency` is not universal. Check `GET /api/commands/{command_id}` for the result. Queued/running/unknown/timed-out do not mean confirmed success. Do not automatically repeat potentially completed hardware operations after a network loss.

## Events

Connect to `/ws` with the authenticated browser session. The backend validates Origin and filters events by live permissions. The stream supplies a snapshot and incremental device state/connection/status, command outcome, alert, chat and weather events. Clients must reconnect/resynchronize after gaps and honor session/permission changes.

Event envelopes include IDs, type, timestamps, source, optional device/actor/correlation IDs, stream ID, sequence and payload. The in-memory event bus is not a durable replay log; use REST snapshots and history for recovery.

Video frames never use this WebSocket. `/api/media/...` handles separate WebRTC preview negotiation and session heartbeats.

## Other modules

- Weather: `/api/weather/current`, `/api/weather/forecast`, `/api/weather/alerts`.
- Chat: `/api/chat/rooms`, messages/read/typing routes and presence/user information.
- Alerts/rules, shows, presets/snapshots, timeline/audit, topology, diagnostics, backups and opt-in discovery: see live OpenAPI.
- Languages: `/api/languages`, `/locales/{language}/core.json`, `/locales/{language}/plugins.json`.
- Themes: `/api/themes` and validated CSS assets.
- OSC mapping library: `/api/mapping-presets`, plus administrator-only device inspection.

Identifiers/configuration values are language independent. UI display labels are localized separately. Use UTC timestamps and stable metric keys; arbitrary vendor metadata is not a guaranteed cross-device schema.
