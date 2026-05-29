# Keenetic Router Pro

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![Version](https://img.shields.io/badge/version-1.7.49-blue.svg)](https://github.com/abovsh/Keenetic-Router-Pro)

Home Assistant custom integration for Keenetic routers. It focuses on local polling, router diagnostics, mesh monitoring, presence tracking, WAN status, traffic counters, firmware updates, and selected client controls.

The integration is local-polling first and has no cloud dependency for direct LAN use. KeenDNS protected mode is optional and only used when you explicitly choose that connection mode.

## Why this fork

A maintained, hardened fork of the original Keenetic Router Pro integration. It keeps the domain name (`keenetic_router_pro`) so your existing dashboards, automations, and entity history carry over without changes.

### Practical reasons to use it

- **Works with current Home Assistant patterns.** The integration uses
  config-entry `runtime_data`, modern reauth/reconfigure helpers, unload-safe
  listeners, and cancellation-safe async paths, so reloads, shutdowns and
  credential changes behave like a maintained HA integration.
- **Safer by default.** Diagnostics, debug logs, API response excerpts and
  client object representations redact router passwords, cookies, Basic Auth
  headers, MACs, SSIDs, BSSIDs and Wi-Fi PSKs. Password fields are masked, old
  passwords are not pre-filled, and plaintext-HTTP setups raise a Repair issue.
- **More reliable authentication.** Direct connections support Basic Auth and
  NDW2 challenge auth with session-cookie reuse, one-shot reauth after expired
  cookies, and automatic mesh-node cookie refresh after 401 responses.
- **KeenDNS protected web app support.** You can connect through a protected
  KeenDNS hostname over HTTPS/443 when Home Assistant cannot reach the router
  directly over LAN or VPN.
- **Lower router load.** Slow data such as mesh topology, firmware info,
  host policies, NDNS and IPsec diagnostics is cached across ticks. Client
  totals reuse already-fetched client data, and interface stats are polled
  only for interfaces that back enabled entities. On KeeneticOS 5.x the
  per-tick reads are batched into a single composite RCI request,
  cutting HTTP round-trips by ~10×.
- **Better WAN and VPN visibility.** Per-uplink devices expose status,
  enabled state, role, public IP, uptime, counters and throughput. VLAN WAN
  interfaces and IPsec crypto-map throughput are handled, and VPN controls are
  grouped with the interface or WAN device they belong to.
- **Mesh is treated as a first-class surface.** Mesh nodes and ports can appear
  dynamically without restarting Home Assistant, removed nodes become
  unavailable instead of stale, firmware update entities support controller and
  extender flows, and mesh unique IDs are scoped to the HA config entry.
- **Useful diagnostics, not just raw counters.** The fork adds DNS proxy
  health and failed-request sensors, IPsec VICI out-of-memory diagnostics,
  ping-check aware WAN interpretation, WireGuard/IPsec state sensors, and
  long-term-statistics-friendly uptime classes.
- **Presence and client controls are less noisy.** Client lookups use a
  precomputed MAC index, per-client entities skip no-op state writes, selected
  client presence is based on Keenetic's own link/active state, and
  tracked-client uptime/last-seen sensors update on their own cadence.
- **Actively regression-tested.** The integration is covered by automated
  checks for API parsing, authentication lifecycle, configuration flows, mesh
  entities, translations, statistics classes, diagnostics redaction and
  cancellation safety.

### Removed surface area

- **QR-code image platform removed** — generating Wi-Fi QR codes from HA is rarely useful when the router already shows them.
- **USB storage polling removed** — this required frequent polling of optional components that may not be present, adding load and noise.
- **English only** — the upstream shipped mixed-language source comments and non-English translations. Everything here is English.
- **HACS source download** — no release ZIP assets required; installs directly from the repository archive.

## Features

- Local polling through the Keenetic RCI API.
- Basic Auth and NDW2 challenge authentication.
- Config flow, reauthentication, and reconfigure support.
- Main router sensors for CPU, memory, uptime, firmware, WAN state, IP, PPPoE uptime, active connections, ports, Wi-Fi radio temperature, and traffic.
- Mesh node sensors for state, CPU, memory, uptime, firmware, clients, local IP, ports, and traffic.
- Router-based device tracking for selected clients.
- Wi-Fi, VPN, and client policy controls where supported by the router firmware.
- Firmware update entities for the controller and mesh nodes.
- WireGuard and IPsec diagnostic sensors.
- IPsec VICI OOM Total: a monotonic counter of
  `IpSec::Vici::Stats: out of memory` events from the router log,
  persisted across HA restarts and HA-Statistics-friendly for
  `events/hour` graphs.
- WAN and IPsec throughput shown in Mbit/s with automatic unit conversion (kbit/s ↔ Mbit/s ↔ Gbit/s) in the HA entity UI.
- WAN interface devices group status, public IP, role, traffic counters,
  throughput and enable/disable control for each uplink.
- VPN controls are grouped with the VPN/interface device they control and use
  a single `Enabled` switch. VPN uplinks share the same WAN device as their
  WAN status sensors, without duplicate WireGuard controls.

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
| Connection mode | Direct/local API or KeenDNS protected web app | `Direct / local` |
| Host | Router IP address or host name | `192.168.1.1` |
| Port | Router web/API port, direct mode only | `100` |
| Username | Router admin username | `admin` |
| Password | Router admin password | `********` |
| SSL | Use HTTPS for router API calls, direct mode only | `off` |
| Use challenge authentication | Enable NDW2 challenge authentication for newer models, direct mode only | `off` |

Use **challenge authentication** for models/firmware that reject Basic Auth, such as newer Keenetic Hero devices. Older devices usually keep it disabled.

### KeenDNS protected web app

The integration can also connect through a KeenDNS **Password protected** web
application. This is useful when Home Assistant cannot reach the router over
LAN/VPN, but the router is published through a protected KeenDNS hostname.

Tested working shape:

```text
Home Assistant -> https://<app>.<domain>.keenetic.pro/rci/...
KeenDNS protected app -> This Keenetic device, internal HTTP app port
```

Use these Home Assistant settings:

| Field | Value |
| --- | --- |
| Connection mode | `KeenDNS protected web app` |
| Host | The protected app hostname, e.g. `rsi.example.keenetic.pro` |

In this mode the form hides port, SSL, and challenge-auth options. The
integration always uses external HTTPS on port `443` with Basic Auth, matching
the tested KeenDNS protected web-app behavior.

On the router, publish the app as:

- Client: `This Keenetic device`
- Internal app protocol: `HTTP`
- Internal app port: the working RCI/web app port for this publication
- External access: HTTPS KeenDNS hostname
- User permission: `HTTP Proxy`

Live testing showed that a user with only `HTTP Proxy` permission could access
`/rci/show/...`, `/rci/parse`, and management commands through the protected
app. Treat that account like a management credential. Use a dedicated random
password and rotate it after sharing verbose curl logs, because Basic Auth
headers in logs can be decoded.

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
- For KeenDNS protected web-app access, use external HTTPS only. Plain HTTP
  sends Basic Auth credentials without transport encryption.

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
  authorization headers. Diagnostics are redacted before export, but avoid
  sharing router diagnostics publicly unless you have reviewed them.
  Redaction reduces accidental exposure; it cannot make every router-specific
  payload safe to publish.
- Nothing is written into `custom_components/keenetic_router_pro/` at
  runtime — no credential cache, no state file.

See [`SECURITY.md`](SECURITY.md) for recommended file permissions on
`/config/.storage/` and the password rotation procedure.

## Entities

Common entity groups:

- Router device: router-wide health, firmware, reboot, client totals, ports,
  Wi-Fi radio temperature and legacy WAN summary sensors kept for compatibility.
- WAN interface devices: per-uplink connectivity, enabled state, enable switch,
  provider, role, public IP, uptime, traffic counters and throughput.
- VPN interface devices: VPN state and enable/disable control for VPN profiles
  that are not WAN uplinks.
- IPsec crypto-map devices: site-to-site tunnel state, IKE state, traffic,
  throughput and enable/disable control.
- Sensors: router health, WAN state, traffic, ports, Wi-Fi radio temperature, mesh diagnostics, VPN diagnostics, client details.
- Binary sensors: firmware/update availability, mesh/client/connectivity status.
- Switches: Wi-Fi networks, WAN interfaces, VPN profiles, client blocks where supported.
- Selects: client connection policy where supported.
- Buttons: router and mesh node reboot.
- Update entities: controller and mesh firmware updates.
- Device trackers: selected client presence via Keenetic link/active state.

Exact entity availability depends on router model, firmware version, enabled Keenetic components, and selected tracked clients. Optional firmware features may appear unavailable or use cached data on routers or KeeneticOS builds that do not expose the corresponding endpoint.

## Troubleshooting

If setup fails:

1. Verify host, port, username, and password.
2. Confirm the router web management API is enabled.
3. Try enabling **challenge authentication** for newer Keenetic models.
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

## License

MIT License.
