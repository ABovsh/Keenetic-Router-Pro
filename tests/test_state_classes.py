"""Regression guards for monotonic uptime state classes."""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "keenetic_router_pro"


def _class_assignments(path: pathlib.Path, class_name: str) -> dict[str, str]:
    tree = ast.parse(path.read_text())
    cls = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    assignments: dict[str, str] = {}
    for node in cls.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                assignments[target.id] = ast.unparse(node.value)
    return assignments


@pytest.mark.parametrize(
    "class_path",
    [
        "sensor.system.KeeneticUptimeSensor",
        "sensor.network.KeeneticPppoeUptimeSensor",
        "sensor.network.KeeneticWanUptimeSensor",
        "sensor.wireguard.KeeneticWgUptimeSensor",
        "sensor.mesh.KeeneticMeshUptimeSensor",
    ],
)
def test_uptime_sensors_are_start_times_without_statistics(class_path: str) -> None:
    """Uptime long-term statistics answer nothing a history graph does not.

    The sensors publish when the session started, set once per session, so
    they cost one row per reboot or reconnect.
    """
    import importlib

    from homeassistant.components.sensor import SensorDeviceClass

    module_name, class_name = class_path.rsplit(".", 1)
    cls = getattr(
        importlib.import_module(f"custom_components.keenetic_router_pro.{module_name}"),
        class_name,
    )
    assert getattr(cls, "_attr_state_class", None) is None
    assert cls._attr_device_class == SensorDeviceClass.TIMESTAMP


def test_client_session_uptime_is_a_timestamp() -> None:
    """A per-client Wi-Fi session publishes its start, not an elapsed counter.

    An elapsed-seconds state is a clock: it advances by the poll interval
    forever, so the recorder stores a row on every tick. The session start is
    the same information in HA's native form — constant for the whole session,
    so one row per connection. A timestamp sensor must carry no state_class.
    """
    assignments = _class_assignments(
        ROOT / "sensor/client.py", "KeeneticClientUptimeSensor"
    )
    assert assignments.get("_attr_device_class") == "SensorDeviceClass.TIMESTAMP"
    assert "_attr_state_class" not in assignments


def test_active_connections_sensor_uses_measurement() -> None:
    """Active connections is an instantaneous gauge, not a lifetime total.

    Using TOTAL caused HA statistics to treat it as a monotonic sum,
    producing nonsense long-term graphs.
    """
    assignments = _class_assignments(
        ROOT / "sensor/network.py", "KeeneticActiveConnectionsSensor"
    )
    assert assignments.get("_attr_state_class") == "SensorStateClass.MEASUREMENT"


@pytest.mark.parametrize(
    "class_name",
    ["KeeneticClientLastSeenSensor"],
)
def test_client_last_seen_sensor_is_a_timestamp(
    class_name: str,
) -> None:
    assignments = _class_assignments(
        ROOT / "sensor/client.py",
        class_name,
    )

    assert assignments.get("_attr_device_class") == "SensorDeviceClass.TIMESTAMP"
    assert "_attr_state_class" not in assignments
