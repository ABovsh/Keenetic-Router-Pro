"""One refresh reads the router's host table once for all of its consumers.

On firmware where client discovery falls back to ``ip/hotspot/host``, the
policy-assignment read wants the same table. Inside one refresh both parse a
single response; outside a refresh every call reads the router again.
"""

from __future__ import annotations

import asyncio

import pytest

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient

_HOSTS = {
    "host": [
        {"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.0.2.10", "policy": "Policy1", "access": "permit"}
    ]
}


def _client(responses: list) -> tuple[KeeneticClient, list[str]]:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._hotspot_subpath_winner = "ip/hotspot/host"
    gets: list[str] = []

    async def _request(method: str, path: str, **_kwargs):
        gets.append(path)
        await asyncio.sleep(0)
        result = responses.pop(0) if len(responses) > 1 else responses[0]
        if isinstance(result, BaseException):
            raise result
        return result

    client._request = _request  # type: ignore[assignment]
    return client, gets


async def _both(client: KeeneticClient):
    return await asyncio.gather(client.async_get_clients(), client.async_get_host_policies())


async def test_concurrent_consumers_share_one_read_within_a_refresh() -> None:
    client, gets = _client([_HOSTS])
    client.begin_tick()
    clients, policies = await _both(client)
    client.clear_tick_cache()

    assert gets == ["/rci/ip/hotspot/host"]
    assert clients[0]["mac"] == "aa:bb:cc:dd:ee:ff"
    assert policies["aa:bb:cc:dd:ee:ff"]["policy"] == "Policy1"


async def test_reads_outside_a_refresh_are_not_shared() -> None:
    client, gets = _client([_HOSTS])
    await _both(client)
    client.begin_tick()
    client.clear_tick_cache()
    await client.async_get_host_policies()

    assert len(gets) == 3


async def test_each_refresh_reads_fresh_data() -> None:
    client, gets = _client([_HOSTS])
    for _ in range(2):
        client.begin_tick()
        await _both(client)
        client.clear_tick_cache()

    assert len(gets) == 2


async def test_a_failed_shared_read_is_retried_on_the_next_refresh() -> None:
    client, gets = _client([KeeneticApiError("Connection error"), _HOSTS])
    client.begin_tick()
    with pytest.raises(KeeneticApiError):
        await client.async_get_host_policies()
    client.clear_tick_cache()

    client.begin_tick()
    policies = await client.async_get_host_policies()
    client.clear_tick_cache()

    assert gets == ["/rci/ip/hotspot/host", "/rci/ip/hotspot/host"]
    assert policies["aa:bb:cc:dd:ee:ff"]["policy"] == "Policy1"


async def test_cancelling_one_consumer_does_not_cancel_the_other() -> None:
    client, _gets = _client([_HOSTS])
    client.begin_tick()
    first = asyncio.ensure_future(client.async_get_host_policies())
    second = asyncio.ensure_future(client.async_get_clients())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert (await second)[0]["mac"] == "aa:bb:cc:dd:ee:ff"
    client.clear_tick_cache()
