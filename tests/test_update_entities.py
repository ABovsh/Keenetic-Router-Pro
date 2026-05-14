"""Firmware update entity behavior tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.exceptions import HomeAssistantError

import custom_components.keenetic_router_pro.update as update_module
from custom_components.keenetic_router_pro.update import (
    KeeneticFirmwareUpdate,
    KeeneticMeshFirmwareUpdate,
)


class _Coordinator:
    """Small coordinator fake for update entity tests."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.client = SimpleNamespace(_host="192.0.2.1", _ssl=False)
        self.refreshes = 0
        self.on_refresh = None

    def async_add_listener(self, *_args: Any, **_kwargs: Any) -> Any:
        return lambda: None

    async def async_request_refresh(self) -> None:
        self.refreshes += 1
        if self.on_refresh:
            self.on_refresh(self)


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router")


async def _no_sleep(_seconds: float) -> None:
    return None


def test_router_firmware_install_handles_reboot_without_progress_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routers without progress support still move through reboot detection and refresh."""
    writes: list[bool | int] = []

    class Client:
        def __init__(self) -> None:
            self.system_checks = 0

        async def async_start_firmware_update(self) -> bool:
            return True

        async def async_get_update_progress(self) -> dict[str, Any]:
            return {"in_progress": False}

        async def async_get_system_info(self) -> dict[str, Any]:
            self.system_checks += 1
            raise RuntimeError("router rebooting")

    monkeypatch.setattr(update_module.asyncio, "sleep", _no_sleep)

    coordinator = _Coordinator({"system": {"title": "4.2.0", "fw-available": "4.3.0"}})
    entity = KeeneticFirmwareUpdate(coordinator, _entry(), Client())
    entity.async_write_ha_state = lambda: writes.append(entity.in_progress)

    asyncio.run(entity.async_install(version=None, backup=False))

    assert writes == [0, 50, 90, False]
    assert coordinator.refreshes == 1


def test_router_firmware_install_rejects_unaccepted_update() -> None:
    """A false router response should surface as a Home Assistant action error."""

    class Client:
        async def async_start_firmware_update(self) -> bool:
            return False

    coordinator = _Coordinator({"system": {"title": "4.2.0", "fw-available": "4.3.0"}})
    entity = KeeneticFirmwareUpdate(coordinator, _entry(), Client())
    entity.async_write_ha_state = lambda: None

    with pytest.raises(HomeAssistantError, match="did not accept"):
        asyncio.run(entity.async_install(version=None, backup=False))

    assert coordinator.refreshes == 0


def test_mesh_firmware_install_requires_online_node_ip() -> None:
    """Mesh updates need a direct node IP before issuing a firmware command."""
    coordinator = _Coordinator(
        {
            "mesh_nodes": [
                {
                    "cid": "node-1",
                    "name": "Extender",
                    "firmware": "4.2.0",
                    "firmware_available": "4.3.0",
                }
            ]
        }
    )
    entity = KeeneticMeshFirmwareUpdate(coordinator, _entry(), "node-1", SimpleNamespace())

    with pytest.raises(HomeAssistantError, match="IP address not available"):
        asyncio.run(entity.async_install(version=None, backup=False))


def test_mesh_firmware_install_refreshes_until_target_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A direct mesh update should refresh until the node reports the new firmware."""
    calls: list[dict[str, Any]] = []

    class Client:
        async def async_start_node_firmware_update(self, **kwargs: Any) -> bool:
            calls.append(kwargs)
            return True

    coordinator = _Coordinator(
        {
            "mesh_nodes": [
                {
                    "cid": "node-1",
                    "name": "Extender",
                    "ip": "192.0.2.20",
                    "firmware": "4.2.0",
                    "firmware_available": "4.3.0",
                }
            ]
        }
    )

    def mark_updated(coord: _Coordinator) -> None:
        coord.data["mesh_nodes"][0]["firmware"] = "4.3.0"

    coordinator.on_refresh = mark_updated
    entity = KeeneticMeshFirmwareUpdate(coordinator, _entry(), "node-1", Client())
    monkeypatch.setattr(update_module.asyncio, "sleep", _no_sleep)

    asyncio.run(entity.async_install(version=None, backup=False))

    assert calls == [
        {
            "node_ip": "192.0.2.20",
            "node_name": "Extender",
            "node_cid": "node-1",
        }
    ]
    assert coordinator.refreshes == 2
