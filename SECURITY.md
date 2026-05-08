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

## Plaintext HTTP vs HTTPS

Keenetic routers accept both HTTP and HTTPS for the web admin / RCI API.
This integration supports both via the **SSL** toggle in setup and
reconfigure. **Use HTTPS** wherever the router supports it.

When the integration is configured for plaintext HTTP and the host is not
a loopback address, every poll sends:

- the router username in cleartext;
- a replayable NDW2 challenge-response password hash (or HTTP Basic Auth
  credentials, depending on the connection mode);
- the authenticated session cookie.

Anyone on the same LAN — an untrusted Wi-Fi guest, a compromised IoT
device, or a malicious Ethernet drop — can capture these and impersonate
you to the router. The integration raises a Home Assistant Repair card
when this is detected so the risk is visible in the UI.

To switch to HTTPS:
1. In the router web UI: *System → Components* → confirm `SSL/TLS support`
   is installed.
2. *Management → Web Admin* → enable HTTPS (port 443 by default).
3. In Home Assistant: *Settings → Devices & Services → Keenetic Router Pro
   → Reconfigure* → enable **SSL** and update the port.
4. After confirming HTTPS works, **rotate the router admin password** —
   anyone who sniffed the LAN since setup may already have it.

The repair card is automatically cleared once the entry reloads with SSL
enabled or with a loopback host.

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
