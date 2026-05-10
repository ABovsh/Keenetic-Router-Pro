"""Utilities for Keenetic Router Pro integration."""
from __future__ import annotations

import re
from typing import Any
from .const import DOMAIN

UNKNOWN_SECONDS_VALUES = (None, "", "unknown", "Unknown")
_MESH_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9_]+")


def coerce_seconds(value: Any, default: int | None = 0) -> int | None:
    """Convert a Keenetic duration value to whole seconds."""
    if value in UNKNOWN_SECONDS_VALUES:
        return default

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def normalize_mac(value: Any) -> str:
    """Lowercase string form of a MAC, empty string for falsy/None."""
    return str(value or "").lower()


def find_client_by_mac(clients: Any, mac: str) -> dict[str, Any] | None:
    """Linear-scan fallback used when no clients_by_mac index is available."""
    if not mac or not clients:
        return None
    target = mac.lower()
    for client in clients:
        if isinstance(client, dict) and normalize_mac(client.get("mac")) == target:
            return client
    return None


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
    if total <= 0:
        return None
    return round(used * 100.0 / total, 1)


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
        # Strip protocol prefix if present.
        clean_domain = ndns_domain.replace("https://", "").replace("http://", "").split("/")[0]
        configuration_url = f"{scheme}://{clean_domain}"
    elif host:
        configuration_url = f"{scheme}://{host}"
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

        if fqdn and fqdn.strip():
            scheme = "https" if ssl else "http"
            configuration_url = f"{scheme}://{fqdn}"
        else:
            scheme = "https" if ssl else "http"
            configuration_url = f"{scheme}://{node_ip}" if node_ip else None

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
    mac: str,
    label: str,
    client: dict[str, Any] | None = None,
    initial_ip: str | None = None,
) -> dict[str, Any]:
    """Build DeviceInfo for a tracked client exposed as its own HA device."""
    device_name = label
    manufacturer = None
    model = None
    if client:
        if client.get("hostname"):
            device_name = client.get("hostname")
        else:
            device_name = client.get("name", "").split(' - ')[0]

        ssdp = client.get("ssdp")
        if ssdp:
            if ssdp.get("manufacturer"):
                manufacturer = ssdp.get("manufacturer")

            if ssdp.get("model"):
                model = ssdp.get("model")

    ip_address = initial_ip
    if client and client.get("ip"):
        ip_address = client.get("ip")

    return {
        "identifiers": {(DOMAIN, f"client_{mac.replace(':', '_')}")},
        "name": device_name,
        "manufacturer": manufacturer,
        "model": model,
        "via_device": (DOMAIN, entry_id),
        "configuration_url": f"http://{ip_address}" if ip_address else None,
        "connections": {("mac", mac.upper())},
    }
