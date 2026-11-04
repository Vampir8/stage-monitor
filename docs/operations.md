# Installation and operations

## Dependencies and startup

Use Python 3.12+ and a source checkout. The backend resolves frontend assets at the repository root, so a backend wheel alone is not a complete dashboard deployment. Windows is the primary deployment target for saved device credentials.

Create `backend/.venv`, install the backend in editable mode and start `python -m stage_monitor` from `backend`. The first launch securely asks for an administrator password. Alternatively a service manager can supply `STAGE_ADMIN_PASSWORD` for first bootstrap; never commit it to YAML. Once users exist, the bootstrap variable does not reset their passwords.

Optional extras: `dev`, `serial`, `modbus`, `video`. NDI needs its Runtime installed separately. The launcher accepts `--host` and `--port`.

## Configuration

Create an untracked YAML file and point `STAGE_CONFIG` to it:

```yaml
data_dir: data
mock_mode: false
allowed_origins: [http://localhost:8080]
secure_cookies: false
session_seconds: 28800
history_sample_seconds: 5
history_retention_days: 14
alert_retention_days: 90
chat_retention_days: 90
timeline_retention_days: 90
history_overrides:
  battery.percent: 30
  network.rx_bps: 2
plugin_dirs: []
```

`STAGE_DATA_DIR` overrides the data path. Paths are relative to the process working directory. Use one instance and one Uvicorn API worker per data directory; a process lock prevents competing SQLite owners. Device/media workers are started by the application itself. Restart after changing plugin code/manifests and refresh the browser after UI updates.

The public build does not ship demo device adapters, automatically seed devices or generate simulated power/weather data. Configure real equipment and the event's Open-Meteo location before use.

## Device and show workflow

The shared Devices panel configures endpoints, polling, feature visibility and plugin fields. Pause monitoring to retain configuration/history while stopping readings and related alarms; this does not switch physical power. Group/tag/rack/location/notes organize inventory. Dependency correlations are diagnostic suggestions, not proof of a root cause.

An active show monitors assigned devices; power devices remain monitored unless manually paused. Roles and device scopes still apply. Chat is independent of show membership.

View settings remember visibility, expansion, docking, theme, clock and decorations in the browser. Language is a separate preference, defaulting to English. Pinned sides remain visible while the middle strip scrolls horizontally. Nested lists retain vertical scrolling; a bottom lane leaves the horizontal scrollbar clear of cards. Each device can select 1–10 minutes of visible history and independent meter/chart visibility.

The alert popup provides active alarms, acknowledgements, rules and history. The optional Alerts column shows unresolved alarms; chat can display critical notifications without duplicating full history.

## Credentials and access

Supported device settings save passwords/tokens in Windows Credential Manager for the backend account. Another Windows user/service account does not automatically share that vault. RouterOS also supports an environment password reference; see its guide. Do not put credentials in custom config, URLs, chat or presets.

Viewer cannot control hardware. Specialist roles use capability domains; operator/admin permissions are broader, with device scope still enforced. Administration/configuration requires admin.

Prefer same-origin browser access. For a separately served frontend, configure the HTML `stage-api-base` meta value and allowed origin. Cookies are HttpOnly; writes require CSRF and Origin checks. Set secure cookies and terminate TLS for HTTPS deployment. Use trusted production networks, not direct Internet exposure.

## Storage and recovery

SQLite stores configuration, accounts/sessions, commands, metrics, alerts, chat, profiles, presets and audit/history. Configure sampling and retention; keep data and backups private. JSON configuration restore is previewed and scheduled for restart. Restore a full SQLite backup only while stopped. Backup exports may contain account records and must not be published.

The mapping preset library has separate JSON import/export; export it separately when transferring mappings. Do not assume the configuration-only backup covers every auxiliary table.

System and device service panels expose health, queues, workers, latency and bounded diagnostics. Trace capture is opt-in/time-limited and raw data is sensitive by default. A successful send or healthy process does not prove command completion.
