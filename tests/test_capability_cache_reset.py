"""Coverage for firmware-version-triggered capability cache resets.

Capability caches on the transport latch for the whole HA session (they
avoid re-probing endpoints that are known missing/present). If a firmware
update adds or repairs an endpoint mid-session, latched-off caches must
not stay stuck until HA restart — a version change detected in
``async_get_current_version_info`` must reset them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from custom_components.keenetic_router_pro.api import KeeneticClient
from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME


def _client() -> KeeneticClient:
    return KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)


def _latch_all(client: KeeneticClient) -> None:
    client._mws_member_supported = False
    client._crypto_map_supported = True
    client._dns_proxy_supported = False
    client._ping_check_supported = True
    client._ndns_supported = False
    client._ipsec_diagnostics_supported = True
    client._hotspot_subpath_skip = {"foo", "bar"}
    client._hotspot_subpath_winner = "foo"
    client._iface_stat_get_only = True
    client._rci_batch_supported = False


def test_reset_capability_caches_restores_defaults() -> None:
    client = _client()
    _latch_all(client)

    client.reset_capability_caches()

    assert client._mws_member_supported is None
    assert client._crypto_map_supported is None
    assert client._dns_proxy_supported is None
    assert client._ping_check_supported is None
    assert client._ndns_supported is None
    assert client._ipsec_diagnostics_supported is None
    assert client._hotspot_subpath_skip == set()
    assert client._hotspot_subpath_winner is None
    assert client._iface_stat_get_only is None
    assert client._rci_batch_supported is None


def test_reset_capability_caches_leaves_fw_version_auth_and_tick_cache() -> None:
    client = _client()
    client._last_seen_fw_version = "3.9.1"
    client._authenticated = True
    client._auth_header = {"Authorization": "Basic xxx"}
    client._tick_cache = {"show": {"system": {}}}
    _latch_all(client)

    client.reset_capability_caches()

    assert client._last_seen_fw_version == "3.9.1"
    assert client._authenticated is True
    assert client._auth_header == {"Authorization": "Basic xxx"}
    assert client._tick_cache == {"show": {"system": {}}}


async def test_first_fetch_records_identity_without_reset() -> None:
    client = _client()
    _latch_all(client)
    client._rci_get = AsyncMock(return_value={"release": "3.9.1", "title": "Foo 3.9.1"})

    await client.async_get_current_version_info()

    assert client._last_seen_fw_version == "3.9.1"
    # No previous identity existed, so nothing should have been reset.
    assert client._mws_member_supported is False
    assert client._rci_batch_supported is False


async def test_version_change_triggers_reset() -> None:
    client = _client()
    client._rci_get = AsyncMock(return_value={"release": "3.9.1"})
    await client.async_get_current_version_info()

    _latch_all(client)
    client._rci_get = AsyncMock(return_value={"release": "3.9.2"})
    await client.async_get_current_version_info()

    assert client._last_seen_fw_version == "3.9.2"
    assert client._mws_member_supported is None
    assert client._crypto_map_supported is None
    assert client._dns_proxy_supported is None
    assert client._ping_check_supported is None
    assert client._ndns_supported is None
    assert client._ipsec_diagnostics_supported is None
    assert client._hotspot_subpath_skip == set()
    assert client._hotspot_subpath_winner is None
    assert client._iface_stat_get_only is None
    assert client._rci_batch_supported is None


async def test_same_version_twice_does_not_reset() -> None:
    client = _client()
    client._rci_get = AsyncMock(return_value={"release": "3.9.1"})
    await client.async_get_current_version_info()

    _latch_all(client)
    client._rci_get = AsyncMock(return_value={"release": "3.9.1"})
    await client.async_get_current_version_info()

    assert client._last_seen_fw_version == "3.9.1"
    assert client._mws_member_supported is False
    assert client._rci_batch_supported is False
    assert client._hotspot_subpath_skip == {"foo", "bar"}


async def test_empty_version_payload_does_not_reset_or_clear_identity() -> None:
    client = _client()
    client._rci_get = AsyncMock(return_value={"release": "3.9.1"})
    await client.async_get_current_version_info()

    _latch_all(client)
    client._rci_get = AsyncMock(return_value={})
    await client.async_get_current_version_info()

    assert client._last_seen_fw_version == "3.9.1"
    assert client._mws_member_supported is False
    assert client._rci_batch_supported is False


async def test_garbled_version_payload_does_not_reset_or_clear_identity() -> None:
    client = _client()
    client._rci_get = AsyncMock(return_value={"release": "3.9.1"})
    await client.async_get_current_version_info()

    _latch_all(client)
    client._rci_get = AsyncMock(return_value="not a dict")

    result = await client.async_get_current_version_info()

    assert result == {}
    assert client._last_seen_fw_version == "3.9.1"
    assert client._mws_member_supported is False
    assert client._rci_batch_supported is False
