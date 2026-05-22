"""Mesh firmware update controller-to-direct fallback behavior."""

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
    _reported_latest_version,
    async_setup_entry,
)
from tests.fixtures.mesh_rci import FALLBACK_MESH_NODE


async def _no_sleep(_seconds: float) -> None:
    return None


class _Coordinator:
    def __init__(self) -> None:
        self.data = {"mesh_nodes": [dict(FALLBACK_MESH_NODE)]}
        self.refreshes = 0

    async def async_request_refresh(self) -> None:
        self.refreshes += 1
        self.data["mesh_nodes"][0]["firmware"] = "4.3.0"


def test_update_setup_adds_initial_and_dynamic_fallback_node_entities() -> None:
    """Update platform setup registers fallback mesh nodes and later additions."""
    listeners = []

    def async_add_listener(listener):
        listeners.append(listener)
        return lambda: None

    added = []
    coordinator = SimpleNamespace(
        data={"system": {}, "mesh_nodes": [FALLBACK_MESH_NODE]},
        async_add_listener=async_add_listener,
    )
    entry = SimpleNamespace(
        entry_id="entry_123",
        title="Router",
        runtime_data=SimpleNamespace(coordinator=coordinator, client=SimpleNamespace()),
        async_on_unload=lambda unsub: None,
    )

    asyncio.run(async_setup_entry(SimpleNamespace(), entry, added.extend))
    coordinator.data["mesh_nodes"].append(
        dict(FALLBACK_MESH_NODE, id="AA:BB:CC:00:00:02", mac="AA:BB:CC:00:00:02")
    )
    listeners[0]()

    assert [entity.unique_id for entity in added] == [
        "entry_123_firmware_update",
        "entry_123_mesh_AA_BB_CC_00_00_01_firmware_update_v2",
        "entry_123_mesh_AA_BB_CC_00_00_02_firmware_update_v2",
    ]


def test_reported_latest_version_uses_available_when_parser_unavailable() -> None:
    """A missing AwesomeVersion dependency still reports the available version."""
    assert _reported_latest_version("4.3.0", "4.2.0") == "4.3.0"


def test_reported_latest_version_propagates_cancelled_version_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation during version comparison is never swallowed."""

    class CancelledAwesomeVersion:
        def __init__(self, value: str) -> None:
            self.value = value

        def __lt__(self, other: "CancelledAwesomeVersion") -> bool:
            raise asyncio.CancelledError

    monkeypatch.setitem(
        __import__("sys").modules,
        "awesomeversion",
        SimpleNamespace(AwesomeVersion=CancelledAwesomeVersion),
    )

    with pytest.raises(asyncio.CancelledError):
        _reported_latest_version("4.3.0", "4.2.0")


def test_controller_update_beta_release_notes_include_channel_warning() -> None:
    """Controller release notes surface non-stable firmware channels."""
    coordinator = SimpleNamespace(
        data={
            "system": {
                "title": "4.2.0",
                "fw-available": "4.3.0",
                "fw-update-sandbox": "preview",
                "model": "Hero",
            }
        }
    )
    entity = KeeneticFirmwareUpdate(
        coordinator,
        SimpleNamespace(entry_id="entry_123", title="Router"),
        SimpleNamespace(),
    )

    assert asyncio.run(entity.async_release_notes()) == (
        "**Hero** firmware update available\n\n"
        "- Current: `4.2.0`\n"
        "- Available: `4.3.0`\n"
        "- Channel: preview\n\n"
        "⚠️ This is a **preview** release.\n\n"
        "Visit [Keenetic Release Notes]"
        "(https://help.keenetic.com/hc/en-us/categories/360000400920-KeeneticOS-Release-Notes) "
        "for detailed changelog."
    )


def test_controller_update_without_available_version_has_release_url_and_no_notes() -> None:
    """Controller update metadata stays quiet when no update is available."""
    entity = KeeneticFirmwareUpdate(
        SimpleNamespace(data={"system": {"title": "4.2.0"}}),
        SimpleNamespace(entry_id="entry_123", title="Router"),
        SimpleNamespace(),
    )

    assert (
        entity.release_url,
        asyncio.run(entity.async_release_notes()),
    ) == (
        "https://help.keenetic.com/hc/en-us/categories/360000400920-KeeneticOS-Release-Notes",
        None,
    )


def test_controller_update_progress_endpoint_writes_reported_percent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Controller updates use progress endpoint values when available."""
    writes: list[bool | int] = []

    class Client:
        def __init__(self) -> None:
            self.progress = [
                {"in_progress": True, "progress_percent": 25},
                {"in_progress": True, "progress_percent": 55},
                {"in_progress": False},
            ]

        async def async_start_firmware_update(self) -> bool:
            return True

        async def async_get_update_progress(self) -> dict[str, Any]:
            return self.progress.pop(0)

    async def refresh() -> None:
        return None

    monkeypatch.setattr(update_module.asyncio, "sleep", _no_sleep)
    coordinator = SimpleNamespace(
        data={"system": {"title": "4.2.0", "fw-available": "4.3.0"}},
        async_request_refresh=refresh,
    )
    entity = KeeneticFirmwareUpdate(
        coordinator,
        SimpleNamespace(entry_id="entry_123", title="Router"),
        Client(),
    )
    entity.async_write_ha_state = lambda: writes.append(entity.in_progress)

    asyncio.run(entity.async_install(version=None, backup=False))

    assert writes == [0, 55, False]


