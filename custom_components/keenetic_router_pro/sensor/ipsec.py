"""IPsec diagnostic sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory

from ..const import FAST_SCAN_INTERVAL
from ..coordinator import KeeneticCoordinator
from ..entity import ControllerEntity


_IPSEC_DIAGNOSTIC_INTERVAL_SECONDS = FAST_SCAN_INTERVAL * 30


class KeeneticIpsecViciStatusSensor(ControllerEntity, SensorEntity):
    """Recent IPsec VICI memory-error status from the router log."""

    _attr_has_entity_name = True
    _attr_name = "IPsec VICI Status"
    _attr_icon = "mdi:shield-alert-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_ipsec_vici_status"

    @property
    def native_value(self) -> str | None:
        diagnostics = self.coordinator.data.get("ipsec_diagnostics", {}) or {}
        return diagnostics.get("status")

    @property
    def icon(self) -> str:
        if self.native_value == "warning":
            return "mdi:shield-alert"
        if self.native_value == "ok":
            return "mdi:shield-check"
        return "mdi:shield-search"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        diagnostics = self.coordinator.data.get("ipsec_diagnostics", {}) or {}
        if not diagnostics:
            return None
        return {
            "vici_out_of_memory_count": diagnostics.get("vici_out_of_memory_count"),
            "last_vici_out_of_memory": diagnostics.get("last_vici_out_of_memory"),
            "last_error_code": diagnostics.get("last_error_code"),
            "recent_matches": diagnostics.get("recent_matches"),
            "scanned_log_lines": diagnostics.get("scanned_log_lines"),
            "command": diagnostics.get("command"),
            "poll_interval_seconds": _IPSEC_DIAGNOSTIC_INTERVAL_SECONDS,
        }


class KeeneticIpsecViciOutOfMemorySensor(ControllerEntity, SensorEntity):
    """Count of recent IPsec VICI out-of-memory log entries."""

    _attr_has_entity_name = True
    _attr_name = "IPsec VICI Out Of Memory"
    _attr_icon = "mdi:counter"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_ipsec_vici_out_of_memory"

    @property
    def native_value(self) -> int | None:
        diagnostics = self.coordinator.data.get("ipsec_diagnostics", {}) or {}
        value = diagnostics.get("vici_out_of_memory_count")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
