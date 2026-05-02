"""Traffic sensors for LAN, WAN and generic interfaces."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfInformation

from ..coordinator import KeeneticCoordinator
from ..entity import ControllerEntity


class _TrafficSensorBase(ControllerEntity, SensorEntity):
    """Shared RX/TX byte counter sensor for one interface."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _direction = "rx"

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        iface_name: str,
        iface_label: str,
    ) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._iface_name = iface_name
        self._iface_label = iface_label

    @property
    def _field(self) -> str:
        return f"{self._direction}bytes"

    @property
    def _stats(self) -> dict[str, Any]:
        stats = self.coordinator.data.get("interface_stats", {})
        return stats.get(self._iface_name, {}) or {}

    @property
    def unique_id(self) -> str:
        safe_name = self._iface_name.replace("/", "_").lower()
        return f"{self._entry_id}_iface_{safe_name}_{self._direction}"

    @property
    def name(self) -> str:
        return f"{self._iface_label} {self._direction.upper()}"

    @property
    def native_value(self) -> float | None:
        value = self._stats.get(self._field, 0)
        if value:
            try:
                return round(float(value) / (1024 ** 3), 2)
            except (TypeError, ValueError):
                return None
        return 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        stats = self._stats
        return {
            "interface": self._iface_name,
            "type": stats.get("interface_type"),
            "link": stats.get("link"),
            "state": stats.get("state"),
            f"{self._direction}packets": stats.get(f"{self._direction}packets"),
            f"{self._direction}errors": stats.get(f"{self._direction}errors"),
            f"{self._direction}dropped": stats.get(f"{self._direction}dropped"),
        }


class KeeneticInterfaceRxSensor(_TrafficSensorBase):
    """Incoming traffic sensor for a specific interface."""

    _attr_icon = "mdi:download-network"
    _direction = "rx"


class KeeneticInterfaceTxSensor(_TrafficSensorBase):
    """Outgoing traffic sensor for a specific interface."""

    _attr_icon = "mdi:upload-network"
    _direction = "tx"


class _FixedTrafficSensor(_TrafficSensorBase):
    """Traffic sensor for a built-in interface name."""

    _fixed_iface_name = ""
    _fixed_iface_label = ""
    _unique_prefix = ""

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            self._fixed_iface_name,
            self._fixed_iface_label,
        )

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_{self._unique_prefix}_{self._direction}"


class KeeneticLanRxSensor(_FixedTrafficSensor):
    """LAN (GigabitEthernet0) RX sensor."""

    _attr_icon = "mdi:download-network"
    _direction = "rx"
    _fixed_iface_name = "GigabitEthernet0"
    _fixed_iface_label = "LAN"
    _unique_prefix = "lan"


class KeeneticLanTxSensor(_FixedTrafficSensor):
    """LAN (GigabitEthernet0) TX sensor."""

    _attr_icon = "mdi:upload-network"
    _direction = "tx"
    _fixed_iface_name = "GigabitEthernet0"
    _fixed_iface_label = "LAN"
    _unique_prefix = "lan"


class KeeneticWanRxSensor(_FixedTrafficSensor):
    """WAN (GigabitEthernet1/ISP) RX sensor."""

    _attr_icon = "mdi:download-network"
    _direction = "rx"
    _fixed_iface_name = "GigabitEthernet1"
    _fixed_iface_label = "WAN"
    _unique_prefix = "wan"


class KeeneticWanTxSensor(_FixedTrafficSensor):
    """WAN (GigabitEthernet1/ISP) TX sensor."""

    _attr_icon = "mdi:upload-network"
    _direction = "tx"
    _fixed_iface_name = "GigabitEthernet1"
    _fixed_iface_label = "WAN"
    _unique_prefix = "wan"
