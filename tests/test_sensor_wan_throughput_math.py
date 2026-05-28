"""Per-WAN throughput sensor behavior."""

from __future__ import annotations

import pytest

from conftest import TEST_HOST

from types import SimpleNamespace

from custom_components.keenetic_router_pro.coordinator import _counter_rate_bytes_per_second
from custom_components.keenetic_router_pro.const import WAN_STATUS_CONNECTED, WAN_STATUS_LINK_UP
from custom_components.keenetic_router_pro.sensor.network import (
    KeeneticActiveConnectionsSensor,
    KeeneticLocalIpSensor,
    KeeneticMainPortSensor,
    KeeneticPppoeUptimeSensor,
    KeeneticWanInterfaceSensor,
    KeeneticWanIpSensor,
    KeeneticWanProviderSensor,
    KeeneticWanPublicIpSensor,
    KeeneticWanRoleSensor,
    KeeneticWanRxBytesSensor,
    KeeneticWanRxThroughputSensor,
    KeeneticWanStatusSensor,
    KeeneticWanTxBytesSensor,
    KeeneticWanTxThroughputSensor,
    KeeneticWanUptimeSensor,
)
from tests.fixtures.sensor_wan import WAN_INTERFACES_WITH_ROLES


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router", data={})


def _coordinator() -> SimpleNamespace:
    return SimpleNamespace(
        data={
            "wan_interfaces": WAN_INTERFACES_WITH_ROLES,
            "wan_by_id": {wan["id"]: wan for wan in WAN_INTERFACES_WITH_ROLES},
        }
    )


def test_counter_rate_bytes_per_second_clamps_counter_reset_to_zero() -> None:
    assert _counter_rate_bytes_per_second(100, 500, 10.0) == pytest.approx(0.0)


def test_counter_rate_bytes_per_second_uses_sample_window() -> None:
    assert _counter_rate_bytes_per_second(1100, 100, 4.0) == pytest.approx(250.0)


def test_wan_throughput_sensor_converts_byte_rate_to_bit_rate() -> None:
    coordinator = _coordinator()
    entry = _entry()

    assert KeeneticWanRxThroughputSensor(coordinator, entry, "PPPoE0").native_value == pytest.approx(1000.0)
    assert KeeneticWanTxThroughputSensor(coordinator, entry, "PPPoE0").native_value == pytest.approx(500.0)


def test_wan_throughput_sensor_exposes_counter_sample_attributes() -> None:
    sensor = KeeneticWanRxThroughputSensor(_coordinator(), _entry(), "PPPoE0")

    assert sensor.extra_state_attributes == {
        "rxbytes": "1000",
        "txbytes": "2000",
        "rxspeed": "1000",
        "txspeed": "500",
        "stats_interface": "PPPoE0",
        "stats_timestamp": 10.0,
    }


def test_wan_public_ip_sensor_exposes_cgnat_global_flag() -> None:
    sensor = KeeneticWanPublicIpSensor(_coordinator(), _entry(), "Wireguard0")

    assert sensor.native_value == "100.64.10.2"
    assert sensor.extra_state_attributes["global"] is True


