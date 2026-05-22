"""Clients domain parser behavior."""

from __future__ import annotations

from tests.conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

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


@pytest.mark.parametrize("exc", [KeeneticApiError("boom"), aiohttp.ClientError("boom"), asyncio.TimeoutError(), ValueError("bad json")])
async def test_clients_error_paths_return_empty_shapes(exc: Exception) -> None:
    client = _client()
    client._rci_get = AsyncMock(side_effect=exc)

    assert await client.async_get_policies() == {}
    assert await client.async_get_host_policies() == {}

