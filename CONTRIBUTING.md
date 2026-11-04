# Contributing

Start with the [plugin SDK](docs/plugin-sdk.md) and [working HTTP adapter example](examples/plugins/http_sensor). Manufacturer protocols belong in adapters; transports, authentication, persistence and UI behavior belong in shared application layers.

## Setup

Use Python 3.12+ and install the checkout in editable mode with development dependencies. Install `serial`, `modbus` or `video` extras when needed. Windows is the primary integration environment; saved credentials use Windows Credential Manager.

Keep runtime data outside version control. Do not commit real device addresses, credentials, account exports, logs, captures, private screenshots or databases. Use documentation addresses such as `192.0.2.10` and example-only configuration.

## Adapter contributions

- Choose a stable unique ID and document the supported model/firmware/protocol scope.
- Add an English manifest and separate language files. Never translate protocol IDs or saved values.
- Bound and cancel I/O. Register long-lived tasks with `PluginContext`; make cleanup idempotent.
- Preserve stale, missing, invalid and uncertain data. Do not invent measurements or confirmation after a bare send.
- Add tests with loopback protocol fixtures or credential-free frames; do not ship production simulator plugins.
- Describe ports, authentication, dependencies and physical verification separately from fixture tests.
- Use shared configuration/dashboard features. Do not add plugin-owned popups or change fonts/header position on expansion.

## Validation

From the root, using the backend virtual environment:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend\tests -q
.\backend\.venv\Scripts\python.exe tools\sync_locales.py --check
```

JavaScript checks use Node.js and dependency-free `.cjs` tests under `backend/tests`. The public distribution retains tests that do not require omitted production simulator packages. Protocol fixture servers are test helpers, not installed adapters.

The existing codebase contains legacy formatting. Run Ruff on changed Python files and avoid unrelated style rewrites. Test affected frontend layouts in existing themes and with keyboard interaction.

Pull requests should explain device behavior, actual validation and remaining hardware limitations. Include protocol references/attribution where applicable. No project-wide license has been selected; discuss reuse and contribution terms with the maintainer.
