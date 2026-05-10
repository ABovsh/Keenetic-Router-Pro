"""System sensors for CPU, memory, uptime and firmware."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime, EntityCategory

from ..coordinator import KeeneticCoordinator
from ..entity import ControllerEntity, MeshEntity
from ..utils import coerce_seconds


class KeeneticCpuLoadSensor(ControllerEntity, SensorEntity):
    """CPU load sensor."""
    _attr_has_entity_name = True
    _attr_translation_key = "cpu_load"
    _attr_icon = "mdi:cpu-64-bit"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_cpu_load"

    @property
    def native_unit_of_measurement(self) -> str:
        return PERCENTAGE

    @property
    def native_value(self) -> float | None:
        sys = self.coordinator.data.get("system", {}) or {}
        for key in ("cpu_load", "cpuload", "cpu", "cpu-utilization"):
            if key in sys:
                return float(sys[key])
        return None


class KeeneticMemoryUsageSensor(ControllerEntity, SensorEntity):
    """RAM usage percentage sensor."""
    _attr_has_entity_name = True
    _attr_translation_key = "memory_usage"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:memory"

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_mem_usage"

    @property
    def native_unit_of_measurement(self) -> str:
        return PERCENTAGE

    @property
    def native_value(self) -> float | None:
        sys = self.coordinator.data.get("system", {}) or {}
        mem = sys.get("memory") or sys.get("mem")
        memtotal = sys.get("memtotal")
        memfree = sys.get("memfree")

        if isinstance(mem, str) and "/" in mem:
            try:
                part_used, part_total = mem.split("/", 1)
                used = float(part_used)
                total = float(part_total)
                if total > 0:
                    return round(used * 100.0 / total, 1)
            except (ValueError, TypeError):
                pass

        if isinstance(memtotal, (int, float)) and isinstance(memfree, (int, float)) and memtotal > 0:
            used = memtotal - memfree
            return round(used * 100.0 / memtotal, 1)

        for key in ("mem_used_percent", "memory_usage", "memusage"):
            if key in sys:
                try:
                    return float(sys[key])
                except (TypeError, ValueError):
                    continue

        return None


class KeeneticUptimeSensor(ControllerEntity, SensorEntity):
    """Router uptime sensor.

    ``TOTAL_INCREASING`` is the right state class for a monotonic
    counter that resets to zero on reboot. Storing uptime as
    ``MEASUREMENT`` produces a sawtooth in long-term statistics
    because every poll inserts a fresh sample.
    """
    _attr_has_entity_name = True
    _attr_translation_key = "uptime"
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_uptime"

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfTime.SECONDS

    @property
    def native_value(self) -> int:
        sys = self.coordinator.data.get("system", {}) or {}
        candidates = []

        for key in ("uptime", "uptime_sec", "uptime_seconds"):
            if key in sys:
                candidates.append(sys.get(key))

        nested = sys.get("system")
        if isinstance(nested, dict):
            for key in ("uptime", "uptime_sec", "uptime_seconds"):
                if key in nested:
                    candidates.append(nested.get(key))

        for value in candidates:
            seconds = coerce_seconds(value, default=None)
            if seconds is not None:
                return seconds

        return 0


class KeeneticFirmwareVersionSensor(ControllerEntity, SensorEntity):
    """Current firmware version sensor for the main router."""
    _attr_has_entity_name = True
    _attr_translation_key = "firmware_version"
    _attr_icon = "mdi:package-variant-closed"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_firmware_version"

    @property
    def native_value(self) -> str | None:
        system = self.coordinator.data.get("system", {}) or {}
        # Keenetic API returns firmware info under "title" and "release" keys
        # from /rci/show/version endpoint (consistent with entity._firmware_version)
        if system.get("title"):
            return str(system["title"])
        if system.get("release"):
            return str(system["release"])
        # Fallback: check ndw4 nested version
        ndw4 = system.get("ndw4", {})
        if isinstance(ndw4, dict) and ndw4.get("version"):
            return str(ndw4["version"])
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        system = self.coordinator.data.get("system", {}) or {}
        attrs: dict[str, Any] = {}
        if system.get("release"):
            attrs["release"] = system["release"]
        if system.get("fw-update-sandbox"):
            attrs["channel"] = system["fw-update-sandbox"]
        if system.get("arch"):
            attrs["architecture"] = system["arch"]
        ndm = system.get("ndm")
        if isinstance(ndm, dict) and ndm.get("exact"):
            attrs["ndm_version"] = ndm["exact"]
        bsp = system.get("bsp")
        if isinstance(bsp, dict) and bsp.get("exact"):
            attrs["bsp_version"] = bsp["exact"]
        return attrs if attrs else None

