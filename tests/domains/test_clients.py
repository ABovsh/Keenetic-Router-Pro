"""Clients domain parser behavior."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient


def _client() -> KeeneticClient:
    return KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"host": [{"mac": "AA:BB:CC:00:00:01", "active": "yes"}]}, 1),
        ({"host": {"mac": "AA:BB:CC:00:00:01", "active": "yes"}}, 1),
        ({}, 0),
        ("bad", 0),
    ],
)
async def test_async_get_clients_accepts_list_dict_and_blank_payloads(
    payload: object, expected: int
) -> None:
    client = _client()
    client._rci_get = AsyncMock(return_value=payload)

    assert len(await client.async_get_clients()) == expected


async def test_async_get_ip_neighbours_filters_malformed_rows_and_requires_mac() -> None:
    client = _client()
    client._rci_get = AsyncMock(return_value={"neighbour": ["bad", {"mac": "AA-BB-CC-00-00-01", "address": "192.0.2.5"}, {"address": "192.0.2.6"}]})

    assert await client.async_get_ip_neighbours() == [
        {"mac": "AA-BB-CC-00-00-01", "address": "192.0.2.5"}
    ]


async def test_async_get_policies_handles_string_booleans_and_missing_keys() -> None:
    client = _client()
    client._rci_get = AsyncMock(return_value={"Policy0": {"description": "VPN"}, "Policy1": {}})

    assert await client.async_get_policies() == {"Policy0": "VPN", "Policy1": "Policy1"}


@pytest.mark.parametrize("exc", [KeeneticApiError("HTTP 503"), aiohttp.ClientError("boom"), asyncio.TimeoutError(), ValueError("bad json")])
async def test_host_policies_transient_errors_propagate(exc: Exception) -> None:
    """Transient failures must reach the coordinator so it can keep the
    previous snapshot instead of silently collapsing to {} (which blanked
    every policy select to Default until the next slow-tier refetch)."""
    client = _client()
    client._rci_get = AsyncMock(side_effect=exc)

    with pytest.raises(type(exc)):
        await client.async_get_host_policies()


async def test_host_policies_missing_endpoint_returns_empty() -> None:
    client = _client()
    client._rci_get = AsyncMock(side_effect=KeeneticApiError("HTTP 404: no such node"))

    assert await client.async_get_host_policies() == {}


async def test_async_get_policies_propagates_transient_api_failure() -> None:
    client = _client()
    client._rci_get = AsyncMock(side_effect=KeeneticApiError("HTTP 503"))

    with pytest.raises(KeeneticApiError):
        await client.async_get_policies()



async def test_async_get_clients_latches_winner_subpath_for_next_call() -> None:
    """After a hotspot subpath returns real data once, future calls try it first."""
    from custom_components.keenetic_router_pro.const import RCI_HOTSPOT_HOST_PATHS

    client = _client()
    winner_path = RCI_HOTSPOT_HOST_PATHS[-1]
    payload = {"host": [{"mac": "AA:BB:CC:00:00:01", "active": "yes"}]}

    call_log: list[str] = []

    async def fake_get(subpath: str, **_):
        call_log.append(subpath)
        return payload if subpath == winner_path else {}

    client._rci_get = fake_get

    items = await client.async_get_clients()
    assert items and items[0]["mac"] == "AA:BB:CC:00:00:01"
    assert client._hotspot_subpath_winner == winner_path
    assert call_log == list(RCI_HOTSPOT_HOST_PATHS)

    call_log.clear()
    items = await client.async_get_clients()
    assert items and items[0]["mac"] == "AA:BB:CC:00:00:01"
    assert call_log[0] == winner_path
    assert len(call_log) == 1


async def test_async_get_clients_raises_after_all_transient_subpath_failures() -> None:
    client = _client()
    client._rci_get = AsyncMock(side_effect=KeeneticApiError("HTTP 503"))

    with pytest.raises(KeeneticApiError, match="503"):
        await client.async_get_clients()
