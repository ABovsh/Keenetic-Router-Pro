"""A router write re-reads what it changed instead of every fetch tier.

A policy selection confirms the client's policy assignment; a mesh firmware
progress check reads the mesh membership. Neither forces the interface,
DNS, IPsec or policy-list reads of a full refresh, and neither consumes the
next scheduled tier.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import custom_components.keenetic_router_pro.update as update_module
from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from custom_components.keenetic_router_pro.coordinator_parts.refresh import (
    PendingRefreshes,
    build_batch_tree,
    refresh_plan,
)
from custom_components.keenetic_router_pro.select import KeeneticClientPolicySelect
from custom_components.keenetic_router_pro.update import (
    KeeneticFirmwareUpdate,
    KeeneticMeshFirmwareUpdate,
)
from tests.test_coordinator_stages import StageFixtureClient, _coordinator, _updated_data
from tests.test_coordinator_update_flow import FakeKeeneticClient

_INCIDENTAL = ("ndns", "ipsec_status", "policies", "wifi", "wireguard", "current_version")


async def _warm(client: StageFixtureClient):
    coordinator = _coordinator(client)
    for attr in ("_full_refresh_token", "_host_policies_refresh_token", "_mesh_refresh_token"):
        setattr(coordinator, attr, 0)
    coordinator.data = await _updated_data(coordinator)
    coordinator._refresh_count = 1
    client.calls.clear()
    return coordinator


def test_scoped_request_does_not_arm_a_full_refresh() -> None:
    coordinator = KeeneticCoordinator(object(), FakeKeeneticClient())  # type: ignore[arg-type]
    asyncio.run(coordinator.async_request_refresh(full=False))
    assert coordinator._full_refresh_pending is False


async def test_policy_confirmation_reads_only_the_assignment() -> None:
    client = StageFixtureClient()
    coordinator = await _warm(client)

    coordinator.request_host_policies_refresh()
    await _updated_data(coordinator)

    assert client.calls.get("host_policies", 0) == 1
    assert {name: client.calls.get(name, 0) for name in (*_INCIDENTAL, "mesh_nodes")} == dict.fromkeys(
        (*_INCIDENTAL, "mesh_nodes"), 0
    )


async def test_failed_host_policy_read_retries_only_that_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StageFixtureClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]
    coordinator.data = await _updated_data(coordinator)
    previous = coordinator.data["host_policies"]
    assert previous
    coordinator._refresh_count = 7

    read = AsyncMock(side_effect=[TimeoutError(), {}])
    monkeypatch.setattr(client, "async_get_host_policies", read)
    coordinator.request_host_policies_refresh()
    coordinator.data = await _updated_data(coordinator)
    assert coordinator.data["host_policies"] == previous

    client.calls.clear()
    coordinator.data = await _updated_data(coordinator)
    assert read.await_count == 2
    assert coordinator.data["host_policies"] == {}
    for name in ("policies", "ndns", "current_version"):
        assert name not in client.calls

    coordinator._refresh_count = 11
    await _updated_data(coordinator)
    assert read.await_count == 2


async def test_failed_policy_list_waits_for_its_regular_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StageFixtureClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]
    coordinator.data = await _updated_data(coordinator)
    previous = coordinator.data["policies"]
    coordinator._refresh_count = 15

    read = AsyncMock(side_effect=[TimeoutError(), {}])
    monkeypatch.setattr(client, "async_get_policies", read)
    coordinator.data = await _updated_data(coordinator)
    assert coordinator.data["policies"] == previous

    while coordinator._refresh_count < 30:
        coordinator.data = await _updated_data(coordinator)
        assert read.await_count == 1
        assert coordinator.data["policies"] == previous

    coordinator.data = await _updated_data(coordinator)
    assert read.await_count == 2
    assert coordinator.data["policies"] == {}


@pytest.mark.parametrize("requested", [False, True], ids=["daily", "requested"])
async def test_failed_update_check_retries_on_very_slow_tier(
    monkeypatch: pytest.MonkeyPatch, requested: bool
) -> None:
    client = StageFixtureClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]
    coordinator.data = await _updated_data(coordinator)
    keys = ("release-available", "fw-update-sandbox", "fw-update-available")
    previous = tuple(coordinator.data["system"][key] for key in keys)
    assert previous == ("4.3.0", "stable", True)

    failed_tick, retry_tick = (7, 15) if requested else (1440, 1455)
    coordinator._refresh_count = failed_tick
    if requested:
        coordinator.request_update_check()
    recovery = {"release": "4.4.0", "sandbox": "preview", "update-available": True}
    read = AsyncMock(side_effect=[TimeoutError(), TimeoutError(), recovery])
    monkeypatch.setattr(client, "async_get_available_version_info", read)
    coordinator.data = await _updated_data(coordinator)

    # A full confirming refresh also runs the very-slow reads, off cadence.
    coordinator.request_full_refresh()
    for expected_calls, next_tick in [(1, retry_tick), (2, retry_tick + 15)]:
        while coordinator._refresh_count < next_tick:
            assert tuple(coordinator.data["system"][key] for key in keys) == previous
            coordinator.data = await _updated_data(coordinator)
            assert read.await_count == expected_calls
        coordinator.data = await _updated_data(coordinator)
        assert read.await_count == expected_calls + 1

    assert tuple(coordinator.data["system"][key] for key in keys) == ("4.4.0", "preview", True)
    coordinator._refresh_count = retry_tick + 30
    coordinator.data = await _updated_data(coordinator)
    assert read.await_count == 3


async def test_first_failed_update_check_leaves_latest_version_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StageFixtureClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]
    monkeypatch.setattr(
        client,
        "async_get_available_version_info",
        AsyncMock(side_effect=TimeoutError()),
    )
    coordinator.data = await _updated_data(coordinator)

    entry = SimpleNamespace(entry_id="e1", title="Router")
    entity = KeeneticFirmwareUpdate(coordinator, entry, client)
    assert entity.installed_version == "4.2.1"
    assert entity.latest_version is None


@pytest.mark.parametrize(
    ("method", "request_method", "fails"),
    [
        ("async_get_host_policies", "request_host_policies_refresh", False),
        ("async_get_available_version_info", "request_update_check", False),
        ("async_get_available_version_info", "request_update_check", True),
    ],
)
async def test_newer_request_during_read_gets_its_own_read(
    monkeypatch: pytest.MonkeyPatch, method: str, request_method: str, fails: bool
) -> None:
    client = StageFixtureClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]
    coordinator.data = await _updated_data(coordinator)
    coordinator._refresh_count = 7
    request_refresh = getattr(coordinator, request_method)
    original = getattr(client, method)
    entered = asyncio.Event()
    resume = asyncio.Event()

    async def read() -> dict:
        if not entered.is_set():
            entered.set()
            async with asyncio.timeout(1):
                await resume.wait()
            if fails:
                raise TimeoutError
        return await original()

    read_mock = AsyncMock(side_effect=read)
    monkeypatch.setattr(client, method, read_mock)
    request_refresh()
    async with asyncio.TaskGroup() as tasks:
        pending_tick = tasks.create_task(_updated_data(coordinator))
        async with asyncio.timeout(1):
            await entered.wait()
        request_refresh()
        resume.set()
    coordinator.data = pending_tick.result()

    coordinator.data = await _updated_data(coordinator)
    assert read_mock.await_count == 2
    coordinator._refresh_count = 11
    await _updated_data(coordinator)
    assert read_mock.await_count == 2


async def test_mesh_progress_check_reads_the_mesh_once() -> None:
    client = StageFixtureClient()
    coordinator = await _warm(client)

    coordinator.request_mesh_refresh()
    data = await _updated_data(coordinator)
    assert client.calls.get("mesh_nodes", 0) == 1
    assert data["mesh_nodes"]
    assert {name: client.calls.get(name, 0) for name in (*_INCIDENTAL, "host_policies")} == dict.fromkeys(
        (*_INCIDENTAL, "host_policies"), 0
    )

    coordinator.data = data
    coordinator._refresh_count = 2
    await _updated_data(coordinator)
    assert client.calls.get("mesh_nodes", 0) == 1


async def test_mesh_progress_check_reads_clients_when_client_data_is_off() -> None:
    client = StageFixtureClient()
    coordinator = await _warm(client)
    coordinator.needs_client_data = False

    coordinator.request_mesh_refresh()
    await _updated_data(coordinator)

    assert client.calls.get("clients", 0) == 1
    assert client.calls.get("mesh_nodes", 0) == 1


def test_batch_tree_carries_the_requested_mesh_and_update_check_paths() -> None:
    fast = refresh_plan(first_refresh=False, refresh_count=1)
    plain = build_batch_tree(fast, include_mesh=True)
    assert "mws" not in plain.get("show", {})
    assert "components" not in plain

    scoped_plan = refresh_plan(
        first_refresh=False,
        refresh_count=1,
        pending=PendingRefreshes(mesh=1, update_check_pending=True, update_check_token=1),
    )
    scoped = build_batch_tree(scoped_plan, include_mesh=True)
    assert "member" in scoped["show"]["mws"]
    assert "check-update" in scoped["components"]


def test_policy_select_confirms_with_a_scoped_refresh() -> None:
    requests: list[Any] = []

    async def async_request_refresh(**kwargs) -> None:
        requests.append(kwargs)

    coordinator = SimpleNamespace(
        data={"host_policies": {}, "policies": {"Policy1": "VPN"}},
        async_request_refresh=async_request_refresh,
        request_host_policies_refresh=lambda: requests.append("assignment"),
        async_add_listener=lambda *_a, **_k: lambda: None,
    )

    class _Api:
        async def async_set_client_policy(self, mac: str, policy: str) -> None:
            pass

    entry = SimpleNamespace(entry_id="e1", title="Router")
    select = KeeneticClientPolicySelect(
        coordinator=coordinator,
        entry=entry,
        api_client=_Api(),
        mac="aa:bb:cc:dd:ee:ff",
        label="Phone",
        initial_ip=None,
        policies={"Policy1": "VPN"},
    )
    select.async_write_ha_state = lambda: None
    asyncio.run(select.async_select_option("Default"))

    assert requests == ["assignment", {"full": False}]


def test_mesh_install_reads_mesh_before_requesting_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[Any] = []

    class _Coordinator:
        data = {
            "mesh_nodes": [
                {"cid": "node-1", "name": "Extender", "ip": "192.0.2.20",
                 "firmware": "4.2.0", "firmware_available": "4.3.0"}
            ]
        }
        client = SimpleNamespace(_host="192.0.2.1", _ssl=False)

        def async_add_listener(self, *_a, **_k):
            return lambda: None

        def request_update_check(self) -> None:
            requests.append("update_check")

        async def async_request_refresh(self, **kwargs) -> None:
            requests.append(kwargs)

    class _Client:
        async def async_start_node_firmware_update(self, **_kwargs) -> bool:
            return True

        async def async_get_mesh_nodes(self) -> list[dict[str, Any]]:
            requests.append("mesh")
            return [{"cid": "node-1", "connected": True, "firmware": "4.3.0"}]

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(update_module.asyncio, "sleep", _no_sleep)
    entity = KeeneticMeshFirmwareUpdate(
        _Coordinator(), SimpleNamespace(entry_id="e1", title="Router"), "node-1", _Client()
    )
    asyncio.run(entity.async_install(version=None, backup=False))

    assert requests == ["mesh", "update_check", {}]
