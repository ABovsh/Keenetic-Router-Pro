"""WAN domain parser behavior."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient


async def test_async_get_wan_status_prefers_up_pppoe_with_ip() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_wan_status(
        iface_list=[
            {"id": "GigabitEthernet0", "type": "GigabitEthernet", "state": "up", "security-level": "public"},
            {"id": "PPPoE0", "type": "PPPoE", "state": "up", "address": "198.51.100.7/32", "gateway": "10.0.0.1", "uptime": "42"},
        ]
    )

    assert result["status"] == "connected"
    assert result["ip"] == "198.51.100.7"
    assert result["type"] == "pppoe"
    assert result["uptime"] == "42"


@pytest.mark.parametrize(
    ("iface", "expected_ip"),
    [
        ({"global-address": [{"ip": "198.51.100.8/24"}]}, "198.51.100.8"),
        ({"global-address": ["198.51.100.9/24"]}, "198.51.100.9"),
        ({"address": [{"address": "198.51.100.10/24"}]}, "198.51.100.10"),
        ({"address": ["198.51.100.11/24"]}, "198.51.100.11"),
    ],
)
async def test_async_get_wan_status_address_shape_extracts_ip(
    iface: dict[str, object], expected_ip: str
) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    iface.update({"id": "ISP", "state": "up", "role": "inet"})

    result = await client.async_get_wan_status(iface_list=[iface])

    assert result["status"] == "connected"
    assert result["ip"] == expected_ip


@pytest.mark.parametrize(
    "iface",
    [
        {"id": "ISP", "state": "down", "security-level": "public"},
        {"interface-name": "Broadband0", "state": "down"},
    ],
)
async def test_async_get_wan_status_down_wan_shape_reports_down(
    iface: dict[str, object],
) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_wan_status(iface_list=[iface])

    assert result["status"] == "down"
    assert result["type"] == "ethernet"


async def test_async_get_wan_status_no_wan_shape_reports_down() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_wan_status(
        iface_list=[{"id": "Bridge0", "state": "up", "address": "10.0.0.1"}]
    )

    assert result == {"status": "down", "ip": None, "link": "down"}


async def test_async_get_wan_interfaces_handles_missing_keys_and_pending_ip() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_wan_interfaces(
        iface_list=[
            {"id": "ISP", "role": ["inet"], "state": "up", "global": True, "priority": "100", "summary": {"layer": {"ipv4": "pending"}}},
            {"id": "LAN", "global": True},
        ]
    )

    assert len(result) == 1
    assert result[0]["id"] == "ISP"
    assert result[0]["internet_access"] is None


async def test_async_get_wan_interfaces_role_string_and_address_list_variants() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_wan_interfaces(
        iface_list=[
            {
                "interface-name": "ISP",
                "role": "wan",
                "state": "up",
                "global": True,
                "address": [{"ip": "198.51.100.12/24"}],
                "summary": {"layer": {"conf": "running", "ipv4": "running"}},
                "fail": "yes",
            },
            {"id": "LTE", "global": True, "priority": 10, "state": "down"},
        ]
    )

    assert [item["id"] for item in result] == ["ISP", "LTE"]
    assert result[0]["role"] == ["wan"]
    assert result[0]["ip"] == "198.51.100.12"
    assert result[0]["internet_access"] is False


async def test_async_get_wan_interfaces_global_address_and_disabled_summary() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_wan_interfaces(
        iface_list=[
            {
                "id": "ISP",
                "global": True,
                "priority": 20,
                "state": "up",
                "global-address": [{"address": "198.51.100.20/24"}],
                "summary": {"layer": {"conf": "disabled"}},
            },
            {
                "id": "LTE",
                "role": ["inet"],
                "state": "up",
                "global": False,
                "address": ["198.51.100.21/24"],
                "summary": {"layer": "bad"},
            },
            {"role": "wan", "state": "up"},
        ]
    )

    assert result[0]["enabled"] is False
    assert result[0]["ip"] == "198.51.100.20"
    assert result[1]["ip"] == "198.51.100.21"
    assert result[1]["internet_access"] is False


async def test_async_get_ping_check_status_dict_profiles_aggregates_unknown() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(
        return_value={
            "pingcheck": {
                "main": {
                    "profile": "main",
                    "host": "probe.example",
                    "interface": {"PPPoE0": {"status": "checking"}},
                },
                "test": {
                    "profile": "test",
                    "interface": {
                        "PPPoE0": {
                            "status": "fail",
                            "ipcache": [{"addresses": ["192.0.2.10"]}],
                        }
                    },
                },
            }
        }
    )

    result = await client.async_get_ping_check_status()

    assert result["PPPoE0"]["status"] == "checking"
    assert result["PPPoE0"]["passing"] is None
    assert result["PPPoE0"]["ignored_profiles"] == ["test"]


async def test_async_get_ping_check_status_pass_and_empty_observations() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(
        return_value={
            "pingcheck": [
                "ignored",
                {"profile": "empty"},
                {"profile": "bad", "interface": {"PPPoE0": "ignored"}},
                {
                    "profile": "main",
                    "host": ["probe.example"],
                    "interface": {
                        "PPPoE0": {
                            "status": "pass",
                            "successcount": 7,
                            "ipcache": [
                                {
                                    "host": "probe.example",
                                    "addresses": ["8.8.8.8"],
                                }
                            ],
                        }
                    },
                },
            ]
        }
    )

    result = await client.async_get_ping_check_status()

    assert result["PPPoE0"]["status"] == "pass"
    assert result["PPPoE0"]["passing"] is True
    assert result["PPPoE0"]["success_count"] == 7


async def test_async_get_ping_check_status_only_test_net_profiles_ignored() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(
        return_value={
            "pingcheck": [
                {
                    "profile": "test",
                    "interface": {
                        "PPPoE0": {
                            "status": "fail",
                            "ipcache": [{"addresses": ["203.0.113.9"]}],
                        }
                    },
                }
            ]
        }
    )

    result = await client.async_get_ping_check_status()

    assert result["PPPoE0"]["status"] is None
    assert result["PPPoE0"]["passing"] is None
    assert result["PPPoE0"]["ignored_profiles"] == ["test"]


@pytest.mark.parametrize("exc", [KeeneticApiError("not found"), aiohttp.ClientError("boom"), asyncio.TimeoutError(), ValueError("bad json")])
async def test_async_get_ping_check_status_errors_return_empty(exc: Exception) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(side_effect=exc)

    assert await client.async_get_ping_check_status() == {}
