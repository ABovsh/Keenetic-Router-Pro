# Changelog

All notable changes to this integration are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Entries are written for end users (HACS installs); each release is grouped by
what you actually notice on your dashboard. For per-commit detail, see the
git log.

## 1.7.1 - HACS validation fixes for 1.7.0

Hotfix for two HACS validation errors that 1.7.0 tripped:

- **`min_ha_version` is not a valid `manifest.json` key** — that field
  is reserved for HA core's internal integration manifests, not custom
  components. The minimum HA version is now declared in `hacs.json`
  via the standard `homeassistant: "2024.5.0"` key, which is what
  HACS actually reads.
- **`CONFIG_SCHEMA` warning** — hassfest requires every integration
  that defines `async_setup` to declare a config schema, even when
  it has no YAML support. The integration root now exposes
  `CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)` — the
  canonical "UI-only, no YAML" helper.

No user-visible behaviour change vs 1.7.0; this release exists so the
HACS marketplace stops rejecting installs.

## 1.7.0 - Hardening, modern HA APIs, and statistics fixes

> ⚠️ **Minimum Home Assistant version bumped to 2024.5.0.** This release
> uses HA's `runtime_data` config-entry pattern, which is unavailable
> on older HA core versions. If you are still on 2024.4 or earlier,
> stay on 1.6.8 until you upgrade HA.

### 🔒 Security

- **`SECURITY.md` shipped.** Documents what the integration redacts
  (diagnostics, logs, repr) and — honestly — what it cannot protect:
  HA stores config-entry passwords as plaintext in
  `<config>/.storage/core.config_entries`, and no integration can fix
  that. If you have ever shared a HA backup or a `.storage/` snapshot,
  rotate your router admin password.
- **Cancellation safety in every error-handling path.** Every
  `except Exception` block in the API client, coordinator, firmware-
  update flow, and config flow now re-raises `asyncio.CancelledError`
  before falling through to its generic handler. The old broad catch
  swallowed HA's shutdown signal during integration reload, sometimes
  producing hangs that needed a HA restart to resolve. A new
  `tests/test_cancellation_safety.py` parses the source and fails CI
  if a regression slips back in.

### 🐛 Bug fixes

- **Uptime sensors no longer produce a sawtooth in long-term graphs.**
  Router uptime, PPPoE uptime, and WireGuard tunnel uptime were
  declared as `MEASUREMENT`, which made HA's recorder treat each poll
  as a separate gauge value and store a 1-week sawtooth in the LTS
  table. They are now `TOTAL_INCREASING` — the right state class for
  a monotonic counter that resets on reboot/reconnect — and the
  long-term-statistics graph for those sensors is now smooth.
- **Reauth and reconfigure use the modern HA helper.** Both flows now
  call `async_update_reload_and_abort` instead of the deprecated
  `async_update_entry` + `async_abort` pair. The previous pattern
  occasionally left users running with stale credentials until they
  manually reloaded the integration; the new flow reloads in the
  same step.

### ✨ Improvements

- **`runtime_data` migration.** The integration now stores its API
  client and coordinators on `entry.runtime_data` instead of
  `hass.data[DOMAIN][entry.entry_id]`. Cleaner platform setup, less
  bookkeeping in `async_unload_entry`, and the data is automatically
  dropped by HA when the entry is removed. No user-visible change —
  but if you happen to write blueprints or scripts that poke at
  `hass.data["keenetic_router_pro"]`, they need updating.
- **`min_ha_version: 2024.5.0` declared in the manifest.** HACS will
  refuse to install on older HA cores rather than letting you hit a
  cryptic `runtime_data` AttributeError at setup time.

### 🔧 Internal

- **Test suite grew from 67 to 81 tests** (one skipped — needs full
  HA env). New regression guards: cancellation propagation in hot-
  path modules, `runtime_data` shape, modern config-flow pattern,
  uptime state classes, and `clients_by_mac` precomputed lookup.
- **Coordinator parallelism audit (no code change).** The Stage 1 +
  Stage 2 + Stage 3 pipeline already uses `asyncio.gather(...,
  return_exceptions=True)` with critical-fetch fail-fast and a
  single aggregated warning per tick — confirmed during this audit
  and now exercised by the cancellation-safety tests.

### Deferred

- **`api.py` module split.** The 2 985-line `api.py` is the codebase's
  largest file. A clean split would move the `_validate_cli_arg`,
  `_response_summary`, `_payload_summary` helpers into their own
  module and break the `KeeneticClient` class apart by RCI surface
  (read / write / parse / redact). The unit tests import the helpers
  by their current names, so the move requires careful re-export
  scaffolding. Punted to a 2.0 release rather than risk a bad split
  in this minor.
- **Generic `RetryableEndpoint` wrapper for endpoint auto-discovery.**
  Initially planned, but the two candidate sites (mesh-node fallback,
  VPN-tunnel discovery) need different cache semantics — one caches
  "endpoint unsupported", the other doesn't. A wrapper covering both
  would be either too thin to be useful or too magic to be readable.
  Per the "don't abstract for fewer than three sites" rule, the
  explicit code is better.

## 1.6.8 - Performance refactor

### Performance

- **Coordinator builds an O(1) MAC-keyed client index.** Per-client entities
  (sensors, switches, device-trackers) used to scan the full client list on
  every coordinator tick to find their own row. The coordinator now publishes
  `clients_by_mac`, and entities look themselves up directly. On a network
  with hundreds of tracked devices this turns an O(N²) per-tick cost into
  O(N).
