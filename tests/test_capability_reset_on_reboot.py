"""A router reboot must not leave capability latches stuck for the session.

While KeeneticOS boots, runtime RCI endpoints such as
``show/ip/hotspot/host`` and ``show/mws/member`` can answer "not found"
for a short time. The capability caches latch that answer for the whole HA
session. Observed on a KN-1811 on 2026-10-02: after one reboot the runtime
hotspot path stayed latched off, so the client list was read from the config
tree. Every client then showed as disconnected, and the mesh node disappeared
until the config entry was reloaded.

``async_get_system_info`` sees the router uptime on every tick. If uptime
goes down, or the router is still inside its boot window, the latches must
be re-probed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from custom_components.keenetic_router_pro.api import KeeneticClient
from custom_components.keenetic_router_pro.const import CAPABILITY_BOOT_GRACE_S
from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

HOTSPOT_RUNTIME_PATH = "show/ip/hotspot/host"


def _client() -> KeeneticClient:
    return KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)


def _latch_boot_answers(client: KeeneticClient) -> None:
    client._hotspot_subpath_skip = {HOTSPOT_RUNTIME_PATH}
    client._hotspot_subpath_winner = "ip/hotspot/host"
    client._mws_member_supported = False


async def _system(client: KeeneticClient, uptime: object) -> dict:
    client._rci_get = AsyncMock(return_value={"hostname": "r", "uptime": uptime})
    return await client.async_get_system_info()


async def test_uptime_drop_resets_latches_set_while_booting() -> None:
    client = _client()
    await _system(client, "115220")
    _latch_boot_answers(client)

    await _system(client, str(CAPABILITY_BOOT_GRACE_S + 5000))

    assert client._hotspot_subpath_skip == set()
    assert client._hotspot_subpath_winner is None
    assert client._mws_member_supported is None


async def test_latch_inside_boot_window_is_reprobed_next_tick() -> None:
    client = _client()
    await _system(client, 49)
    _latch_boot_answers(client)

    await _system(client, 109)

    assert HOTSPOT_RUNTIME_PATH not in client._hotspot_subpath_skip
    assert client._mws_member_supported is None


async def test_settled_router_keeps_its_latches() -> None:
    client = _client()
    await _system(client, CAPABILITY_BOOT_GRACE_S + 100)
    _latch_boot_answers(client)

    await _system(client, CAPABILITY_BOOT_GRACE_S + 160)

    assert client._hotspot_subpath_skip == {HOTSPOT_RUNTIME_PATH}
    assert client._mws_member_supported is False


async def test_unparseable_uptime_changes_nothing() -> None:
    client = _client()
    await _system(client, 115220)
    _latch_boot_answers(client)

    await _system(client, None)
    await _system(client, "n/a")

    assert client._hotspot_subpath_skip == {HOTSPOT_RUNTIME_PATH}
    assert client._mws_member_supported is False


async def test_clients_recover_runtime_path_after_boot_latch() -> None:
    client = _client()
    await _system(client, 115220)
    _latch_boot_answers(client)
    await _system(client, 70)

    runtime = {"host": [{"mac": "50:ff:20:00:15:af", "system-mode": "extender", "active": True}]}
    client._rci_get = AsyncMock(return_value=runtime)

    clients = await client.async_get_clients()

    assert client._rci_get.await_args_list[0].args[0] == HOTSPOT_RUNTIME_PATH
    assert clients[0]["system-mode"] == "extender"
