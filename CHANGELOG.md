# Changelog

## 1.4.0 - Bug fixes, throughput units, and code cleanup

### Bug Fixes

- **Reauth/reconfigure flow never completed** — `_async_update_existing_entry`
  returned a `dict` on success and the caller checked `not isinstance(result, dict)`
  to detect completion. `FlowResult` is always a `dict` in Home Assistant, so the
  branch was unreachable: re-auth and reconfigure flows never called `async_abort` and
  instead rendered a form with the abort payload treated as error strings. Fixed by
  changing the helper to return `None` on success and `dict[str, str]` on failure; callers
  now call `async_abort` explicitly.
- **Stale mesh node auth cache** — `_node_auth_headers[(ip, port)]` was populated on
  successful authentication but never evicted on HTTP 401. A credential rotation or
  node session reset left the coordinator locked out with a cached bad token until
  HA restarted. The 401 branch now evicts the cache entry before retrying.
- **Private attribute access in sensor setup** — `KeeneticLocalIpSensor` was
  constructed with `client._host` (private), which bypasses encapsulation and
  breaks if the attribute is renamed. Changed to read `entry.data.get("host") or
  entry.data.get("ip")` directly from the config entry.

### Throughput Units

- WAN and IPsec tunnel throughput sensors now use `UnitOfDataRate.MEGABITS_PER_SECOND`
  instead of `BYTES_PER_SECOND`. The coordinator stores throughput as bytes/s (delta /
  elapsed); the sensor layer multiplies by 8 and divides by 1 000 000 to produce Mbit/s.
  With `SensorDeviceClass.DATA_RATE`, Home Assistant automatically offers unit conversion
  (kbit/s ↔ Mbit/s ↔ Gbit/s) in the entity customisation dialog — no dashboard template
  workarounds needed. Display precision raised to 2 decimal places.

### Performance

- Replaced three async inner functions (`_cached`, `_cached_update_info`,
  `_cached_version_info`) that did zero I/O with a single sync precomputation
  (`_prev` / `_prev_sys`) before the gather call and a trivial `async def _resolve`
  wrapper. Eliminates unnecessary coroutine objects on every 10 s tick.

### Code Quality

- `entity.py`: added `from __future__ import annotations`; replaced all `Dict[str, Any]`
  and `Optional[X]` annotations with built-in `dict[str, Any]` and `X | None`;
  removed Russian/Turkish docstrings.
- `device_tracker.py`: removed four redundant `__init__` assignments already set by
  `ClientEntity.__init__`; fixed `extra_state_attributes` to reuse the already-fetched
  `client` local variable instead of calling `_client_from_main` a second time;
  translated Turkish/Russian inline comments to English.
- `binary_sensor.py`: removed two useless f-strings (`f"Connected"`, `f"Update Available"`).
- `coordinator.py`: translated Turkish class docstring and inline comments to English.

---

## 1.3.0 - Fork hardening and performance update

This release keeps the Home Assistant integration domain as `keenetic_router_pro`
for compatibility, while hardening authentication, reducing router load, and
improving diagnostics for Keenetic controller and mesh deployments.

### Security

- Added safer Basic Auth header construction without exposing credentials to logs.
- Added NDW2 challenge-auth handling for newer Keenetic models, including session-cookie reuse.
- Added one-shot reauthentication after expired auth cookies before surfacing failures to Home Assistant.
- Redacted sensitive values from API error excerpts and debug logs, including passwords, PSKs, cookies, authorization headers, keys, and secrets.
- Removed raw config-flow form input logging so entered passwords are not written to debug logs.
- Removed raw hotspot host payload logging when client parsing fails.
- Validated CLI arguments before sending `/rci/parse` commands to prevent command-injection style input.
- Avoided storing existing passwords as defaults in reconfigure forms.

### Home Assistant Setup And Recovery

- Added proper reauthentication and reconfigure flows.
- Mapped authentication failures to `ConfigEntryAuthFailed` so Home Assistant can trigger reauth.
- Mapped setup-time API failures to `ConfigEntryNotReady` where appropriate.
- Normalized setup and options-flow client selection handling.
- Clamped configurable ping intervals to the supported range.
- Preserved offline tracked clients in the options flow.
- Added missing config strings and English translations for reauth and reconfigure.

### Performance And Router Load

- Reduced repeated polling of slow router endpoints by caching slower-changing data.
- Reused fetched client data to derive connected/disconnected/extender counts instead of issuing extra calls.
- Limited interface-stat polling to interfaces that back enabled sensors.
- Cached version/update data without overwriting fresh system data.
- Removed the Wi-Fi QR image platform entirely.
- Removed `pyqrcode` and `pypng` dependencies.
- Removed USB polling for controller and mesh nodes.
- Switched coordinator timing to the running event loop.

### API And Mesh Behavior

- Added safer mesh-node authentication with NDW2-first behavior and Basic Auth fallback when no challenge is provided.
- Cached mesh-node auth headers per node and port.
- Improved firmware update endpoint fallbacks for controller and mesh nodes.
- Redacted node firmware-update response excerpts before logging.
- Added helper summaries for client statistics.
- Improved parsing and normalization of interface payloads.

### Entities

- Fixed entity helpers that accidentally referenced `coordinator._client`.
- Fixed device URLs that could become `http://None`.
- Made device-tracker ping coordination null-safe.
- Preserved existing traffic sensor unique IDs while consolidating duplicated RX/TX sensor code.
- Made traffic sensors resilient to non-numeric router counters.
- Reduced duplicated firmware update version-comparison logic.

### Metadata And Packaging

- Disabled HACS ZIP-release mode so HACS downloads the repository archive directly.
- Removed the ZIP release workflow and release asset expectation.
- Replaced the README with a lighter install/config/troubleshooting guide and removed donation links.
- Fixed integration logger metadata to `custom_components.keenetic_router_pro`.
- Aligned manifest version with the documented `1.3.0` release.
- Repointed manifest documentation and issue tracker metadata to the fork repository.
- Repointed README download badge to the fork repository.
- Updated README presence-tracking interval documentation to match the configurable 5-300 second option.
- Removed an unused legacy ping-interval import from the coordinator.
- Removed non-English translation files so only English translations are shipped.
- Removed USB sensor setup and USB parser code after disabling USB polling.

### Tests And Tooling

- Added lightweight pytest configuration.
- Added helper tests for CLI argument validation, Basic Auth header generation, interface normalization, client-stat summaries, response redaction, and payload redaction.
- Added repository checkout to HACS validation workflow.
- Added GitHub Actions test job for `compileall` and the lightweight pytest suite.
- Updated hassfest workflow checkout action to `actions/checkout@v4`.
- Verified Python syntax with `compileall`.
- Verified JSON metadata and translations with `json.tool`.
