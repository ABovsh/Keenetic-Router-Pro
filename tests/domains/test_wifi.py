"""Wi-Fi domain parser behavior."""

from __future__ import annotations

from tests.conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

from unittest.mock import AsyncMock

import pytest

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient


async def test_wifi_networks_group_bands_and_skip_secret_keys() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_wifi_networks(
        iface_list=[
            {"id": "Bridge0", "type": "Bridge", "interface-name": "Main"},
            {"id": "WifiMaster0/AccessPoint0", "type": "AccessPoint", "state": "up", "ssid": "Home", "group": "Bridge0", "channel": "6", "psk": TEST_PASSWORD},
            {"id": "WifiMaster1/AccessPoint0", "type": "AccessPoint", "state": "down", "ssid": "Home", "group": "Bridge0", "channel": "36"},
        ]
    )

    assert [item["band"] for item in result] == ["2.4 GHz", "5 GHz"]
    assert result[0]["enabled"] is True
    assert TEST_PASSWORD not in str(result)


async def test_wifi_networks_falls_back_to_bridge_label_for_disabled_group() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_wifi_networks(
        iface_list=[
            {"id": "Bridge0", "type": "Bridge", "interface-name": "Guest"},
            {"id": "WifiMaster0/AccessPoint1", "type": "AccessPoint", "state": "down", "group": "Bridge0", "band": "2"},
        ]
    )

    assert result[0]["ssid"] == "Guest"
    assert result[0]["enabled"] is False


async def test_async_get_wifi_networks_interfaces_shape_normalizes_groups() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_wifi_networks(
        interfaces={
            "Bridge1": {"type": "Bridge", "description": "Guest Bridge"},
            "WifiMaster0/AccessPoint2": {
                "type": "AccessPoint",
                "state": "up",
                "group": "Bridge1",
                "channel": "bad",
            },
            "ignored": {"type": "AccessPoint", "state": "up"},
        }
    )

    assert result == [
        {
            "id": "WifiMaster0/AccessPoint2",
            "name": "Guest Bridge 2.4 GHz",
            "ssid": "Guest Bridge",
            "band": "2.4 GHz",
            "enabled": True,
            "state": "up",
            "group": "Bridge1",
            "channel": "bad",
            "tx_power": None,
        }
    ]


async def test_async_get_wifi_networks_trait_ap_without_band_uses_default() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_wifi_networks(
        iface_list=[
            {
                "name": "RadioVirtual0",
                "traits": ["Wifi", "AccessPoint"],
                "state": "down",
                "ssid": "Lab",
            },
            {
                "id": "RadioVirtual1",
                "type": "AccessPoint",
                "state": "up",
                "ssid": "Lab",
            },
        ]
    )

    assert len(result) == 1
    assert result[0]["name"] == "Lab default"
    assert result[0]["band"] == "default"
    assert result[0]["enabled"] is False


async def test_async_get_wifi_networks_empty_group_uses_wifi_fallback() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_wifi_networks(
        iface_list=[
            {},
            {"id": "Bridge0", "type": "Bridge"},
            {
                "id": "AccessPointCustom",
                "type": "AccessPoint",
                "state": "up",
                "ssid": "Wi-Fi",
                "channel": "not-a-number",
                "band": "6",
                "wpa": "hidden",
            },
        ]
    )

    assert result[0]["name"] == "Wi-Fi 6"
    assert result[0]["ssid"] == "Wi-Fi"
    assert result[0]["band"] == "6"
    assert "hidden" not in str(result)


async def test_async_get_wifi_networks_group_without_bridge_uses_group_id() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_wifi_networks(
        iface_list=[
            {
                "id": "AccessPointGuest",
                "type": "AccessPoint",
                "state": "up",
                "group": "Bridge9",
                "channel": "44",
            }
        ]
    )

    assert result[0]["ssid"] == "Bridge9"
    assert result[0]["band"] == "5 GHz"


async def test_set_wifi_enabled_validates_interface_before_parse() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_parse = AsyncMock()

    with pytest.raises(KeeneticApiError):
        await client.async_set_wifi_enabled("WifiMaster0; reboot", True)

    client._rci_parse.assert_not_called()
