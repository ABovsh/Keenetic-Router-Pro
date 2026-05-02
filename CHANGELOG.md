# Changelog

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
