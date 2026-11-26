"""Firmware update platform for Keenetic Router Pro."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

import aiohttp

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import KeeneticApiError, KeeneticClient
from .const import FIELD_CONNECTED, FIRMWARE_UPDATE_POLL_S, FIRMWARE_UPDATE_WINDOW_S
from .coordinator import KeeneticCoordinator
from .entity import ControllerEntity, MeshEntity
from .entity_setup import DynamicEntityTracker, register_dynamic_entities
from .release_notes import KEENETIC_SUPPORT_URL, async_resolve_release_url
from .utils import find_mesh_node, iter_new_items, mask_identifier

_LOGGER = logging.getLogger(__name__)

# Serialize update-platform writes (install/reboot) to protect the router CPU.
PARALLEL_UPDATES = 1

_REBOOT_WAIT_ERRORS = (
    KeeneticApiError,
    aiohttp.ClientError,
    asyncio.TimeoutError,
    TypeError,
    ValueError,
    KeyError,
)


def _reported_latest_version(available: str | None, current: str | None) -> str | None:
    """Return the version HA should treat as latest."""
    if not available or not current or available == current:
        return current

    # HA's UpdateEntity uses AwesomeVersion comparison (latest > installed).
    # When switching channels (e.g. dev→stable), the available version may be
    # numerically lower. Append a suffix to bypass version comparison.
    try:
        from awesomeversion import AwesomeVersion

        if AwesomeVersion(available) < AwesomeVersion(current):
            return f"{available} (channel switch)"
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        pass
    return available


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Keenetic Router Pro update entities."""
    runtime = entry.runtime_data
    coordinator: KeeneticCoordinator = runtime.coordinator
    client: KeeneticClient = runtime.client

    entities: list[UpdateEntity] = [
        KeeneticFirmwareUpdate(coordinator, entry, client),
    ]

    tracker = DynamicEntityTracker()

    def _build_dynamic_updates() -> list[UpdateEntity]:
        dynamic_entities: list[UpdateEntity] = []
        _add_mesh_update_entities(
            dynamic_entities,
            coordinator,
            entry,
            client,
            tracker.mesh_nodes,
        )
        return dynamic_entities

    register_dynamic_entities(
        entry,
        coordinator,
        async_add_entities,
        _build_dynamic_updates,
        initial_entities=entities,
    )


def _add_mesh_update_entities(
    entities: list[UpdateEntity],
    coordinator: KeeneticCoordinator,
    entry: ConfigEntry,
    client: KeeneticClient,
    known_mesh_ids: set[str],
) -> None:
    """Append update entities for newly discovered mesh nodes."""
    for node in iter_new_items(coordinator, "mesh_nodes", known_mesh_ids, ("cid", "id")):
        node_id = str(node.get("cid") or node.get("id"))
        entities.append(KeeneticMeshFirmwareUpdate(coordinator, entry, node_id, client))


class _FirmwareUpdate(UpdateEntity):
    """Shared progress and outcome confirmation for router and mesh updates."""

    _attr_has_entity_name = True
    _attr_translation_key = "firmware_update"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.RELEASE_NOTES
    )
    _update_progress: int | None = None

    @property
    def in_progress(self) -> bool | int:
        """Return the last reported progress until the installed version is verified."""
        return self._update_progress if self._update_progress is not None else False

    async def _async_read_installed_version(self) -> str | None:
        raise NotImplementedError

    async def _async_read_update_progress(self) -> int | None:
        return None

    async def _async_install_and_confirm(
        self, start_update: Callable[[], Awaitable[bool]], device: str
    ) -> None:
        if self._update_progress is not None:
            raise HomeAssistantError(f"{device} already has a firmware update in progress")

        self._update_progress = 0
        try:
            self.async_write_ha_state()
            before = self.installed_version or await self._async_read_installed_version()
            if not before:
                raise HomeAssistantError(f"Cannot read the installed firmware version from {device}")
            if not await start_update():
                raise HomeAssistantError(f"{device} did not accept the update command")

            installed = await self._async_wait_for_version(before)
            if installed:
                self.coordinator.request_update_check()
                await self.coordinator.async_request_refresh()
            minutes = FIRMWARE_UPDATE_WINDOW_S // 60
            if installed == before:
                raise HomeAssistantError(
                    f"{device} still reports firmware {before} after {minutes} minutes"
                )
            if not installed:
                raise HomeAssistantError(
                    f"{device} did not return with a readable firmware version within "
                    f"{minutes} minutes"
                )
        finally:
            self._update_progress = None
            self.async_write_ha_state()

    async def _async_wait_for_version(self, before: str) -> str | None:
        """Return the changed version or the last observation at the deadline."""
        deadline = monotonic() + FIRMWARE_UPDATE_WINDOW_S
        installed = None
        while (remaining := deadline - monotonic()) > 0:
            await asyncio.sleep(min(FIRMWARE_UPDATE_POLL_S, remaining))
            if (remaining := deadline - monotonic()) <= 0:
                break

            progress = installed = None
            try:
                async with asyncio.timeout(remaining):
                    progress = await self._async_read_update_progress()
                    installed = await self._async_read_installed_version()
            except _REBOOT_WAIT_ERRORS as err:
                _LOGGER.debug("Firmware reboot poll failed: %s", type(err).__name__)
            if installed and installed != before:
                break
            if (
                progress is not None
                and progress != self._update_progress
                and deadline - monotonic() >= FIRMWARE_UPDATE_POLL_S
            ):
                self._update_progress = progress
                self.async_write_ha_state()
        return installed


