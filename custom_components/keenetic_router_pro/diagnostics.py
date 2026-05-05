"""Diagnostics support for Keenetic Router Pro.

Home Assistant exposes a "Download diagnostics" button on every config entry.
The dump is written to a JSON file the user is encouraged to attach to bug
reports — it MUST NOT contain credentials, session cookies, MAC addresses,
SSIDs, or pre-shared keys. We use Home Assistant's built-in
``async_redact_data`` helper to strip those keys recursively.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import (
    DATA_CLIENT,
    DATA_COORDINATOR,
    DATA_PING_COORDINATOR,
    DOMAIN,
)

# Keys whose values should NEVER appear in a diagnostics dump.
# Matching is case-insensitive (HA's redactor lower-cases keys).
TO_REDACT: set[str] = {
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_HOST,
    "password",
    "username",
    "login",
    "host",
    "ip",
    "ip_address",
    "ipv4",
    "ipv6",
    "mac",
    "mac_address",
    "bssid",
    "ssid",
    "psk",
    "passphrase",
    "pre_shared_key",
    "key",
    "secret",
    "token",
    "cookie",
    "Cookie",
    "set-cookie",
    "Set-Cookie",
    "authorization",
    "Authorization",
    "x-ndm-challenge",
    "x-ndm-realm",
    "serial",
    "serial_number",
    "hw_id",
    "hwid",
    "device_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a Keenetic config entry."""
    domain_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = domain_data.get(DATA_COORDINATOR)
    ping_coordinator = domain_data.get(DATA_PING_COORDINATOR)
    client = domain_data.get(DATA_CLIENT)

    coordinator_data: Any = None
    if coordinator is not None:
        coordinator_data = getattr(coordinator, "data", None)

    ping_data: Any = None
    if ping_coordinator is not None:
        ping_data = getattr(ping_coordinator, "data", None)

    payload: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "domain": entry.domain,
            "source": entry.source,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "client": {
            # Defensive: KeeneticClient overrides __repr__ to redact creds,
            # but we still pass it through async_redact_data on the entry data.
            "repr": repr(client) if client is not None else None,
        },
        "coordinator_data": coordinator_data,
        "ping_coordinator_data": ping_data,
    }

    return async_redact_data(payload, TO_REDACT)