def test_wan_identity_and_counter_sensors_read_current_wan_data() -> None:
    coordinator = _coordinator()
    entry = _entry()

    assert KeeneticWanProviderSensor(coordinator, entry, "PPPoE0").unique_id == "entry_123_wan_PPPoE0_provider"
    assert KeeneticWanProviderSensor(coordinator, entry, "PPPoE0").name == "Provider"
    assert KeeneticWanProviderSensor(coordinator, entry, "PPPoE0").native_value == "Fiber ISP"
    assert KeeneticWanInterfaceSensor(coordinator, entry, "PPPoE0").unique_id == "entry_123_wan_PPPoE0_interface"
    assert KeeneticWanInterfaceSensor(coordinator, entry, "PPPoE0").name == "Interface"
    assert KeeneticWanInterfaceSensor(coordinator, entry, "PPPoE0").native_value == "GigabitEthernet1"
    assert KeeneticWanUptimeSensor(coordinator, entry, "PPPoE0").unique_id == "entry_123_wan_PPPoE0_uptime"
    assert KeeneticWanUptimeSensor(coordinator, entry, "PPPoE0").name == "Uptime"
    assert KeeneticWanUptimeSensor(coordinator, entry, "PPPoE0").native_value == 120
    assert KeeneticWanRxBytesSensor(coordinator, entry, "PPPoE0").unique_id == "entry_123_wan_PPPoE0_rx_bytes"
    assert KeeneticWanRxBytesSensor(coordinator, entry, "PPPoE0").name == "RX Bytes"
    assert KeeneticWanRxBytesSensor(coordinator, entry, "PPPoE0").native_value == 1000
    assert KeeneticWanTxBytesSensor(coordinator, entry, "PPPoE0").unique_id == "entry_123_wan_PPPoE0_tx_bytes"
    assert KeeneticWanTxBytesSensor(coordinator, entry, "PPPoE0").name == "TX Bytes"
    assert KeeneticWanTxBytesSensor(coordinator, entry, "PPPoE0").native_value == 2000
    assert KeeneticWanRxThroughputSensor(coordinator, entry, "PPPoE0").unique_id == "entry_123_wan_PPPoE0_rx_throughput"
    assert KeeneticWanRxThroughputSensor(coordinator, entry, "PPPoE0").name == "RX Throughput"
    assert KeeneticWanTxThroughputSensor(coordinator, entry, "PPPoE0").unique_id == "entry_123_wan_PPPoE0_tx_throughput"
    assert KeeneticWanTxThroughputSensor(coordinator, entry, "PPPoE0").name == "TX Throughput"


def test_wan_status_sensor_exposes_connected_metadata_and_icons() -> None:
    data = {
        "wan_status": {
            "status": WAN_STATUS_CONNECTED,
            "interface": "PPPoE0",
            "type": "pppoe",
            "ip": "198.51.100.20",
            "gateway": "10.0.0.1",
            "link": "up",
        }
    }
    sensor = KeeneticWanStatusSensor(SimpleNamespace(data=data), _entry())

    assert sensor.unique_id == "entry_123_wan_status"
    assert sensor.native_value == WAN_STATUS_CONNECTED
    assert sensor.icon == "mdi:web-check"
    assert sensor.extra_state_attributes == {
        "interface": "PPPoE0",
        "type": "pppoe",
        "ip": "198.51.100.20",
        "gateway": "10.0.0.1",
        "link": "up",
    }

    data["wan_status"] = {"status": WAN_STATUS_LINK_UP}
    assert sensor.icon == "mdi:web-remove"

    data["wan_status"] = {}
    assert sensor.native_value == "down"
    assert sensor.icon == "mdi:web-off"
    assert sensor.extra_state_attributes is None


def test_wan_ip_and_pppoe_uptime_sensors_read_wan_status() -> None:
    coordinator = SimpleNamespace(
        data={
            "wan_status": {
                "status": WAN_STATUS_CONNECTED,
                "interface": "PPPoE0",
                "type": "pppoe",
                "ip": "198.51.100.20",
                "gateway": "10.0.0.1",
                "uptime": "3600",
            }
        }
    )
    entry = _entry()
    ip_sensor = KeeneticWanIpSensor(coordinator, entry)
    uptime_sensor = KeeneticPppoeUptimeSensor(coordinator, entry)

    assert ip_sensor.unique_id == "entry_123_wan_ip"
    assert ip_sensor.name == "WAN IP"
    assert ip_sensor.native_value == "198.51.100.20"
    assert ip_sensor.extra_state_attributes == {
        "interface": "PPPoE0",
        "gateway": "10.0.0.1",
        "status": WAN_STATUS_CONNECTED,
    }
    assert uptime_sensor.unique_id == "entry_123_pppoe_uptime"
    assert uptime_sensor.native_unit_of_measurement == "seconds"
    assert uptime_sensor.native_value == 3600
    assert uptime_sensor.extra_state_attributes == {
        "interface": "PPPoE0",
        "type": "pppoe",
        "status": WAN_STATUS_CONNECTED,
        "ip": "198.51.100.20",
    }


