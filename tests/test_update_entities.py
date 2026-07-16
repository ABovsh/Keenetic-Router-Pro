"""Firmware update entity behavior tests."""

from __future__ import annotations

from conftest import TEST_HOST

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.exceptions import HomeAssistantError

import custom_components.keenetic_router_pro.update as update_module
from custom_components.keenetic_router_pro.api import KeeneticApiError
from custom_components.keenetic_router_pro.update import (
    KeeneticFirmwareUpdate,
    KeeneticMeshFirmwareUpdate,
)


class _Coordinator:
    """Small coordinator fake for update entity tests."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.client = SimpleNamespace(_host=TEST_HOST, _ssl=False)
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
            raise KeeneticApiError("router rebooting")

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


def test_router_firmware_install_polls_progress_until_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routers with progress support should publish progress and refresh once."""
    writes: list[bool | int] = []

    class Client:
        def __init__(self) -> None:
            self.progress = [
                {"in_progress": True, "progress_percent": 0},
                {"in_progress": True, "progress_percent": 10},
                {"in_progress": True, "progress_percent": 40},
                {"in_progress": False, "progress_percent": 100},
            ]

        async def async_start_firmware_update(self) -> bool:
            return True

        async def async_get_update_progress(self) -> dict[str, Any]:
            return self.progress.pop(0)

    monkeypatch.setattr(update_module.asyncio, "sleep", _no_sleep)

    coordinator = _Coordinator({"system": {"title": "4.2.0", "fw-available": "4.3.0"}})
    entity = KeeneticFirmwareUpdate(coordinator, _entry(), Client())
    entity.async_write_ha_state = lambda: writes.append(entity.in_progress)

    asyncio.run(entity.async_install(version=None, backup=False))

    assert writes == [0, 10, 40, False]
    assert coordinator.refreshes == 1


def test_router_firmware_install_preserves_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Firmware update sleeps and polls must not swallow cancellation."""

    class Client:
        async def async_start_firmware_update(self) -> bool:
            return True

        async def async_get_update_progress(self) -> dict[str, Any]:
            raise asyncio.CancelledError

    monkeypatch.setattr(update_module.asyncio, "sleep", _no_sleep)

    coordinator = _Coordinator({"system": {"title": "4.2.0", "fw-available": "4.3.0"}})
    entity = KeeneticFirmwareUpdate(coordinator, _entry(), Client())
    entity.async_write_ha_state = lambda: None

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(entity.async_install(version=None, backup=False))

    assert coordinator.refreshes == 0


def test_mesh_firmware_install_rejects_unaccepted_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mesh node command rejection should surface as a user action error."""

    class Client:
        async def async_start_node_firmware_update(self, **kwargs: Any) -> bool:
            return False

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
    entity = KeeneticMeshFirmwareUpdate(coordinator, _entry(), "node-1", Client())
    monkeypatch.setattr(update_module.asyncio, "sleep", _no_sleep)

    with pytest.raises(HomeAssistantError, match="did not accept"):
        asyncio.run(entity.async_install(version=None, backup=False))

    assert coordinator.refreshes == 0


def test_mesh_firmware_install_retries_transient_refresh_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rebooting mesh node may make refresh fail before the final successful check."""

    class Client:
        async def async_start_node_firmware_update(self, **kwargs: Any) -> bool:
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
    attempts = {"count": 0}

    async def flaky_refresh() -> None:
        coordinator.refreshes += 1
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise KeeneticApiError("node rebooting")
        coordinator.data["mesh_nodes"][0]["firmware"] = "4.3.0"

    coordinator.async_request_refresh = flaky_refresh
    entity = KeeneticMeshFirmwareUpdate(coordinator, _entry(), "node-1", Client())
    monkeypatch.setattr(update_module.asyncio, "sleep", _no_sleep)

    asyncio.run(entity.async_install(version=None, backup=False))

    assert coordinator.refreshes == 3


# ---------- per-model release-notes deep links ----------

_SYSTEM_WITH_UPDATE = {
    "title": "5.0.12",
    "fw-available": "5.1.1",
    "fw-update-sandbox": "stable",
    "model": "Titan (KN-1812)",
    "device": "Titan",
    "hw_id": "KN-1812",
    "region": "UA",
}
_DEEP_LINK = (
    "https://support.keenetic.ua/titan/kn-1812/en/41380-latest-main-release.html"
)


def test_controller_release_url_defaults_to_support_portal() -> None:
    coordinator = _Coordinator({"system": dict(_SYSTEM_WITH_UPDATE)})
    entity = KeeneticFirmwareUpdate(coordinator, _entry(), SimpleNamespace())
    assert entity.release_url == "https://support.keenetic.com/"


def test_controller_release_notes_use_resolved_deep_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def _fake_resolve(_session: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return _DEEP_LINK

    monkeypatch.setattr(update_module, "async_resolve_release_url", _fake_resolve)

    coordinator = _Coordinator({"system": dict(_SYSTEM_WITH_UPDATE)})
    entity = KeeneticFirmwareUpdate(
        coordinator, _entry(), SimpleNamespace(_session=object())
    )
    entity.async_write_ha_state = lambda: None

    notes = asyncio.run(entity.async_release_notes())

    assert f"[Keenetic Release Notes]({_DEEP_LINK})" in notes
    assert entity.release_url == _DEEP_LINK
    assert seen["model"] == "Titan (KN-1812)"
    assert seen["hw_id"] == "KN-1812"
    assert seen["device"] == "Titan"
    assert seen["region"] == "UA"
    assert seen["channel"] == "stable"


def test_controller_release_notes_fall_back_when_resolution_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_resolve(_session: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(update_module, "async_resolve_release_url", _fake_resolve)

    coordinator = _Coordinator({"system": dict(_SYSTEM_WITH_UPDATE)})
    entity = KeeneticFirmwareUpdate(
        coordinator, _entry(), SimpleNamespace(_session=object())
    )
    entity.async_write_ha_state = lambda: None

    notes = asyncio.run(entity.async_release_notes())

    assert "[Keenetic Release Notes](https://support.keenetic.com/)" in notes
    assert entity.release_url == "https://support.keenetic.com/"


def test_mesh_release_notes_use_node_model_and_controller_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    deep = "https://support.keenetic.ua/giga/kn-1011/en/9109-latest-main-release.html"

    async def _fake_resolve(_session: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return deep

    monkeypatch.setattr(update_module, "async_resolve_release_url", _fake_resolve)

    coordinator = _Coordinator(
        {
            "system": {"region": "UA", "fw-update-sandbox": "stable"},
            "mesh_nodes": [
                {
                    "cid": "node-1",
                    "name": "NH - Keenetic Giga",
                    "model": "Giga (KN-1011)",
                    "hw_id": "KN-1011",
                    "region": "UA",
                    "firmware": "5.0.12",
                    "firmware_available": "5.1.1",
                }
            ],
        }
    )
    entity = KeeneticMeshFirmwareUpdate(
        coordinator, _entry(), "node-1", SimpleNamespace(_session=object())
    )
    entity.async_write_ha_state = lambda: None

    notes = asyncio.run(entity.async_release_notes())

    assert f"[Keenetic Release Notes]({deep})" in notes
    assert entity.release_url == deep
    assert seen["model"] == "Giga (KN-1011)"
    assert seen["hw_id"] == "KN-1011"
    assert seen["region"] == "UA"
    assert seen["channel"] == "stable"
