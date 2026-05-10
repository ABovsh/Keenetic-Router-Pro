"""Mesh entity registry migration tests."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

from custom_components.keenetic_router_pro import _async_migrate_mesh_unique_ids
from custom_components.keenetic_router_pro.const import DOMAIN
from custom_components.keenetic_router_pro.utils import mesh_unique_id


class FakeEntityRegistry:
    """Small registry double for unique-id migration tests."""

    def __init__(self, existing: dict[tuple[str, str, str], str]) -> None:
        self.existing = existing
        self.updated: dict[str, str] = {}

    def async_get_entity_id(self, platform: str, domain: str, unique_id: str) -> str | None:
        return self.existing.get((platform, domain, unique_id))

    def async_update_entity(self, entity_id: str, *, new_unique_id: str) -> None:
        self.updated[entity_id] = new_unique_id


def test_mesh_unique_id_migration_covers_mesh_platform_entities() -> None:
    """Old truncated mesh unique IDs migrate to entry-scoped full IDs."""
    entry = SimpleNamespace(entry_id="entry_123")
    node_id = "aa:bb:cc:dd:ee:ff:11:22:33"
    old_safe = node_id.replace("-", "_").replace(":", "_")[:16]
    old_compact = node_id.replace("-", "").replace(":", "")[:16]
    existing = {
        ("sensor", DOMAIN, f"{old_safe}_uptime_v2"): "sensor.mesh_uptime",
        ("sensor", DOMAIN, f"{old_safe}_port_1_v2"): "sensor.mesh_port_1",
        ("binary_sensor", DOMAIN, f"{old_safe}_connect_v2"): "binary_sensor.mesh_connect",
        (
            "binary_sensor",
            DOMAIN,
            f"{entry.entry_id}_mesh_{old_compact}_update_v2",
        ): "binary_sensor.mesh_update",
        ("button", DOMAIN, f"{old_safe}_reboot_button_v2"): "button.mesh_reboot",
        ("update", DOMAIN, f"{old_safe}_firmware_update_v2"): "update.mesh_firmware",
    }
    registry = FakeEntityRegistry(existing)
    entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda _hass: registry
    sys.modules["homeassistant.helpers.entity_registry"] = entity_registry

    _async_migrate_mesh_unique_ids(
        None,
        entry,
        [{"cid": node_id, "port": [{"label": "1"}]}],
    )

    assert registry.updated == {
        "sensor.mesh_uptime": mesh_unique_id(entry.entry_id, node_id, "uptime_v2"),
        "sensor.mesh_port_1": mesh_unique_id(entry.entry_id, node_id, "port_1_v2"),
        "binary_sensor.mesh_connect": mesh_unique_id(
            entry.entry_id, node_id, "connect_v2"
        ),
        "binary_sensor.mesh_update": mesh_unique_id(entry.entry_id, node_id, "update_v2"),
        "button.mesh_reboot": mesh_unique_id(entry.entry_id, node_id, "reboot_button_v2"),
        "update.mesh_firmware": mesh_unique_id(
            entry.entry_id, node_id, "firmware_update_v2"
        ),
    }