def test_active_connections_sensor_clamps_bad_and_negative_values() -> None:
    coordinator = SimpleNamespace(data={"system": {"conntotal": "100", "connfree": "25"}})
    sensor = KeeneticActiveConnectionsSensor(coordinator, _entry())

    assert sensor.unique_id == "entry_123_active_connections"
    assert sensor.native_value == 75
    assert sensor.extra_state_attributes == {
        "total_capacity": 100,
        "free": 25,
        "used_percent": 75.0,
    }

    coordinator.data["system"] = {"conntotal": "10", "connfree": "20"}
    assert sensor.native_value == 0

    coordinator.data["system"] = {"conntotal": "bad", "connfree": None}
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {
        "total_capacity": 0,
        "free": 0,
        "used_percent": 0,
    }


def test_local_ip_and_main_port_sensors_expose_current_port_state() -> None:
    entry = _entry()
    coordinator = SimpleNamespace(
        data={
            "port_info": [
                "bad",
                {"label": "1", "appearance": "Port", "link": "up", "speed": "1000", "duplex": "full"},
                {"label": "2", "appearance": "Port", "link": "down"},
            ]
        }
    )

    local_ip = KeeneticLocalIpSensor(coordinator, entry, TEST_HOST)
    up_port = KeeneticMainPortSensor(coordinator, entry, "1")
    down_port = KeeneticMainPortSensor(coordinator, entry, "2")
    missing_port = KeeneticMainPortSensor(coordinator, entry, "9")

    assert local_ip.unique_id == "entry_123_local_ip"
    assert local_ip.native_value == TEST_HOST
    assert up_port.name == "Port 1"
    assert up_port.unique_id == "entry_123_port_1"
    assert up_port.native_value == "up"
    assert up_port.icon == "mdi:ethernet"
    assert up_port.extra_state_attributes == {
        "label": "1",
        "appearance": "Port",
        "speed": "1000",
        "duplex": "full",
    }
    assert down_port.native_value == "down"
    assert down_port.icon == "mdi:ethernet-off"
    assert down_port.extra_state_attributes == {"label": "2", "appearance": "Port"}
    assert missing_port.native_value == "not_found"
    assert missing_port.extra_state_attributes is None


def test_wan_sensors_return_none_when_wan_disappears_or_value_is_invalid() -> None:
    coordinator = SimpleNamespace(data={"wan_interfaces": [], "wan_by_id": {}})
    entry = _entry()

    assert KeeneticWanProviderSensor(coordinator, entry, "missing").native_value is None
    assert KeeneticWanRoleSensor(coordinator, entry, "missing").native_value is None
    assert KeeneticWanRoleSensor(coordinator, entry, "missing").extra_state_attributes is None
    assert KeeneticWanInterfaceSensor(coordinator, entry, "missing").native_value is None
    assert KeeneticWanInterfaceSensor(coordinator, entry, "missing").extra_state_attributes is None
    assert KeeneticWanPublicIpSensor(coordinator, entry, "missing").native_value is None
    assert KeeneticWanPublicIpSensor(coordinator, entry, "missing").extra_state_attributes is None
    assert KeeneticWanUptimeSensor(coordinator, entry, "missing").native_value is None
    assert KeeneticWanRxBytesSensor(coordinator, entry, "missing").native_value is None
    assert KeeneticWanRxThroughputSensor(coordinator, entry, "missing").native_value is None
    assert KeeneticWanRxThroughputSensor(coordinator, entry, "missing").extra_state_attributes is None

    coordinator.data = {
        "wan_by_id": {
            "bad": {
                "id": "bad",
                "rx_bytes": object(),
                "tx_bytes": None,
                "rx_throughput": object(),
                "tx_throughput": None,
            }
        }
    }
    assert KeeneticWanRxBytesSensor(coordinator, entry, "bad").native_value is None
    assert KeeneticWanTxBytesSensor(coordinator, entry, "bad").native_value is None
    assert KeeneticWanRxThroughputSensor(coordinator, entry, "bad").native_value is None
    assert KeeneticWanTxThroughputSensor(coordinator, entry, "bad").native_value is None
