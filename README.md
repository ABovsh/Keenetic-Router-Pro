# Keenetic Router Pro

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![Version](https://img.shields.io/badge/version-1.4.0-blue.svg)](https://github.com/abovsh/Keenetic-Router-Pro)

Home Assistant custom integration for Keenetic routers. It focuses on local polling, router diagnostics, mesh monitoring, presence tracking, WAN status, traffic counters, firmware updates, and selected client controls.

## Why this fork

This is a personal fork maintained for two Keenetic Titan routers (KN-1812) running as a Mesh system with an IPsec site-to-site tunnel between two properties. It exists for three reasons.

**Trimmed to what's actually used.** The upstream integration ships a QR-code image platform (449 lines, requires `pyqrcode`/`pypng`) and USB storage polling that are not useful for most router monitoring setups. Both are removed. The result is a smaller codebase that is easier to reason about.

**English only.** The upstream included non-English translations and mixed-language source comments. This fork ships only English strings and keeps the source code in English throughout.

**Bugs fixed during review.**

| Bug | Impact |
|---|---|
| Reauth/reconfigure flow never completed | After a credential change HA showed a broken form instead of completing re-auth |
| Stale mesh node auth cache | A 401 from a node left the coordinator locked out with a bad cached token until HA restarted |
| Throughput reported in B/s | Network speeds are measured in bits/s; sensors now report Mbit/s with automatic kbit/s ↔ Mbit/s ↔ Gbit/s conversion in the HA entity UI |
| Private `client._host` access in sensor setup | Bypassed encapsulation; replaced with `entry.data` lookup |

The integration domain stays `keenetic_router_pro` so existing HA configurations, dashboards, and entity history are preserved.

## Features

- Local polling through the Keenetic RCI API.
- Basic Auth and NDW2 challenge authentication.
- Config flow, reauthentication, and reconfigure support.
- Main router sensors for CPU, memory, uptime, firmware, WAN state, IP, PPPoE uptime, active connections, ports, Wi-Fi radio temperature, and traffic.
- Mesh node sensors for state, CPU, memory, uptime, firmware, clients, local IP, ports, and traffic.
- Optional ping-based device tracking for selected clients.
- Wi-Fi, VPN, and client policy controls where supported by the router firmware.
- Firmware update entities for the controller and mesh nodes.
- WireGuard and IPsec diagnostic sensors.
- WAN and IPsec throughput shown in Mbit/s with automatic unit conversion (kbit/s ↔ Mbit/s ↔ Gbit/s) in the HA entity UI.

Removed from this fork: QR image entities, USB polling, bundled non-English translations, and ZIP-release mode for HACS.

## Install With HACS

1. In HACS, add this repository as a custom integration repository:
   `https://github.com/abovsh/Keenetic-Router-Pro`
2. Install **Keenetic Router Pro**.
3. Restart Home Assistant.
4. Go to **Settings > Devices & services > Add integration**.
5. Search for **Keenetic Router Pro**.

This repository uses standard HACS source downloads and does not require release assets.

## Configuration

Required fields:

| Field | Description | Example |
| --- | --- | --- |
| Host | Router IP address or host name | `192.168.1.1` |
| Port | Router web/API port | `100` |
| Username | Router admin username | `admin` |
| Password | Router admin password | `********` |
| SSL | Use HTTPS for router API calls | `off` |
| Use Challenge Auth | Enable NDW2 challenge auth for newer models | `off` |

Use **Challenge Auth** for models/firmware that reject Basic Auth, such as newer Keenetic Hero devices. Older devices usually keep it disabled.

## Polling

- Main coordinator: every `10s`.
- Slow data: every `60s` after startup.
- Very slow data: every `300s` after startup.
- Ping presence tracking: default `5s`, configurable from `5` to `300s`.

Slow and very slow data is cached between refreshes to reduce router load.

## Security Notes

- Keep the router management API reachable only from Home Assistant.
- Prefer LAN-only access.
- If you expose a custom management port, restrict it with firewall rules to the Home Assistant IP.
- Do not expose router management to WAN without strict firewall rules.

## Entities

Common entity groups:

- Sensors: router health, WAN state, traffic, ports, Wi-Fi radio temperature, mesh diagnostics, VPN diagnostics, client details.
- Binary sensors: firmware/update availability, mesh/client/connectivity status.
- Switches: Wi-Fi networks, VPN profiles, client blocks where supported.
- Selects: client connection policy where supported.
- Buttons: router and mesh node reboot.
- Update entities: controller and mesh firmware updates.
- Device trackers: selected client presence via ICMP ping.

Exact entity availability depends on router model, firmware version, enabled Keenetic components, and selected tracked clients.

## Troubleshooting

If setup fails:

1. Verify host, port, username, and password.
2. Confirm the router web management API is enabled.
3. Try enabling **Use Challenge Auth** for newer Keenetic models.
4. Check that Home Assistant can reach the router over the configured port.

If HACS download fails:

1. Make sure HACS has this repository as a custom integration repository.
2. Remove the repository from HACS and add it again after updating this fork.
3. Clear any failed pending download and retry.
4. Confirm `hacs.json` only contains the integration name and `render_readme`.

For debug logs:

```yaml
logger:
  default: warning
  logs:
    custom_components.keenetic_router_pro: debug
```

Sensitive values are redacted from integration logs where practical.

## Development

Run lightweight checks:

```bash
python -m compileall -q custom_components/keenetic_router_pro tests
python -m pytest -q
```

The lightweight tests cover helper behavior without requiring a full Home Assistant runtime or a live router.

## License

MIT License.
