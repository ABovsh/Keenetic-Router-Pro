"""First-seen client events from fresh hotspot observations."""

import asyncio
from collections import Counter
from copy import deepcopy

import pytest

from tests.test_refresh_request_log import _expected, _get, _router, _tick

_PHONE = "aa:bb:cc:dd:ee:ff"
_HOSTS = "show/ip/hotspot/host"
_FALLBACK_HOSTS = "ip/hotspot/host"
_NEW_MAC = "02:00:00:00:00:01"


@pytest.fixture(params=[True, False], ids=["batch", "405-fallback"])
def batch(request: pytest.FixtureRequest) -> bool:
    return request.param


async def test_first_hotspot_observation_seeds_without_events(batch: bool) -> None:
    coordinator, session = await _router(batch)

    assert await _tick(coordinator, session) == _expected(batch, "startup", "startup")
    assert _PHONE in coordinator.data["online_clients"]
    assert coordinator.data["new_clients"] == set()
    assert coordinator.data["connected_clients"] == set()


@pytest.mark.parametrize("status", [503, 404], ids=["hotspot-outage", "hotspot-unsupported"])
async def test_startup_without_hotspot_waits_for_first_fresh_observation(
    batch: bool, status: int,
) -> None:
    coordinator, session = await _router(batch)
    coordinator.needs_policies = False
    session.status.update({"show/ip/hotspot": status, _HOSTS: status, _FALLBACK_HOSTS: status})
    startup_reads = _expected(batch, "startup", "startup") - Counter(
        [_get("ip/policy"), _get("show/mws/member")]
    )
    if batch:
        startup_reads[_get(_HOSTS)] += 1
    if status == 503:
        del startup_reads[_get(_FALLBACK_HOSTS)]

    assert await _tick(coordinator, session) == startup_reads
    data = coordinator.data
    assert data["clients_by_mac"][_PHONE]["neighbour-only"]
    if status == 503:
        assert data["clients_stale"] is True
    assert data["new_clients"] == set()
    assert data["connected_clients"] == set()

    session.status.clear()
    if status == 404:
        coordinator.client._hotspot_subpath_skip.clear()
    assert await _tick(coordinator, session) == _expected(batch, "fast")
    assert coordinator.data["clients_stale"] is False
    assert coordinator.data["new_clients"] == set()
    assert _PHONE in coordinator.data["connected_clients"]


async def test_partial_startup_must_not_seed() -> None:
    coordinator, session = await _router(False)
    coordinator.needs_policies = False
    session.status[_HOSTS] = 503
    session.payloads[_FALLBACK_HOSTS] = {"host": []}
    await _tick(coordinator, session)
    assert coordinator.data["new_clients"] == set()

    del session.status[_HOSTS]
    await _tick(coordinator, session)
    assert _PHONE in coordinator.data["online_clients"]
    assert coordinator.data["new_clients"] == set()


async def test_partial_hotspot_must_not_publish_false_departure() -> None:
    coordinator, session = await _router(False)
    await _tick(coordinator, session)
    assert _PHONE in coordinator.data["online_clients"]

    session.status[_HOSTS] = 503
    session.payloads[_FALLBACK_HOSTS] = {"host": []}
    await _tick(coordinator, session)
    assert _PHONE not in coordinator.data["disconnected_clients"]
    assert _PHONE in coordinator.data["online_clients"]
    assert coordinator.data["clients_stale"] is True


async def test_partial_nonempty_config_must_not_emit_new_device() -> None:
    coordinator, session = await _router(False)
    coordinator.needs_policies = False
    await _tick(coordinator, session)
    session.status[_HOSTS] = 503
    session.payloads[_FALLBACK_HOSTS] = {"host": [{"mac": _NEW_MAC, "policy": "Guest"}]}
    await _tick(coordinator, session)

    assert _NEW_MAC not in coordinator.data["new_clients"]
    assert _NEW_MAC not in coordinator._seen_hotspot_macs


async def test_hotspot_absence_latch_reset_must_not_seed_from_skipped_read() -> None:
    coordinator, session = await _router(False)
    coordinator.needs_policies = False
    session.status.update({_HOSTS: 404, _FALLBACK_HOSTS: 404})
    await _tick(coordinator, session)
    assert coordinator._seen_hotspot_macs is None
    assert coordinator.client._hotspot_subpath_skip == {_HOSTS, _FALLBACK_HOSTS}

    session.pause_system = True
    session.payloads["show/system"] = dict(session.payloads["show/system"], uptime=30)
    session.status.clear()
    async with asyncio.timeout(2):
        running = asyncio.create_task(_tick(coordinator, session))
        await session.entered.wait()
        # The hosts read skips both endpoints before the system read resets the latches.
        await session.wait_for_log(Counter([_get("show/ip/neighbour")]))
        session.resume.set()
        requests = await running
    assert _get(_HOSTS) not in requests
    assert _get(_FALLBACK_HOSTS) not in requests
    assert not coordinator.client._hotspot_subpath_skip

    await _tick(coordinator, session)
    assert coordinator.data["new_clients"] == set()


async def test_seen_client_can_leave_and_rejoin_without_second_new_device(batch: bool) -> None:
    coordinator, session = await _router(batch)
    assert await _tick(coordinator, session) == _expected(batch, "startup", "startup")
    session.payloads = deepcopy(session.payloads)
    mac = "12:34:56:78:9a:bc"
    hosts = session.payloads[_HOSTS]["host"]
    session.payloads["show/ip/neighbour"]["neighbour"].append(
        {"mac": mac, "address-family": "ipv4", "address": "192.0.2.90"}
    )
    hosts.append({"mac": mac, "active": True})

    assert await _tick(coordinator, session) == _expected(batch, "fast")
    assert coordinator.data["new_clients"] == {mac}
    assert coordinator.data["connected_clients"] == {mac}

    departed = hosts.pop()
    assert await _tick(coordinator, session) == _expected(batch, "fast", "stats")
    assert coordinator.data["clients_by_mac"][mac]["neighbour-only"]
    assert coordinator.data["disconnected_clients"] == {mac}
    assert coordinator.data["new_clients"] == set()

    hosts.append(departed)
    assert await _tick(coordinator, session) == _expected(batch, "slow")
    assert coordinator.data["connected_clients"] == {mac}
    assert coordinator.data["new_clients"] == set()


async def test_new_coordinator_starts_a_new_first_seen_history(batch: bool) -> None:
    coordinator, session = await _router(batch)
    assert await _tick(coordinator, session) == _expected(batch, "startup", "startup")
    assert _PHONE in coordinator.data["online_clients"]

    reloaded, session = await _router(batch)
    session.payloads = deepcopy(session.payloads)
    hosts = session.payloads[_HOSTS]["host"]
    phone = hosts.pop(0)
    assert await _tick(reloaded, session) == _expected(batch, "startup", "startup")
    assert reloaded.data["clients_by_mac"][_PHONE]["neighbour-only"]
    assert reloaded.data["new_clients"] == set()
    assert reloaded.data["connected_clients"] == set()

    hosts.append(phone)
    assert await _tick(reloaded, session) == _expected(batch, "fast")
    assert reloaded.data["connected_clients"] == {_PHONE}
    assert reloaded.data["new_clients"] == {_PHONE}
