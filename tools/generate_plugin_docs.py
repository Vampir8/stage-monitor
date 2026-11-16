"""Generate the public adapter catalog from English manifest metadata."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DETAILS = {
    "openwrt.ubus": (
        "Generic OpenWrt router",
        "Monitor an OpenWrt router or gateway through its uhttpd ubus JSON-RPC endpoint.",
        """Install or enable `uhttpd-mod-ubus` and `rpcd` on the router. Use HTTPS on port 443 with a trusted certificate or supply the CA file on the backend host. Save the RPC password in the device editor. For a legacy device on an isolated LAN, select HTTP port 80 and explicitly enable the plain HTTP option.

Grant the RPC login read access to the following ACL group. Save the example as `/usr/share/rpcd/acl.d/stage-monitor-openwrt.json`, add the group to the login's read list in `/etc/config/rpcd` if needed, and restart `rpcd`.

```json
{
  "stage-monitor-openwrt": {
    "description": "Read OpenWrt telemetry",
    "read": {
      "ubus": {
        "system": ["board", "info"],
        "network.device": ["status"]
      }
    }
  }
}
```

By default the adapter selects common physical Ethernet, SFP and Wi-Fi interface names. Enter a JSON list of interface names to override the selection. RX/TX rates are calculated per interface from byte counters and require two consecutive samples. CPU load is the Linux load average. Temperature is optional. Set an exact Linux thermal or hwmon sysfs path and permit the `file.read` ubus method and read access to that path in the RPC account's ACL. The path must expose millidegrees Celsius.""",
        "Verified on a Dragino MS14N-S with its 4.3.7 firmware and the legacy OpenWrt 15.05.1 ubus endpoint. Loopback tests cover session expiration, per-interface counters, reboot detection and an optional thermal sensor. [OpenWrt ubus documentation](https://openwrt.org/docs/techref/ubus?s%5B%5D=1&s%5B%5D=default).",
    ),
    "nut.ups": (
        "Generic NUT UPS",
        "Monitor a UPS exposed by Network UPS Tools (NUT), including a Legrand Keor PDU connected to a Dragino running NUT.",
        "Point the device at the upsd host, typically on TCP port 3493. In the device editor, click **Load options from server** and select the UPS by its NUT name, or enter the name manually. An empty name works when the server exposes a single UPS. Add one Stage Monitor device per UPS. Standard NUT status codes remain visible (OL means line power, OB means on battery and LB means low battery). To add alarms, configure parameter rules for `ups.on_battery` and `ups.low_battery`, with operator **equals** and value **true**. The adapter sends only LIST requests. When measured active power is unavailable, the adapter can estimate it from `ups.load × ups.realpower.nominal ÷ 100`, rounded to whole watts. The estimate assumes that the UPS expresses load as a percentage of its rated active power. The dashboard labels it separately from measured watts.",
        "A local NUT protocol fixture exercises UPS selection, multiple devices, an alert rule and stale telemetry. Live telemetry was verified with a Legrand Keor PDU 800 over USB through a Dragino MS14N-S running NUT 2.8.1. For USB ID `1cb0:0038` on Legrand HID 0.2, the adapter rescales a legacy battery reading by a factor of 10 only when NUT reports a nominal battery voltage of 1.2 V. A corrected NUT driver can report battery voltage directly, preserving decimal precision. The adapter independently rescales underscaled output voltage and marks it unverified, which excludes it from threshold alerts. Metric labels retain the original NUT value and scale factor. See the [NUT wire protocol](https://networkupstools.org/historic/v2.8.1/docs/developer-guide.chunked/net-protocol.html).",
    ),
    "shure.slxd": (
        "Shure SLX-D / SLXD4 / SLXD4D",
        "Monitor RF RSSI, audio RMS/peak, transmitter battery bars/runtime and receiver settings; optionally change frequency and gain.",
        "Enable Advanced Settings → Controller Access on the receiver and use TCP port 2202. Select channel 1 for SLXD4; SLXD4D allows channel 1 or 2 (one device instance per selected channel). Use one external controller per receiver; two independent channel instances/connections on an SLXD4D have not been validated. Meter subscription is required even when frequency/gain control is disabled. Audio RMS/peak and RF RSSI use the documented raw-minus-120 dBFS/dBm scales. Battery bars are 0–5, not a percentage; runtime depends on the transmitter/battery and sentinel values remain unknown. Control is off by default and requires the actual receiver band limits. No mute, temperature, SLX-D+ support or discovery is implemented. See the official [Shure user guide](https://www.shure.com/en-US/docs/guide/SLXD) and [command reference](https://www.shure.com/en-US/docs/commandstrings/SLXD).",
        "Parser and real loopback TCP tests cover fragmented frames, selected channels, documented meter scales, battery sentinels, confirmed frequency/gain replies, timeouts and cleanup. No physical SLXD4 or SLXD4D was connected during development; verify telemetry and control against your receiver/firmware before show use.",
    ),
    "shure.qlxd": (
        "Shure QLX-D / QLXD4",
        "Monitor receiver RF/audio/battery information and optionally configure frequency and gain.",
        "Uses the receiver's TCP control interface. Enable control explicitly; configure receiver band limits before changing frequency. Frequency/gain commands are not a generic mute API. QLX-D support does not imply verified ULX-D compatibility. Audio meters indicate measured signal; receiver battery availability depends on the transmitter and battery type.",
        "Protocol parser/stream fixture tests are included. Compare the selected receiver/firmware and transmitter measurements before show use.",
    ),
    "sennheiser.iem_g4": (
        "Sennheiser SR IEM G4",
        "Read IEM transmitter identity, status and exposed RF/audio/settings information.",
        "UDP monitoring is read-only. The adapter does not remotely control an IEM bodypack or invent its battery/RF reception data. Allow device replies through the local firewall and configure a reachable transmitter address. Available fields depend on the device response and firmware.",
        "Protocol tests are included. Verify the exact SR IEM G4 hardware and network reply behavior in the deployment.",
    ),
    "obsbot.tail_air": (
        "OBSBOT Tail Air",
        "Monitor camera position/zoom/focus/tracking and optionally control PTZ, HOME, tracking and presets.",
        "VISCA over IP uses UDP. Control is off by default. Completion/readback matters: ACK alone does not prove motion completed. The shared UI uses bounded relative PTZ steps, not a continuous joystick implementation. Optional camera portal telemetry reads battery and temperatures; that portal schema is firmware-dependent and undocumented. REC, arbitrary exposure/WB/microphone control are not implemented by this adapter. Video uses the separate media service; source metadata is not a video transport.",
        "VISCA/portal protocol tests are included. Local development verified telemetry on physical Tail Air hardware; this is not certification of every command/firmware combination. Verify movement and preset writes deliberately on your rig.",
    ),
    "mikrotik.crs326": (
        "MikroTik CRS326 / RouterOS",
        "Read RouterOS resources, identity, network traffic, Ethernet/SFP+ status and port diagnostics.",
        "Requires RouterOS REST API and an authorized account; not SwOS and not SNMP. Use HTTPS with a trusted certificate/optional CA file. Plain HTTP requires explicit trusted-network opt-in and carries credentials without encryption. Set the actual REST service port; the manifest default is 80. The password can be saved in Windows Credential Manager or referenced by the configured environment variable. The adapter is read-only: no reboot, PoE, VLAN or port configuration commands.",
        "HTTP fixture tests are included and local development used RouterOS hardware. Check API availability, account permissions and field differences on the deployed firmware.",
    ),
    "mikrotik.routeros": (
        "MikroTik RouterOS 7 — basic adapter",
        "Provide a broader RouterOS identity/resource/traffic/link summary without assuming CRS326 port topology.",
        "Uses authenticated RouterOS REST, not SwOS or SNMP. HTTPS is preferred; plain HTTP needs explicit opt-in. Supported data depends on RouterOS version and board capabilities. Missing resource/health fields remain missing; the generic adapter does not imply full CRS326-style inventory or configuration support. No write/reboot commands are declared.",
        "Protocol fixture tests cover the basic contract. Validate each additional MikroTik model and firmware separately.",
    ),
    "netron.en12": (
        "Obsidian NETRON EN12",
        "Monitor the DMX gateway, configured universes and exposed input/output/port state.",
        "HTTP polling reads the gateway web data; UDP mode uses Art-Net information. A configured universe or ArtPoll response is not a measurement of live DMX values or packet throughput. Port/state details differ between modes. The adapter does not send DMX levels, reboot/configure the gateway, or switch power. Showtec NET-8/3 is not advertised as verified support.",
        "HTTP/Art-Net tests are included; local development also used NETRON hardware. Verify the deployed firmware and compare available fields in the chosen communication mode.",
    ),
    "phoenix.eem_ma370": (
        "Phoenix Contact EEM-MA370",
        "Read three-phase voltage/current, power, frequency, power factor and energy over Modbus TCP.",
        "Read-only FC03 operations use two bounded blocks: 0x8000/30 registers and 0x8100/8 registers, with float32 pairs. Confirm Unit ID and low_first/high_first word order on real hardware. Measurements are primary values returned by the meter; CT/VT ratios are not multiplied again. API/history use base units, while the UI may display kW/kWh. No CT/VT, counter-reset or output commands are supported. No synthetic meter data is included in the public build.",
        "Register mapping and TCP/float/bounds behavior were tested against protocol fixtures. A physical EEM-MA370 was not available during development; hardware/firmware and configured ratios must be verified before operational use.",
    ),
    "denon.sc6000": (
        "Denon DJ SC6000",
        "Read StageLinQ track metadata and deck state independently for layers A/B.",
        "Read-only StageLinQ discovery/Directory/StateMap session; the device endpoint uses UDP 51337 and negotiates TCP services. The shared UDP relay supports multiple players. This is a consumer of state, not transport/tempo/sync/library control. No audio stream, waveform, playhead/remaining time, cover art, BeatInfo or inferred audio VU is implemented. Track/BPM/playing availability depends on Engine OS and exposed StateMap fields. StageLinQ facts come from community protocol observations, not an official Denon SDK.",
        "Tests exercise loopback UDP/TCP handshakes, fragmentation, both layers and cleanup. Physical SC6000/latest Engine OS compatibility is not yet verified. Compare track metadata and state changes on hardware.",
    ),
    "obs.wave_player": (
        "OBS Wave Player",
        "Integrate a separate Wave Player application's API for playback, library selection and live audio meters.",
        "The player itself is not bundled. Configure its HTTP API endpoint and connection key using the shared credential input. Commands operate playback, seek, volume, mode and group/track selection. RMS/peak meters require the player's real audio meter/SSE API; a volume setting is not signal level. The plugin cannot manufacture meters when the external player does not publish them.",
        "HTTP/API and meter-stream tests are included. Match the external player's API version and validate control/state/meter behavior together before use.",
    ),
    "generic.rtsp": (
        "Generic RTSP camera",
        "Describe a camera RTSP source and open browser preview through the media service.",
        "Configure the camera endpoint, RTSP path and optional credentials. The manifest's TCP endpoint describes the source host/port; the browser receives WebRTC, not RTSP directly. This is not ONVIF, PTZ, camera inventory or arbitrary camera configuration. A reachable port does not prove a decodable stream. Install the optional video dependencies and check the camera codec/stream permissions.",
        "Media tests include bounded preview/session behavior and a loopback RTSP fixture. Camera-specific stream paths/codecs must be verified individually.",
    ),
    "generic.osc": (
        "Generic OSC UDP",
        "Map device-defined OSC feedback and logical controls with a shared visual editor.",
        "Supports statuses, trigger buttons, momentary holds/releases, feedback-based ON/OFF toggles, numeric sliders and vertical/horizontal meters. Configure remote endpoint and local feedback port separately. Typed OSC arguments are bounded; immediate bundles are supported, future-timed execution is not. UDP sends are not device acknowledgements. Toggles require fresh feedback; momentary leases auto-release on lost control contact. Administrator inspection/learning is opt-in and bounded. Mapping presets can be saved or imported/exported as JSON without copying endpoint credentials.",
        "Codec, real UDP feedback, control leases, bounds, presets and frontend mapping tests are included. Verify exact addresses/types/ranges and feedback semantics against your target device's protocol.",
    ),
}


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def main():
    target = ROOT / "docs/plugins"
    target.mkdir(parents=True, exist_ok=True)
    index = ["# Device plugin catalog", "", "Each page documents the current English manifest, setup, telemetry, controls and limitations. Manifest IDs are stable API values; titles can be localized.", "", "| Plugin | ID | Protocols |", "| --- | --- | --- |"]
    for path in sorted((ROOT / "backend/src/stage_monitor/plugins").glob("*/manifest.yaml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        plugin_id = manifest["plugin_id"]
        if plugin_id.startswith("mock."):
            continue
        title, purpose, limitations, verification = DETAILS[plugin_id]
        filename = plugin_id.replace(".", "-") + ".md"
        protocols = [p for p in manifest["supported_protocols"] if p != "mock"]
        index.append(f"| [{title}]({filename}) | `{plugin_id}` | {', '.join(protocols)} |")
        schema = manifest["configuration_schema"]
        lines = [f"# {title}", "", purpose, "", f"- Plugin ID: `{plugin_id}`", f"- Adapter version: `{manifest['plugin_version']}`", f"- Plugin API: `{manifest['plugin_api_version']}`", f"- Communication: `{manifest.get('communication_mode', 'polling')}`", f"- Endpoint transports: {', '.join(f'`{p}`' for p in protocols)}", f"- Default endpoint port: `{schema.get('x-default-port', 'adapter-defined')}`", f"- [Manifest/source](../../backend/src/stage_monitor/plugins/{path.parent.name})", "", "## Setup and behavior", "", "Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.", "", limitations, "", "## Telemetry", "", "| Key | Label | Type | Unit |", "| --- | --- | --- | --- |"]
        for metric in manifest["telemetry"]:
            lines.append(f"| `{metric['key']}` | {cell(metric.get('title', metric['key']))} | {cell(metric.get('value_type', 'scalar'))} | {cell(metric.get('unit') or '—')} |")
        lines.extend(["", "Metrics can be channelized. Missing/stale/invalid values are not invented zeros. Optional data depends on device/firmware and the selected communication mode.", "", "## Commands", ""])
        for command in manifest["commands"]:
            props = command["parameters_schema"].get("properties", {})
            arguments = ", ".join(f"`{k}` ({v.get('type', 'schema')})" for k, v in props.items()) or "none"
            lines.append(f"- `{command['name']}` — capability `{command['capability_id']}`; parameters: {arguments}.")
        if not manifest["commands"]:
            lines.append("Read-only adapter; no device write commands are declared.")
        lines.extend(["", "Available commands may be narrowed by configuration. All requests still pass role/device-scope checks. Transport acceptance is not automatically confirmed execution.", "", "## Custom configuration", "", "| Key | Label | Type | Default |", "| --- | --- | --- | --- |"])
        for key, definition in schema.get("properties", {}).items():
            if plugin_id == "phoenix.eem_ma370" and key == "seed":
                continue
            default = definition.get("default", "not set")
            default = "see manifest" if isinstance(default, (dict, list)) else json.dumps(default, ensure_ascii=False)
            lines.append(f"| `{key}` | {cell(definition.get('title', key.replace('_', ' ')))} | {definition.get('type', 'schema')} | {cell(default)} |")
        if not schema.get("properties"):
            lines.append("| — | No additional custom fields | — | — |")
        if schema.get("x-credential-label"):
            lines.extend(["", f"Credential input: **{schema['x-credential-label']}**. Stored outside custom config in the backend account's credential vault; never embed it in a URL."])
        lines.extend(["", "The manifest contains authoritative ranges, enums, required fields and defaults. Device name, category, endpoint, enabled, tags, polling and stale timeout are common fields, not custom config.", "", "## Verification and limitations", "", verification, "", "See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.", ""])
        (target / filename).write_text("\n".join(lines), encoding="utf-8")
    index.extend(["", "New adapters should follow the [plugin SDK](../plugin-sdk.md) and [example](../../examples/plugins/http_sensor). No bundled hardware adapter currently advertises the optional discovery API; do not confuse a connection handshake with automatic device adoption.", ""])
    (target / "README.md").write_text("\n".join(index), encoding="utf-8")


if __name__ == "__main__":
    main()
