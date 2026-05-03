# Keenetic Router Pro

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![Version](https://img.shields.io/badge/version-1.4.0-blue.svg)](https://github.com/abovsh/Keenetic-Router-Pro)

Home Assistant custom integration for Keenetic routers. It focuses on local polling, router diagnostics, mesh monitoring, presence tracking, WAN status, traffic counters, firmware updates, and selected client controls.

## Why this fork

The upstream integration had a handful of issues that affected real-world use:

- **Reauth flow broken** — the `_async_update_existing_entry` helper returned a `dict` on both success and failure, but the caller checked `not isinstance(result, dict)` to detect success. `FlowResult` is always a `dict`, so the check was always `False`: re-auth and reconfigure flows never completed the `async_abort` step and instead showed a form filled with the abort dict as "errors".
- **Throughput shown in B/s** — WAN and IPsec tunnel throughput sensors used `BYTES_PER_SECOND`. Network speeds are conventionally shown in bits per second; this fork converts to Mbit/s with `SensorDeviceClass.DATA_RATE` so HA can auto-convert between kbit/s, Mbit/s, and Gbit/s in the entity UI.
- **Stale mesh auth cache** — mesh node auth headers were cached per (ip, port) but never evicted on a 401 response. Rotating credentials or a session reset would leave the coordinator locked out until HA restarted.
- **Dead weight** — QR image platform (449 lines), USB polling module (175 lines), and several unused constants added noise without providing any working feature.
- **Coordinator helper overhead** — three async inner functions (`_cached`, `_cached_update_info`, `_cached_version_info`) that did zero I/O were called inside every `asyncio.gather`, creating unnecessary coroutine objects every 10 s.
- **Private attribute access** — sensor setup accessed `client._host` instead of reading the value from `entry.data`.

This fork keeps the integration domain unchanged (`keenetic_router_pro`) so existing HA configurations and entity histories are preserved.

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
