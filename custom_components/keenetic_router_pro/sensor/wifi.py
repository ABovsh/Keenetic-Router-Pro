"""WiFi sensors for temperature and traffic."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfInformation, UnitOfTemperature, EntityCategory

from ..coordinator import KeeneticCoordinator
from ..const import COUNTER_DEADBAND_BYTES
from ..entity import (
    ControllerEntity,
    CounterDeadbandMixin,
    DeadbandMixin,
    SourceFreshnessMixin,
)
from ..utils import bytes_to_gib, coerce_byte_count, coerce_float

# Measured on live hardware: the radio temperature alternates between two
# readings 2 °C apart on every poll, writing ~1,000 recorder rows a day while
# saying nothing. A radio thermometer is a trend indicator, not an instrument.
_TEMPERATURE_DEADBAND_C = 3.0


class _RadioTemperature(DeadbandMixin, ControllerEntity, SensorEntity):
    """Radio temperature sensor; leaves set the radio and the ID suffix."""
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = None
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:thermometer"
    _DEADBAND = _TEMPERATURE_DEADBAND_C

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_{self._suffix}"

    @property
    def native_value(self) -> float | None:
        interfaces = self.coordinator.data.get("interfaces", {}) or {}
        for iface_id, iface_data in interfaces.items():
            if iface_id.startswith(self._interface_prefix) and isinstance(iface_data, dict):
                temp = coerce_float(iface_data.get("temperature"))
                # A finite-but-implausible glitch reading (sensor/driver
                # fault) must not reach MEASUREMENT long-term statistics —
                # and must not be held alive by the deadband either.
                if temp is not None and -40 <= temp <= 150:
                    return self._apply_deadband(temp)
        return self._apply_deadband(None)

    @property
    def available(self) -> bool:
        return bool(getattr(super(), "available", True)) and self.native_value is not None


class _RadioTraffic(
    SourceFreshnessMixin, CounterDeadbandMixin, ControllerEntity, SensorEntity
):
    """Radio traffic counter; leaves set the radio, direction and ID suffix."""
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_state_class = None
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Shared byte step expressed in this sensor's own unit (GiB).
    _COUNTER_DEADBAND = COUNTER_DEADBAND_BYTES / 1024**3
    _freshness_key = "interface_stats_fresh"

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_{self._suffix}"

    @property
    def name(self) -> str:
        return self._name

    @property
    def native_value(self) -> float | None:
        stats = self.coordinator.data.get("interface_stats", {})
        iface_stats = stats.get(self._iface_name, {})
        raw = coerce_byte_count(iface_stats.get(self._counter_key))
        return self._publish_counter(bytes_to_gib(raw), raw_value=raw)


class KeeneticWifi24TemperatureSensor(_RadioTemperature):
    _attr_translation_key = "wifi_24_temperature"
    _interface_prefix = "WifiMaster0"
    _suffix = "wifi_24_temperature"


class KeeneticWifi24RxSensor(_RadioTraffic):
    _attr_icon = "mdi:download-network"
    _iface_name = "WifiMaster0"
    _suffix = "wifi_24_rx"
    _name = "WiFi 2.4GHz RX"
    _counter_key = "rxbytes"


class KeeneticWifi24TxSensor(_RadioTraffic):
    _attr_icon = "mdi:upload-network"
    _iface_name = "WifiMaster0"
    _suffix = "wifi_24_tx"
    _name = "WiFi 2.4GHz TX"
    _counter_key = "txbytes"


class KeeneticWifi5TemperatureSensor(_RadioTemperature):
    _attr_translation_key = "wifi_5_temperature"
    _interface_prefix = "WifiMaster1"
    _suffix = "wifi_5_temperature"


class KeeneticWifi5RxSensor(_RadioTraffic):
    _attr_icon = "mdi:download-network"
    _iface_name = "WifiMaster1"
    _suffix = "wifi_5_rx"
    _name = "WiFi 5GHz RX"
    _counter_key = "rxbytes"


class KeeneticWifi5TxSensor(_RadioTraffic):
    _attr_icon = "mdi:upload-network"
    _iface_name = "WifiMaster1"
    _suffix = "wifi_5_tx"
    _name = "WiFi 5GHz TX"
    _counter_key = "txbytes"
