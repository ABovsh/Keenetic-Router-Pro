"""Log redaction tests for MAC-taking actions."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import logging
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.keenetic_router_pro.api import KeeneticClient
from custom_components.keenetic_router_pro.api.errors import KeeneticApiError
from tests.test_aiohttp_lifecycle import _Response, _Session


MAC = "AA:BB:CC:DD:EE:FF"
MAC_LOWER = MAC.lower()


def _logged_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("async_block_client", (MAC,)),
        ("async_set_client_policy", (MAC, "Policy1")),
        ("async_reboot_mesh_node", (MAC_LOWER,)),
        ("async_start_node_firmware_update", ("192.0.2.5", "Kitchen", MAC_LOWER)),
    ],
)
async def test_mac_taking_actions_do_not_log_raw_mac(
    caplog: pytest.LogCaptureFixture,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._session = object()
    client._rci_parse = AsyncMock(return_value={"message": "ok"})
    client._authenticate_to_node = AsyncMock(return_value=None)

    caplog.set_level(logging.DEBUG, logger="custom_components.keenetic_router_pro")
    method = getattr(client, method_name)

    await method(*args)

    logged = _logged_text(caplog)
    assert MAC not in logged
    assert MAC_LOWER not in logged


async def test_transport_debug_logs_do_not_expose_router_host(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    session = _Session([_Response({}), _Response({})])
    caplog.set_level(logging.DEBUG, logger="custom_components.keenetic_router_pro")

    await client.async_start(session)
    await client._request("GET", "/rci/show/system")

    assert TEST_HOST not in _logged_text(caplog)


async def test_mesh_update_fallback_logs_do_not_expose_node_name_or_ip(
    caplog: pytest.LogCaptureFixture,
) -> None:
    node_ip = "192.0.2.55"
    node_name = "Private Bedroom Router"
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._session = object()
    client._authenticate_to_node = AsyncMock(return_value=None)
    caplog.set_level(logging.DEBUG, logger="custom_components.keenetic_router_pro")

    with pytest.raises(HomeAssistantError):
        await client.async_start_node_firmware_update(node_ip, node_name)

    logged = _logged_text(caplog)
    assert node_ip not in logged
    assert node_name not in logged


async def test_mesh_auth_exception_message_does_not_expose_node_ip(
    caplog: pytest.LogCaptureFixture,
) -> None:
    node_ip = "192.0.2.55"
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._session = AsyncMock()
    client._session.get.side_effect = KeeneticApiError(f"connection failed for {node_ip}")
    caplog.set_level(logging.DEBUG, logger="custom_components.keenetic_router_pro")

    assert await client._authenticate_to_node(node_ip) is None

    logged = _logged_text(caplog)
    assert node_ip not in logged
    assert "KeeneticApiError" in logged
