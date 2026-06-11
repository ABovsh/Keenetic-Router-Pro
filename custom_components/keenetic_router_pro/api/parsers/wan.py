"""WAN parser and derivation helpers."""

from __future__ import annotations

from typing import Any

from ...const import INTERFACE_CONF_DISABLED, LINK_STATE_UP, UPLINK_ROLE_TOKENS
from ...utils import coerce_bool, first_present, usable_ip


def _extract_ip_from_value(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value.split("/")[0]
    if isinstance(value, dict):
        # Firmware variant: {"address": {"address": "1.2.3.4", "mask": ...}}
        ip = first_present(value, "address", "ip")
        return str(ip).split("/")[0] if ip else None
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict):
            ip = first_present(first, "address", "ip")
            if ip:
                return str(ip).split("/")[0]
        elif isinstance(first, str):
            return first.split("/")[0]
    return None


def extract_wan_ip(
    iface: dict[str, Any],
    *,
    prefer_global_address: bool = False,
) -> str | None:
    """Extract an IPv4/IPv6 address from known Keenetic WAN address shapes."""
    address_fields = (
        ("global-address", "address")
        if prefer_global_address
        else ("address", "global-address")
    )
    for key in address_fields:
        ip = _extract_ip_from_value(iface.get(key))
        if ip:
            return ip

    for key in ("ip", "ipv4", "ip-address"):
        val = iface.get(key)
        if val and isinstance(val, str):
            return val.split("/")[0]

    return None


def is_ranked_wan_interface(iface: dict[str, Any]) -> bool:
    """Return whether an interface participates in Keenetic uplink ranking."""
    role = iface.get("role")
    if isinstance(role, list) and any(
        str(item).lower() in UPLINK_ROLE_TOKENS for item in role
    ):
        return True
    if isinstance(role, str) and role.lower() in UPLINK_ROLE_TOKENS:
        return True

    is_global = coerce_bool(iface.get("global"))
    has_priority = iface.get("priority") is not None
    return is_global and has_priority


def derive_wan_enabled(iface: dict[str, Any]) -> bool:
    """Return whether the WAN interface is configured up."""
    summary = iface.get("summary") or {}
    if not isinstance(summary, dict):
        summary = {}
    layer = summary.get("layer") or {}
    if not isinstance(layer, dict):
        layer = {}
    conf = str(layer.get("conf") or "").lower()
    if conf == INTERFACE_CONF_DISABLED:
        return False
    if conf == "running":
        return True
    return True


def derive_wan_internet_access(iface: dict[str, Any]) -> bool | None:
    """Return the existing heuristic WAN internet-access state."""
    state = str(iface.get("state") or "").lower()
    if state != LINK_STATE_UP:
        return False
    if not coerce_bool(iface.get("global")):
        return False
    # A placeholder address (0.0.0.0/::) is not real connectivity — treat it
    # the same as "no address" so an up/global interface awaiting a real
    # lease is not falsely reported as internet-connected.
    ip = usable_ip(extract_wan_ip(iface))
    if not ip:
        summary = iface.get("summary") or {}
        if not isinstance(summary, dict):
            summary = {}
        layer = summary.get("layer") or {}
        if not isinstance(layer, dict):
            layer = {}
        if str(layer.get("ipv4") or "").lower() == "pending":
            return None
        return False
    fail = str(iface.get("fail") or "").lower()
    if coerce_bool(fail):
        return False
    return True

