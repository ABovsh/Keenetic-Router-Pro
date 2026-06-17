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


def test_client_session_uptime_uses_measurement() -> None:
    """A per-client Wi-Fi session is an instantaneous gauge, not a lifetime total.

    Unlike infrastructure uptimes (router/PPPoE/WireGuard/mesh), which reset
    cleanly to ~0 only on a reboot, a client's reported session length resets
    to a non-zero value on every roam/reconnect. TOTAL_INCREASING then logs
    "state is not strictly increasing" recorder warnings and produces nonsense
    monotonic sums — the same problem KeeneticActiveConnectionsSensor avoids by
    using MEASUREMENT.
    """
    assignments = _class_assignments(
        ROOT / "sensor/client.py", "KeeneticClientUptimeSensor"
    )
    assert assignments.get("_attr_state_class") == "SensorStateClass.MEASUREMENT"


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
def test_client_last_seen_sensor_is_exact_datetime_text(
    class_name: str,
) -> None:
    assignments = _class_assignments(
        ROOT / "sensor/client.py",
        class_name,
    )

    assert assignments.get("_attr_device_class") == "None"
    assert "_attr_state_class" not in assignments
