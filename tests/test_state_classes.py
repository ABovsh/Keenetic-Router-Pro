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
    ("relative_path", "class_name"),
    [
        ("sensor/system.py", "KeeneticUptimeSensor"),
        ("sensor/network.py", "KeeneticPppoeUptimeSensor"),
        ("sensor/wireguard.py", "KeeneticWgUptimeSensor"),
        ("sensor/mesh.py", "KeeneticMeshUptimeSensor"),
        ("sensor/client.py", "KeeneticClientUptimeSensor"),
    ],
)
def test_uptime_sensors_use_total_increasing(
    relative_path: str,
    class_name: str,
) -> None:
    assignments = _class_assignments(ROOT / relative_path, class_name)

    assert (
        assignments.get("_attr_state_class") == "SensorStateClass.TOTAL_INCREASING"
    ), f"{class_name} must use TOTAL_INCREASING for monotonic uptime"


@pytest.mark.parametrize(
    "class_name",
    ["KeeneticClientFirstSeenSensor", "KeeneticClientLastSeenSensor"],
)
def test_client_seen_sensors_are_timestamps_not_duration_measurements(
    class_name: str,
) -> None:
    assignments = _class_assignments(
        ROOT / "sensor/client.py",
        class_name,
    )

    assert assignments.get("_attr_device_class") == "SensorDeviceClass.TIMESTAMP"
    assert "_attr_state_class" not in assignments
