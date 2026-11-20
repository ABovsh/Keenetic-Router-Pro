"""Client data off still feeds mesh discovery and router client events.

With per-client sensors off and no tracked clients nothing shows client
entities, but mesh discovery finds extenders in the client list and the
router device offers connect/disconnect triggers. Those consumers read the
client list on the slow tier; ticks in between reuse the last read without
inventing arrivals or departures.
"""

from __future__ import annotations

from typing import Any

from tests.test_coordinator_stages import StageFixtureClient, _coordinator, _updated_data

_PHONE = "aa:bb:cc:dd:ee:ff"


class _RecordingMeshClient(StageFixtureClient):
    def __init__(self) -> None:
        super().__init__()
        self.mesh_inputs: list[list[dict[str, Any]] | None] = []

    async def async_get_mesh_nodes(self, clients=None):
        self.mesh_inputs.append(clients)
        return await super().async_get_mesh_nodes(clients=clients)


async def _tick(coordinator, refresh_count: int) -> dict:
    coordinator._refresh_count = refresh_count
    data = await _updated_data(coordinator)
    coordinator.data = data
    return data


async def test_mesh_discovery_receives_the_client_list_with_client_data_off() -> None:
    client = _RecordingMeshClient()
    coordinator = _coordinator(client)
    coordinator.needs_client_data = False

    await _tick(coordinator, 0)

    assert client.calls.get("clients", 0) == 1
    assert client.mesh_inputs and client.mesh_inputs[0]


async def test_client_list_is_read_on_the_slow_tier_only() -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    coordinator.needs_client_data = False

    for count in range(0, 7):
        await _tick(coordinator, count)

    # First refresh plus refresh counts 3 and 6.
    assert client.calls.get("clients", 0) == 3


async def test_reused_client_list_fires_no_events_and_a_real_departure_does() -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    coordinator.needs_client_data = False

    await _tick(coordinator, 0)
    client.clients = [c for c in client.clients if c["mac"].lower().replace("-", ":") != _PHONE]
    between = await _tick(coordinator, 1)
    assert between["connected_clients"] == set()
    assert between["disconnected_clients"] == set()
    assert _PHONE in between["online_clients"]

    after = await _tick(coordinator, 3)
    assert _PHONE in after["disconnected_clients"]


async def test_client_data_on_still_reads_clients_every_tick() -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    coordinator.needs_client_data = True

    for count in range(0, 4):
        await _tick(coordinator, count)

    assert client.calls.get("clients", 0) == 4
