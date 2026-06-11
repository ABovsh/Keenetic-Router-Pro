"""Firmware update platform for Keenetic Router Pro."""
from __future__ import annotations

import asyncio
import logging
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

from .api import KeeneticApiError, KeeneticAuthError, KeeneticClient
from .coordinator import KeeneticCoordinator
from .entity import ControllerEntity, MeshEntity
from .utils import mask_identifier
from .entity_setup import DynamicEntityTracker, register_dynamic_entities
from .utils import iter_new_items

_LOGGER = logging.getLogger(__name__)

KEENETIC_RELEASE_NOTES_URL = "https://help.keenetic.com/hc/en-us/categories/360000400920-KeeneticOS-Release-Notes"

_UPDATE_PROGRESS_FALLBACK_ERRORS = (
    KeeneticApiError,
    aiohttp.ClientError,
    asyncio.TimeoutError,
    TypeError,
    ValueError,
    KeyError,
)
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

    entities.extend(_build_dynamic_updates())
    async_add_entities(entities)

    register_dynamic_entities(
        entry,
        coordinator,
        async_add_entities,
        _build_dynamic_updates,
        add_initial=False,
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


class KeeneticFirmwareUpdate(ControllerEntity, UpdateEntity):
    """Firmware update entity for the main Keenetic router."""

    _attr_has_entity_name = True
    _attr_translation_key = "firmware_update"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        client: KeeneticClient,
    ) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._client = client
        self._update_progress: int | None = None

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

        if system.get("fw-update-available", True):
            return _reported_latest_version(available, current)

        # No update available → return current so HA shows "up to date"
        return current

    @property
    def in_progress(self) -> bool | int:
        """Return update progress."""
        if self._update_progress is not None:
            return self._update_progress
        return False

    @property
    def release_url(self) -> str | None:
        """Return the release notes URL."""
        return KEENETIC_RELEASE_NOTES_URL

    async def async_release_notes(self) -> str | None:
        """Return release notes for the latest version."""
        system = self.coordinator.data.get("system", {}) or {}
        available = system.get("fw-available") or system.get("release-available")
        current = system.get("title") or system.get("release")
        model = self._model_name or "Keenetic"
        channel = system.get("fw-update-sandbox", "stable")

        if available and current and available != current:
            notes = (
                f"**{model}** firmware update available\n\n"
                f"- Current: `{current}`\n"
                f"- Available: `{available}`\n"
                f"- Channel: {channel}\n\n"
            )
            if channel and channel != "stable":
                notes += f"⚠️ This is a **{channel}** release.\n\n"
            notes += (
                f"Visit [Keenetic Release Notes]({KEENETIC_RELEASE_NOTES_URL}) "
                f"for detailed changelog."
            )
            return notes
        return None

    async def async_install(
        self,
        version: str | None,
        backup: bool,
        **kwargs: Any,
    ) -> None:
        """Install the firmware update."""
        _LOGGER.info("Starting firmware update for Keenetic router")

        try:
            self._update_progress = 0
            self.async_write_ha_state()

            result = await self._client.async_start_firmware_update()

            if not result:
                self._update_progress = None
                self.async_write_ha_state()
                raise HomeAssistantError("Router did not accept the update command")

            # Try to get initial progress to detect if endpoint is available
            progress_supported = False
            try:
                initial = await self._client.async_get_update_progress()
                progress_supported = bool(initial and initial.get("in_progress"))
            except asyncio.CancelledError:
                raise
            except KeeneticAuthError:
                raise
            except _UPDATE_PROGRESS_FALLBACK_ERRORS as err:
                _LOGGER.debug("Update progress endpoint not available: %s", err)

            if progress_supported:
                # Poll progress until complete or timeout
                for _ in range(120):  # ~4 min max polling
                    await asyncio.sleep(2)
                    try:
                        progress = await self._client.async_get_update_progress()
                    except asyncio.CancelledError:
                        raise
                    except KeeneticAuthError:
                        raise
                    except _REBOOT_WAIT_ERRORS:
                        self._update_progress = 95
                        self.async_write_ha_state()
                        break

                    if not progress.get("in_progress", False):
                        break

                    percent = progress.get("progress_percent", 0)
                    if isinstance(percent, (int, float)) and 0 <= percent <= 100:
                        self._update_progress = int(percent)
                        self.async_write_ha_state()
            else:
                # No progress endpoint — wait for router to reboot
                _LOGGER.info(
                    "Update progress not available on this router, "
                    "waiting for reboot"
                )
                self._update_progress = 50
                self.async_write_ha_state()

                # Wait until connection is lost (router rebooting)
                for _ in range(60):  # ~2 min to start reboot
                    await asyncio.sleep(2)
                    try:
                        await self._client.async_get_system_info()
                    except asyncio.CancelledError:
                        raise
                    except KeeneticAuthError:
                        raise
                    except _REBOOT_WAIT_ERRORS:
                        self._update_progress = 90
                        self.async_write_ha_state()
                        break

        except HomeAssistantError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001  # surface any unexpected failure as HA error to the user
            _LOGGER.exception("Firmware update failed: %s", err)
            raise HomeAssistantError(f"Firmware update failed: {err}") from err
        finally:
            self._update_progress = None
            self.async_write_ha_state()

        # Refresh coordinator to pick up new version
        await self.coordinator.async_request_refresh()


