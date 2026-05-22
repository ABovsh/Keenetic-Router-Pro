"""DNS domain parser behavior."""

from __future__ import annotations

from tests.conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient


@pytest.mark.parametrize(
    ("payload", "status", "doh_count"),
    [
        ({"proxy-status": [{"proxy-name": "main", "proxy-config": "server https://dns.example/id", "proxy-stat": "1.1.1.1 53 2 1 0 5ms 6ms 10", "proxy-https": {"server-https": {"uri": "https://dns.example/private/path"}}}]}, "degraded", 1),
        ({"proxy-status": []}, "unknown", 0),
        ({"proxy-status": "bad"}, None, 0),
        ({}, "unknown", 0),
    ],
)
async def test_async_get_dns_proxy_status_normalizes_shapes(
    payload: object, status: str | None, doh_count: int
) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(return_value=payload)

    result = await client.async_get_dns_proxy_status()

    if status is None:
        assert result == {}
    else:
        assert result["status"] == status
        assert result["doh_server_count"] == doh_count
        assert "private" not in str(result)


@pytest.mark.parametrize("exc", [KeeneticApiError("boom"), aiohttp.ClientError("boom"), asyncio.TimeoutError(), ValueError("bad json")])
async def test_async_get_dns_proxy_status_errors_return_empty(exc: Exception) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(side_effect=exc)

    assert await client.async_get_dns_proxy_status() == {}

