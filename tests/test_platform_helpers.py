"""Tests for platform helper functions and high-value entity contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.keenetic_router_pro.binary_sensor import (
    KeeneticCryptoMapConnectedSensor,
    KeeneticWanConnectedSensor,
    _add_mesh_binary_sensors,
)
from custom_components.keenetic_router_pro.button import (
    KeeneticMeshRebootButton,
    _add_mesh_reboot_buttons,
)
from custom_components.keenetic_router_pro.entity_setup import DynamicEntityTracker
from custom_components.keenetic_router_pro.update import (
    KeeneticFirmwareUpdate,
    KeeneticMeshFirmwareUpdate,
    _add_mesh_update_entities,
    _reported_latest_version,
)


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router", data={})


def _coordinator(data: dict) -> SimpleNamespace:
    async def async_request_refresh() -> None:
        return None

    return SimpleNamespace(data=data, async_request_refresh=async_request_refresh)


def test_dynamic_mesh_helpers_add_each_entity_once() -> None:
    """Button, binary-sensor and update mesh helpers share the no-duplicate contract."""
    entry = _entry()
    client = SimpleNamespace()
    coordinator = _coordinator(
        {
            "mesh_nodes": [
                {"cid": "aa:bb:cc:dd:ee:ff", "ip": "192.0.2.10"},
                {"id": "11:22:33:44:55:66", "ip": "192.0.2.11"},
                {"name": "missing id"},
            ]
        }
    )

    binary_entities = []
    button_entities = []
    update_entities = []
    known_binary: set[str] = set()
    known_buttons: set[str] = set()
    known_updates: set[str] = set()

    _add_mesh_binary_sensors(binary_entities, coordinator, entry, known_binary)
    _add_mesh_reboot_buttons(button_entities, coordinator, entry, client, known_buttons)
    _add_mesh_update_entities(update_entities, coordinator, entry, client, known_updates)

    assert len(binary_entities) == 4
    assert len(button_entities) == 2
    assert len(update_entities) == 2
    assert len({entity.unique_id for entity in binary_entities}) == 4
    assert len({entity.unique_id for entity in button_entities}) == 2
    assert len({entity.unique_id for entity in update_entities}) == 2

    _add_mesh_binary_sensors(binary_entities, coordinator, entry, known_binary)
    _add_mesh_reboot_buttons(button_entities, coordinator, entry, client, known_buttons)
    _add_mesh_update_entities(update_entities, coordinator, entry, client, known_updates)

    assert len(binary_entities) == 4
    assert len(button_entities) == 2
    assert len(update_entities) == 2


def test_dynamic_entity_tracker_reports_new_ids_once() -> None:
    """Shared dynamic setup tracker should mark every id only once."""
    tracker = DynamicEntityTracker()

    assert tracker.mark_mesh_node("node-1") is True
    assert tracker.mark_mesh_node("node-1") is False
    assert tracker.mark_mesh_local_ip("node-1") is True
    assert tracker.mark_mesh_local_ip("node-1") is False
    assert tracker.mark_mesh_port("node-1", "0") is True
    assert tracker.mark_mesh_port("node-1", "0") is False
    assert tracker.mark_wan("PPPoE0") is True
    assert tracker.mark_wan("PPPoE0") is False
    assert tracker.mark_vpn("Wireguard0") is True
    assert tracker.mark_vpn("Wireguard0") is False
    assert tracker.mark_crypto_map("SITE") is True
    assert tracker.mark_crypto_map("SITE") is False


def test_wan_connected_sensor_exposes_pending_as_unavailable() -> None:
    """Unknown ping-check state should not fire false outage automations."""
    entry = _entry()
    coordinator = _coordinator(
        {
            "wan_interfaces": [
                {
                    "id": "PPPoE0",
                    "internet_access": None,
                    "link_state": "up",
                    "description": "ISP",
                }
            ]
        }
    )

    sensor = KeeneticWanConnectedSensor(coordinator, entry, "PPPoE0")

    assert sensor.available is False
    assert sensor.is_on is False
    assert sensor.icon == "mdi:web-remove"

    coordinator.data["wan_interfaces"][0]["internet_access"] = False
    coordinator.data["wan_interfaces"][0]["ping_check"] = {
        "passing": False,
        "check_hosts": ["captive.keenetic.net"],
        "fail_count": 3,
        "max_fails": 3,
        "status": "fail",
    }

    assert sensor.available is True
    assert sensor.extra_state_attributes["failure_reason"].startswith(
        "ping check failing"
    )


def test_crypto_map_connected_sensor_unavailable_when_map_disappears() -> None:
    """Deleted IPsec crypto-map rows should not leave stale connected sensors."""
    entry = _entry()
    coordinator = _coordinator(
        {
            "crypto_maps": {
                "OfficeVPN": {
                    "connected": True,
                    "state": "PHASE2_ESTABLISHED",
                }
            }
        }
    )
    sensor = KeeneticCryptoMapConnectedSensor(coordinator, entry, "OfficeVPN")

    assert sensor.available is True
    assert sensor.is_on is True

    coordinator.data["crypto_maps"] = {}

    assert sensor.available is False
    assert sensor.is_on is False


def test_reported_latest_version_handles_channel_downgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """HA should still show a channel-switch update when version number decreases."""

    class FakeAwesomeVersion:
        def __init__(self, value: str) -> None:
            self.value = value

        def __lt__(self, other: "FakeAwesomeVersion") -> bool:
            return self.value < other.value

    monkeypatch.setitem(
        __import__("sys").modules,
        "awesomeversion",
        SimpleNamespace(AwesomeVersion=FakeAwesomeVersion),
    )

    assert (
        _reported_latest_version("4.2.0", "4.3.0")
        == "4.2.0 (channel switch)"
    )
    assert _reported_latest_version("4.4.0", "4.3.0") == "4.4.0"
    assert _reported_latest_version(None, "4.3.0") == "4.3.0"


def test_controller_firmware_update_versions_and_notes() -> None:
    """Controller update entity reports installed/latest versions from coordinator data."""
    entry = _entry()
    client = SimpleNamespace()
    coordinator = _coordinator(
        {
            "system": {
                "title": "4.2.0",
                "release-available": "4.3.0",
                "fw-update-available": True,
                "fw-update-sandbox": "stable",
                "model": "Hero",
            }
        }
    )

    entity = KeeneticFirmwareUpdate(coordinator, entry, client)

    assert entity.unique_id == "entry_123_firmware_update"
    assert entity.installed_version == "4.2.0"
    assert entity.latest_version == "4.3.0"
    assert "4.3.0" in asyncio.run(entity.async_release_notes())

    coordinator.data["system"]["fw-update-available"] = False

    assert entity.latest_version == "4.2.0"


def test_mesh_reboot_button_uses_node_cid() -> None:
    """Mesh reboot button must address the stable node id used in its unique id."""
    entry = _entry()
    calls = []
    client = SimpleNamespace(async_reboot_mesh_node=lambda cid: calls.append(cid))
    coordinator = _coordinator(
        {
            "mesh_nodes": [
                {
                    "cid": "aa:bb:cc:dd:ee:ff",
                    "name": "Kitchen Extender",
                }
            ]
        }
    )

    async def reboot(cid: str) -> None:
        calls.append(cid)

    client.async_reboot_mesh_node = reboot
    button = KeeneticMeshRebootButton(
        coordinator,
        entry,
        client,
        "aa:bb:cc:dd:ee:ff",
    )

    assert button.name == "Reboot Kitchen Extender"

    asyncio.run(button.async_press())

    assert calls == ["aa:bb:cc:dd:ee:ff"]


def test_mesh_firmware_update_requires_node_ip() -> None:
    """Mesh update install should fail clearly when the node is missing an IP."""
    entry = _entry()
    client = SimpleNamespace()
    coordinator = _coordinator(
        {
            "mesh_nodes": [
                {
                    "cid": "aa:bb:cc:dd:ee:ff",
                    "name": "Kitchen Extender",
                    "firmware": "4.2.0",
                    "firmware_available": "4.3.0",
                }
            ]
        }
    )
    entity = KeeneticMeshFirmwareUpdate(
        coordinator,
        entry,
        "aa:bb:cc:dd:ee:ff",
        client,
    )

    assert entity.installed_version == "4.2.0"
    assert entity.latest_version == "4.3.0"
    assert "Kitchen Extender" in asyncio.run(entity.async_release_notes())

    with pytest.raises(HomeAssistantError, match="node IP address not available"):
        asyncio.run(entity.async_install(None, False))
