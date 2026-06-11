"""WireGuard VPN sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfInformation, UnitOfTime, EntityCategory

from ..coordinator import KeeneticCoordinator
from ..entity import ControllerEntity
from ..utils import bytes_to_mib, coerce_seconds


class _BaseWgSensor(ControllerEntity, SensorEntity):
    """Base class for WireGuard sensors."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry, wg_name: str) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._wg_name = wg_name

    @property
    def _wg_profiles(self) -> dict[str, Any]:
        return self.coordinator.data.get("wireguard", {}).get("profiles", {}) or {}

    @property
    def _wg(self) -> dict[str, Any]:
        return self._wg_profiles.get(self._wg_name, {}) or {}

    @property
    def available(self) -> bool:
        """Become unavailable when this WireGuard profile disappears."""
        return bool(getattr(super(), "available", True)) and self._wg_name in self._wg_profiles

    @property
    def _wg_label(self) -> str:
        profile = self._wg
        label = profile.get("label")
        if isinstance(label, str) and label.strip():
            return label.strip()
        return self._wg_name


class KeeneticWgUptimeSensor(_BaseWgSensor):
    """WireGuard tunnel uptime sensor.

    Override the base ``MEASUREMENT`` default with ``TOTAL_INCREASING``:
    uptime resets to zero when the tunnel reconnects, which is exactly
    the semantics ``TOTAL_INCREASING`` expects, and avoids the sawtooth
    long-term-statistics graph that ``MEASUREMENT`` would produce.
    """
    _attr_has_entity_name = True
    _attr_suggested_display_precision = 0
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wg_{self._wg_name}_uptime"

    @property
    def name(self) -> str:
        return f"WireGuard {self._wg_label} Uptime"

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfTime.SECONDS

    @property
    def native_value(self) -> int:
        for key in ("uptime", "uptime_sec", "uptime_seconds"):
            seconds = coerce_seconds(self._wg.get(key), default=None)
            if seconds is not None:
                return seconds
        return 0


class KeeneticWgRxSensor(_BaseWgSensor):
    """WireGuard RX (received traffic) sensor."""
    _attr_has_entity_name = True
    # RX bytes is a cumulative counter that resets when the tunnel restarts —
    # TOTAL_INCREASING (not the base MEASUREMENT) is the correct contract so
    # HA long-term statistics chart reset-aware deltas rather than the raw
    # absolute counter.
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wg_{self._wg_name}_rx"

    @property
    def name(self) -> str:
        return f"WireGuard {self._wg_label} RX"

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfInformation.MEGABYTES

    @property
    def native_value(self) -> float | None:
        for key in ("rxbytes", "rx", "received"):
            mib = bytes_to_mib(self._wg.get(key))
            if mib is not None:
                return mib
        return None


class KeeneticWgTxSensor(_BaseWgSensor):
    """WireGuard TX (sent traffic) sensor."""
    _attr_has_entity_name = True
    # See KeeneticWgRxSensor: cumulative counter → TOTAL_INCREASING.
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wg_{self._wg_name}_tx"

    @property
    def name(self) -> str:
        return f"WireGuard {self._wg_label} TX"

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfInformation.MEGABYTES

    @property
    def native_value(self) -> float | None:
        for key in ("txbytes", "tx", "sent"):
            mib = bytes_to_mib(self._wg.get(key))
            if mib is not None:
                return mib
        return None