- **Per-client entities skip no-op state writes.** `ClientEntity` now compares
  a fingerprint of its client row (excluding `last-seen` / `uptime` ticks) and
  short-circuits `_handle_coordinator_update` when nothing meaningful changed.
  Idle clients no longer trigger HA state writes every poll cycle.
- **Interface stats fetched in parallel.** `async_get_all_interface_stats`
  now uses `asyncio.gather` instead of sequential awaits, cutting WAN-stats
  fetch latency on multi-interface routers.
- **Interface list shared across the polling stages.** Stage 1 now fetches
  `iface_list` once and passes it through to stage 2, mesh fetch, and the
  WAN-status projection — eliminating ~3 redundant `show interface` round-trips
  per coordinator tick.
- **Mesh fetch reuses the already-fetched client list.** `_get_mesh_nodes_from_clients`
  accepts a pre-fetched `clients=` argument so we don't re-call
  `async_get_clients()` when the coordinator just fetched it.

### Internal

- Migrated `async_timeout` → stdlib `asyncio.timeout` (Python 3.11+).
- Centralized magic strings (`WAN_STATUS_*`, `IPSEC_STATE_ESTABLISHED`,
  `TRUTHY_STRINGS`, RCI paths) in `const.py`.
- Extracted `normalize_mac`, `find_client_by_mac`, `parse_memory_fraction`
  helpers into `utils.py` with unit tests.
- Removed dead duplicate `Mesh*` sensor classes from `sensor/system.py`.
- Narrowed bare `except:` in API helpers to log at debug.
- Added pytest coverage: `test_utils.py` and `test_entity_fingerprint.py`,
  plus extended `test_api_helpers.py` for the iface_list short-circuit and
  parallel interface-stats paths.

### Notes

- No user-facing config changes. Entity unique IDs preserved.
- 1.6.6 mesh `device_info` None-guard and 1.6.7 plaintext-HTTP repair card
  are preserved.

## 1.6.7 - Plaintext-HTTP repair warning

### 🔒 Security

- **Repair card now warns when the integration is configured for plaintext
  HTTP to a non-loopback router.** When SSL is disabled and the host is not
  a loopback address, the integration raises a Home Assistant Repair issue
  explaining that your router username, NDW2 password hash, and session
  cookie traverse the LAN unencrypted on every poll. The card links to the
  remediation steps in `SECURITY.md` and is automatically cleared once you
  reconfigure the entry to use HTTPS. No configuration changes required —
  existing setups will see the card on next reload.

## 1.6.6 - Internal cleanup and bug fixes

### Fixes

- **Mesh device info no longer crashes when a node briefly disappears.** The
  `MeshEntity.device_info` property could raise `AttributeError` when the
  underlying mesh node had been removed from the router response between
  ticks; it now safely returns the fallback router device info.
- **Hotspot client fetch no longer swallows unrelated exceptions.** The fallback
  loop in `async_get_clients` previously caught `Exception` indiscriminately,
  hiding unexpected errors; it now narrows to `KeeneticApiError` and logs
  fallthroughs at debug level.

### Improvements

- **Internal refactor and dead-code removal.** Removed an unused duplicate
  `dns.py` module, several unreferenced API helpers
  (`async_check_firmware_update`, `async_get_client_stats`, `async_ping_ip`,
  `async_ping_multiple`, `async_set_wireguard_enabled`), unused imports and
  dead helper properties. No user-facing entities or unique IDs changed.
- **Tighter `ControllerEntity` model lookup.** The `_model_name` helper now
  iterates a single tuple of candidate keys instead of a fall-through ladder.
- **De-duplicated mesh-association math** in the "router clients" sensor.

## 1.6.5 - IPsec VICI diagnostics

### Improvements

- **Added IPsec VICI diagnostic sensors.** The integration now summarizes
  recent `IpSec::Vici::Stats: out of memory` router log entries so these
  firmware/IPsec-stat issues are visible in Home Assistant without manually
  scraping logs.
- **Reduced IPsec crypto-map polling pressure.** Site-to-site IPsec tunnel
  data now uses the very-slow coordinator cadence, matching other diagnostic
  endpoints and avoiding unnecessary hits to Keenetic's IPsec statistics path.

## 1.6.4 - KeenDNS protected web app access

### Improvements

- **Added a KeenDNS protected web app connection mode.** The integration can
  now be configured with a password-protected KeenDNS app hostname over HTTPS
  while keeping the existing direct/local API mode unchanged.
- **Setup and reconfigure now show mode-specific fields.** KeenDNS protected
  mode hides direct-only port, SSL and challenge-auth options and uses the
  tested HTTPS/443 Basic Auth defaults automatically.
- **Full URL input is normalized safely.** Setup and reconfigure accept either
  a bare host name or a full `https://...` URL, reject paths/query strings, and
  store a clean host/port/SSL target.
- **Clearer 502 errors for protected apps.** Bad Gateway responses now point to
  the KeenDNS published application/upstream configuration instead of looking
  like a generic router API failure.

### Documentation

- Documented the tested protected-access setup and the minimal `HTTP Proxy`
  permission observed to allow full proxied RCI access.
- Added a warning that verbose curl logs expose Basic Auth headers and should
  be followed by password rotation when shared.

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
