"""WireGuard VPN sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfInformation, EntityCategory

from ..coordinator import KeeneticCoordinator
from ..const import COUNTER_DEADBAND_BYTES
from ..entity import ControllerEntity, CounterDeadbandMixin, LinkActiveMixin, UptimeMixin
from ..utils import bytes_to_mib, coerce_byte_count, coerce_seconds


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


class KeeneticWgUptimeSensor(UptimeMixin, _BaseWgSensor):
    """WireGuard tunnel uptime, published hourly (see ``UptimeMixin``).

    Unavailable while the profile is down: there is no session to time.
    """
    _attr_has_entity_name = True

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wg_{self._wg_name}_uptime"

    @property
    def name(self) -> str:
        return f"WireGuard {self._wg_label} Uptime"

    @property
    def available(self) -> bool:
        return super().available and bool(self._wg.get("enabled"))

    @property
    def native_value(self) -> int | None:
        for key in ("uptime", "uptime_sec", "uptime_seconds"):
            seconds = coerce_seconds(self._wg.get(key), default=None)
            if seconds is not None:
                return self._publish_uptime(seconds)
        return self._publish_uptime(None)


class _WgLinkActiveMixin(LinkActiveMixin):
    """Gate a WireGuard traffic sensor on the profile being up."""

    def _link_active(self) -> bool:
        return bool(self._wg.get("enabled"))


class _WgUplinkStatisticsMixin:
    """Leave a WAN uplink's traffic statistics to its WAN RX/TX Bytes sensor.

    A profile used as an uplink is also a WAN, whose own byte sensors record
    the same counter; two statistics streams for one counter double the rows.
    """

    @property
    def state_class(self) -> SensorStateClass | None:
        wans = (self.coordinator.data or {}).get("wan_interfaces") or []
        if any(isinstance(w, dict) and w.get("id") == self._wg_name for w in wans):
            return None
        return self._attr_state_class


class KeeneticWgRxSensor(
    _WgUplinkStatisticsMixin, _WgLinkActiveMixin, CounterDeadbandMixin, _BaseWgSensor
):
    """WireGuard RX (received traffic) sensor."""
    _attr_has_entity_name = True
    # RX bytes is a cumulative counter that resets when the tunnel restarts —
    # TOTAL_INCREASING (not the base MEASUREMENT) is the correct contract so
    # HA long-term statistics chart reset-aware deltas rather than the raw
    # absolute counter.
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    # Shared byte step expressed in this sensor's own unit (MiB).
    _COUNTER_DEADBAND = COUNTER_DEADBAND_BYTES / 1024**2

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
            raw = coerce_byte_count(self._wg.get(key))
            mib = bytes_to_mib(raw)
            if mib is not None:
                return self._publish_counter(mib, raw_value=raw)
        return self._publish_counter(None)


class KeeneticWgTxSensor(
    _WgUplinkStatisticsMixin, _WgLinkActiveMixin, CounterDeadbandMixin, _BaseWgSensor
):
    """WireGuard TX (sent traffic) sensor."""
    _attr_has_entity_name = True
    # See KeeneticWgRxSensor: cumulative counter → TOTAL_INCREASING.
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    # Shared byte step expressed in this sensor's own unit (MiB).
    _COUNTER_DEADBAND = COUNTER_DEADBAND_BYTES / 1024**2

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
            raw = coerce_byte_count(self._wg.get(key))
            mib = bytes_to_mib(raw)
            if mib is not None:
                return self._publish_counter(mib, raw_value=raw)
        return self._publish_counter(None)
