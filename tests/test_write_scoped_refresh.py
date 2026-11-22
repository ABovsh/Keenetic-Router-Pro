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

import pytest

import custom_components.keenetic_router_pro.update as update_module
from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from custom_components.keenetic_router_pro.coordinator_parts.refresh import (
    build_batch_tree,
    refresh_plan,
)
from custom_components.keenetic_router_pro.select import KeeneticClientPolicySelect
from custom_components.keenetic_router_pro.update import KeeneticMeshFirmwareUpdate
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

    scoped = build_batch_tree(fast, include_mesh=True, mesh_refresh=True, update_check=True)
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
