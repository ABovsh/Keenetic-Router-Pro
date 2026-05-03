# Security

This document describes how Keenetic Router Pro handles your router
credentials and what you can do to keep them safe.

## Where the password is stored

Home Assistant stores config-entry data on disk in:

```
/config/.storage/core.config_entries
```

That file contains the username, password and host of every integration
you have configured — including this one. **Home Assistant stores it in
plain text by design.** That is a global HA architecture choice and is
not specific to this integration.

What this integration controls:

- It only reads the password from the config entry.
- It does NOT write the password (or any other config-entry value) to
  any file inside `custom_components/keenetic_router_pro/`.
- It does NOT cache credentials in `/config/` outside of HA's own
  `.storage/` directory.
- The in-memory client object overrides `__repr__` so that a stray
  `_LOGGER.debug("client=%s", client)` cannot accidentally print the
  password.

## Recommendations

- Restrict permissions on `/config/.storage/`:
  ```bash
  chmod 700 /config/.storage
  chmod 600 /config/.storage/core.config_entries
  ```
  (HA Container / HAOS already do this; verify after restores.)
- Keep `/config/` backups encrypted — they contain `.storage/`.
- If you ever shared a backup, log file or diagnostics dump from before
  this hardening, **rotate the router password**:
  Keenetic web UI → *Management → Users and access* → edit the admin
  user → set a new password → reconfigure the integration in HA
  (*Settings → Devices & Services → Keenetic Router Pro → Reconfigure*).

## Diagnostics dumps

When you click *Download diagnostics* on the config entry, HA produces a
JSON file. This integration's `diagnostics.py` runs the dump through
`homeassistant.components.diagnostics.async_redact_data` and strips at
least:

`password`, `username`, `login`, `host`, `ip`, `mac`, `bssid`, `ssid`,
`psk`, `passphrase`, `pre_shared_key`, `key`, `secret`, `token`,
`cookie`, `set-cookie`, `authorization`, `x-ndm-challenge`,
`x-ndm-realm`, `serial`, `serial_number`, `hw_id`, `device_id`.

Any value under one of these keys is replaced with `**REDACTED**`. You
can safely attach the dump to bug reports.

## Logs

The API client redacts known sensitive fields (`password`, `cookie`,
`authorization`, `psk`, `secret`, `key`) from request payload summaries
and HTTP response excerpts before they reach the logger. Authentication
headers are constructed at call-time and never logged.

If you see something that looks like a credential leak in the logs,
please open an issue (with the offending log line redacted).

## Reporting vulnerabilities

Please open a GitHub issue with the label `security`, or contact the
maintainer directly via the email in the commit history. Do not include
real credentials in bug reports — use obvious placeholders.