def test_controller_update_progress_failure_marks_rebooting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Progress polling errors during an active update surface reboot progress."""
    writes: list[bool | int] = []

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        async def async_start_firmware_update(self) -> bool:
            return True

        async def async_get_update_progress(self) -> dict[str, Any]:
            self.calls += 1
            if self.calls == 1:
                return {"in_progress": True}
            raise RuntimeError("rebooting")

    async def refresh() -> None:
        return None

    monkeypatch.setattr(update_module.asyncio, "sleep", _no_sleep)
    entity = KeeneticFirmwareUpdate(
        SimpleNamespace(data={"system": {}}, async_request_refresh=refresh),
        SimpleNamespace(entry_id="entry_123", title="Router"),
        Client(),
    )
    entity.async_write_ha_state = lambda: writes.append(entity.in_progress)

    asyncio.run(entity.async_install(version=None, backup=False))

    assert writes == [0, 95, False]


def test_controller_update_progress_cancel_is_propagated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation during progress polling is not converted to an HA error."""

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        async def async_start_firmware_update(self) -> bool:
            return True

        async def async_get_update_progress(self) -> dict[str, Any]:
            self.calls += 1
            if self.calls == 1:
                return {"in_progress": True}
            raise asyncio.CancelledError

    monkeypatch.setattr(update_module.asyncio, "sleep", _no_sleep)
    entity = KeeneticFirmwareUpdate(
        SimpleNamespace(data={"system": {}}, async_request_refresh=lambda: None),
        SimpleNamespace(entry_id="entry_123", title="Router"),
        Client(),
    )
    entity.async_write_ha_state = lambda: None

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(entity.async_install(version=None, backup=False))


