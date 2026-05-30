"""Utilities for Keenetic Router Pro integration."""
from __future__ import annotations

import math
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .const import CONF_TRACKED_CLIENTS, DOMAIN, LINK_STATE_UP, TRUTHY_STRINGS

UNKNOWN_SECONDS_VALUES = (None, "", "unknown", "Unknown")
_MESH_ID_SAFE_RE = re.compile(r"\W+")
PLACEHOLDER_IPS = frozenset({"", "0.0.0.0", "::"})


def _url_host(value: str) -> str:
    """Return host part from a user-supplied host or URL."""
    stripped = value.strip()
    parsed = urlsplit(stripped if "://" in stripped else f"//{stripped}")
    return (parsed.netloc or parsed.path).split("/", 1)[0]


def bracket_host(host: Any) -> str:
    """Return a URL-authority-safe host, wrapping bare IPv6 literals in brackets.

    ``http://::1:100`` is not a valid authority; ``http://[::1]:100`` is. A
    bare IPv6 literal is recognised by containing a colon while not already
    being bracketed. Hostnames and IPv4 literals never contain a colon, so
    they pass through unchanged.
    """
    text = str(host or "").strip()
    if not text:
        return text
    if ":" in text and not text.startswith("["):
        return f"[{text}]"
    return text


def coerce_seconds(value: Any, default: int | None = 0) -> int | None:
    """Convert a Keenetic duration value to whole seconds."""
    if value in UNKNOWN_SECONDS_VALUES:
        return default

    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return default
    # ``int(float("inf"))`` raises OverflowError, and NaN/inf is never a
    # meaningful uptime. Treat both as missing so HA sees a clean default.
    if not math.isfinite(as_float):
        return default
    # Negative durations are physically impossible; a firmware glitch must
    # not publish a negative uptime to a DURATION / TOTAL_INCREASING sensor.
    if as_float < 0:
        return default
    try:
        return int(as_float)
    except (OverflowError, ValueError):
        return default


def coerce_int(value: Any, default: int = 0) -> int:
    """Return an int from loosely typed Keenetic RCI values."""
    try:
        return int(value)
    except (OverflowError, TypeError, ValueError):
        return default


def coerce_float(value: Any, default: float | None = None) -> float | None:
    """Return a float from loosely typed Keenetic RCI values.

    NaN and infinity are rejected: HA recorder/statistics cannot store them
    cleanly, so a malformed firmware value like ``"nan"`` would otherwise
    poison long-term stats for that sensor.
    """
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result


def coerce_byte_count(value: Any) -> int | None:
    """Return a non-negative integer byte counter, or None when unusable.

    Byte counters feed ``TOTAL_INCREASING`` / ``DATA_SIZE`` sensors, so a
    missing, non-numeric, non-finite (NaN/inf), or negative firmware value
    must become ``None`` (sensor unavailable) rather than ``0`` — returning
    ``0`` for a transiently-absent counter would look like a counter reset
    and double-count the value back up in HA long-term statistics.
    """
    if value in (None, ""):
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(as_float) or as_float < 0:
        return None
    return int(as_float)


def bytes_to_gib(value: Any) -> float | None:
    """Convert a byte counter to GiB (÷1024³), or None when unusable."""
    count = coerce_byte_count(value)
    return None if count is None else round(count / (1024 ** 3), 2)


def bytes_to_mib(value: Any) -> float | None:
    """Convert a byte counter to MiB (÷1024²), or None when unusable."""
    count = coerce_byte_count(value)
    return None if count is None else round(count / (1024 ** 2), 2)


def coerce_bool(value: Any) -> bool:
    """Return True only for actual truthy Keenetic-style values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY_STRINGS
    return bool(value)


def normalize_mac(value: Any) -> str:
    """Return a stable lower-case MAC address token."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    compact = re.sub(r"[^0-9a-f]", "", text)
    if len(compact) == 12:
        return ":".join(compact[i : i + 2] for i in range(0, 12, 2))
    return ""


def usable_ip(value: Any) -> str | None:
    """Return an IP-like value unless it is a router placeholder."""
    text = str(value or "").strip()
    return None if text in PLACEHOLDER_IPS else text


def first_present(d: dict, *keys: str, default=None):
    """Return the first present, non-empty value from ``d``."""
    for key in keys:
        value = d.get(key)
        if value not in (None, ""):
            return value
    return default


def find_client_by_mac(clients: Any, mac: str) -> dict[str, Any] | None:
    """Linear-scan fallback used when no clients_by_mac index is available."""
    if not mac or not clients:
        return None
    target = normalize_mac(mac)
    for client in clients:
        if isinstance(client, dict) and normalize_mac(client.get("mac")) == target:
            return client
    return None


