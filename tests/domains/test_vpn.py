"""VPN domain parser behavior."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient


async def test_wireguard_status_accepts_peer_dict_and_numeric_strings() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_wireguard_status(
        iface_list=[
            {
                "id": "Wireguard0",
                "type": "Wireguard",
                "state": "up",
                "description": "WG",
                "wireguard": {
                    "peer": {
                        "remote-endpoint-address": "198.51.100.2",
                        "rxbytes": "100",
                        "txbytes": "200",
                    }
                },
            },
            {"id": "Bridge0", "type": "Bridge"},
        ]
    )

    assert result["profiles"]["Wireguard0"]["enabled"] is True
    assert result["profiles"]["Wireguard0"]["rxbytes"] == "100"


async def test_async_get_wireguard_status_interfaces_shape_skips_missing_name() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_wireguard_status(
        interfaces={
            "Wireguard1": {
                "type": "Other",
                "traits": ["WireGuard"],
                "state": "down",
                "wireguard": {
                    "peer": [
                        {
                            "remote-endpoint-address": "198.51.100.3",
                            "rxbytes": "3",
                            "txbytes": "4",
                        }
                    ]
                },
            },
            "broken": {"type": "Wireguard", "id": ""},
        }
    )

    profile = result["profiles"]["Wireguard1"]
    assert profile["enabled"] is False
    assert profile["remote"] == "198.51.100.3"
    # Peer counters reported as numeric strings are coerced to ints.
    assert profile["rxbytes"] == 3
    assert profile["txbytes"] == 4


async def test_vpn_tunnels_skip_missing_ids_and_disabled_summary() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_vpn_tunnels(
        iface_list=[
            {"type": "OpenVPN", "summary": {"layer": {"conf": "disabled"}}},
            {"id": "OpenVpn0", "type": "OpenVPN", "state": "down", "summary": {"layer": {"conf": "disabled"}}},
            {"id": "Wireguard0", "type": "Wireguard", "state": "up"},
        ]
    )

    assert result["profiles"]["OpenVpn0"]["enabled"] is False
    assert result["profiles"]["Wireguard0"]["enabled"] is True


async def test_async_get_vpn_tunnels_interfaces_shape_uses_label_fallbacks() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_vpn_tunnels(
        interfaces={
            "Sstp0": {
                "type": "SSTP",
                "interface-name": "SSTP Client",
                "state": "up",
                "summary": "bad",
            },
            "Bridge0": {"type": "Bridge", "state": "up"},
        }
    )

    assert result["profiles"]["Sstp0"]["label"] == "SSTP Client"
    assert result["profiles"]["Sstp0"]["enabled"] is True


async def test_crypto_maps_normalize_single_sa_and_string_booleans() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(
        return_value={
            "crypto_map": {
                "Office": {
                    "config": {"enabled": "yes", "remote_peer": " 198.51.100.1 "},
                    "status": {
                        "state": "PHASE2_ESTABLISHED",
                        "phase2_sa_list": {
                            "phase2_sa": {"in_bytes": "7", "out_bytes": "9"}
                        },
                    },
                },
                "bad": "ignored",
            }
        }
    )

    result = await client.async_get_crypto_maps()

    assert result["Office"]["enabled"] is True
    assert result["Office"]["connected"] is True
    assert result["Office"]["remote_peer"] == "198.51.100.1"
    assert result["Office"]["rx_bytes"] == 7
    assert result["Office"]["tx_bytes"] == 9


async def test_async_get_crypto_maps_phase1_and_invalid_sections_normalize() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(
        return_value={
            "crypto_map": {
                "Office": {
                    "config": "bad",
                    "status": {
                        "state": "down",
                        "phase1": {"ike_state": "IKE_ESTABLISHED"},
                        "phase2_sa_list": {
                            "phase2_sa": [
                                {"in_bytes": "5", "out_packets": "2"},
                                "ignored",
                            ]
                        },
                    },
                }
            }
        }
    )

    result = await client.async_get_crypto_maps()

    assert result["Office"]["enabled"] is False
    assert result["Office"]["ike_state"] == "IKE_ESTABLISHED"
    assert result["Office"]["rx_bytes"] == 5
    assert result["Office"]["tx_packets"] == 2


@pytest.mark.parametrize("payload", [None, {"crypto_map": []}])
async def test_async_get_crypto_maps_invalid_payload_returns_empty(
    payload: object,
) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(return_value=payload)

    assert await client.async_get_crypto_maps() == {}


async def test_crypto_map_endpoint_missing_is_cached() -> None:
    """A missing optional crypto-map endpoint should not be retried every tick."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(side_effect=KeeneticApiError("404 not found"))

    assert await client.async_get_crypto_maps() == {}
    assert await client.async_get_crypto_maps() == {}
    assert client._rci_get.await_count == 1
    assert client._crypto_map_supported is False


async def test_set_wireguard_enabled_delegates_to_generic_interface_toggle() -> None:
    """WireGuard keeps the compatibility method but uses the generic toggle path."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client.async_set_interface_enabled = AsyncMock()

    await client.async_set_wireguard_enabled("Wireguard0", True)

    client.async_set_interface_enabled.assert_awaited_once_with("Wireguard0", True)


async def test_async_set_crypto_map_enabled_save_failure_keeps_toggle() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_parse = AsyncMock(
        side_effect=[None, KeeneticApiError("save unavailable")]
    )

    await client.async_set_crypto_map_enabled("Office", False)

    assert [call.args[0] for call in client._rci_parse.call_args_list] == [
        "crypto map Office\nno enable",
        "system configuration save",
    ]

@pytest.mark.parametrize("exc", [KeeneticApiError("not found"), aiohttp.ClientError("boom"), asyncio.TimeoutError(), ValueError("bad json")])
async def test_vpn_error_paths_return_empty(exc: Exception) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(side_effect=exc)
    client._rci_parse = AsyncMock(side_effect=exc)

    assert await client.async_get_crypto_maps() == {}
    assert await client.async_get_ipsec_diagnostics() == {}
