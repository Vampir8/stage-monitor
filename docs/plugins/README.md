# Device plugin catalog

Each page documents the current English manifest, setup, telemetry, controls and limitations. Manifest IDs are stable API values; titles can be localized.

| Plugin | ID | Protocols |
| --- | --- | --- |
| [Denon DJ SC6000](denon-sc6000.md) | `denon.sc6000` | udp |
| [Generic OSC UDP](generic-osc.md) | `generic.osc` | udp |
| [Generic RTSP camera](generic-rtsp.md) | `generic.rtsp` | tcp |
| [MikroTik CRS326 / RouterOS](mikrotik-crs326.md) | `mikrotik.crs326` | http, https |
| [MikroTik RouterOS 7 — basic adapter](mikrotik-routeros.md) | `mikrotik.routeros` | http, https |
| [Obsidian NETRON EN12](netron-en12.md) | `netron.en12` | http, udp |
| [Generic NUT UPS](nut-ups.md) | `nut.ups` | tcp |
| [OBS Wave Player](obs-wave_player.md) | `obs.wave_player` | http |
| [OBSBOT Tail Air](obsbot-tail_air.md) | `obsbot.tail_air` | udp |
| [Generic OpenWrt router](openwrt-ubus.md) | `openwrt.ubus` | https, http |
| [Phoenix Contact EEM-MA370](phoenix-eem_ma370.md) | `phoenix.eem_ma370` | modbus |
| [Sennheiser SR IEM G4](sennheiser-iem_g4.md) | `sennheiser.iem_g4` | udp |
| [Shure QLX-D / QLXD4](shure-qlxd.md) | `shure.qlxd` | tcp |
| [Shure SLX-D / SLXD4 / SLXD4D](shure-slxd.md) | `shure.slxd` | tcp |

New adapters should follow the [plugin SDK](../plugin-sdk.md) and [example](../../examples/plugins/http_sensor). No bundled hardware adapter currently advertises the optional discovery API; do not confuse a connection handshake with automatic device adoption.
