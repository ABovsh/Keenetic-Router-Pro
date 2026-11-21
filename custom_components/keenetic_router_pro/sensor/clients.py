"""Client sensors for connected/disconnected devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry

from ..const import FIELD_CONNECTED
from ..coordinator import KeeneticCoordinator
from ..entity import ControllerEntity


class KeeneticConnectedClientsSensor(ControllerEntity, SensorEntity):
    """Connected clients count sensor."""
    _attr_has_entity_name = True
    _attr_translation_key = "connected_clients"
    _attr_icon = "mdi:devices"
    _attr_state_class = None
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_connected_clients_v2"

    @property
    def native_value(self) -> int:
        stats = self.coordinator.data.get("client_stats", {})
        return stats.get(FIELD_CONNECTED, 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        stats = self.coordinator.data.get("client_stats", {})
        return {
            "total": stats.get("total", 0),
            "per_ap": stats.get("per_ap", {}),
        }


class KeeneticRouterClientsSensor(ControllerEntity, SensorEntity):
    """Clients directly connected to main router sensor."""
    _attr_has_entity_name = True
    _attr_translation_key = "router_clients"
    _attr_icon = "mdi:devices"
    # Per-AP distribution stays in recent history; the aggregate keeps statistics.
    _attr_state_class = None
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_router_clients_v2"

    def _mesh_associations(self) -> int:
        associations = self.coordinator.data.get("mesh_associations") or {}
        if isinstance(associations, dict):
            return int(associations.get("total") or 0)
        return 0

    @property
    def native_value(self) -> int:
        stats = self.coordinator.data.get("client_stats", {})
        return max(stats.get(FIELD_CONNECTED, 0) - self._mesh_associations(), 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        stats = self.coordinator.data.get("client_stats", {})
        return {
            "total_connected": stats.get(FIELD_CONNECTED, 0),
            "mesh_clients": self._mesh_associations(),
            "per_ap": stats.get("per_ap", {}),
        }
