"""Buttons for Keenetic Router Pro (e.g. reboot)."""
from __future__ import annotations
from typing import Any
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .api import KeeneticClient
from .const import DOMAIN
from .coordinator import KeeneticCoordinator
from .entity import ControllerEntity, MeshEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Keenetic Router Pro buttons."""
    runtime = entry.runtime_data
    coordinator: KeeneticCoordinator = runtime.coordinator
    client: KeeneticClient = runtime.client
    entities: list[ButtonEntity] = [KeeneticRebootButton(coordinator, entry, client)]

    # Mesh node reboot butonları
    known_mesh_ids: set[str] = set()
    _add_mesh_reboot_buttons(entities, coordinator, entry, client, known_mesh_ids)

    async_add_entities(entities)

    @callback
    def _async_add_new_mesh_buttons() -> None:
        new_entities: list[ButtonEntity] = []
        _add_mesh_reboot_buttons(
            new_entities, coordinator, entry, client, known_mesh_ids
        )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        coordinator.async_add_listener(_async_add_new_mesh_buttons)
    )


def _add_mesh_reboot_buttons(
    entities: list[ButtonEntity],
    coordinator: KeeneticCoordinator,
    entry: ConfigEntry,
    client: KeeneticClient,
    known_mesh_ids: set[str],
) -> None:
    """Append reboot buttons for newly discovered mesh nodes."""
    for node in coordinator.data.get("mesh_nodes", []) or []:
        node_cid = node.get("cid") or node.get("id")
        if not node_cid:
            continue
        node_id = str(node_cid)
        if node_id in known_mesh_ids:
            continue
        known_mesh_ids.add(node_id)
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
        node = self._node
        node_name = node.get("name") if node else None
        if node_name:
            return f"Reboot {node_name}"
        return "Reboot"

    async def async_press(self, **_: Any) -> None:
        await self._client.async_reboot_mesh_node(self._node_cid)
