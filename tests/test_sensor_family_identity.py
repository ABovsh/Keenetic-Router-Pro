"""Persistent identity of the shared-base sensor families.

A band or direction swap inside a shared base looks correct when both legs
receive the same input, so every leaf is pinned to its own unique ID, name,
icon, unit and registry default.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.const import UnitOfInformation

from custom_components.keenetic_router_pro.sensor.client import (
    KeeneticClientRxSensor,
    KeeneticClientTxSensor,
)
from custom_components.keenetic_router_pro.sensor.wifi import (
    KeeneticWifi5RxSensor,
    KeeneticWifi5TemperatureSensor,
    KeeneticWifi5TxSensor,
    KeeneticWifi24RxSensor,
    KeeneticWifi24TemperatureSensor,
    KeeneticWifi24TxSensor,
)
from custom_components.keenetic_router_pro.sensor.wireguard import (
    KeeneticWgRxSensor,
    KeeneticWgTxSensor,
)

_ENTRY = SimpleNamespace(entry_id="e1", title="Router")
_MAC = "aa:bb:cc:dd:ee:ff"
_GIB = 1024**3
_MIB = 1024**2


def _coordinator() -> SimpleNamespace:
    return SimpleNamespace(
        data={
            "interfaces": {
                "WifiMaster0": {"temperature": 41},
                "WifiMaster1": {"temperature": 57},
            },
            "interface_stats": {
                "WifiMaster0": {"rxbytes": 1 * _GIB, "txbytes": 2 * _GIB},
                "WifiMaster1": {"rxbytes": 3 * _GIB, "txbytes": 4 * _GIB},
            },
            "interface_stats_fresh": True,
            "clients": [
                {"mac": _MAC, "active": True, "rxbytes": 5 * _GIB, "txbytes": 6 * _GIB}
            ],
            "wireguard": {
                "profiles": {
                    "Wireguard0": {
                        "label": "Office",
                        "enabled": True,
                        "rxbytes": 7 * _MIB,
                        "txbytes": 8 * _MIB,
                    }
                }
            },
        }
    )


@pytest.mark.parametrize(
    ("factory", "unique_id", "name", "icon", "unit", "enabled_default", "value"),
    [
        (lambda c: KeeneticWifi24RxSensor(c, _ENTRY), "e1_wifi_24_rx", "WiFi 2.4GHz RX", "mdi:download-network", UnitOfInformation.GIGABYTES, True, 1.0),
        (lambda c: KeeneticWifi24TxSensor(c, _ENTRY), "e1_wifi_24_tx", "WiFi 2.4GHz TX", "mdi:upload-network", UnitOfInformation.GIGABYTES, True, 2.0),
        (lambda c: KeeneticWifi5RxSensor(c, _ENTRY), "e1_wifi_5_rx", "WiFi 5GHz RX", "mdi:download-network", UnitOfInformation.GIGABYTES, True, 3.0),
        (lambda c: KeeneticWifi5TxSensor(c, _ENTRY), "e1_wifi_5_tx", "WiFi 5GHz TX", "mdi:upload-network", UnitOfInformation.GIGABYTES, True, 4.0),
        (lambda c: KeeneticClientRxSensor(c, _ENTRY, _MAC, "Phone"), f"e1_client_{_MAC}_rx", "RX", "mdi:download-network", UnitOfInformation.GIGABYTES, False, 5.0),
        (lambda c: KeeneticClientTxSensor(c, _ENTRY, _MAC, "Phone"), f"e1_client_{_MAC}_tx", "TX", "mdi:upload-network", UnitOfInformation.GIGABYTES, False, 6.0),
        (lambda c: KeeneticWgRxSensor(c, _ENTRY, "Wireguard0"), "e1_wg_Wireguard0_rx", "WireGuard Office RX", None, UnitOfInformation.MEGABYTES, True, 7.0),
        (lambda c: KeeneticWgTxSensor(c, _ENTRY, "Wireguard0"), "e1_wg_Wireguard0_tx", "WireGuard Office TX", None, UnitOfInformation.MEGABYTES, True, 8.0),
    ],
)
def test_traffic_leaf_identity(factory, unique_id, name, icon, unit, enabled_default, value) -> None:
    sensor = factory(_coordinator())
    assert sensor.unique_id == unique_id
    assert sensor.name == name
    assert getattr(sensor, "_attr_icon", None) == icon
    assert (getattr(sensor, "native_unit_of_measurement", None) or sensor._attr_native_unit_of_measurement) == unit
    assert getattr(sensor, "_attr_entity_registry_enabled_default", True) is enabled_default
    assert sensor.native_value == pytest.approx(value)


@pytest.mark.parametrize(
    ("cls", "unique_id", "translation_key", "value"),
    [
        (KeeneticWifi24TemperatureSensor, "e1_wifi_24_temperature", "wifi_24_temperature", 41.0),
        (KeeneticWifi5TemperatureSensor, "e1_wifi_5_temperature", "wifi_5_temperature", 57.0),
    ],
)
def test_radio_temperature_identity(cls, unique_id, translation_key, value) -> None:
    sensor = cls(_coordinator(), _ENTRY)
    assert sensor.unique_id == unique_id
    assert sensor._attr_translation_key == translation_key
    assert sensor.native_value == pytest.approx(value)