def test_controller_update_reboot_wait_cancel_is_propagated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation while waiting for reboot is not swallowed."""

    class Client:
        async def async_start_firmware_update(self) -> bool:
            return True

        async def async_get_update_progress(self) -> dict[str, Any]:
            return {"in_progress": False}

        async def async_get_system_info(self) -> dict[str, Any]:
            raise asyncio.CancelledError

    monkeypatch.setattr(update_module.asyncio, "sleep", _no_sleep)
    entity = KeeneticFirmwareUpdate(
        SimpleNamespace(data={"system": {}}, async_request_refresh=lambda: None),
        SimpleNamespace(entry_id="entry_123", title="Router"),
        Client(),
    )
    entity.async_write_ha_state = lambda: None

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(entity.async_install(version=None, backup=False))


def test_controller_update_client_error_is_home_assistant_error() -> None:
    """Unexpected controller update errors are wrapped for HA services."""

    class Client:
        async def async_start_firmware_update(self) -> bool:
            raise RuntimeError("boom")

    coordinator = SimpleNamespace(data={"system": {}}, async_request_refresh=lambda: None)
    entity = KeeneticFirmwareUpdate(
        coordinator,
        SimpleNamespace(entry_id="entry_123", title="Router"),
        Client(),
    )
    entity.async_write_ha_state = lambda: None

    with pytest.raises(HomeAssistantError, match="Firmware update failed: boom"):
        asyncio.run(entity.async_install(version=None, backup=False))


def test_mesh_update_missing_node_has_no_versions_or_notes() -> None:
    """Removed fallback mesh update entities expose no stale firmware metadata."""
    entity = KeeneticMeshFirmwareUpdate(
        SimpleNamespace(data={"mesh_nodes": []}),
        SimpleNamespace(entry_id="entry_123", title="Router"),
        "AA:BB:CC:00:00:01",
        SimpleNamespace(),
    )

    assert (
        entity.installed_version,
        entity.latest_version,
        entity.release_url,
        asyncio.run(entity.async_release_notes()),
    ) == (
        None,
        None,
        "https://help.keenetic.com/hc/en-us/categories/360000400920-KeeneticOS-Release-Notes",
        None,
    )


def test_mesh_update_current_firmware_has_no_release_notes() -> None:
    """Mesh update release notes are absent when node firmware is current."""
    entity = KeeneticMeshFirmwareUpdate(
        SimpleNamespace(
            data={
                "mesh_nodes": [
                    dict(FALLBACK_MESH_NODE, firmware="4.3.0", firmware_available="4.3.0")
                ]
            }
        ),
        SimpleNamespace(entry_id="entry_123", title="Router"),
        "AA:BB:CC:00:00:01",
        SimpleNamespace(),
    )

    assert asyncio.run(entity.async_release_notes()) is None


def test_mesh_update_rejects_unaccepted_direct_fallback() -> None:
    """A node update false response surfaces as an HA action error."""

    class Client:
        async def async_start_node_firmware_update(self, **kwargs: Any) -> bool:
            return False

    entity = KeeneticMeshFirmwareUpdate(
        SimpleNamespace(data={"mesh_nodes": [FALLBACK_MESH_NODE]}),
        SimpleNamespace(entry_id="entry_123", title="Router"),
        "AA:BB:CC:00:00:01",
        Client(),
    )

    with pytest.raises(HomeAssistantError, match="did not accept"):
        asyncio.run(entity.async_install(version=None, backup=False))


def test_mesh_update_refresh_failure_is_ignored_during_direct_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient refresh failures do not fail a started mesh update."""

    class Coordinator:
        def __init__(self) -> None:
            self.data = {"mesh_nodes": [dict(FALLBACK_MESH_NODE)]}
            self.calls = 0

        async def async_request_refresh(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary")
            self.data["mesh_nodes"][0]["firmware"] = "4.3.0"

    class Client:
        async def async_start_node_firmware_update(self, **kwargs: Any) -> bool:
            return True

    monkeypatch.setattr(update_module.asyncio, "sleep", _no_sleep)
    coordinator = Coordinator()
    entity = KeeneticMeshFirmwareUpdate(
        coordinator,
        SimpleNamespace(entry_id="entry_123", title="Router"),
        "AA:BB:CC:00:00:01",
        Client(),
    )

    asyncio.run(entity.async_install(version=None, backup=False))

    assert coordinator.calls == 3


def test_mesh_update_cancel_during_refresh_is_propagated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation during mesh refresh polling is never swallowed."""

    class Coordinator:
        data = {"mesh_nodes": [FALLBACK_MESH_NODE]}

        async def async_request_refresh(self) -> None:
            raise asyncio.CancelledError

    class Client:
        async def async_start_node_firmware_update(self, **kwargs: Any) -> bool:
            return True

    monkeypatch.setattr(update_module.asyncio, "sleep", _no_sleep)
    entity = KeeneticMeshFirmwareUpdate(
        Coordinator(),
        SimpleNamespace(entry_id="entry_123", title="Router"),
        "AA:BB:CC:00:00:01",
        Client(),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(entity.async_install(version=None, backup=False))


def test_mesh_update_unexpected_client_error_is_home_assistant_error() -> None:
    """Unexpected mesh update client errors are wrapped for HA services."""

    class Client:
        async def async_start_node_firmware_update(self, **kwargs: Any) -> bool:
            raise RuntimeError("boom")

    entity = KeeneticMeshFirmwareUpdate(
        SimpleNamespace(data={"mesh_nodes": [FALLBACK_MESH_NODE]}),
        SimpleNamespace(entry_id="entry_123", title="Router"),
        "AA:BB:CC:00:00:01",
        Client(),
    )

    with pytest.raises(HomeAssistantError, match="Mesh firmware update failed"):
        asyncio.run(entity.async_install(version=None, backup=False))


def test_mesh_firmware_update_fallback_node_uses_mac_for_mws_then_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback mesh nodes pass their MAC as the controller member token."""
    calls: list[dict[str, Any]] = []

    class Client:
        async def async_start_node_firmware_update(self, **kwargs: Any) -> bool:
            calls.append(kwargs)
            return True

    monkeypatch.setattr(update_module.asyncio, "sleep", _no_sleep)
    entry = SimpleNamespace(entry_id="entry_123", title="Router")
    coordinator = _Coordinator()
    entity = KeeneticMeshFirmwareUpdate(
        coordinator,
        entry,
        "AA:BB:CC:00:00:01",
        Client(),
    )

    asyncio.run(entity.async_install(version=None, backup=False))

    assert calls == [
        {
            "node_ip": "192.0.2.20",
            "node_name": "Kitchen Extender",
            "node_cid": "AA:BB:CC:00:00:01",
        }
    ]
