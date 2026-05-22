"""Edge-value tests for sensors that read a specific coordinator field."""

from __future__ import annotations

import pytest

from custom_components.keenetic_router_pro.sensor.crypto import (
    KeeneticCryptoMapRxBytesSensor,
    KeeneticCryptoMapRxThroughputSensor,
    KeeneticCryptoMapTxBytesSensor,
    KeeneticCryptoMapTxThroughputSensor,
)
from custom_components.keenetic_router_pro.sensor.network import (
    KeeneticWanRxBytesSensor,
    KeeneticWanRxThroughputSensor,
    KeeneticWanTxBytesSensor,
    KeeneticWanTxThroughputSensor,
)
from custom_components.keenetic_router_pro.sensor.traffic import (
    KeeneticInterfaceRxSensor,
    KeeneticInterfaceTxSensor,
    KeeneticLanRxSensor,
    KeeneticLanTxSensor,
    KeeneticWanRxSensor,
    KeeneticWanTxSensor,
)

EDGE_VALUES = (None, "", "not-a-number")


WAN_FIELD_SENSORS = (
    (KeeneticWanRxBytesSensor, "rx_bytes"),
    (KeeneticWanTxBytesSensor, "tx_bytes"),
    (KeeneticWanRxThroughputSensor, "rx_throughput"),
    (KeeneticWanTxThroughputSensor, "tx_throughput"),
)


CRYPTO_MAP_FIELD_SENSORS = (
    (KeeneticCryptoMapRxBytesSensor, "rx_bytes"),
    (KeeneticCryptoMapTxBytesSensor, "tx_bytes"),
    (KeeneticCryptoMapRxThroughputSensor, "rx_throughput"),
    (KeeneticCryptoMapTxThroughputSensor, "tx_throughput"),
)


TRAFFIC_FIELD_SENSORS = (
    (KeeneticInterfaceRxSensor, "GigabitEthernet2", "Guest", "rxbytes"),
    (KeeneticInterfaceTxSensor, "GigabitEthernet2", "Guest", "txbytes"),
    (KeeneticLanRxSensor, "GigabitEthernet0", "LAN", "rxbytes"),
    (KeeneticLanTxSensor, "GigabitEthernet0", "LAN", "txbytes"),
    (KeeneticWanRxSensor, "GigabitEthernet1", "WAN", "rxbytes"),
    (KeeneticWanTxSensor, "GigabitEthernet1", "WAN", "txbytes"),
)


@pytest.mark.parametrize(("sensor_cls", "field"), WAN_FIELD_SENSORS)
@pytest.mark.parametrize("value", EDGE_VALUES)
def test_wan_field_sensor_edge_value_returns_none(
    keenetic_entry,
    keenetic_coordinator_factory,
    sensor_cls,
    field,
    value,
) -> None:
    coordinator = keenetic_coordinator_factory(
        {"wan_interfaces": [{"id": "PPPoE0", field: value}]}
    )
    sensor = sensor_cls(coordinator, keenetic_entry, "PPPoE0")

    assert sensor.native_value is None


@pytest.mark.parametrize(("sensor_cls", "field"), CRYPTO_MAP_FIELD_SENSORS)
@pytest.mark.parametrize("value", EDGE_VALUES)
def test_crypto_map_field_sensor_edge_value_returns_none(
    keenetic_entry,
    keenetic_coordinator_factory,
    sensor_cls,
    field,
    value,
) -> None:
    coordinator = keenetic_coordinator_factory(
        {"crypto_maps": {"OfficeVPN": {field: value}}}
    )
    sensor = sensor_cls(coordinator, keenetic_entry, "OfficeVPN")

    assert sensor.native_value is None


@pytest.mark.parametrize(("sensor_cls", "iface_name", "iface_label", "field"), TRAFFIC_FIELD_SENSORS)
@pytest.mark.parametrize("value", EDGE_VALUES)
def test_traffic_field_sensor_edge_value_returns_none_or_zero(
    keenetic_entry,
    keenetic_coordinator_factory,
    sensor_cls,
    iface_name,
    iface_label,
    field,
    value,
) -> None:
    coordinator = keenetic_coordinator_factory(
        {"interface_stats": {iface_name: {field: value}}}
    )
    if sensor_cls in (KeeneticInterfaceRxSensor, KeeneticInterfaceTxSensor):
        sensor = sensor_cls(coordinator, keenetic_entry, iface_name, iface_label)
    else:
        sensor = sensor_cls(coordinator, keenetic_entry)

    assert sensor.native_value in (None, 0.0)
