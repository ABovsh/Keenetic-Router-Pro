"""Sensor statistics contracts and preservation of ordinary readings."""

from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
from types import SimpleNamespace

import pytest

from homeassistant.components.sensor import SensorStateClass

from custom_components.keenetic_router_pro.sensor.client import (
    KeeneticClientRssiSensor,
    KeeneticClientTxRateSensor,
)
from custom_components.keenetic_router_pro.sensor.clients import (
    KeeneticConnectedClientsSensor,
    KeeneticRouterClientsSensor,
)
from custom_components.keenetic_router_pro.sensor.mesh import KeeneticMeshClientsSensor
from custom_components.keenetic_router_pro.sensor.network import KeeneticWanLinkDowntimeSensor

ROOT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "keenetic_router_pro"


def test_only_downtime_families_opt_into_statistics() -> None:
    """Optional sensor creation must not bypass the reliability-only budget."""
    from homeassistant.components.sensor import SensorEntity

    keep = {"KeeneticWanDowntimeSensor", "KeeneticWanLinkDowntimeSensor"}
    offenders = []
    for path in (ROOT / "sensor").glob("*.py"):
        if path.stem == "__init__":
            continue
        module = importlib.import_module(
            f"custom_components.keenetic_router_pro.sensor.{path.stem}"
        )
        for name, cls in vars(module).items():
            if (name.startswith("_") or not inspect.isclass(cls)
                    or cls.__module__ != module.__name__
                    or not issubclass(cls, SensorEntity) or name in keep):
                continue
            sensor = object.__new__(cls)
            sensor.coordinator = SimpleNamespace(data={"wan_interfaces": []})
            sensor._wg_name = "Wireguard0"
            state_class = getattr(sensor, "state_class", getattr(sensor, "_attr_state_class", None))
            if state_class is not None:
                offenders.append(name)
    assert not offenders, f"Unexpected statistics streams: {sorted(offenders)}"


@pytest.mark.parametrize("wan_type", ["Ethernet", "PPPoE", "WireGuard", "OpenVPN"])
def test_only_provider_downtime_keeps_statistics(wan_type: str) -> None:
    coordinator = SimpleNamespace(data={"wan_interfaces": [{"id": "ISP", "type": wan_type}]})
    entry = SimpleNamespace(entry_id="entry", title="Router")
    sensor = KeeneticWanLinkDowntimeSensor(coordinator, entry, "ISP")
    expected = None if wan_type in ("WireGuard", "OpenVPN") else SensorStateClass.TOTAL_INCREASING
    assert sensor._attr_state_class == expected
    assert sensor.unique_id == "entry_wan_ISP_downtime"
    assert sensor.native_value == 0 and sensor.available


@pytest.mark.parametrize(
    "sensor_cls,field,initial,changed,suffix",
    [
        (KeeneticClientRssiSensor, "rssi", -60, -66, "rssi"),
        (KeeneticClientTxRateSensor, "txrate", 84, 96, "txrate"),
    ],
)
def test_client_link_diagnostics_keep_readings_without_statistics(
    sensor_cls, field: str, initial: int, changed: int, suffix: str,
) -> None:
    mac = "aa:bb:cc:dd:ee:ff"
    client = {"mac": mac, "link": "up", field: initial}
    coordinator = SimpleNamespace(data={"clients_by_mac": {mac: client}})
    entry = SimpleNamespace(entry_id="entry", title="Router")
    sensor = sensor_cls(coordinator, entry, mac, "Phone")

    assert sensor._attr_state_class is None
    assert sensor.unique_id == f"entry_client_{mac}_{suffix}"
    assert sensor.available and sensor.native_value == initial
    client[field] = changed
    assert sensor.native_value == changed
    client["link"] = "down"
    assert not sensor.available


def test_router_and_aggregate_counts_keep_normal_history() -> None:
    coordinator = SimpleNamespace(data={
        "client_stats": {"connected": 8},
        "mesh_associations": {"total": 3},
    })
    entry = SimpleNamespace(entry_id="entry", title="Router")
    router = KeeneticRouterClientsSensor(coordinator, entry)
    aggregate = KeeneticConnectedClientsSensor(coordinator, entry)

    assert router._attr_state_class is None
    assert aggregate._attr_state_class is None
    assert router.unique_id == "entry_router_clients_v2"
    assert aggregate.unique_id == "entry_connected_clients_v2"
    assert (router.native_value, aggregate.native_value) == (5, 8)
    coordinator.data["client_stats"]["connected"] = 10
    assert (router.native_value, aggregate.native_value) == (7, 10)


def test_mesh_count_keeps_readings_and_source_availability_without_statistics() -> None:
    node = {"cid": "node_1", "associations": 3}
    coordinator = SimpleNamespace(data={"mesh_nodes": [node]})
    entry = SimpleNamespace(entry_id="entry", title="Router")
    sensor = KeeneticMeshClientsSensor(coordinator, entry, "node_1")

    assert sensor._attr_state_class is None
    assert sensor.unique_id == "entry_mesh_node_1_clients_v2"
    assert sensor.available and sensor.native_value == 3
    node["associations"] = 0
    assert sensor.available and sensor.native_value == 0
    coordinator.data["mesh_nodes_fresh"] = False
    assert not sensor.available


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


def test_active_connections_sensor_keeps_normal_history() -> None:
    """Active connections is an instantaneous gauge, not a lifetime total.

    Using TOTAL caused HA statistics to treat it as a monotonic sum,
    producing nonsense long-term graphs.
    """
    assignments = _class_assignments(
        ROOT / "sensor/network.py", "KeeneticActiveConnectionsSensor"
    )
    assert assignments.get("_attr_state_class") == "None"


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
