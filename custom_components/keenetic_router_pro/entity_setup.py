"""Shared helpers for dynamic platform entity setup."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback

if TYPE_CHECKING:
    from .coordinator import KeeneticCoordinator

EntityT = TypeVar("EntityT")


@dataclass
class DynamicEntityTracker:
    """Known-ID registry used by platform setup listeners."""

    mesh_nodes: set[str] = field(default_factory=set)
    mesh_local_ips: set[str] = field(default_factory=set)
    mesh_ports: set[tuple[str, str]] = field(default_factory=set)
    main_ports: set[str] = field(default_factory=set)
    wan_ids: set[str] = field(default_factory=set)
    wireguard_ids: set[str] = field(default_factory=set)
    vpn_ids: set[str] = field(default_factory=set)
    crypto_maps: set[str] = field(default_factory=set)

    def mark_mesh_node(self, node_id: str) -> bool:
        """Return true the first time a mesh node id is seen."""
        return self._mark(self.mesh_nodes, node_id)

    def mark_mesh_local_ip(self, node_id: str) -> bool:
        """Return true the first time a mesh local-IP sensor id is seen."""
        return self._mark(self.mesh_local_ips, node_id)

    def mark_mesh_port(self, node_id: str, port_label: str) -> bool:
        """Return true the first time a mesh port key is seen."""
        return self._mark(self.mesh_ports, (node_id, port_label))

    def mark_wan(self, wan_id: str) -> bool:
        """Return true the first time a WAN id is seen."""
        return self._mark(self.wan_ids, wan_id)

    def mark_main_port(self, port_label: str) -> bool:
        """Return true the first time a main-router port is seen."""
        return self._mark(self.main_ports, port_label)

    def mark_wireguard(self, profile_id: str) -> bool:
        """Return true the first time a WireGuard profile is seen."""
        return self._mark(self.wireguard_ids, profile_id)

    def mark_vpn(self, iface_id: str) -> bool:
        """Return true the first time a VPN interface id is seen."""
        return self._mark(self.vpn_ids, iface_id)

    def mark_crypto_map(self, cmap_name: str) -> bool:
        """Return true the first time a crypto-map name is seen."""
        return self._mark(self.crypto_maps, cmap_name)

    @staticmethod
    def _mark(seen: set[Any], key: Any) -> bool:
        if key in seen:
            return False
        seen.add(key)
        return True


def register_dynamic_entities(
    entry: ConfigEntry,
    coordinator: KeeneticCoordinator,
    async_add_entities: Callable[[list[EntityT]], None],
    build_entities: Callable[[], list[EntityT]],
    *,
    add_initial: bool = True,
) -> None:
    """Add current dynamic entities and register a listener for future additions."""
    if add_initial:
        initial = build_entities()
        if initial:
            async_add_entities(initial)

    @callback
    def _async_add_new_entities() -> None:
        new_entities = build_entities()
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))
