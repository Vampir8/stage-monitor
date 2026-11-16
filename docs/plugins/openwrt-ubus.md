# Generic OpenWrt router

Monitor an OpenWrt router or gateway through its uhttpd ubus JSON-RPC endpoint.

- Plugin ID: `openwrt.ubus`
- Adapter version: `0.1.0`
- Plugin API: `1.2`
- Communication: `polling`
- Endpoint transports: `https`, `http`
- Default endpoint port: `443`
- [Manifest/source](../../backend/src/stage_monitor/plugins/openwrt)

## Setup and behavior

Add the adapter in **Devices → Add device**, choose the appropriate category, set the reachable host/port and configure polling/stale timeout. Keep monitoring paused when equipment is not in use. Configuration is shared across operators; dashboard layout can select collapsed/expanded features.

Install or enable `uhttpd-mod-ubus` and `rpcd` on the router. Use HTTPS on port 443 with a trusted certificate or supply the CA file on the backend host. Save the RPC password in the device editor. For a legacy device on an isolated LAN, select HTTP port 80 and explicitly enable the plain HTTP option.

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

By default the adapter selects common physical Ethernet, SFP and Wi-Fi interface names. Enter a JSON list of interface names to override the selection. RX/TX rates are calculated per interface from byte counters and require two consecutive samples. CPU load is the Linux load average. Temperature is optional. Set an exact Linux thermal or hwmon sysfs path and permit the `file.read` ubus method and read access to that path in the RPC account's ACL. The path must expose millidegrees Celsius.

## Telemetry

| Key | Label | Type | Unit |
| --- | --- | --- | --- |
| `system.uptime` | Uptime | string | — |
| `system.uptime_seconds` | Uptime in seconds | number | s |
| `system.memory_free_mib` | Free memory | number | MiB |
| `system.memory_total_mib` | Total memory | number | MiB |
| `system.load_1m` | Load average (1 min) | number | — |
| `system.load_5m` | Load average (5 min) | number | — |
| `system.load_15m` | Load average (15 min) | number | — |
| `system.firmware` | Firmware version | string | — |
| `system.kernel` | Linux kernel | string | — |
| `temperature` | Temperature | number | degC |
| `network.ports_summary` | Interfaces UP | string | — |
| `network.ports_up` | Interfaces UP | number | — |
| `network.ports_total` | Interfaces | number | — |
| `network.port_up` | Link up | boolean | — |
| `network.port_rx_bps` | Interface RX | number | bit/s |
| `network.port_tx_bps` | Interface TX | number | bit/s |
| `network.port_rx_errors` | RX errors | number | — |
| `network.port_tx_errors` | TX errors | number | — |
| `network.port_rx_dropped` | RX dropped packets | number | — |
| `network.port_tx_dropped` | TX dropped packets | number | — |

Metrics can be channelized. Missing/stale/invalid values are not invented zeros. Optional data depends on device/firmware and the selected communication mode.

## Commands

Read-only adapter; no device write commands are declared.

Available commands may be narrowed by configuration. All requests still pass role/device-scope checks. Transport acceptance is not automatically confirmed execution.

## Custom configuration

| Key | Label | Type | Default |
| --- | --- | --- | --- |
| `username` | OpenWrt RPC username | string | "root" |
| `allow_plain_http` | Allow HTTP on a trusted network (unencrypted password) | boolean | false |
| `ca_file` | HTTPS CA file (optional) | string | "not set" |
| `interfaces` | Monitored interface names | array | see manifest |
| `temperature_path` | Temperature sensor path (optional) | string | "not set" |

Credential input: **OpenWrt password**. Stored outside custom config in the backend account's credential vault; never embed it in a URL.

The manifest contains authoritative ranges, enums, required fields and defaults. Device name, category, endpoint, enabled, tags, polling and stale timeout are common fields, not custom config.

## Verification and limitations

Verified on a Dragino MS14N-S with its 4.3.7 firmware and the legacy OpenWrt 15.05.1 ubus endpoint. Loopback tests cover session expiration, per-interface counters, reboot detection and an optional thermal sensor. [OpenWrt ubus documentation](https://openwrt.org/docs/techref/ubus?s%5B%5D=1&s%5B%5D=default).

See [operations](../operations.md), [API](../api.md), [media](../media.md) and the [plugin SDK](../plugin-sdk.md) for shared behavior. A local fixture test does not certify all hardware variants.