class KeeneticMeshFirmwareUpdate(MeshEntity, UpdateEntity):
    """Firmware update entity for a Keenetic mesh node."""

    _attr_has_entity_name = True
    _attr_translation_key = "firmware_update"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        node_cid: str,
        client: KeeneticClient,
    ) -> None:
        MeshEntity.__init__(self, coordinator, entry.entry_id, entry.title, node_cid)
        self._client = client

    @property
    def unique_id(self) -> str:
        return self._mesh_unique_id("firmware_update_v2")

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
        return KEENETIC_RELEASE_NOTES_URL

    async def async_release_notes(self) -> str | None:
        """Return release notes for the latest version."""
        node = self._node
        if not node:
            return None
        available = node.get("firmware_available")
        current = node.get("firmware")
        name = node.get("name") or node.get("model") or self._node_cid

        if available and current and available != current:
            return (
                f"**{name}** firmware update available\n\n"
                f"- Current: `{current}`\n"
                f"- Available: `{available}`\n\n"
                f"Update is managed by the controller router.\n\n"
                f"Visit [Keenetic Release Notes]({KEENETIC_RELEASE_NOTES_URL}) "
                f"for detailed changelog."
            )
        return None

    async def async_install(
        self,
        version: str | None,
        backup: bool,
        **kwargs: Any,
    ) -> None:
        """Install firmware update for this mesh node via direct connection."""
        node = self._node
        node_name = (node.get("name") or self._node_cid) if node else self._node_cid
        node_ip = node.get("ip") if node else None

        if not node_ip:
            raise HomeAssistantError(
                f"Cannot update {node_name}: node IP address not available. "
                f"Is the node online?"
            )

        _LOGGER.info(
            "Starting firmware update for mesh node %s (%s)",
            mask_identifier(node_name),
            mask_identifier(node_ip),
        )

        try:
            result = await self._client.async_start_node_firmware_update(
                node_ip=node_ip,
                node_name=node_name,
                node_cid=node.get("cid") or node.get("mac") or self._node_cid,
            )

            if not result:
                raise HomeAssistantError(
                    f"Node {node_name} did not accept the update command"
                )

            _LOGGER.info(
                "Update started on %s, waiting for node to reboot",
                mask_identifier(node_name),
            )
            await asyncio.sleep(10)

            # Wait until node reports updated firmware or timeout. Poll every
            # ~10s (not 2s) so a node update doesn't hammer the controller
            # with refreshes for the whole reboot window.
            for _ in range(18):  # ~3 min
                await asyncio.sleep(10)
                try:
                    await self.coordinator.async_request_refresh()
                    updated_node = self._node
                    if updated_node:
                        new_fw = updated_node.get("firmware")
                        avail = updated_node.get("firmware_available")
                        if new_fw and avail and new_fw == avail:
                            _LOGGER.info(
                                "Mesh node %s updated to %s",
                                mask_identifier(node_name),
                                new_fw,
                            )
                            break
                except asyncio.CancelledError:
                    raise
                except KeeneticAuthError:
                    raise
                except _REBOOT_WAIT_ERRORS as err:
                    _LOGGER.debug(
                        "Mesh node %s firmware re-check failed: %s",
                        mask_identifier(node_name),
                        err,
                    )

        except HomeAssistantError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001  # surface any unexpected failure as HA error to the user
            _LOGGER.exception(
                "Mesh firmware update failed for %s: %s",
                mask_identifier(node_name),
                err,
            )
            raise HomeAssistantError(
                f"Mesh firmware update failed for {node_name}: {err}"
            ) from err

        await self.coordinator.async_request_refresh()
