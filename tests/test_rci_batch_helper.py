"""Coverage for the RCI tree-batching helper.

The coordinator uses this helper to prefetch a tick-scoped cache. Its
contract is part of the integration's API surface: default-on, latches
off on first real failure, and never raises except for cancellation.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.keenetic_router_pro.api import (
    KeeneticApiError,
    KeeneticClient,
)
from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME


def _client() -> KeeneticClient:
    return KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)


async def test_rci_batch_returns_response_on_success_and_latches_supported() -> None:
    client = _client()
    expected = {"show": {"system": {"hostname": "router"}, "interface": {}}}
    client._request = AsyncMock(return_value=expected)

    result = await client._rci_batch({"show": {"system": {}, "interface": {}}})

    assert result is expected
    assert client._rci_batch_supported is True


async def test_rci_batch_returns_none_on_empty_or_non_dict_input() -> None:
    client = _client()
    client._request = AsyncMock(return_value={"any": "thing"})

    assert await client._rci_batch({}) is None
    assert await client._rci_batch(None) is None  # type: ignore[arg-type]
    client._request.assert_not_awaited()


async def test_rci_batch_latches_off_on_keenetic_error_and_returns_none() -> None:
    client = _client()
    client._request = AsyncMock(side_effect=KeeneticApiError("404"))

    assert await client._rci_batch({"show": {"system": {}}}) is None
    assert client._rci_batch_supported is False

    # Once latched off, the helper short-circuits without calling _request.
    client._request = AsyncMock(return_value={"show": {"system": {}}})
    assert await client._rci_batch({"show": {"system": {}}}) is None
    client._request.assert_not_awaited()


@pytest.mark.parametrize(
    "exc",
    [aiohttp.ClientError("boom"), asyncio.TimeoutError()],
)
async def test_rci_batch_swallows_transport_errors(exc: Exception) -> None:
    client = _client()
    client._request = AsyncMock(side_effect=exc)

    assert await client._rci_batch({"show": {"system": {}}}) is None
    assert client._rci_batch_supported is False


async def test_rci_batch_propagates_cancellation() -> None:
    client = _client()
    client._request = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await client._rci_batch({"show": {"system": {}}})
    # Cancellation must NOT latch the capability off — it's not a real failure.
    assert client._rci_batch_supported is not False


async def test_rci_batch_treats_non_dict_response_as_unsupported() -> None:
    client = _client()
    client._request = AsyncMock(return_value="<html>oops</html>")

    assert await client._rci_batch({"show": {"system": {}}}) is None
    assert client._rci_batch_supported is False


# --- Tick-cache wiring contract ---------------------------------------------


async def test_prefetch_tick_populates_cache_and_rci_get_serves_from_it() -> None:
    """After prefetch, params-less GETs return the cached subtree without HTTP."""
    client = _client()
    payload = {
        "show": {
            "system": {"hostname": "r"},
            "interface": {"PPPoE0": {"state": "up"}},
            "ip": {"neighbour": [{"mac": "AA:BB:CC:00:00:01"}]},
        }
    }
    client._request = AsyncMock(return_value=payload)

    ok = await client.prefetch_tick({"show": {"system": {}, "interface": {}, "ip": {"neighbour": {}}}})
    assert ok is True

    # Reset the mock so we can prove the next GETs do NOT hit transport.
    client._request.reset_mock()

    sys_data = await client._rci_get("show/system")
    iface_data = await client._rci_get("show/interface")
    neigh_data = await client._rci_get("show/ip/neighbour")

    assert sys_data == {"hostname": "r"}
    assert iface_data == {"PPPoE0": {"state": "up"}}
    assert neigh_data == [{"mac": "AA:BB:CC:00:00:01"}]
    client._request.assert_not_awaited()


async def test_rci_get_falls_back_to_http_when_subpath_not_cached() -> None:
    client = _client()
    client._tick_cache = {"show": {"system": {"hostname": "r"}}}
    client._request = AsyncMock(return_value={"some": "thing"})

    result = await client._rci_get("show/ndns")

    client._request.assert_awaited_once()
    assert result == {"some": "thing"}


async def test_rci_get_with_params_bypasses_cache() -> None:
    """Cache cannot represent param-dependent GETs — must always hit HTTP."""
    client = _client()
    client._tick_cache = {"show": {"interface": {"PPPoE0": {"state": "up"}}}}
    client._request = AsyncMock(return_value={"fresh": True})

    result = await client._rci_get("show/interface/stat", params={"name": "PPPoE0"})

    client._request.assert_awaited_once()
    assert result == {"fresh": True}


async def test_clear_tick_cache_drops_cache_and_next_get_hits_http() -> None:
    client = _client()
    client._tick_cache = {"show": {"system": {"hostname": "cached"}}}
    client._request = AsyncMock(return_value={"fresh": True})

    client.clear_tick_cache()
    result = await client._rci_get("show/system")

    client._request.assert_awaited_once()
    assert result == {"fresh": True}


async def test_prefetch_tick_returns_false_when_batch_unsupported() -> None:
    client = _client()
    client._rci_batch_supported = False  # latched off from prior failure
    client._request = AsyncMock()

    ok = await client.prefetch_tick({"show": {"system": {}}})

    assert ok is False
    assert client._tick_cache is None
    client._request.assert_not_awaited()
