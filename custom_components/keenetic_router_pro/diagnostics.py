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
from .utils import normalize_mac

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
    # LAN device hostnames (clients, mesh nodes, extenders) are personal data.
    "name",
    "known-host",
    "http_host",
    # Network addresses / endpoints / public DNS names that identify the
    # network or its peers. ``ip``/``host`` above don't cover these aliases.
    "address",
    "global-address",
    "remote",
    "remote_peer",
    "remote-peer",
    "remote_endpoint",
    "remote-endpoint",
    "remote-endpoint-address",
    "local_endpoint",
    "local-endpoint",
    "endpoint",
    "gateway",
    "default-gateway",
    "fqdn",
    "domain",
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

# Coordinator fields that are a bare set of MAC strings rather than a dict
# keyed by one. ``async_redact_data`` only scrubs dict VALUES by key, so every
# one of these leaks its MACs (and a set is not JSON-native either). All four
# are published together by ``KeeneticCoordinator._async_update_data``; keep
# this tuple in step with it.
_MAC_SET_FIELDS = (
    "new_clients",
    "online_clients",
    "connected_clients",
    "disconnected_clients",
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
    for field in _MAC_SET_FIELDS:
        macs = stripped.get(field)
        if isinstance(macs, (set, frozenset, list, tuple)):
            stripped[field] = {"<redacted-mac-set>": len(macs)}
    # Mesh node ``id``/``cid`` fall back to the node MAC on routers without
    # MWS member data; redact those identifiers (the ``mac`` key is already
    # redacted, but ``id``/``cid`` are not in TO_REDACT).
    mesh_nodes = stripped.get("mesh_nodes")
    if isinstance(mesh_nodes, list):
        stripped["mesh_nodes"] = [
            _redact_mesh_node_ids(node) for node in mesh_nodes
        ]
    return stripped


def _redact_mesh_node_ids(node: Any) -> Any:
    """Replace MAC-shaped mesh node id/cid with a placeholder."""
    if not isinstance(node, dict):
        return node
    redacted = dict(node)
    for key in ("id", "cid"):
        if normalize_mac(redacted.get(key)):
            redacted[key] = "**REDACTED**"
    return redacted


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


# API capabilities the client latches at runtime. "Why is feature X missing?"
# is the most common question a Keenetic bug report has to answer, and the
# answer is almost always one of these coming back False on that firmware.
_CAPABILITY_ATTRS = {
    "rci_batch": "_rci_batch_supported",
    "mws_member": "_mws_member_supported",
    "crypto_map": "_crypto_map_supported",
    "ipsec_diagnostics": "_ipsec_diagnostics_supported",
    "ping_check": "_ping_check_supported",
    "dns_proxy": "_dns_proxy_supported",
    "ndns": "_ndns_supported",
}


def _health(coordinator: Any, client: Any) -> dict[str, Any]:
    """Summarise poll state and capability latches — costs no API call."""
    source = coordinator if coordinator is not None else None
    api = getattr(source, "client", None) or client
    interval = getattr(source, "update_interval", None)
    return {
        "last_update_success": getattr(source, "last_update_success", None),
        "refresh_count": getattr(source, "_refresh_count", None),
        "update_interval_seconds": (
            interval.total_seconds() if interval is not None else None
        ),
        "capabilities": {
            name: getattr(api, attr, None) for name, attr in _CAPABILITY_ATTRS.items()
        },
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
        "health": _health(coordinator, client),
    }

    result = async_redact_data(payload, TO_REDACT)
    # "domain" in TO_REDACT scrubs router-network FQDN fields; the entry's
    # own ``.domain`` is the constant integration slug, not user data, and
    # is useful in diagnostics dumps.
    result["entry"]["domain"] = entry.domain
    return result