def find_mesh_node(data: dict, cid: str) -> dict[str, Any] | None:
    """Return a mesh node by cid/id using the coordinator index when available."""
    if not cid:
        return None
    index = data.get("mesh_nodes_by_cid")
    if isinstance(index, dict):
        node = index.get(cid)
        if isinstance(node, dict):
            return node
    for node in data.get("mesh_nodes", []) or []:
        if isinstance(node, dict) and (node.get("cid") or node.get("id")) == cid:
            return node
    return None


def iter_new_items(
    coordinator: Any,
    key: str,
    known: set[Any],
    id_keys: tuple[str, ...] = ("id",),
) -> Iterator[dict[str, Any]]:
    """Yield newly discovered dict items from coordinator data."""
    for item in coordinator.data.get(key, []) or []:
        if not isinstance(item, dict):
            continue
        item_id = next((item.get(id_key) for id_key in id_keys if item.get(id_key)), None)
        if not item_id or item_id in known:
            continue
        known.add(item_id)
        yield item


def iter_tracked_clients(entry: Any) -> Iterator[tuple[str, str, str | None]]:
    """Yield normalized, deduplicated tracked-client config records."""
    seen_macs: set[str] = set()
    for client_info in entry.data.get(CONF_TRACKED_CLIENTS, []):
        if not isinstance(client_info, dict):
            continue

        mac = normalize_mac(client_info.get("mac"))
        if not mac or mac in seen_macs:
            continue
        seen_macs.add(mac)

        yield mac, client_info.get("name") or mac.upper(), client_info.get("ip")


def client_display_name(client: dict[str, Any] | None, fallback: str) -> str:
    """Return a stable human display name for a tracked client."""
    if not client:
        return fallback
    hostname = client.get("hostname")
    if isinstance(hostname, str) and hostname.strip():
        return hostname.strip()
    name = client.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip().split(" - ")[0]
    return fallback


def is_client_online(client: dict[str, Any] | None) -> bool:
    """Return whether hotspot data says a client is currently connected.

    Canonical definition used by the coordinator merge step, the device
    tracker, the per-client sensors and the ClientEntity base class.
    A client is online when its hotspot ``link`` is ``up`` or, lacking
    that, ``active`` is truthy. ``neighbour-expired`` (set by the
    neighbour-discovery merge) overrides ``active`` to false.
    """
    if not client:
        return False
    if str(client.get("link", "")).strip().lower() == LINK_STATE_UP:
        return True
    if coerce_bool(client.get("neighbour-expired")):
        return False
    return coerce_bool(client.get("active"))


def parse_memory_fraction(value: Any) -> float | None:
    """Parse Keenetic ``"used/total"`` memory string into a 0–100 percentage."""
    if not isinstance(value, str) or "/" not in value:
        return None
    try:
        part_used, part_total = value.split("/", 1)
        used = float(part_used)
        total = float(part_total)
    except (ValueError, TypeError):
        return None
    # Reject NaN/inf before dividing — otherwise a malformed "nan/100" would
    # clamp to a healthy-looking 0/100% instead of reporting unavailable.
    if not (math.isfinite(used) and math.isfinite(total)):
        return None
    if total <= 0:
        return None
    pct = used * 100.0 / total
    # Clamp to [0, 100] — inconsistent firmware values must not produce
    # negative or above-100% sensor readings that confuse HA statistics.
    pct = max(0.0, min(100.0, pct))
    return round(pct, 1)


def sanitize_mesh_id(value: Any) -> str:
    """Return a full, stable mesh-node token for unique IDs."""
    token = str(value or "").strip().replace("-", "_").replace(":", "_")
    token = _MESH_ID_SAFE_RE.sub("_", token).strip("_")
    return token or "unknown"


def mesh_unique_id(entry_id: str, node_id: Any, suffix: str) -> str:
    """Build an entry-scoped unique ID for a mesh-node entity."""
    return f"{entry_id}_mesh_{sanitize_mesh_id(node_id)}_{sanitize_mesh_id(suffix)}"


def get_main_device_info(
        title: str,
        entry_id: str,
        firmware_version: str | None,
        model: str | None,
        host: str | None,
        ssl: bool = False,
        ndns_domain: str | None = None,
    ) -> dict[str, Any]:
    """Build DeviceInfo for the main router."""
    scheme = "https" if ssl else "http"

    if ndns_domain and ndns_domain.strip():
        clean_domain = _url_host(ndns_domain)
        configuration_url = urlunsplit((scheme, clean_domain, "", "", ""))
    elif host:
        configuration_url = urlunsplit((scheme, bracket_host(host), "", "", ""))
    else:
        configuration_url = None

    return {
        "identifiers": {(DOMAIN, entry_id)},
        "name": title,
        "manufacturer": "Keenetic",
        "model": model or "Controller",
        "sw_version": firmware_version,
        "configuration_url": configuration_url,
    }


