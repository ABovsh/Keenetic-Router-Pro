"""IPsec diagnostic sensors.

A single monotonic sensor exposes the cumulative count of
``IpSec::Vici::Stats: out of memory`` events emitted by ``ndm`` on
KeeneticOS 5.x. Backed by a persistent ``Store`` in the coordinator,
the count survives HA restarts and dedups against the per-event
router timestamp, so each OOM is reported exactly once.

The previous windowed ``vici_status`` and ``vici_out_of_memory``
sensors were intentionally dropped in 1.7.46: their value depended on
how many router log lines happened to fit the scan window at poll
time, making rate comparisons meaningless. The new TOTAL_INCREASING
sensor lets HA Statistics derive a real ``events/hour`` graph and
answer "when did the problem spike" by inspecting the LTS history.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory

from ..coordinator import KeeneticCoordinator
from ..entity import ControllerEntity


class KeeneticIpsecViciOomTotalSensor(ControllerEntity, SensorEntity):
    """Cumulative count of IPsec VICI out-of-memory events (monotonic)."""

    _attr_has_entity_name = True
    _attr_name = "IPsec VICI OOM Total"
    _attr_icon = "mdi:counter"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_ipsec_vici_oom_total"

    @property
    def native_value(self) -> int | None:
        diag = self.coordinator.data.get("ipsec_diagnostics", {}) or {}
        value = diag.get("oom_total")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        diag = self.coordinator.data.get("ipsec_diagnostics", {}) or {}
        if not diag:
            return None
        return {
            "last_event_router_time": diag.get("oom_last_seen"),
            "last_message": diag.get("last_vici_out_of_memory"),
            "last_error_code": diag.get("last_error_code"),
        }
