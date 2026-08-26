"""Hardening tests for the 1.7.76 deep-audit round.

One test per verified defect:

* R1-F01 — a fingerprinted client entity must publish the availability
  change when the coordinator starts serving a preserved (stale) client
  snapshot. The client row itself is byte-identical on that tick, so the
  fingerprint dedup suppressed the write and ``ClientEntity.available``
  never reached Home Assistant.
* R1-F02 — the post-write confirming refresh must actually re-fetch the
  tier-gated data the write entities read. ``async_request_refresh()`` on a
  fast tick republished the cached medium/slow snapshot, so a toggled
  switch snapped back to its old position until the next tier tick.
"""

from __future__ import annotations

import asyncio
from typing import Any

from test_coordinator_update_flow import FakeKeeneticClient

from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from custom_components.keenetic_router_pro.entity import ClientEntity

MAC = "aa:bb:cc:dd:ee:ff"


def _client_row() -> dict[str, Any]:
    return {"mac": MAC, "ip": "192.0.2.40", "name": "Phone", "link": "up"}


def test_client_entity_publishes_availability_when_snapshot_goes_stale(
    keenetic_coordinator_factory,
) -> None:
    """R1-F01: preserved-snapshot availability must reach HA.

    A transient ``clients`` fetch failure keeps the previous client list and
    flags ``clients_stale``. The client dict is unchanged, so the fingerprint
    matches and the state write is suppressed — leaving the entity published
    as available while serving data the coordinator itself marked stale.
    """
    coordinator = keenetic_coordinator_factory(
        {"clients_by_mac": {MAC: _client_row()}}
    )
    entity = ClientEntity(coordinator, "entry_123", "Router", MAC, "Phone")

    writes: list[bool] = []
    entity.async_write_ha_state = lambda: writes.append(entity.available)

    entity._handle_coordinator_update()
    assert entity.available is True
    assert writes == [True]
    writes.clear()

    # Same row, but the coordinator is now serving a preserved snapshot.
    coordinator.data = {
        "clients_by_mac": {MAC: _client_row()},
        "clients_stale": True,
    }
    entity._handle_coordinator_update()

    assert entity.available is False
    assert writes == [False]


def test_fingerprinted_entity_publishes_unavailable_on_coordinator_outage(
    keenetic_coordinator_factory,
) -> None:
    """R1-F01: the same suppression hid the outage transition.

    ``DataUpdateCoordinator`` notifies listeners on a FAILED refresh too, with
    ``data`` unchanged — so the fingerprint matched, the write was skipped and
    the entity stayed published as available for the whole outage.
    """
    coordinator = keenetic_coordinator_factory(
        {"clients_by_mac": {MAC: _client_row()}}
    )
    entity = ClientEntity(coordinator, "entry_123", "Router", MAC, "Phone")

    writes: list[bool] = []
    entity.async_write_ha_state = lambda: writes.append(entity.available)

    entity._handle_coordinator_update()
    writes.clear()

    coordinator.last_update_success = False
    entity._handle_coordinator_update()
    assert writes == [False]

    # ...and the recovery transition must publish too.
    writes.clear()
    coordinator.last_update_success = True
    entity._handle_coordinator_update()
    assert writes == [True]


def test_client_entity_still_suppresses_writes_when_nothing_changed(
    keenetic_coordinator_factory,
) -> None:
    """R1-F01 guard: the volatile-field write suppression must survive."""
    coordinator = keenetic_coordinator_factory(
        {"clients_by_mac": {MAC: _client_row()}}
    )
    entity = ClientEntity(coordinator, "entry_123", "Router", MAC, "Phone")

    writes: list[bool] = []
    entity.async_write_ha_state = lambda: writes.append(entity.available)

    entity._handle_coordinator_update()
    writes.clear()

    coordinator.data = {
        "clients_by_mac": {MAC: {**_client_row(), "last-seen": 42, "uptime": 7}}
    }
    entity._handle_coordinator_update()

    assert writes == []


def test_request_full_refresh_forces_tier_gated_fetches_on_next_tick() -> None:
    """R1-F02: a confirming refresh must re-fetch the medium/slow tiers.

    Wi-Fi / VPN / WAN / crypto-map switches read data that is only rebuilt on
    a medium or slow tick, so the refresh they trigger after writing to the
    router republished the stale snapshot and the toggle snapped back.
    """
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())

    # Baseline: a plain fast tick keeps the cached medium/slow snapshots.
    coordinator.data = {**first, "wifi": [{"stale": True}], "mesh_nodes": ["stale"]}
    coordinator._refresh_count = 1  # 1 % 2 and 1 % 3 are both non-zero
    second = asyncio.run(coordinator._async_update_data())
    assert second["wifi"] == [{"stale": True}]
    assert second["mesh_nodes"] == ["stale"]

    # With the one-shot request armed, the same fast tick refetches them.
    coordinator.data = {**second, "wifi": [{"stale": True}], "mesh_nodes": ["stale"]}
    coordinator._refresh_count = 5
    coordinator.request_full_refresh()
    third = asyncio.run(coordinator._async_update_data())
    assert third["wifi"] == [{"id": "WifiMaster0/AccessPoint0", "ssid": "Main"}]
    assert third["mesh_nodes"] != ["stale"]

    # One-shot: the next fast tick caches again.
    coordinator.data = {**third, "wifi": [{"stale": True}]}
    coordinator._refresh_count = 7
    fourth = asyncio.run(coordinator._async_update_data())
    assert fourth["wifi"] == [{"stale": True}]


def test_pending_full_refresh_survives_a_failing_tick() -> None:
    """R1-F02: a tick that raises must keep the one-shot request armed."""
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())
    coordinator.data = {**first, "wifi": [{"stale": True}]}
    coordinator._refresh_count = 1
    coordinator.request_full_refresh()

    async def failing_system_info() -> dict[str, Any]:
        raise asyncio.TimeoutError("transient")

    original_system_info = client.async_get_system_info
    client.async_get_system_info = failing_system_info  # type: ignore[assignment]
    # Exhaust the grace window so the tick actually raises.
    coordinator._critical_fail_streak = 99
    try:
        asyncio.run(coordinator._async_update_data())
    except Exception:  # noqa: BLE001 — the tick is expected to fail
        pass
    client.async_get_system_info = original_system_info  # type: ignore[assignment]

    assert coordinator._full_refresh_pending is True


def test_async_request_refresh_arms_the_one_shot_full_refresh() -> None:
    """R1-F02: every post-write caller routes through async_request_refresh."""
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    assert coordinator._full_refresh_pending is False
    asyncio.run(coordinator.async_request_refresh())
    assert coordinator._full_refresh_pending is True
