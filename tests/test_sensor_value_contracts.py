"""Value-level regression tests for sensor payload edge cases."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.keenetic_router_pro.sensor.client import (
    KeeneticClientConnectionTypeSensor,
    KeeneticClientRxSensor,
    KeeneticClientTxSensor,
    KeeneticClientWifiBandSensor,
    KeeneticClientWifiModeSensor,
)
from custom_components.keenetic_router_pro.sensor.mesh import (
    KeeneticMeshClientsSensor,
    KeeneticMeshCpuLoadSensor,
    KeeneticMeshMemorySensor,
    KeeneticMeshPortSensor,
    KeeneticMeshSystemStateSensor,
)
from custom_components.keenetic_router_pro.sensor.system import (
    KeeneticCpuLoadSensor,
    KeeneticMemoryUsageSensor,
)
from custom_components.keenetic_router_pro.sensor.traffic import (
    KeeneticInterfaceRxSensor,
)

MAC = "aa:bb:cc:dd:ee:ff"


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router")


def _coordinator(data: dict) -> SimpleNamespace:
    return SimpleNamespace(data=data)


def test_system_numeric_sensors_return_none_for_malformed_values() -> None:
    """Malformed router stats should produce unavailable sensors, not exceptions."""
    coordinator = _coordinator(
        {
            "system": {
                "cpu_load": "not-a-number",
                "memory": "bad/fraction",
                "memtotal": "also-bad",
                "memfree": "512",
                "memory_usage": "not-a-number",
            }
        }
    )
    entry = _entry()

    assert KeeneticCpuLoadSensor(coordinator, entry).native_value is None
    assert KeeneticMemoryUsageSensor(coordinator, entry).native_value is None


def test_mesh_numeric_sensors_return_defaults_for_malformed_values() -> None:
    """Extender diagnostics should share the same tolerant parsing contract."""
    coordinator = _coordinator(
        {
            "mesh_nodes": [
                {
                    "cid": "aa:bb:cc:dd:ee:ff",
                    "connected": True,
                    "associations": "not-a-number",
                    "cpuload": "bad",
                    "memory": "bad/fraction",
                    "port": ["bad-port", {"label": "0", "link": "up"}],
                },
                "bad-node",
            ]
        }
    )
    entry = _entry()

    assert KeeneticMeshSystemStateSensor(coordinator, entry).native_value == "ok"
    assert (
        KeeneticMeshClientsSensor(
            coordinator,
            entry,
            "aa:bb:cc:dd:ee:ff",
        ).native_value
        == 0
    )
    assert (
        KeeneticMeshCpuLoadSensor(
            coordinator,
            entry,
            "aa:bb:cc:dd:ee:ff",
        ).native_value
        is None
    )
    assert (
        KeeneticMeshMemorySensor(
            coordinator,
            entry,
            "aa:bb:cc:dd:ee:ff",
        ).native_value
        is None
    )
    assert (
        KeeneticMeshPortSensor(
            coordinator,
            entry,
            "aa:bb:cc:dd:ee:ff",
            "0",
        ).native_value
        == "up"
    )


def test_mesh_port_sensor_reports_down_and_missing_port_states() -> None:
    """Mesh port entities should not leak old link details across shape drift."""
    coordinator = _coordinator(
        {
            "mesh_nodes": [
                {
                    "cid": "aa:bb:cc:dd:ee:ff",
                    "port": [
                        {"label": "1", "link": "down", "appearance": "ethernet"},
                        "bad-port",
                    ],
                }
            ]
        }
    )
    entry = _entry()
    down_port = KeeneticMeshPortSensor(coordinator, entry, MAC, "1")
    missing_port = KeeneticMeshPortSensor(coordinator, entry, MAC, "2")

    assert (down_port.native_value, down_port.icon) == ("down", "mdi:ethernet-off")
    assert down_port.extra_state_attributes == {
        "label": "1",
        "appearance": "ethernet",
    }
    # A vanished port goes unavailable instead of publishing "not_found".
    assert (missing_port.native_value, missing_port.extra_state_attributes) == (
        None,
        None,
    )
    assert missing_port.available is False


def test_client_counter_sensors_distinguish_offline_zero_from_online_bad_values() -> None:
    """Offline zero counters are hidden; online malformed counters stay unavailable."""
    coordinator = _coordinator(
        {
            "clients_by_mac": {
                MAC: {
                    "mac": MAC,
                    "active": False,
                    "rxbytes": "0",
                    "txbytes": "bad",
                }
            }
        }
    )
    entry = _entry()
    rx_sensor = KeeneticClientRxSensor(coordinator, entry, MAC, "Phone")
    tx_sensor = KeeneticClientTxSensor(coordinator, entry, MAC, "Phone")

    assert (rx_sensor.available, rx_sensor.native_value) == (False, None)
    assert (tx_sensor.available, tx_sensor.native_value) == (True, None)

    coordinator.data["clients_by_mac"][MAC].update(
        {"active": True, "rxbytes": str(1024**3), "txbytes": str(2 * 1024**3)}
    )

    assert (rx_sensor.available, rx_sensor.native_value) == (True, 1.0)
    assert (tx_sensor.available, tx_sensor.native_value) == (True, 2.0)


def test_client_wifi_sensors_prefer_mws_band_and_mode() -> None:
    """MWS payloads are the authoritative Wi-Fi source for mesh clients."""
    coordinator = _coordinator(
        {
            "clients_by_mac": {
                MAC: {
                    "mac": MAC,
                    "active": True,
                    "mws": {
                        "ap": "WifiMaster1/AccessPoint0",
                        "mode": "11ax",
                        "ht": "80",
                        "security": "wpa3",
                        "authenticated": True,
                        "roam": "fast",
                    },
                }
            }
        }
    )
    entry = _entry()

    connection = KeeneticClientConnectionTypeSensor(coordinator, entry, MAC, "Phone")
    band = KeeneticClientWifiBandSensor(coordinator, entry, MAC, "Phone")
    mode = KeeneticClientWifiModeSensor(coordinator, entry, MAC, "Phone")

    assert (connection.native_value, connection.icon) == (
        "WiFi 5 GHz (Mesh)",
        "mdi:wifi-strength-4",
    )
    assert connection.extra_state_attributes == {
        "ap": "WifiMaster1/AccessPoint0",
        "mode": "11ax",
        "ht": "80",
        "security": "wpa3",
        "authenticated": True,
        "roaming": "fast",
    }
    assert (band.native_value, band.icon) == ("5 GHz", "mdi:wifi-strength-4")
    assert (mode.available, mode.native_value, mode.icon) == (
        True,
        "11AX",
        "mdi:wifi-strength-4",
    )


def test_traffic_sensor_positive_value_attrs_and_sanitized_unique_id() -> None:
    """Generic interface traffic sensors expose GiB values and stable IDs."""
    coordinator = _coordinator(
        {
            "interface_stats": {
                "Wireguard0/Office": {
                    "rxbytes": str(3 * 1024**3),
                    "interface_type": "Wireguard",
                    "link": "up",
                    "state": "up",
                    "rxpackets": "42",
                    "rxerrors": "1",
                    "rxdropped": "0",
                }
            }
        }
    )
    sensor = KeeneticInterfaceRxSensor(
        coordinator,
        _entry(),
        "Wireguard0/Office",
        "Office VPN",
    )

    assert sensor.unique_id == "entry_123_iface_wireguard0_office_rx"
    assert (sensor.name, sensor.native_value) == ("Office VPN RX", 3.0)
    assert sensor.extra_state_attributes == {
        "interface": "Wireguard0/Office",
        "type": "Wireguard",
        "link": "up",
        "state": "up",
        "rxpackets": "42",
        "rxerrors": "1",
        "rxdropped": "0",
    }
