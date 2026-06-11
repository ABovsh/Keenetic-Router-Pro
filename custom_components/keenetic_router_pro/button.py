"""Buttons for Keenetic Router Pro (e.g. reboot)."""
from __future__ import annotations
from typing import Any
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .api import KeeneticClient
from .coordinator import KeeneticCoordinator
from .entity import ControllerEntity, MeshEntity
from .entity_setup import DynamicEntityTracker, register_dynamic_entities
from .utils import iter_new_items


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Keenetic Router Pro buttons."""
    runtime = entry.runtime_data
    coordinator: KeeneticCoordinator = runtime.coordinator
    client: KeeneticClient = runtime.client
    entities: list[ButtonEntity] = [KeeneticRebootButton(coordinator, entry, client)]

    tracker = DynamicEntityTracker()

    def _build_dynamic_buttons() -> list[ButtonEntity]:
        dynamic_entities: list[ButtonEntity] = []
        _add_mesh_reboot_buttons(
            dynamic_entities,
            coordinator,
            entry,
            client,
            tracker.mesh_nodes,
        )
        return dynamic_entities

    entities.extend(_build_dynamic_buttons())
    async_add_entities(entities)

    register_dynamic_entities(
        entry,
        coordinator,
        async_add_entities,
        _build_dynamic_buttons,
        add_initial=False,
    )


def _add_mesh_reboot_buttons(
    entities: list[ButtonEntity],
    coordinator: KeeneticCoordinator,
    entry: ConfigEntry,
    client: KeeneticClient,
    known_mesh_ids: set[str],
) -> None:
    """Append reboot buttons for newly discovered mesh nodes."""
    for node in iter_new_items(coordinator, "mesh_nodes", known_mesh_ids, ("cid", "id")):
        node_id = str(node.get("cid") or node.get("id"))
        entities.append(KeeneticMeshRebootButton(coordinator, entry, client, node_id))


class KeeneticRebootButton(ControllerEntity, ButtonEntity):
    """Button to reboot the router."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:restart"
    _attr_translation_key = "reboot"

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        client: KeeneticClient,
    ) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._client = client

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_reboot_button"

    async def async_press(self, **_: Any) -> None:
        await self._client.async_reboot()


class KeeneticMeshRebootButton(MeshEntity, ButtonEntity):
    """Button to reboot a mesh/extender node."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:restart"

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        client: KeeneticClient,
        node_cid: str,
    ) -> None:
        MeshEntity.__init__(self, coordinator, entry.entry_id, entry.title, node_cid)
        self._client = client

    @property
    def unique_id(self) -> str:
        return self._mesh_unique_id("reboot_button_v2")

    @property
    def name(self) -> str:
        # has_entity_name=True: HA prepends the device (node) name itself;
        # embedding the node name here double-printed it.
        return "Reboot"

    async def async_press(self, **_: Any) -> None:
        await self._client.async_reboot_mesh_node(self._node_cid)
