"""Payload shape helpers for the Keenetic coordinator."""

from __future__ import annotations

from typing import Any

from ..utils import is_client_online, normalize_mac, usable_ip


def dict_or_empty(value: Any) -> dict[str, Any]:
    """Return a dict payload, or an empty dict for malformed endpoint data."""
    return value if isinstance(value, dict) else {}


def list_or_empty(value: Any) -> list[Any]:
    """Return a list payload, or an empty list for malformed endpoint data."""
    return value if isinstance(value, list) else []


def merge_clients_with_neighbours(
    clients: list[dict[str, Any]],
    neighbours: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach IP-neighbour discovery data to hotspot client records."""
    neighbours_by_mac = {
        normalize_mac(neighbour.get("mac")): neighbour
        for neighbour in neighbours
        if isinstance(neighbour, dict) and neighbour.get("mac")
    }
    merged: list[dict[str, Any]] = []
    seen_macs: set[str] = set()

    for client in clients:
        if not isinstance(client, dict):
            continue
        mac = normalize_mac(client.get("mac"))
        if not mac:
            merged.append(client)
            continue
        seen_macs.add(mac)
        neighbour = neighbours_by_mac.get(mac)
        if not neighbour:
            merged.append(client)
            continue

        item = dict(client)
        item["neighbour"] = neighbour
        if (
            not is_client_online(item)
            and neighbour.get("last-seen") not in (None, "")
        ):
            item["last-seen"] = neighbour.get("last-seen")
            item["last-seen-source"] = "neighbour"
        elif item.get("last-seen") in (None, "", 0, "0"):
            item["last-seen"] = neighbour.get("last-seen")
            item.setdefault("last-seen-source", "neighbour")
        else:
            item.setdefault("last-seen-source", "hotspot")
        if item.get("first-seen") in (None, ""):
            item["first-seen"] = neighbour.get("first-seen")
            item.setdefault("first-seen-source", "neighbour")
        else:
            item.setdefault("first-seen-source", "hotspot")
        if usable_ip(item.get("ip")) is None and neighbour.get("address-family") == "ipv4":
            item["ip"] = neighbour.get("address")
        item["neighbour-expired"] = neighbour.get("expired")
        item["neighbour-wireless"] = neighbour.get("wireless")
        item["neighbour-leasetime"] = neighbour.get("leasetime")
        merged.append(item)

    for mac, neighbour in neighbours_by_mac.items():
        if mac in seen_macs:
            continue
        merged.append(
            {
                "mac": mac,
                "via": neighbour.get("via"),
                "ip": neighbour.get("address")
                if neighbour.get("address-family") == "ipv4"
                else None,
                "active": False,
                "last-seen": neighbour.get("last-seen"),
                "last-seen-source": "neighbour",
                "first-seen": neighbour.get("first-seen"),
                "first-seen-source": "neighbour",
                "neighbour": neighbour,
                "neighbour-expired": neighbour.get("expired"),
                "neighbour-wireless": neighbour.get("wireless"),
                "neighbour-leasetime": neighbour.get("leasetime"),
            }
        )

    return merged
