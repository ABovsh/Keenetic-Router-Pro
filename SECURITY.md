# Security policy

This document describes the security model and trust boundaries of the
Keenetic Router Pro Home Assistant integration, what the integration
itself protects, and what it cannot protect on your behalf.

## Reporting a vulnerability

Please open a private security advisory on GitHub
(`Security → Advisories → New draft advisory`) rather than a public
issue. Include enough detail to reproduce the problem, the affected
version (`manifest.json` `version` field), and the impact you
observed.

## What this integration protects

The integration tries hard to keep your router credentials out of any
output that a user is likely to share publicly:

- **Diagnostics download** — `Settings → Devices & Services → Keenetic
  Router Pro → ⋮ → Download diagnostics` redacts `username`,
  `password`, `mac`, `ssid`, `psk`, `cookie`, `set-cookie`,
  `authorization`, and similar keys before producing the file. The
  redaction set is enforced by tests in `tests/test_diagnostics.py`.
- **Logs** — request and response payloads are passed through a
  redactor before they reach `_LOGGER`. The API client's `__repr__`
  is hard-coded to never show username/password, so a stray
  `_LOGGER.debug("client=%s", client)` cannot leak them.
- **Config flow input** — the password field uses Home Assistant's
  password selector, so it is masked in the UI on initial setup,
  reauth, and reconfigure flows.
- **Plaintext HTTP** — if you configure the integration to talk to a
  router on a non-loopback host without TLS, a Repair card is raised
  warning that credentials traverse the network in plaintext.

## What this integration **cannot** protect

Home Assistant stores every config-entry's data — including the router
password — as plaintext JSON in `<config>/.storage/core.config_entries`.
Anyone with read access to that file can recover your router password.
**No HA integration can prevent this.** Mitigations are HA-wide:

- Restrict filesystem permissions on `<config>/.storage/` (HA already
  does this by default).
- Enable full-disk encryption on the host running Home Assistant.
- Avoid running HA on hosts shared with untrusted users.

If you previously shared a HA backup, a `.storage/` snapshot, or a
diagnostics file produced by an older version of this integration,
**rotate your router admin password now**.

## Reducing risk on the router itself

Anything that limits the blast radius of a leaked router credential is
worth doing on the router itself:

- Use a unique password (do not reuse your KeenDNS / mywifi.keenetic
  account password).
- Disable Web UI access from the WAN; require VPN or a local network
  for admin access.
- Disable Telnet (plaintext); use SSH or the Web UI.
- Disable WPS on Wi-Fi.
- Keep firmware up to date — older KeeneticOS releases have
  CVE-tracked auth bugs.

## Supported versions

Only the most recent **minor** release is supported for security
fixes. Older minor versions receive bug fixes on a best-effort basis
when a user reports a regression. There is no LTS branch.
