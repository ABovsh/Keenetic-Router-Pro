"""VPN domain parser behavior."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from unittest.mock import AsyncMock

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


async def test_ipsec_status_raises_when_show_ipsec_times_out() -> None:
    """A failed status read must not be merged into "every tunnel is down".

    The coordinator keeps its previous snapshot for a raised read, so a
    disabled tunnel stays available/off and an established one stays on.
    """
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    async def _get(subpath: str, **_kw):
        if subpath == "show/ipsec":
            raise KeeneticApiError("Timeout for /rci/show/ipsec")
        return {"Office": {"enable": False}}

    client._rci_get = AsyncMock(side_effect=_get)

    with pytest.raises(KeeneticApiError):
        await client.async_get_ipsec_status()


async def test_ipsec_status_raises_when_crypto_map_config_times_out() -> None:
    """A failed config read must not drop disabled tunnels for a tick."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    async def _get(subpath: str, **_kw):
        if subpath == "crypto/map":
            raise KeeneticApiError("Timeout for /rci/crypto/map")
        return {
            "ipsec_statusall": (
                "Security Associations (1 up, 0 connecting):\n"
                "        Office[1]: ESTABLISHED 10 seconds ago, "
                "192.0.2.1[192.0.2.1]...198.51.100.1[198.51.100.1]\n"
            )
        }

    client._rci_get = AsyncMock(side_effect=_get)

    with pytest.raises(KeeneticApiError):
        await client.async_get_ipsec_status()


async def test_ipsec_status_propagates_cancellation_from_either_read() -> None:
    """Cancellation must never be swallowed into a degraded result."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(side_effect=asyncio.CancelledError())
    client.async_get_crypto_map_config = AsyncMock(return_value={})

    with pytest.raises(asyncio.CancelledError):
        await client.async_get_ipsec_status()
