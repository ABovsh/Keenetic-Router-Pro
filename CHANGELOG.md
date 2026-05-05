# Changelog

All notable changes to this integration are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Entries are written for end users (HACS installs); each release is grouped by
what you actually notice on your dashboard. For per-commit detail, see the
git log.

## 1.6.3 - WireGuard entity cleanup

### Fixes

- **Removed duplicate WireGuard entities from the main router device.** The old
  WireGuard-specific RX/TX/Uptime sensors are no longer created because the
  per-interface/WAN device model already exposes the relevant state in the
  correct place.
- **Removed duplicate VPN controls on WireGuard WAN devices.** VPN uplinks now
  keep the WAN `Enabled` switch only, instead of showing both `Enabled` and a
  separate `WireGuard` switch.
- **Duration sensors now request whole-second display precision.** WAN and
  PPPoE uptime sensors set HA's suggested precision to `0`, avoiding noisy
  values like `212.00 s` where Home Assistant respects the hint.

## 1.6.2 - Interface device organization and VLAN WAN throughput

### Fixes

- **VLAN WAN throughput now works.** WAN VLAN interfaces such as
  `GigabitEthernet0/Vlan5` are no longer skipped when collecting interface
  statistics. The integration now uses Keenetic's working
  `show interface <name> stat` command first and keeps the older RCI GET form
  as a fallback.

### Improvements

- **WAN interfaces now have an Enable switch on their own HA device.** The
  switch uses the same Keenetic interface up/down control as the web UI and is
  grouped with the WAN's status, IP, role, counters and throughput sensors.
- **VPN controls are grouped with the interface they control.** VPN switches
  now attach to the matching WAN device when the VPN is an uplink; otherwise
  they appear under their own VPN/interface device instead of the main router.
- **Throughput sensors expose raw stat details.** Per-WAN throughput entities
  now include the raw `rxbytes`, `txbytes`, `rxspeed`, `txspeed`, stat
  interface and stat timestamp as attributes for easier troubleshooting.

## 1.6.1 - Mesh firmware update start fix

### Fixes

- **Mesh node firmware updates now use the controller MWS command first.**
  KeeneticOS starts extender updates with `mws member <member> update start`;
  the previous direct-node component update path could fail with "Could not
  start firmware update on node ...". The direct-node path remains as a
  fallback for older or unusual setups.

## 1.6.0 - DNS over HTTPS diagnostics

### Improvements

- **New DNS Proxy Status sensor** — shows whether the router's DNS proxy is
  healthy, degraded, down or unknown. This helps detect the failure mode where
  raw IP connectivity still works but DNS over HTTPS stops answering.
- **New DNS Proxy Failed Requests sensor** — exposes failed upstream DNS proxy
  requests from the router's own stats so you can build Home Assistant
  automations around DNS/DoH trouble without scraping router logs.

### Internal

- DNS proxy health is polled on the existing slow coordinator cadence and reuses
  Keenetic RCI state (`show dns-proxy`), so it does not add a separate polling
  loop or parse local Home Assistant logs.

## 1.5.1 - Stability and reload hygiene

### Bug fixes

- **Memory leak when reloading the integration** — every "Reload" action (or
  options-flow change, which reloads under the hood) used to leave behind an
  invisible event listener bound to the previous coordinator. Over enough
  reloads this could grow Home Assistant's memory footprint and cause
  duplicate "new device" events. The listener is now properly unregistered
  on unload.
- **Cleaner shutdown when Home Assistant is stopping** — the ICMP ping loop
  now correctly propagates cancellation during HA shutdown instead of
  swallowing it. Stopping HA mid-tick no longer logs spurious "ping failed"
  noise.
- **Friendlier error if the router host is missing from the config entry** —
  if a config entry somehow ends up without a `host` value (e.g. after a
  migration glitch), setup now fails fast with a clear "please reconfigure"
  message instead of crashing later with an opaque `NoneType` error.

### Improvements

- **Slightly faster fast-tick** — the 10-second coordinator tick no longer
  rebuilds two intermediate cache dicts on every iteration. Negligible by
  itself, but Home Assistant runs this loop ~8 600 times per day.
- **Cleaner setup-error logs** — fixed a duplicated error message when an
  unexpected exception happens during initial config-flow setup. The
  traceback was already included; the duplicate string is gone.

### Internal

- Removed dead code paths and a redundant attribute declaration on the
  device-tracker entity. No behaviour change.

## 1.5.0 - Security hardening

### Security

- **Diagnostics downloads no longer leak your router password.** Before this
  release, the "Download diagnostics" button on the integration card produced
  a JSON file that could include your Keenetic credentials, session cookies,
  Wi-Fi PSKs, MAC addresses and SSIDs in plain text. If you attached that
  file to a GitHub issue or shared it for support, you were leaking secrets.
  The diagnostics dump is now passed through Home Assistant's redaction
  helper and all of those fields are replaced with `**REDACTED**` before the
  file is written.
