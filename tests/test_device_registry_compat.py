"""Compatibility coverage for Home Assistant's device-registry API change."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

from custom_components.keenetic_router_pro import _parent_device_id, _router_device_id
from custom_components.keenetic_router_pro.const import DOMAIN
from custom_components.keenetic_router_pro.entity import WanEntity
from custom_components.keenetic_router_pro.utils import (
    get_client_device_info,
    get_crypto_map_device_info,
    get_mesh_device_info,
    get_vpn_interface_device_info,
    get_wan_device_info,
)


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123")


def test_new_registry_uses_scoped_identifier_and_creates_parent(
    monkeypatch,
) -> None:
    """New HA links child devices by id, including on a first install."""
    calls: list[tuple[object, tuple[str, str], str]] = []
    devices: dict[tuple[str, str], str] = {}

    def lookup(hass, identifier, *, config_entry_id):
        calls.append((hass, identifier, config_entry_id))
        if identifier not in devices:
            raise ValueError("device not found")
        return devices[identifier]

    class Registry:
        def async_get_or_create(self, *, config_entry_id, identifiers):
            assert config_entry_id == "entry_123"
            devices[next(iter(identifiers))] = "router-device-id"
            return SimpleNamespace(id="router-device-id")

    registry = types.ModuleType("homeassistant.helpers.device_registry")
    registry.async_get_device_id_by_identifier = lookup
    registry.async_get = lambda hass: Registry()
    monkeypatch.setitem(sys.modules, registry.__name__, registry)

    hass = object()
    assert _parent_device_id(hass, _entry()) == "router-device-id"
    assert calls == [(hass, (DOMAIN, "entry_123"), "entry_123")]
    assert _router_device_id(hass, _entry()) == "router-device-id"


def test_old_registry_keeps_identifier_parent_link(monkeypatch) -> None:
    """HA 2024.5 lacks the scoped lookup and must not use the new field."""
    registry = types.ModuleType("homeassistant.helpers.device_registry")
    registry.async_get = lambda hass: SimpleNamespace(
        async_get_device=lambda *, identifiers: SimpleNamespace(id="legacy-router-id")
    )
    monkeypatch.setitem(sys.modules, registry.__name__, registry)

    assert _parent_device_id(object(), _entry()) is None
    assert _router_device_id(object(), _entry()) == "legacy-router-id"

    device_info = get_wan_device_info("Router", "entry_123", "WAN0")
    assert device_info["via_device"] == (DOMAIN, "entry_123")
    assert "via_device_id" not in device_info


def test_subdevice_builders_and_entities_use_resolved_parent_id() -> None:
    """All child types preserve identifiers while receiving the modern link."""
    parent_device_id = "router-device-id"
    builders = [
        get_mesh_device_info(
            "Router", "entry_123", {"name": "Node"}, "node", parent_device_id=parent_device_id
        ),
        get_wan_device_info("Router", "entry_123", "WAN0", parent_device_id=parent_device_id),
        get_vpn_interface_device_info(
            "Router", "entry_123", "WG0", parent_device_id=parent_device_id
        ),
        get_crypto_map_device_info(
            "Router", "entry_123", "Office", parent_device_id=parent_device_id
        ),
        get_client_device_info(
            "entry_123", "Router", "aa:bb:cc:dd:ee:ff", "Phone", parent_device_id=parent_device_id
        ),
    ]
    for device_info in builders:
        assert device_info["via_device_id"] == parent_device_id
        assert "via_device" not in device_info

    coordinator = SimpleNamespace(
        parent_device_id=parent_device_id,
        data={"wan_interfaces": [{"id": "WAN0"}], "wan_by_id": {"WAN0": {"id": "WAN0"}}},
        client=SimpleNamespace(),
    )
    entity = WanEntity(coordinator, "entry_123", "Router", "WAN0")
    assert entity.device_info["via_device_id"] == parent_device_id
    assert entity.device_info["identifiers"] == {(DOMAIN, "entry_123_wan_WAN0")}
