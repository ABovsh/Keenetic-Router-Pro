"""Mesh fallback node entity behavior."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.keenetic_router_pro.sensor.mesh import (
    KeeneticMeshClientsSensor,
    KeeneticMeshCpuLoadSensor,
    KeeneticMeshFirmwareVersionSensor,
    KeeneticMeshLocalIpSensor,
    KeeneticMeshMemorySensor,
    KeeneticMeshPortSensor,
    KeeneticMeshSystemStateSensor,
    KeeneticMeshUptimeSensor,
)
from tests.fixtures.mesh_rci import FALLBACK_MESH_NODE


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router", data={})


def _coordinator(data: dict) -> SimpleNamespace:
    return SimpleNamespace(data=data)


def test_mesh_uptime_sensor_fallback_node_id_mac_has_stable_unique_id() -> None:
    """Fallback nodes use id=mac when cid is absent."""
    entry = _entry()
    coordinator = _coordinator({"mesh_nodes": [FALLBACK_MESH_NODE]})

    entity = KeeneticMeshUptimeSensor(
        coordinator,
        entry,
        "AA:BB:CC:00:00:01",
    )

    assert entity.unique_id == "entry_123_mesh_AA_BB_CC_00_00_01_uptime_v2"


def test_mesh_system_state_mixed_fallback_nodes_reports_problem_summary() -> None:
    """Mixed mesh connectivity reports a degraded system summary."""
    node = dict(FALLBACK_MESH_NODE, associations="2", model="Buddy")
    offline = dict(
        FALLBACK_MESH_NODE,
        id="AA:BB:CC:00:00:02",
        mac="AA:BB:CC:00:00:02",
        connected=False,
        name="Bedroom Extender",
    )
    sensor = KeeneticMeshSystemStateSensor(
        _coordinator({"mesh_nodes": [node, offline, "ignored"]}),
        _entry(),
    )

    assert sensor.extra_state_attributes == {
        "total_nodes": 2,
        "connected_nodes": 1,
        "disconnected_nodes": 1,
        "health_percent": 50.0,
        "state": "problem",
        "nodes": [
            {
                "name": "Kitchen Extender",
                "mac": "AA:BB:CC:00:00:01",
                "ip": "192.0.2.20",
                "model": "Buddy",
                "mode": "extender",
                "connected": True,
                "firmware": "4.2.0",
                "associations": "2",
            },
            {
                "name": "Bedroom Extender",
                "mac": "AA:BB:CC:00:00:02",
                "ip": "192.0.2.20",
                "model": None,
                "mode": "extender",
                "connected": False,
                "firmware": "4.2.0",
                "associations": 0,
            },
        ],
    }


def test_mesh_system_state_empty_nodes_reports_no_nodes() -> None:
    """Empty mesh data remains an explicit no-nodes state."""
    sensor = KeeneticMeshSystemStateSensor(_coordinator({"mesh_nodes": []}), _entry())

    assert (sensor.native_value, sensor.icon, sensor.extra_state_attributes) == (
        "no_nodes",
        "mdi:help-network",
        {
            "total_nodes": 0,
            "connected_nodes": 0,
            "disconnected_nodes": 0,
            "nodes": [],
        },
    )


def test_mesh_node_value_sensors_read_fallback_node_payload() -> None:
    """Per-node mesh value sensors read the MAC-keyed fallback node."""
    node = dict(
        FALLBACK_MESH_NODE,
        associations="3",
        cpuload="12.5",
        memory="60/100",
        model="Buddy 6",
        hw_id="KN-3411",
    )
    coordinator = _coordinator({"mesh_nodes": [node]})
    entry = _entry()
    node_id = "AA:BB:CC:00:00:01"

    values = {
        "uptime_unit": KeeneticMeshUptimeSensor(
            coordinator, entry, node_id
        ).native_unit_of_measurement,
        "uptime": KeeneticMeshUptimeSensor(coordinator, entry, node_id).native_value,
        "clients": KeeneticMeshClientsSensor(
            coordinator, entry, node_id
        ).native_value,
        "client_attrs": KeeneticMeshClientsSensor(
            coordinator, entry, node_id
        ).extra_state_attributes,
        "ip": KeeneticMeshLocalIpSensor(
            coordinator, entry, node_id, "192.0.2.99"
        ).native_value,
        "cpu": KeeneticMeshCpuLoadSensor(coordinator, entry, node_id).native_value,
        "memory": KeeneticMeshMemorySensor(coordinator, entry, node_id).native_value,
        "firmware": KeeneticMeshFirmwareVersionSensor(
            coordinator, entry, node_id
        ).native_value,
        "firmware_attrs": KeeneticMeshFirmwareVersionSensor(
            coordinator, entry, node_id
        ).extra_state_attributes,
    }

    assert values == {
        "uptime_unit": "seconds",
        "uptime": 120,
        "clients": 3,
        "client_attrs": {
            "cid": "AA:BB:CC:00:00:01",
            "mac": "AA:BB:CC:00:00:01",
            "ip": "192.0.2.20",
            "model": "Buddy 6",
            "mode": "extender",
        },
        "ip": "192.0.2.20",
        "cpu": 12.5,
        "memory": 60.0,
        "firmware": "4.2.0",
        "firmware_attrs": {
            "firmware_available": "4.3.0",
            "hardware_id": "KN-3411",
            "model": "Buddy 6",
        },
    }


def test_mesh_port_sensor_reports_link_details_for_fallback_node() -> None:
    """Mesh port sensors expose the selected port state and link details."""
    node = dict(
        FALLBACK_MESH_NODE,
        port=[
            "ignored",
            {
                "label": "1",
                "appearance": "ethernet",
                "link": "up",
                "speed": "1000",
                "duplex": "full",
            },
        ],
    )
    sensor = KeeneticMeshPortSensor(
        _coordinator({"mesh_nodes": [node]}),
        _entry(),
        "AA:BB:CC:00:00:01",
        "1",
    )

    assert (
        sensor.name,
        sensor.unique_id,
        sensor.native_value,
        sensor.icon,
        sensor.extra_state_attributes,
    ) == (
        "Port 1",
        "entry_123_mesh_AA_BB_CC_00_00_01_port_1_v2",
        "up",
        "mdi:ethernet",
        {
            "label": "1",
            "appearance": "ethernet",
            "speed": "1000",
            "duplex": "full",
        },
    )


def test_mesh_port_sensor_missing_node_reports_unknown() -> None:
    """Removed fallback mesh nodes do not leak stale port state."""
    sensor = KeeneticMeshPortSensor(
        _coordinator({"mesh_nodes": []}),
        _entry(),
        "AA:BB:CC:00:00:01",
        "1",
    )

    assert (sensor.native_value, sensor.extra_state_attributes) == ("unknown", None)
