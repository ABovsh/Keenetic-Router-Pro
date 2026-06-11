"""Derived coordinator data helpers."""

from __future__ import annotations

from typing import Any

from ..utils import coerce_byte_count, coerce_int, is_client_online, normalize_mac


def counter_rate_bytes_per_second(
    current: Any,
    previous: Any,
    elapsed_seconds: float,
) -> float:
    """Calculate a monotonic byte-counter rate, clamping resets to zero."""
    if elapsed_seconds <= 0:
        return 0.0
    # A missing/garbage sample on either side must not fabricate a delta:
    # coerce_byte_count rejects None/bool/negative/non-finite values, so a
    # malformed previous can't read as 0 (massive spike) and a huge current
    # can't raise OverflowError out of the refresh.
    cur = coerce_byte_count(current)
    prev = coerce_byte_count(previous)
    if cur is None or prev is None:
        return 0.0
    delta = cur - prev
    if delta < 0:
        return 0.0
    try:
        return max(0.0, delta / elapsed_seconds)
    except OverflowError:
        return 0.0


def mesh_associations(mesh_nodes: Any) -> dict[str, Any]:
    """Return total and per-node mesh client association counts."""
    by_node: dict[str, int] = {}
    total = 0
    for node in mesh_nodes or []:
        if not isinstance(node, dict):
            continue
        node_id = node.get("cid") or node.get("id")
        if not node_id:
            continue
        associations = node.get("associations")
        if isinstance(associations, (list, dict)):
            # Some firmwares expose the associated stations themselves
            # instead of a count.
            count = len(associations)
        else:
            count = coerce_int(associations, 0)
        by_node[str(node_id)] = count
        total += count
    return {"total": total, "by_node": by_node}


def real_client_macs(clients_by_mac: dict[str, dict[str, Any]]) -> set[str]:
    """Return MACs of genuine hotspot clients, excluding neighbour-only ghosts.

    Synthetic records built from the IP-neighbour (ARP/ND) table alone must
    not participate in new-device detection — a host that was merely pinged
    once is not a "new device connected".
    """
    return {
        mac
        for mac, client in clients_by_mac.items()
        if isinstance(client, dict) and not client.get("neighbour-only")
    }


def build_clients_by_mac(clients: list[Any]) -> dict[str, dict[str, Any]]:
    """Build a normalized MAC address index for hotspot clients."""
    clients_by_mac: dict[str, dict[str, Any]] = {}
    for client in clients:
        if not isinstance(client, dict):
            continue
        mac = normalize_mac(client.get("mac"))
        if not mac:
            continue
        existing = clients_by_mac.get(mac)
        if existing is not None and is_client_online(existing) and not is_client_online(client):
            # A roaming client can appear twice (once per mesh node); keep
            # the online record instead of last-entry-wins.
            continue
        clients_by_mac[mac] = client
    return clients_by_mac


def order_wan_interfaces(
    wan_interfaces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Order WANs by role and assign role labels in place."""
    default_idx: int | None = None
    for i, wan in enumerate(wan_interfaces):
        if wan.get("defaultgw"):
            default_idx = i
            break

    def _prio_key(wan: dict[str, Any]) -> int:
        return -coerce_int(wan.get("priority"))

    if default_idx is not None:
        default = wan_interfaces[default_idx]
        backups = [w for i, w in enumerate(wan_interfaces) if i != default_idx]
        backups.sort(key=_prio_key)
        ordered = [default] + backups
    else:
        ordered = sorted(wan_interfaces, key=_prio_key)

    for position, wan in enumerate(ordered):
        if position == 0 and (wan.get("defaultgw") or default_idx is None):
            wan["role_label"] = "Default connection"
            wan["role_index"] = 0
        else:
            wan["role_label"] = f"Backup connection {position}"
            wan["role_index"] = position
    return ordered