- **Password input is now masked in the UI.** The router password field in
  the initial setup, re-auth and reconfigure dialogs is now a proper
  password input — characters render as dots instead of plain text. Prevents
  shoulder-surfing and accidental screenshot leaks during setup.
- **Internal client object can no longer leak credentials in logs.** A
  defensive change so that any stray debug-log line or traceback that
  includes the API client object now shows `<redacted>` for username and
  password (host/port/SSL stay visible for troubleshooting).

### Documentation

- New [`SECURITY.md`](SECURITY.md) explaining where Home Assistant stores
  the router password (`/config/.storage/core.config_entries`, plain text by
  HA design — this is not specific to this integration), recommended file
  permissions, password-rotation procedure, and what the integration
  redacts in logs and diagnostics.

### Notes

- No config-entry schema change → no migration required, just upgrade and
  restart.
- These changes are part of an ongoing security audit of the fork. If you
  previously shared a diagnostics dump publicly, consider rotating your
  router password as a precaution.

## 1.4.0 - Bug fixes, throughput units, and code cleanup

### Bug fixes

- **Re-auth and reconfigure flows finally work.** Previously, when Home
  Assistant prompted you to re-enter the router password (after a
  credential change or session expiry), submitting the form silently did
  nothing — the dialog re-rendered with cryptic error strings instead of
  completing. Both flows now correctly close on success.
- **Mesh nodes no longer get stuck after a password change.** If you
  rotated the password on a mesh node, the integration kept using the old
  cached auth token until you restarted Home Assistant. The bad token is
  now evicted automatically on the first `401 Unauthorized` response.
- **Local-IP sensor is more robust to internal refactors.** A small
  encapsulation fix that prevents the sensor from breaking if an internal
  attribute on the API client is ever renamed.

### Improvements

- **WAN and IPsec throughput shown in Mbit/s, not bytes/s.** All
  networking equipment and ISP plans are quoted in megabits per second,
  so the previous `B/s` reading required mental math. Sensors now report
  in Mbit/s with two decimal places, and Home Assistant offers automatic
  unit conversion (kbit/s ↔ Mbit/s ↔ Gbit/s) directly in the entity
  customisation dialog — no template tricks needed.

### Internal

- Removed redundant initialisations in the device-tracker entity; cleaned
  up a few useless f-strings; replaced legacy `Dict[...]` / `Optional[...]`
  type annotations with the modern built-in syntax; translated leftover
  Russian/Turkish comments to English. Coordinator fast-tick made cheaper
  by collapsing three no-op async wrappers into one sync precomputation.

---

## 1.3.0 - Fork hardening and performance

This is the first release of the maintained fork. The Home Assistant domain
stays `keenetic_router_pro` so existing dashboards, automations, and entity
history carry over unchanged from the upstream version.

### Security

- Safer Basic Auth header construction that no longer risks leaking
  credentials into debug logs.
- Support for the newer NDW2 challenge-auth scheme used by recent Keenetic
  firmwares, including session-cookie reuse so we don't re-authenticate on
  every call.
- Automatic one-shot re-authentication after expired session cookies before
  surfacing failure to Home Assistant.
- Sensitive values (passwords, PSKs, cookies, `Authorization` headers, keys,
  secrets) are now redacted from API error excerpts and debug logs.
- Raw config-flow form input is no longer logged at debug level — your
  password is no longer written to `home-assistant.log` if debug logging
  is enabled.
- CLI arguments sent to `/rci/parse` are now validated against an allow-list
  to prevent command-injection style input.
- The reconfigure form no longer pre-fills the existing password as a
  default value.

### Improvements

- **Proper re-auth and reconfigure flows.** When your router password
  changes, HA now correctly prompts you to re-enter it instead of marking
  the integration as permanently failed.
- **Lower router CPU load.** Slow-changing data (firmware version, mesh
  topology, NDNS info) is now polled on a much longer cycle. Interface
  statistics are only fetched for interfaces that back enabled sensors.
- Connected/disconnected/extender counts are now derived from already-
  fetched client data instead of issuing extra API calls.
- Fixed a class of bugs where device URLs could appear as `http://None` in
  the device registry.
- Wi-Fi presence-tracking interval is configurable from 5 to 300 seconds
  via the integration's options.
- The integration no longer requires the `pyqrcode` and `pypng`
  dependencies — the Wi-Fi QR-image platform was removed.

### Removed

- USB device polling (controller and mesh nodes) — produced more noise
  than value and added load to slower routers.
- Wi-Fi QR-code image platform.
- Non-English translation files (English only is shipped — translations
  can be contributed back via PR if there is demand).

### Documentation

- Lighter, more practical README focused on install / config /
  troubleshooting.
- Manifest documentation and issue-tracker links repointed to the fork.

### Tests and tooling

- Added a lightweight pytest suite covering CLI argument validation,
  Basic Auth header generation, interface normalisation, client-stat
  summaries, and log/payload redaction.
- GitHub Actions workflow now runs `compileall` and the pytest suite on
  every push.