class KeeneticFirmwareUpdate(ControllerEntity, _FirmwareUpdate):
    """Firmware update entity for the main Keenetic router."""

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        client: KeeneticClient,
    ) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._client = client
        self._resolved_release_url: str | None = None

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_firmware_update"

    @property
    def installed_version(self) -> str | None:
        """Return the current firmware version."""
        system = self.coordinator.data.get("system", {}) or {}
        return system.get("title") or system.get("release")

    @property
    def latest_version(self) -> str | None:
        """Return the latest available firmware version."""
        system = self.coordinator.data.get("system", {}) or {}
        available = system.get("fw-available") or system.get("release-available")
        current = system.get("title") or system.get("release")

        update_available = system.get("fw-update-available", True)
        if update_available is None:
            return None
        if update_available:
            return _reported_latest_version(available, current)

        # No update available → return current so HA shows "up to date"
        return current

    @property
    def release_url(self) -> str | None:
        """Return the release notes URL."""
        return self._resolved_release_url or KEENETIC_SUPPORT_URL

    async def _async_resolve_release_url(self) -> str | None:
        """Resolve the model-specific changelog URL and cache it on the entity."""
        system = self.coordinator.data.get("system", {}) or {}
        url = await async_resolve_release_url(
            getattr(self._client, "_session", None),
            model=system.get("model"),
            hw_id=system.get("hw_id"),
            device=system.get("device"),
            region=system.get("region"),
            channel=system.get("fw-update-sandbox"),
        )
        if url and url != self._resolved_release_url:
            self._resolved_release_url = url
            self.async_write_ha_state()
        return url

    async def async_release_notes(self) -> str | None:
        """Return release notes for the latest version."""
        system = self.coordinator.data.get("system", {}) or {}
        available = system.get("fw-available") or system.get("release-available")
        current = system.get("title") or system.get("release")
        model = self._model_name or "Keenetic"
        channel = system.get("fw-update-sandbox", "stable")

        if available and current and available != current:
            notes_url = await self._async_resolve_release_url() or KEENETIC_SUPPORT_URL
            notes = (
                f"**{model}** firmware update available\n\n"
                f"- Current: `{current}`\n"
                f"- Available: `{available}`\n"
                f"- Channel: {channel}\n\n"
            )
            if channel and channel != "stable":
                notes += f"⚠️ This is a **{channel}** release.\n\n"
            notes += (
                f"Visit [Keenetic Release Notes]({notes_url}) "
                f"for detailed changelog."
            )
            return notes
        return None

    async def _async_read_installed_version(self) -> str | None:
        version = await self._client.async_get_current_version_info()
        return version.get("title") or version.get("release")

    async def _async_read_update_progress(self) -> int | None:
        progress = await self._client.async_get_update_progress()
        percent = progress.get("progress_percent")
        if progress.get("in_progress") and isinstance(percent, (int, float)) and 0 <= percent <= 100:
            return int(percent)
        return None

    async def async_install(
        self,
        version: str | None,
        backup: bool,
        **kwargs: Any,
    ) -> None:
        """Install firmware and confirm the router reports a different version."""
        _LOGGER.info("Starting firmware update for Keenetic router")
        try:
            await self._async_install_and_confirm(
                self._client.async_start_firmware_update, "Router"
            )
        except (asyncio.CancelledError, HomeAssistantError):
            raise
        except Exception as err:  # noqa: BLE001  # surface unexpected failures to the user
            _LOGGER.exception("Firmware update failed: %s", err)
            raise HomeAssistantError(f"Firmware update failed: {err}") from err


