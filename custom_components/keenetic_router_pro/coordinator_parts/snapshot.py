"""Snapshot assembly from the coordinator's resolved reads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..utils import is_client_online, normalize_mac
from .derived import build_clients_by_mac, mesh_associations, real_client_macs
from .enrichment import carry_wan_samples, enrich_crypto_maps, enrich_wan_interfaces
from .refresh import Source


@dataclass(slots=True)
class _PrimaryData:
    """Resolved primary payloads and their freshness and retry inputs."""

    system: dict[str, Any]
    version: Any
    version_available: Any
    interfaces: dict[str, Any]
    clients: list[dict[str, Any]]
    clients_stale: bool
    mesh_nodes: list[dict[str, Any]]
    mesh_nodes_fresh: bool
    host_policies: dict[str, Any]
    policies: dict[str, Any]
    ndns: dict[str, Any]
    ping_check_status: dict[str, Any]
    ping_check_failed: bool
    crypto_maps: dict[str, Any]
    crypto_maps_fresh: bool
    crypto_maps_failed: bool
    host_policies_failed: bool
    available_version_failed: bool


@dataclass(slots=True)
class _InterfaceData:
    """Resolved interface data and the outcomes of its reads."""

    fingerprint: tuple
    wifi: list[Any]
    wireguard: list[Any]
    vpn_tunnels: list[Any]
    wan_status: dict[str, Any]
    wan_interfaces: list[dict[str, Any]]
    wan_interfaces_failed: bool
    traffic_stats: dict[str, Any]
    port_info: list[Any]
    interface_stats: dict[str, Any]
    interface_stats_fresh: bool
    interface_stats_failed: bool


# Publish the CPU and RAM gauges on the medium tier.
_GAUGE_KEYS = (
    "cpu_load",
    "cpuload",
    "cpu",
    "cpu-utilization",
    "memory",
    "mem",
    "memtotal",
    "memfree",
)


def _merge_system(
    primary: _PrimaryData,
    prev_system: dict[str, Any],
    due: frozenset[Source],
) -> dict[str, Any]:
    """Combine system and firmware data before the dependent reads."""
    system = {**primary.system, **primary.version}
    if Source.SYSTEM_GAUGES not in due and prev_system:
        for key in _GAUGE_KEYS:
            if key in prev_system:
                system[key] = prev_system[key]
            else:
                system.pop(key, None)
    available = primary.version_available
    if available is None:
        system["release-available"] = prev_system.get("release-available")
        system["fw-update-sandbox"] = prev_system.get("fw-update-sandbox")
        system["fw-update-available"] = prev_system.get("fw-update-available")
    else:
        system["release-available"] = available.get("title") or available.get("release")
        system["fw-update-sandbox"] = available.get("sandbox")
        system["fw-update-available"] = available.get("update-available", False)
    return system


def _assemble_snapshot(
    primary: _PrimaryData,
    links: _InterfaceData,
    *,
    due: frozenset[Source],
    previous: dict[str, Any],
    cached: dict[str, Any],
    merged_system: dict[str, Any],
    client_stats: dict[str, Any],
    wan_observation_fresh: bool,
    clock: Callable[[], float],
) -> dict[str, Any]:
    """Enrich current data and derive indexes and events for the snapshot."""
    wan_interfaces = links.wan_interfaces
    if Source.INTERFACE_STATS in due and not links.interface_stats_failed:
        now_ts = clock()
        wan_interfaces, wan_by_id = enrich_wan_interfaces(
            wan_interfaces,
            links.interface_stats,
            primary.ping_check_status,
            previous.get("wan_interfaces", []),
            now_ts,
        )
    else:
        wan_interfaces, wan_by_id = carry_wan_samples(
            wan_interfaces,
            primary.ping_check_status,
            previous.get("wan_interfaces", []),
        )

    if Source.IPSEC in due:
        now_ts = clock()
        # A failed read carries the previous rates over without a new sample.
        enrich_crypto_maps(
            primary.crypto_maps,
            previous.get("crypto_maps"),
            now_ts,
            not primary.crypto_maps_failed,
        )

    mesh_nodes = primary.mesh_nodes
    if Source.MESH_MEMBERS in due:
        mesh_associations_data = mesh_associations(mesh_nodes)
        mesh_nodes_by_cid = {
            (n.get("cid") or n.get("id")): n
            for n in (mesh_nodes if isinstance(mesh_nodes, list) else [])
            if isinstance(n, dict) and (n.get("cid") or n.get("id"))
        }
    else:
        # Keep the indexes from the snapshot that supplied the cached mesh nodes.
        mesh_associations_data = cached.get("mesh_associations", {})
        mesh_nodes_by_cid = cached.get("mesh_nodes_by_cid", {})

    clients_by_mac = build_clients_by_mac(primary.clients)
    hotspot_fresh = Source.HOSTS in due and not primary.clients_stale
    new_macs: set[str] = set()
    if hotspot_fresh:
        # Neighbour-only rows can enrich the index before the hotspot first
        # reports a client. Exclude them from both sides of the event diff.
        previous_by_mac = previous.get("clients_by_mac")
        if not isinstance(previous_by_mac, dict) or not previous_by_mac:
            previous_by_mac = build_clients_by_mac(previous.get("clients", []))
        previous_macs = {
            mac for key in real_client_macs(previous_by_mac) if (mac := normalize_mac(key))
        }
        new_macs = real_client_macs(clients_by_mac) - previous_macs

    # ---------- Connect / disconnect / failover events ----------
    # Automations otherwise have to trigger on entity state and poll
    # their way to "who just left". These diffs cost one set operation
    # a tick and nothing at all while the network is quiet.
    online_macs = {
        mac
        for mac, client in clients_by_mac.items()
        if isinstance(client, dict) and is_client_online(client)
    }
    previous_online = previous.get("online_clients", set())
    if not isinstance(previous_online, set):
        previous_online = set()
    # On the very first tick every client looks like an arrival; the
    # integration has simply never seen this network before.
    first_tick = not previous
    if not hotspot_fresh:
        # The hotspot table is the PREVIOUS tick's snapshot, re-merged
        # with this tick's neighbour data — diffing that invents
        # arrivals and departures the router never reported.
        # ClientEntity.available and the device tracker already refuse
        # to trust a stale tick. Keeping the previous online set also
        # stops the next healthy tick from firing a phantom reconnect.
        online_macs = set(previous_online)
        connected_macs: set[str] = set()
        disconnected_macs: set[str] = set()
    else:
        connected_macs = set() if first_tick else online_macs - previous_online
        disconnected_macs = set() if first_tick else previous_online - online_macs

    active_wan = next(
        (
            wan_id
            for wan_id, wan in wan_by_id.items()
            if isinstance(wan, dict) and wan.get("defaultgw")
        ),
        None,
    )
    previous_wan = previous.get("active_wan")
    wan_failover = (
        {"from": previous_wan, "to": active_wan}
        if not first_tick and active_wan != previous_wan
        else None
    )

    return {
        "system": merged_system,
        "traffic_stats": links.traffic_stats,
        "interfaces": primary.interfaces,
        "wifi": links.wifi,
        "wireguard": links.wireguard,
        "vpn_tunnels": links.vpn_tunnels,
        "clients": primary.clients,
        "clients_stale": primary.clients_stale,
        "clients_by_mac": clients_by_mac,
        "wan_status": links.wan_status,
        "wan_interfaces": wan_interfaces,
        "wan_observation_fresh": wan_observation_fresh,
        "wan_by_id": wan_by_id,
        "mesh_nodes": mesh_nodes,
        "mesh_nodes_fresh": primary.mesh_nodes_fresh,
        "mesh_associations": mesh_associations_data,
        "mesh_nodes_by_cid": mesh_nodes_by_cid,
        "interface_stats": links.interface_stats,
        "interface_stats_fresh": links.interface_stats_fresh,
        "client_stats": client_stats,
        "ndns": primary.ndns,
        "host_policies": primary.host_policies,
        "policies": primary.policies,
        "port_info": links.port_info,
        "ping_check_status": primary.ping_check_status,
        "_iface_fingerprint": links.fingerprint,
        "crypto_maps": primary.crypto_maps,
        "crypto_maps_fresh": primary.crypto_maps_fresh,
        "new_clients": new_macs,
        "online_clients": online_macs,
        "connected_clients": connected_macs,
        "disconnected_clients": disconnected_macs,
        "active_wan": active_wan,
        "wan_failover": wan_failover,
    }
