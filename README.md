# Keenetic Router Pro

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![Version](https://img.shields.io/badge/version-1.5.1-blue.svg)](https://github.com/abovsh/Keenetic-Router-Pro)

Home Assistant custom integration for Keenetic routers. It focuses on local polling, router diagnostics, mesh monitoring, presence tracking, WAN status, traffic counters, firmware updates, and selected client controls.

## Why this fork

A maintained, hardened fork of the original Keenetic Router Pro integration. It keeps the domain name (`keenetic_router_pro`) so your existing dashboards, automations, and entity history carry over without changes.

### Bugs fixed

**Re-authentication and reconfigure never completed.** After changing router credentials in HA, the flow showed a broken form instead of finishing. The helper function returned the same type on both success and failure, making the success branch unreachable. This is fixed — credential updates now work correctly.

**Mesh nodes could lock up until HA restarted.** Auth headers for each mesh node were cached but never cleared on a 401 response. A credential rotation or a node session reset left the coordinator permanently locked out with a stale token. The cache is now evicted on 401 so the next poll re-authenticates automatically.

**Throughput displayed in bits instead of bytes.** The upstream reported WAN and IPsec throughput in B/s. All networking equipment and ISP plans use Mbit/s. Sensors now report in bits/s and HA offers automatic unit conversion to kbit/s or Gbit/s from the entity settings — no dashboard template tricks needed.

**Device URLs could render as `http://None`.** Fixed. Configuration URLs are only set when a valid address is available.

### Reliability and security improvements

- Credentials and session tokens are **redacted from all log output** — passwords, PSKs, cookies, and authorization headers never appear in debug logs.
- Authentication failures are correctly mapped to **`ConfigEntryAuthFailed`** so HA prompts for re-authentication instead of marking the entry as unavailable indefinitely.
- Mesh node authentication uses **NDW2-first with Basic Auth fallback**, matching actual Keenetic firmware behavior.
- **One-shot re-authentication** on expired session cookies before surfacing errors to HA.
- CLI arguments sent to `/rci/parse` are **validated** to prevent injection-style inputs.
- Existing passwords are **not pre-filled** in reconfigure forms.

### Reduced router load

- Slow-changing data (mesh topology, firmware info, host policies, NDNS) is **cached across ticks** — fetched every 60 s or 300 s instead of every 10 s.
- Client connection, disconnection, and extender counts are **derived from already-fetched client data** instead of separate API calls.
- Interface stats are **only polled for interfaces** that back enabled sensors.

### Leaner footprint

- **QR-code image platform removed** (449 lines, `pyqrcode`/`pypng` dependencies dropped) — generating Wi-Fi QR codes from HA is rarely useful when the router already shows them.
- **USB storage polling removed** — this required frequent polling of optional components that may not be present, adding load and noise.
- **English only** — the upstream shipped mixed-language source comments and non-English translations. Everything here is English.
- **HACS source download** — no release ZIP assets required; installs directly from the repository archive.

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

### Credential handling

Home Assistant stores integration credentials in plain text at
`/config/.storage/core.config_entries`. That is an HA-wide design choice
and is not specific to this integration. What this integration does on
top of that:

- The password input in the config flow (setup, reauth, reconfigure) is
  rendered as a masked field (`TextSelectorType.PASSWORD`).
- The API client overrides `__repr__` so accidental log statements that
  include the client object cannot leak the password.
- Request-payload summaries and HTTP response excerpts in debug logs
  are redacted for known sensitive keys (`password`, `cookie`,
  `authorization`, `psk`, `secret`, `key`).
- The HA *Download diagnostics* button on the config entry produces a
  JSON dump that runs through `async_redact_data`, stripping
  credentials, MACs, SSIDs, BSSIDs, PSKs, session cookies and
  authorization headers. Safe to attach to bug reports.
- Nothing is written into `custom_components/keenetic_router_pro/` at
  runtime — no credential cache, no state file.

See [`SECURITY.md`](SECURITY.md) for recommended file permissions on
`/config/.storage/` and the password rotation procedure.

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
