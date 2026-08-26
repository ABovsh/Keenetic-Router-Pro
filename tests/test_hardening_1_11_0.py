"""Regression tests for the 1.10.2 audit round."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from test_api_lifecycle import FakeResponse, FakeSession
from test_coordinator_update_flow import FakeKeeneticClient

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient
from custom_components.keenetic_router_pro.api.domains.wifi import WifiMixin
from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from custom_components.keenetic_router_pro.entity import MeshEntity

MAC = "AA:BB:CC:DD:EE:FF"


# --- 1. A stalled challenge-GET body read must not escape as TimeoutError ---


class _StallingBodyResponse(FakeResponse):
    """Headers arrive, then the router stops sending the body."""

    async def text(self) -> str:
        raise TimeoutError


def test_challenge_get_body_stall_raises_api_error() -> None:
    """The GET leg lacked the try/except the POST leg documents and has.

    A raw ``TimeoutError`` is not in ``async_setup_entry``'s except tuple, so
    it crashes setup instead of becoming a retryable ``ConfigEntryNotReady``.
    """
    get_resp = _StallingBodyResponse(
        503,
        headers={"X-NDM-Challenge": "challenge", "X-NDM-Realm": "Keenetic"},
    )
    client = KeeneticClient(
        TEST_HOST,
        TEST_USERNAME,
        TEST_PASSWORD,
        use_challenge_auth=True,
    )
    client._session = FakeSession([get_resp])

    with pytest.raises(KeeneticApiError):
        asyncio.run(client._async_authenticate_challenge())


# --- 2. A valid tx-power of 0 must survive the fallback-key lookup ---


class _Wifi(WifiMixin):
    pass


def test_wifi_tx_power_zero_is_published() -> None:
    """``a or b`` turned a legitimate minimum transmit power into None."""
    iface_list = [
        {
            "type": "accesspoint",
            "id": "AccessPoint0",
            "ssid": "Main",
            "state": "up",
            "tx-power": 0,
            "traits": ["wifi", "accesspoint"],
        }
    ]

    networks = asyncio.run(_Wifi().async_get_wifi_networks(iface_list=iface_list))

    assert [net["tx_power"] for net in networks] == [0]


# --- 3. The mesh fingerprint must ignore the keys the payload really has ---


class _DummyCoordinator:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def async_add_listener(self, *_a: Any, **_kw: Any):
        return lambda: None


def _mesh_node(**overrides: Any) -> dict[str, Any]:
    node = {
        "cid": "node-1",
        "connected": True,
        "ip": "192.0.2.20",
        "uptime": 100,
        "cpuload": 5,
        "memory": "51380224/268435456",
    }
    node.update(overrides)
    return node


def test_mesh_memory_only_tick_skips_state_write() -> None:
    """``memory`` moves every poll; the ignore set named keys that never exist."""
    coordinator = _DummyCoordinator({"mesh_nodes_by_cid": {"node-1": _mesh_node()}})
    entity = MeshEntity(coordinator, "entry", "Router", "node-1")
    writes: list[None] = []
    entity.async_write_ha_state = lambda: writes.append(None)  # type: ignore[method-assign]

    entity._handle_coordinator_update()
    coordinator.data["mesh_nodes_by_cid"]["node-1"] = _mesh_node(
        uptime=130, cpuload=7, memory="51396608/268435456"
    )
    entity._handle_coordinator_update()

    assert len(writes) == 1


def test_mesh_fingerprint_ignore_only_names_real_payload_keys() -> None:
    """Every ignored key must be one the mesh node dict actually carries."""
    produced = set(_mesh_node()) | {"associations", "rci_errors", "firmware"}

    assert MeshEntity._FINGERPRINT_IGNORE <= produced


# --- 4. A stale client snapshot must not invent presence transitions ---


def test_stale_client_snapshot_fires_no_presence_events() -> None:
    """The hotspot table is last tick's; diffing it against fresh ARP lies.

    ``ClientEntity.available`` and the device tracker already refuse to trust a
    ``clients_stale`` tick — the connect/disconnect diffs did not.
    """
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())
    coordinator.data = first
    assert first["online_clients"] == {MAC.lower()}

    async def fail_clients() -> list[dict[str, Any]]:
        raise RuntimeError("hotspot table unavailable")

    client.async_get_clients = fail_clients  # type: ignore[assignment]
    # The neighbour entry ages out in the very same tick.
    client.ip_neighbours = [
        {**client.ip_neighbours[0], "expired": True, "leasetime": 0}
    ]

    second = asyncio.run(coordinator._async_update_data())

    assert second["clients_stale"] is True
    assert second["disconnected_clients"] == set()
    assert second["connected_clients"] == set()
    # The baseline must survive too, or the next healthy tick fires a
    # phantom reconnect.
    assert second["online_clients"] == first["online_clients"]


def test_healthy_tick_still_reports_a_real_disconnect() -> None:
    """The stale guard must not swallow a genuine departure."""
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())
    coordinator.data = first

    client.clients = [{**client.clients[0], "active": False}]
    client.ip_neighbours = [{**client.ip_neighbours[0], "expired": True}]

    second = asyncio.run(coordinator._async_update_data())

    assert second["clients_stale"] is False
    assert second["disconnected_clients"] == {MAC.lower()}
