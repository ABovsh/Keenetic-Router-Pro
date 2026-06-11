"""System domain parser behavior."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from unittest.mock import AsyncMock

import aiohttp
import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient
from tests.test_api_lifecycle import FakeResponse, FakeSession


async def test_check_firmware_update_pins_available_shape() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(
        return_value={
            "title": "1.0",
            "release": "1.0",
            "fw-available": "1.1",
            "release-available": "1.1",
            "fw-update-sandbox": "stable",
        }
    )

    result = await client.async_check_firmware_update()

    assert result["has_update"] is True
    assert result["current"]["title"] == "1.0"
    assert result["available"]["release"] == "1.1"


async def test_async_check_firmware_update_release_available_nonstable_no_update() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(
        return_value={
            "release": "1.0",
            "release-available": "1.1",
            "fw-update-sandbox": "preview",
        }
    )

    result = await client.async_check_firmware_update()

    assert result["current"]["title"] == "1.0"
    assert result["available"] is None
    assert result["has_update"] is False


async def test_async_start_firmware_update_components_404_uses_system_update() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(return_value={"ndw": {"components": "base,wifi"}})
    client._request = AsyncMock(side_effect=KeeneticApiError("404 not found"))
    client._rci_post = AsyncMock(return_value={"status": "accepted"})

    assert await client.async_start_firmware_update() is True
    client._rci_post.assert_awaited_once_with(
        "system/update", {"confirm": True}, allow_text=True
    )


async def test_async_start_firmware_update_system_update_text_result_succeeds() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(return_value={})
    client._rci_post = AsyncMock(return_value="started")

    assert await client.async_start_firmware_update() is True


@pytest.mark.parametrize(
    "result",
    [
        {"status": "failed", "message": "insufficient storage"},
        False,
        "error: update unavailable",
    ],
)
async def test_async_start_firmware_update_rejects_failure_results(result: object) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(return_value={})
    client._rci_post = AsyncMock(return_value=result)

    with pytest.raises(HomeAssistantError):
        await client.async_start_firmware_update()


@pytest.mark.parametrize(
    ("request_error", "post_error"),
    [
        (KeeneticApiError("500 failed"), None),
        (None, KeeneticApiError("500 failed")),
    ],
)
async def test_async_start_firmware_update_non404_error_raises(
    request_error: Exception | None, post_error: Exception | None
) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(return_value={"ndw": {"components": "base"}})
    client._request = AsyncMock(side_effect=request_error)
    client._rci_post = AsyncMock(side_effect=post_error or KeeneticApiError("404"))

    with pytest.raises(HomeAssistantError):
        await client.async_start_firmware_update()


async def test_async_start_firmware_update_no_endpoint_raises() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(return_value={})
    client._rci_post = AsyncMock(side_effect=KeeneticApiError("404 not found"))

    with pytest.raises(HomeAssistantError):
        await client.async_start_firmware_update()


async def test_async_start_node_firmware_update_missing_session_or_ip_raises() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    with pytest.raises(HomeAssistantError, match="Cannot connect"):
        await client.async_start_node_firmware_update("")


async def test_async_start_node_firmware_update_mws_error_uses_direct_system_update() -> None:
    responses = [
        FakeResponse(200, json_data={"ndw": {"components": ""}}),
        FakeResponse(204),
    ]
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, port=80)
    client._session = FakeSession(responses.copy())
    client._rci_parse = AsyncMock(return_value=[{"message": "error: busy"}])
    client._authenticate_to_node = AsyncMock(return_value={"Cookie": "session=node"})

    assert await client.async_start_node_firmware_update(
        "192.0.2.2", "Extender", "AA:BB:CC:DD:EE:FF"
    ) is True


async def test_async_start_node_firmware_update_component_failures_try_fallback() -> None:
    responses = [
        FakeResponse(200, json_data={"ndw": {"components": "base"}}),
        FakeResponse(500, text="stage failed"),
        FakeResponse(500, text="legacy failed"),
    ]
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, port=80)
    client._session = FakeSession(responses.copy())
    client._authenticate_to_node = AsyncMock(return_value={"Cookie": "session=node"})

    with pytest.raises(HomeAssistantError):
        await client.async_start_node_firmware_update("192.0.2.2")

    assert [response.closed for response in responses] == [True, True, False]


async def test_async_start_node_firmware_update_auth_fail_tries_next_port() -> None:
    responses = [FakeResponse(200, json_data={"ndw": {"components": ""}}), FakeResponse(204)]
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, port=8080)
    client._session = FakeSession(responses.copy())
    client._authenticate_to_node = AsyncMock(
        side_effect=[None, {"Cookie": "session=node"}]
    )

    assert await client.async_start_node_firmware_update("192.0.2.2") is True
    assert [call.args[1] for call in client._authenticate_to_node.call_args_list] == [
        8080,
        80,
    ]


async def test_async_start_node_firmware_update_version_401_clears_cache() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, port=80)
    client._session = FakeSession([FakeResponse(401)])
    client._node_auth_headers[("192.0.2.2", 80)] = {"Cookie": "stale"}
    client._authenticate_to_node = AsyncMock(return_value={"Cookie": "stale"})

    with pytest.raises(HomeAssistantError):
        await client.async_start_node_firmware_update("192.0.2.2")

    assert ("192.0.2.2", 80) not in client._node_auth_headers


async def test_async_start_node_firmware_update_commit_401_clears_cache() -> None:
    responses = [
        FakeResponse(200, json_data={"ndw": {"components": "base"}}),
        FakeResponse(204),
        FakeResponse(401),
    ]
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, port=80)
    client._session = FakeSession(responses.copy())
    client._node_auth_headers[("192.0.2.2", 80)] = {"Cookie": "stale"}
    client._authenticate_to_node = AsyncMock(return_value={"Cookie": "stale"})

    with pytest.raises(HomeAssistantError):
        await client.async_start_node_firmware_update("192.0.2.2")

    assert ("192.0.2.2", 80) not in client._node_auth_headers


async def test_async_start_node_firmware_update_component_exception_uses_legacy() -> None:
    class FailingJsonResponse(FakeResponse):
        async def json(self) -> object:
            raise ValueError("bad json")

    responses = [FailingJsonResponse(200), FakeResponse(204)]
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, port=80)
    client._session = FakeSession(responses.copy())
    client._authenticate_to_node = AsyncMock(return_value={"Cookie": "session=node"})

    assert await client.async_start_node_firmware_update("192.0.2.2") is True


async def test_async_start_node_firmware_update_legacy_non404_failure_raises() -> None:
    responses = [FakeResponse(500, text="version failed"), FakeResponse(500, text="legacy")]
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, port=80)
    client._session = FakeSession(responses.copy())
    client._authenticate_to_node = AsyncMock(return_value={"Cookie": "session=node"})

    with pytest.raises(HomeAssistantError):
        await client.async_start_node_firmware_update("192.0.2.2")


async def test_authenticate_to_node_post_rejection_returns_none() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._session = FakeSession(
        [
            FakeResponse(
                401,
                headers={
                    "X-NDM-Challenge": "challenge",
                    "X-NDM-Realm": "Keenetic",
                    "Set-Cookie": "session=initial; Path=/",
                },
            ),
            FakeResponse(403, text="denied"),
        ]
    )

    assert await client._authenticate_to_node("192.0.2.2", 80) is None


async def test_authenticate_to_node_client_error_returns_none() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._session = FakeSession([])
    client._session.get = AsyncMock(side_effect=aiohttp.ClientError("offline"))

    assert await client._authenticate_to_node("192.0.2.2", 80) is None


async def test_update_progress_coerces_string_values_and_blank_payloads() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(return_value={"in-progress": "yes", "progress": "42", "eta": "60"})

    assert await client.async_get_update_progress() == {
        "in_progress": True,
        "progress_percent": 42,
        "stage": None,
        "eta_seconds": "60",
    }

    client._rci_get = AsyncMock(return_value="")
    assert await client.async_get_update_progress() == {}


async def test_ndns_info_filters_malformed_tunnels_and_numeric_strings() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(
        return_value={
            "name": "router",
            "ttp": {"tunnel": [{"uptime": "12", "idle": "bad"}, "ignored"]},
        }
    )

    result = await client.async_get_ndns_info()

    assert result["ttp"]["tunnel"] == [{"uptime": 12, "idle": "bad"}]


@pytest.mark.parametrize("exc", [KeeneticApiError("boom"), aiohttp.ClientError("boom"), asyncio.TimeoutError(), ValueError("bad json")])
async def test_system_error_paths_return_empty(exc: Exception) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(side_effect=exc)

    assert await client.async_check_firmware_update() == {}
    assert await client.async_get_update_progress() == {}
    assert await client.async_get_ndns_info() == {}
