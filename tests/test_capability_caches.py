"""Capability-cache tests for endpoints that may not exist on every firmware.

When the router does not implement an RCI endpoint (e.g. no IPsec component,
no Ping Check, no DNS proxy, no NDNS), each poll cycle produces a router-side
``ndm: ... not found`` error log. The client must latch each missing endpoint
off on the first ``not found`` response and stop calling it.
"""

from __future__ import annotations

from tests.conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from typing import Any

import pytest

from custom_components.keenetic_router_pro.api import (
    KeeneticApiError,
    KeeneticClient,
    _is_endpoint_missing,
)


def _make_client() -> KeeneticClient:
    return KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)


class _RecordingClient:
    """Patches ``_rci_get`` on a real client to record paths and raise on demand."""

    def __init__(self, client: KeeneticClient, raise_not_found_for: set[str]) -> None:
        self.client = client
        self.calls: list[str] = []
        self._raise_not_found_for = raise_not_found_for

        async def _fake_rci_get(path: str, params: Any = None) -> Any:
            self.calls.append(path)
            if path in self._raise_not_found_for:
                raise KeeneticApiError(
                    f"HTTP error 404 for {path}: not found"
                )
            return {}

        client._rci_get = _fake_rci_get  # type: ignore[assignment]


def test_is_endpoint_missing_detects_not_found_and_404() -> None:
    assert _is_endpoint_missing(KeeneticApiError("not found: 'crypto/map'"))
    assert _is_endpoint_missing(KeeneticApiError("HTTP error 404 for show/ndns"))
    assert not _is_endpoint_missing(KeeneticApiError("Connection refused"))
    assert not _is_endpoint_missing(KeeneticApiError("Timeout"))


def test_crypto_map_latches_off_after_not_found() -> None:
    """show/crypto/map must be called once, then never again on the same client."""
    client = _make_client()
    rec = _RecordingClient(client, raise_not_found_for={"show/crypto/map"})

    async def run() -> None:
        # First call hits the router and triggers the not-found path.
        assert await client.async_get_crypto_maps() == {}
        # Second call must be short-circuited by the capability cache.
        assert await client.async_get_crypto_maps() == {}

    asyncio.run(run())
    assert rec.calls == ["show/crypto/map"], (
        "second call must be skipped after capability latch"
    )
    assert client._crypto_map_supported is False


def test_dns_proxy_latches_off_after_not_found() -> None:
    client = _make_client()
    rec = _RecordingClient(client, raise_not_found_for={"show/dns-proxy"})

    async def run() -> None:
        await client.async_get_dns_proxy_status()
        await client.async_get_dns_proxy_status()
        await client.async_get_dns_proxy_status()

    asyncio.run(run())
    assert rec.calls == ["show/dns-proxy"]
    assert client._dns_proxy_supported is False


def test_ping_check_latches_off_after_not_found() -> None:
    client = _make_client()
    rec = _RecordingClient(client, raise_not_found_for={"show/ping-check"})

    async def run() -> None:
        await client.async_get_ping_check_status()
        await client.async_get_ping_check_status()

    asyncio.run(run())
    assert rec.calls == ["show/ping-check"]
    assert client._ping_check_supported is False


def test_ndns_latches_off_after_not_found() -> None:
    client = _make_client()
    rec = _RecordingClient(client, raise_not_found_for={"show/ndns"})

    async def run() -> None:
        await client.async_get_ndns_info()
        await client.async_get_ndns_info()

    asyncio.run(run())
    assert rec.calls == ["show/ndns"]
    assert client._ndns_supported is False


def test_capability_caches_do_not_latch_on_transient_errors() -> None:
    """A connection error must not permanently disable the endpoint."""
    client = _make_client()
    calls: list[str] = []

    async def _flaky(path: str, params: Any = None) -> Any:
        calls.append(path)
        raise KeeneticApiError("Connection refused")

    client._rci_get = _flaky  # type: ignore[assignment]

    async def run() -> None:
        await client.async_get_crypto_maps()
        await client.async_get_crypto_maps()

    asyncio.run(run())
    # Both polls must reach the router; transient failures must NOT latch.
    assert len(calls) == 2
    assert client._crypto_map_supported is not False
