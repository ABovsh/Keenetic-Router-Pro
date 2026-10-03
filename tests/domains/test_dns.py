"""DNS domain parser behavior."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient


@pytest.mark.parametrize(
    ("payload", "status", "doh_count"),
    [
        # Small sample with failures: below the 20-query minimum -> "ok".
        ({"proxy-status": [{"proxy-name": "main", "proxy-config": "server https://dns.example/id", "proxy-stat": "1.1.1.1 53 2 1 0 5ms 6ms 10", "proxy-https": {"server-https": {"uri": "https://dns.example/private/path"}}}]}, "ok", 1),
        # 10 % unanswered is race-loser noise on a healthy resolver -> ok.
        ({"proxy-status": [{"proxy-name": "main", "proxy-config": "server https://dns.example/id", "proxy-stat": "1.1.1.1 53 100 90 0 5ms 6ms 10", "proxy-https": {"server-https": {"uri": "https://dns.example/private/path"}}}]}, "ok", 1),
        # An upstream answering under half of >=20 queries -> degraded.
        ({"proxy-status": [{"proxy-name": "main", "proxy-config": "server https://dns.example/id", "proxy-stat": "1.1.1.1 53 100 40 0 5ms 6ms 10", "proxy-https": {"server-https": {"uri": "https://dns.example/private/path"}}}]}, "degraded", 1),
        # Large sample but failure rate <5% -> ok.
        ({"proxy-status": [{"proxy-name": "main", "proxy-config": "server https://dns.example/id", "proxy-stat": "1.1.1.1 53 200 198 0 5ms 6ms 10", "proxy-https": {"server-https": {"uri": "https://dns.example/private/path"}}}]}, "ok", 1),
        # Traffic flowing but zero answers -> down.
        ({"proxy-status": [{"proxy-name": "main", "proxy-config": "server https://dns.example/id", "proxy-stat": "1.1.1.1 53 25 0 0 5ms 6ms 10", "proxy-https": {"server-https": {"uri": "https://dns.example/private/path"}}}]}, "down", 1),
        ({"proxy-status": []}, "unknown", 0),
        # Malformed (non-list/dict) proxy-status degrades to "no proxies".
        ({"proxy-status": "bad"}, "unknown", 0),
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
async def test_async_get_dns_proxy_status_transient_errors_reach_the_coordinator(exc: Exception) -> None:
    """The coordinator keeps its previous snapshot; an empty one read as unknown."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(side_effect=exc)

    with pytest.raises(type(exc)):
        await client.async_get_dns_proxy_status()
    assert client._dns_proxy_supported is None


async def test_async_get_dns_proxy_status_missing_endpoint_returns_empty() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(side_effect=KeeneticApiError("HTTP error 404", status=404))

    assert await client.async_get_dns_proxy_status() == {}
    assert client._dns_proxy_supported is False



def test_dns_proxy_failed_requests_keeps_no_statistics() -> None:
    """A diagnostic counter: its state is enough, statistics only added rows."""
    from custom_components.keenetic_router_pro.sensor.dns import (
        KeeneticDnsProxyFailedRequestsSensor,
    )

    assert KeeneticDnsProxyFailedRequestsSensor._attr_state_class is None
