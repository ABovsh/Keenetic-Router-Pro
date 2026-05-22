"""Per-WAN role sensor behavior."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.keenetic_router_pro.sensor.network import KeeneticWanRoleSensor
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


def test_wan_role_sensor_primary_connection_uses_default_label_and_priority() -> None:
    sensor = KeeneticWanRoleSensor(_coordinator(), _entry(), "PPPoE0")

    assert sensor.native_value == "Default connection"
    assert sensor.extra_state_attributes == {
        "priority": 100,
        "role_index": 0,
        "defaultgw": True,
    }


def test_wan_role_sensor_backup_connection_uses_ordered_backup_label() -> None:
    sensor = KeeneticWanRoleSensor(_coordinator(), _entry(), "Wireguard0")

    assert sensor.native_value == "Backup connection 1"


def test_wan_role_sensor_unused_connection_exposes_missing_priority() -> None:
    sensor = KeeneticWanRoleSensor(_coordinator(), _entry(), "UsbModem0")

    assert sensor.native_value == "Backup connection 2"
    assert sensor.extra_state_attributes["priority"] is None

