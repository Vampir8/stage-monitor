# HTTP sensor plugin example

This is a real adapter template, not a simulator. It does not generate telemetry and will stay offline until a compatible HTTP device responds.

Read [the plugin SDK guide](../../../docs/plugin-sdk.md) first. Copy this directory under an external plugin root and choose your own stable plugin ID/manufacturer/model. The default `plugin:HTTPSensorPlugin` entrypoint works with the included `plugin.py`; update it if you rename/package the module. Plugin directories are searched one level below each configured `plugin_dirs` root.

The device must implement:

- `GET /api/status` → a JSON object with numeric `temperature` (°C), numeric `humidity` (%) and optional boolean `output`.
- Optional `PUT /api/output`, accepting `{"enabled": true}`. Control is off by default. A subsequent status read must confirm the requested `output` before the adapter reports a confirmed result.

The example demonstrates all abstract SDK methods, bounded HTTP I/O, invalid/missing data, a readback-confirmed command, schema-driven configuration, declarative dashboard features and separate language files. Adapt the actual endpoints, fields and acknowledgement semantics to the manufacturer's documented protocol before connecting hardware.
