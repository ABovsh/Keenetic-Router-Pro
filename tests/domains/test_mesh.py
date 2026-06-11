"""Mesh domain parser behavior."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient


async def test_mesh_nodes_fallback_from_clients_without_mws_endpoint() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    # MWS endpoint is absent on this model: only "not found" may trigger the
    # MAC-keyed fallback (a transient error now raises instead).
    client._rci_get = AsyncMock(side_effect=KeeneticApiError('not found: "member"'))

    result = await client.async_get_mesh_nodes(
        clients=[
            {"mac": "AA:BB:CC:00:00:01", "system-mode": "extender", "active": "yes", "name": "Kitchen", "ip": "192.0.2.2"},
            {"mac": "AA:BB:CC:00:00:02", "active": True},
        ]
    )

    assert result[0]["id"] == "AA:BB:CC:00:00:01"
    assert result[0]["cid"] is None
    assert result[0]["connected"] is True
    assert result[0]["name"] == "Kitchen"


async def test_mesh_nodes_mws_list_skips_bad_members_and_string_counts() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(
        return_value={
            "member": [
                "bad",
                {"cid": "node-1", "name": "Kitchen", "state": "up", "rci": {"errors": "0"}, "client": {"count": "3"}},
                {"name": "missing cid"},
            ]
        }
    )

    result = await client.async_get_mesh_nodes(
        clients=[{"mac": "AA:BB:CC:00:00:01", "system-mode": "extender"}]
    )

    assert len(result) == 1
    assert result[0]["cid"] == "node-1"
    assert result[0]["connected"] is True


@pytest.mark.parametrize("exc", [KeeneticApiError("boom"), aiohttp.ClientError("boom"), asyncio.TimeoutError(), ValueError("bad json")])
async def test_mesh_reboot_propagates_safe_parse_errors(exc: Exception) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_parse = AsyncMock(side_effect=exc)

    with pytest.raises(type(exc)):
        await client.async_reboot_mesh_node("aa:bb:cc:00:00:01")
