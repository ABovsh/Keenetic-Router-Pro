"""WiFi sensors for temperature and traffic."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfInformation, UnitOfTemperature, EntityCategory

from ..coordinator import KeeneticCoordinator
from ..entity import ControllerEntity
from ..utils import bytes_to_gib, coerce_float


class KeeneticWifi24TemperatureSensor(ControllerEntity, SensorEntity):
    """WiFi 2.4GHz radio temperature sensor."""
    _attr_has_entity_name = True
    _attr_translation_key = "wifi_24_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._interface_prefix = "WifiMaster0"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wifi_24_temperature"

    @property
    def native_value(self) -> float | None:
        interfaces = self.coordinator.data.get("interfaces", {}) or {}
        for iface_id, iface_data in interfaces.items():
            if iface_id.startswith(self._interface_prefix) and isinstance(iface_data, dict):
                temp = coerce_float(iface_data.get("temperature"))
                # A finite-but-implausible glitch reading (sensor/driver
                # fault) must not reach MEASUREMENT long-term statistics.
                if temp is not None and -40 <= temp <= 150:
                    return temp
        return None

    @property
    def available(self) -> bool:
        return bool(getattr(super(), "available", True)) and self.native_value is not None


class KeeneticWifi5TemperatureSensor(ControllerEntity, SensorEntity):
    """WiFi 5GHz radio temperature sensor."""
    _attr_has_entity_name = True
    _attr_translation_key = "wifi_5_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._interface_prefix = "WifiMaster1"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wifi_5_temperature"

    @property
    def native_value(self) -> float | None:
        interfaces = self.coordinator.data.get("interfaces", {}) or {}
        for iface_id, iface_data in interfaces.items():
            if iface_id.startswith(self._interface_prefix) and isinstance(iface_data, dict):
                temp = coerce_float(iface_data.get("temperature"))
                # A finite-but-implausible glitch reading (sensor/driver
                # fault) must not reach MEASUREMENT long-term statistics.
                if temp is not None and -40 <= temp <= 150:
                    return temp
        return None

    @property
    def available(self) -> bool:
        return bool(getattr(super(), "available", True)) and self.native_value is not None


class KeeneticWifi24RxSensor(ControllerEntity, SensorEntity):
    """WiFi 2.4GHz RX sensor."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:download-network"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._iface_name = "WifiMaster0"
        self._band = "2.4GHz"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wifi_24_rx"

    @property
    def name(self) -> str:
        return f"WiFi {self._band} RX"

    @property
    def native_value(self) -> float | None:
        stats = self.coordinator.data.get("interface_stats", {})
        iface_stats = stats.get(self._iface_name, {})
        return bytes_to_gib(iface_stats.get("rxbytes"))


class KeeneticWifi24TxSensor(ControllerEntity, SensorEntity):
    """WiFi 2.4GHz TX sensor."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:upload-network"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._iface_name = "WifiMaster0"
        self._band = "2.4GHz"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wifi_24_tx"

    @property
    def name(self) -> str:
        return f"WiFi {self._band} TX"

    @property
    def native_value(self) -> float | None:
        stats = self.coordinator.data.get("interface_stats", {})
        iface_stats = stats.get(self._iface_name, {})
        return bytes_to_gib(iface_stats.get("txbytes"))


class KeeneticWifi5RxSensor(ControllerEntity, SensorEntity):
    """WiFi 5GHz RX sensor."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:download-network"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._iface_name = "WifiMaster1"
        self._band = "5GHz"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wifi_5_rx"

    @property
    def name(self) -> str:
        return f"WiFi {self._band} RX"

    @property
    def native_value(self) -> float | None:
        stats = self.coordinator.data.get("interface_stats", {})
        iface_stats = stats.get(self._iface_name, {})
        return bytes_to_gib(iface_stats.get("rxbytes"))


class KeeneticWifi5TxSensor(ControllerEntity, SensorEntity):
    """WiFi 5GHz TX sensor."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:upload-network"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._iface_name = "WifiMaster1"
        self._band = "5GHz"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wifi_5_tx"

    @property
    def name(self) -> str:
        return f"WiFi {self._band} TX"

    @property
    def native_value(self) -> float | None:
        stats = self.coordinator.data.get("interface_stats", {})
        iface_stats = stats.get(self._iface_name, {})
        return bytes_to_gib(iface_stats.get("txbytes"))