def get_mesh_device_info(
    title: str,
    entry_id: str,
    node: dict[str, Any] | None = None,
    node_cid: str | None = None,
    host: str | None = None,
    ssl: bool = False,
    fqdn: str | None = None,
) -> dict[str, Any]:
    """Build DeviceInfo for a Mesh extender node."""
    if node and node_cid:
        node_name = node.get("name") or node.get("mac") or node_cid
        node_ip = node.get("ip") or host

        scheme = "https" if ssl else "http"
        if fqdn and fqdn.strip():
            configuration_url = f"{scheme}://{fqdn}"
        else:
            configuration_url = (
                f"{scheme}://{bracket_host(node_ip)}" if node_ip else None
            )

        return {
            "identifiers": {(DOMAIN, f"{entry_id}_mesh_{sanitize_mesh_id(node_cid)}")},
            "name": node_name,
            "manufacturer": "Keenetic",
            "model": node.get("model") or "Extender",
            "sw_version": node.get("firmware"),
            "via_device": (DOMAIN, entry_id),
            "configuration_url": configuration_url,
        }

    # Fallback to the main router device.
    return get_main_device_info(title, entry_id, None, None, host, ssl)


def get_wan_device_info(
    title: str,
    entry_id: str,
    wan_id: str,
    description: str | None = None,
    iface_type: str | None = None,
    role_label: str | None = None,
) -> dict[str, Any]:
    """Device info for a single WAN interface.

    Each WAN appears in HA as its own sub-device under the main router,
    so the user can see one card per uplink with all its sensors grouped.
    """
    name_parts = []
    if description and description != wan_id:
        name_parts.append(description)
    else:
        name_parts.append(wan_id)
    if role_label:
        name_parts.append(f"({role_label})")
    device_name = " ".join(name_parts)

    return {
        "identifiers": {(DOMAIN, f"{entry_id}_wan_{wan_id}")},
        "name": f"{title} — {device_name}",
        "manufacturer": "Keenetic",
        "model": f"WAN ({iface_type})" if iface_type else "WAN",
        "via_device": (DOMAIN, entry_id),
    }


def get_vpn_interface_device_info(
    title: str,
    entry_id: str,
    iface_id: str,
    label: str | None = None,
    iface_type: str | None = None,
) -> dict[str, Any]:
    """Device info for a VPN/interface that is not a WAN uplink."""
    display = label or iface_id
    model = "VPN interface"
    if iface_type:
        model = f"{str(iface_type).upper()} interface"

    return {
        "identifiers": {(DOMAIN, f"{entry_id}_iface_{iface_id}")},
        "name": f"{title} — {display}",
        "manufacturer": "Keenetic",
        "model": model,
        "via_device": (DOMAIN, entry_id),
    }


def get_crypto_map_device_info(
    title: str,
    entry_id: str,
    cmap_name: str,
    remote_peer: str | None = None,
) -> dict[str, Any]:
    """Device info for a single site-to-site IPsec `crypto map` tunnel.

    Each configured tunnel appears in HA as its own sub-device under
    the main router, so the user can see one card per tunnel with all
    its sensors grouped (state, IKE state, RX/TX, throughput, enable
    switch, ...).

    The HA device identifier is keyed on the crypto map name, which
    is stable for the lifetime of the tunnel. Renaming the tunnel in
    the router web UI will orphan the old HA device and create a new
    one — there is no truly stable id for a crypto map entry, so this
    is an accepted tradeoff.
    """
    name_parts = [cmap_name]
    if remote_peer:
        name_parts.append(f"→ {remote_peer}")
    device_name = " ".join(name_parts)

    return {
        "identifiers": {(DOMAIN, f"{entry_id}_cmap_{cmap_name}")},
        "name": f"{title} — IPsec {device_name}",
        "manufacturer": "Keenetic",
        "model": "IPsec site-to-site tunnel",
        "via_device": (DOMAIN, entry_id),
    }


def get_client_device_info(
    entry_id: str,
    title: str,
    mac: str,
    label: str,
    client: dict[str, Any] | None = None,
    initial_ip: str | None = None,
) -> dict[str, Any]:
    """Build DeviceInfo for a tracked client exposed as its own HA device."""
    device_name = client_display_name(client, label)
    manufacturer = None
    model = None
    if client:
        ssdp = client.get("ssdp")
        if ssdp:
            if ssdp.get("manufacturer"):
                manufacturer = ssdp.get("manufacturer")

            if ssdp.get("model"):
                model = ssdp.get("model")

    ip_address = usable_ip(initial_ip)
    if client and usable_ip(client.get("ip")):
        ip_address = usable_ip(client.get("ip"))

    display_name = f"{device_name} ({title})" if title else device_name

    return {
        "identifiers": {(DOMAIN, f"{entry_id}_client_{mac.replace(':', '_')}")},
        "name": display_name,
        "manufacturer": manufacturer,
        "model": model,
        "via_device": (DOMAIN, entry_id),
        "configuration_url": (
            urlunsplit(("http", bracket_host(ip_address), "", "", ""))
            if ip_address
            else None
        ),
    }