class KeeneticMeshFirmwareUpdate(MeshEntity, _FirmwareUpdate):
    """Firmware update entity for a Keenetic mesh node."""

    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        node_cid: str,
        client: KeeneticClient,
    ) -> None:
        MeshEntity.__init__(self, coordinator, entry.entry_id, entry.title, node_cid)
        self._client = client
        self._resolved_release_url: str | None = None

    @property
    def unique_id(self) -> str:
        return self._mesh_unique_id("firmware_update_v2")

    @property
    def in_progress(self) -> bool:
        """Report whether the node is updating without a numeric estimate."""
        return self._update_progress is not None

    @property
    def installed_version(self) -> str | None:
        """Return the current firmware version of the mesh node."""
        node = self._node
        if not node:
            return None
        return node.get("firmware")

    @property
    def latest_version(self) -> str | None:
        """Return the latest available firmware for the mesh node."""
        node = self._node
        if not node:
            return None
        return _reported_latest_version(
            node.get("firmware_available"),
            node.get("firmware"),
        )

    @property
    def release_url(self) -> str | None:
        """Return the release notes URL."""
        return self._resolved_release_url or KEENETIC_SUPPORT_URL

    async def _async_resolve_release_url(self) -> str | None:
        """Resolve the node-model changelog URL and cache it on the entity."""
        node = self._node or {}
        system = self.coordinator.data.get("system", {}) or {}
        url = await async_resolve_release_url(
            getattr(self._client, "_session", None),
            model=node.get("model"),
            hw_id=node.get("hw_id"),
            region=node.get("region") or system.get("region"),
            channel=system.get("fw-update-sandbox"),
        )
        if url and url != self._resolved_release_url:
            self._resolved_release_url = url
            self.async_write_ha_state()
        return url

    async def async_release_notes(self) -> str | None:
        """Return release notes for the latest version."""
        node = self._node
        if not node:
            return None
        available = node.get("firmware_available")
        current = node.get("firmware")
        name = node.get("name") or node.get("model") or self._node_cid

        if available and current and available != current:
            notes_url = await self._async_resolve_release_url() or KEENETIC_SUPPORT_URL
            return (
                f"**{name}** firmware update available\n\n"
                f"- Current: `{current}`\n"
                f"- Available: `{available}`\n\n"
                f"Update is managed by the controller router.\n\n"
                f"Visit [Keenetic Release Notes]({notes_url}) "
                f"for detailed changelog."
            )
        return None

    async def _async_read_installed_version(self) -> str | None:
        nodes = await self._client.async_get_mesh_nodes()
        node = find_mesh_node({"mesh_nodes": nodes}, self._node_cid)
        if node and node.get(FIELD_CONNECTED):
            return node.get("firmware")
        return None

    async def async_install(
        self,
        version: str | None,
        backup: bool,
        **kwargs: Any,
    ) -> None:
        """Install firmware and confirm the mesh node reports a different version."""
        node = self._node or {}
        node_name = node.get("name") or self._node_cid
        node_ip = node.get("ip")
        label = mask_identifier(node_name)
        if not node_ip:
            raise HomeAssistantError(
                f"Cannot update {label}: node IP address not available. Is the node online?"
            )

        async def start_update() -> bool:
            return await self._client.async_start_node_firmware_update(
                node_ip=node_ip,
                node_name=node_name,
                node_cid=node.get("cid") or node.get("mac") or self._node_cid,
            )

        _LOGGER.info("Starting firmware update for mesh node %s", label)
        try:
            await self._async_install_and_confirm(start_update, f"Node {label}")
        except (asyncio.CancelledError, HomeAssistantError):
            raise
        except Exception as err:  # noqa: BLE001  # surface unexpected failures to the user
            _LOGGER.debug("Mesh firmware update failed for %s: %s", label, type(err).__name__)
            raise HomeAssistantError(
                f"Mesh firmware update failed for {label}: {type(err).__name__}"
            ) from err
