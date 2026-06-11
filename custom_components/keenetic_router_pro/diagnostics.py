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

from .const import CONF_TRACKED_CLIENTS

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
    "hostname",
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


# Coordinator indexes whose keys are MAC addresses or MAC-derived mesh ids
# (used as O(1) lookups). async_redact_data only scrubs values; we strip these
# keys entirely from the diagnostics dump so MACs cannot leak through dict keys.
# `mesh_nodes_by_cid` and `mesh_associations.by_node` are keyed by mesh cid,
# which falls back to MAC when the controller doesn't expose a separate cid.
_MAC_KEYED_INDEXES = (
    "clients_by_mac",
    "host_policies",
    "client_stats",
    "mesh_nodes_by_cid",
)


def _strip_mac_keyed_indexes(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    stripped = dict(data)
    for key in _MAC_KEYED_INDEXES:
        if key in stripped and isinstance(stripped[key], dict):
            stripped[key] = {"<redacted-mac-keys>": len(stripped[key])}
    mesh_assoc = stripped.get("mesh_associations")
    if isinstance(mesh_assoc, dict) and isinstance(mesh_assoc.get("by_node"), dict):
        stripped["mesh_associations"] = {
            **mesh_assoc,
            "by_node": {"<redacted-mac-keys>": len(mesh_assoc["by_node"])},
        }
    return stripped


def _redact_tracked_client_names(data: dict[str, Any]) -> dict[str, Any]:
    """Strip human-readable client names from stored entry data.

    Tracked-client dicts carry ``name`` (a LAN hostname / personal label);
    ``async_redact_data`` already scrubs their ``mac``/``ip`` keys, but a
    blanket ``name`` redaction would also blank harmless labels elsewhere,
    so we redact it only inside the tracked-clients list.
    """
    tracked = data.get(CONF_TRACKED_CLIENTS)
    if not isinstance(tracked, list):
        return data
    return {
        **data,
        CONF_TRACKED_CLIENTS: [
            {**c, "name": "**REDACTED**"} if isinstance(c, dict) and "name" in c else c
            for c in tracked
        ],
    }


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a Keenetic config entry."""
    runtime = getattr(entry, "runtime_data", None)
    coordinator = getattr(runtime, "coordinator", None) if runtime else None
    client = getattr(runtime, "client", None) if runtime else None

    coordinator_data: Any = None
    if coordinator is not None:
        coordinator_data = _strip_mac_keyed_indexes(getattr(coordinator, "data", None))

    payload: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "domain": entry.domain,
            "source": entry.source,
            "data": _redact_tracked_client_names(dict(entry.data)),
            "options": _redact_tracked_client_names(dict(entry.options)),
        },
        "client": {
            # Defensive: KeeneticClient overrides __repr__ to redact creds,
            # but we still pass it through async_redact_data on the entry data.
            "repr": repr(client) if client is not None else None,
        },
        "coordinator_data": coordinator_data,
    }

    return async_redact_data(payload, TO_REDACT)
