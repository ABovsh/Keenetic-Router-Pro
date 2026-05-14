"""Value-level regression tests for sensor payload edge cases."""

from __future__ import annotations

from types import SimpleNamespace

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
