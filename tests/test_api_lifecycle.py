"""Lifecycle regression tests for raw aiohttp response handling."""

from __future__ import annotations

from tests.conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.keenetic_router_pro.api import KeeneticAuthError, KeeneticClient


class FakeResponse:
    """Async context-manager response that records release/read usage."""

    def __init__(
        self,
        status: int = 200,
        *,
        headers: dict[str, str] | None = None,
        text: str = "",
        json_data: Any = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._text = text
        self._json_data = json_data
        self.closed = False
        self.read_called = False

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.closed = True

    async def text(self) -> str:
        return self._text

    async def json(self) -> Any:
        return self._json_data

    async def read(self) -> bytes:
        self.read_called = True
        return self._text.encode()


class FakeSession:
    """Queue-backed fake client session."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)


def test_challenge_auth_closes_get_and_post_responses() -> None:
    """NDW2 auth releases both raw /auth responses."""
    get_resp = FakeResponse(
        401,
        headers={
            "X-NDM-Challenge": "challenge",
            "X-NDM-Realm": "Keenetic",
            "Set-Cookie": "session=abc; Path=/",
        },
    )
    post_resp = FakeResponse(200, text="ok")
    client = KeeneticClient(
        TEST_HOST,
        TEST_USERNAME,
        TEST_PASSWORD,
        use_challenge_auth=True,
    )
    client._session = FakeSession([get_resp, post_resp])

    asyncio.run(client._async_authenticate_challenge())

    assert get_resp.closed
    assert post_resp.closed
    assert client._authenticated is True
    assert client._auth_header == {"Cookie": "session=abc"}


def test_challenge_auth_prefers_post_cookie_when_rotated() -> None:
    """NDW2 auth keeps the final cookie when the router rotates it on POST."""
    get_resp = FakeResponse(
        401,
        headers={
            "X-NDM-Challenge": "challenge",
            "X-NDM-Realm": "Keenetic",
            "Set-Cookie": "session=initial; Path=/",
        },
    )
    post_resp = FakeResponse(
        200,
        headers={"Set-Cookie": "session=final; Path=/"},
        text="ok",
    )
    client = KeeneticClient(
        TEST_HOST,
        TEST_USERNAME,
        TEST_PASSWORD,
        use_challenge_auth=True,
    )
    client._session = FakeSession([get_resp, post_resp])

    asyncio.run(client._async_authenticate_challenge())

    assert client._auth_header == {"Cookie": "session=final"}


def test_basic_auth_missing_session_raises_auth_error() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    with pytest.raises(KeeneticAuthError, match="ClientSession"):
        asyncio.run(client._async_authenticate())


@pytest.mark.parametrize(
    ("side_effect", "message"),
    [
        (asyncio.TimeoutError(), "timed out"),
        (aiohttp.ClientError("offline"), "connection failed"),
    ],
)
def test_basic_auth_transport_error_raises_auth_error(
    side_effect: Exception, message: str
) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._session = FakeSession([])
    client._session.get = AsyncMock(side_effect=side_effect)

    with pytest.raises(KeeneticAuthError, match=message):
        asyncio.run(client._async_authenticate())


def test_challenge_auth_missing_session_raises_auth_error() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, use_challenge_auth=True)

    with pytest.raises(KeeneticAuthError, match="ClientSession"):
        asyncio.run(client._async_authenticate_challenge())


@pytest.mark.parametrize(
    ("side_effect", "message"),
    [
        (asyncio.TimeoutError(), "GET timed out"),
        (aiohttp.ClientError("offline"), "GET failed"),
    ],
)
def test_challenge_auth_get_transport_error_raises_auth_error(
    side_effect: Exception, message: str
) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, use_challenge_auth=True)
    client._session = FakeSession([])
    client._session.get = AsyncMock(side_effect=side_effect)

    with pytest.raises(KeeneticAuthError, match=message):
        asyncio.run(client._async_authenticate_challenge())


@pytest.mark.parametrize(
    ("get_response", "message"),
    [
        (FakeResponse(500, text="server error body"), "Unexpected status"),
        (FakeResponse(401), "X-NDM-Challenge"),
    ],
)
def test_challenge_auth_get_response_shape_raises_auth_error(
    get_response: FakeResponse, message: str
) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, use_challenge_auth=True)
    client._session = FakeSession([get_response])

    with pytest.raises(KeeneticAuthError, match=message):
        asyncio.run(client._async_authenticate_challenge())


@pytest.mark.parametrize(
    ("post_response", "message"),
    [
        (FakeResponse(401, text="bad credentials"), "rejected"),
        (FakeResponse(500, text="server error body"), "status=500"),
    ],
)
def test_challenge_auth_post_response_shape_raises_auth_error(
    post_response: FakeResponse, message: str
) -> None:
    get_resp = FakeResponse(
        401,
        headers={
            "X-NDM-Challenge": "challenge",
            "X-NDM-Realm": "Keenetic",
            "Set-Cookie": "session=initial; Path=/",
        },
    )
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, use_challenge_auth=True)
    client._session = FakeSession([get_resp, post_response])

    with pytest.raises(KeeneticAuthError, match=message):
        asyncio.run(client._async_authenticate_challenge())


@pytest.mark.parametrize(
    ("side_effect", "message"),
    [
        (asyncio.TimeoutError(), "POST timed out"),
        (aiohttp.ClientError("offline"), "POST failed"),
    ],
)
def test_challenge_auth_post_transport_error_raises_auth_error(
    side_effect: Exception, message: str
) -> None:
    get_resp = FakeResponse(
        401,
        headers={
            "X-NDM-Challenge": "challenge",
            "X-NDM-Realm": "Keenetic",
        },
    )
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, use_challenge_auth=True)
    client._session = FakeSession([get_resp])
    client._session.post = AsyncMock(side_effect=side_effect)

    with pytest.raises(KeeneticAuthError, match=message):
        asyncio.run(client._async_authenticate_challenge())


def test_ensure_auth_authenticated_inside_lock_returns_without_handshake() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._async_authenticate = AsyncMock()

    async def run() -> None:
        async with client._auth_lock:
            client._authenticated = True
        await client._ensure_auth()

    asyncio.run(run())

    client._async_authenticate.assert_not_called()


def test_ensure_auth_waiting_task_returns_after_other_task_authenticates() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._async_authenticate = AsyncMock()

    async def run() -> None:
        await client._auth_lock.acquire()
        task = asyncio.create_task(client._ensure_auth())
        await asyncio.sleep(0)
        client._authenticated = True
        client._auth_lock.release()
        await task

    asyncio.run(run())

    client._async_authenticate.assert_not_called()


def test_mesh_node_basic_auth_fallback_releases_probe_response() -> None:
    """Node auth fallback consumes/releases the challenge probe response."""
    get_resp = FakeResponse(200)
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._session = FakeSession([get_resp])

    headers = asyncio.run(client._authenticate_to_node("192.0.2.2", 80))

    assert get_resp.closed
    assert get_resp.read_called
    assert headers is not None
    assert "Authorization" in headers


def test_mesh_node_challenge_auth_prefers_post_cookie_when_rotated() -> None:
    """Mesh-node direct auth also keeps a rotated cookie from POST /auth."""
    get_resp = FakeResponse(
        401,
        headers={
            "X-NDM-Challenge": "challenge",
            "X-NDM-Realm": "Keenetic",
            "Set-Cookie": "session=initial; Path=/",
        },
    )
    post_resp = FakeResponse(
        200,
        headers={"Set-Cookie": "session=final; Path=/"},
    )
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._session = FakeSession([get_resp, post_resp])

    headers = asyncio.run(client._authenticate_to_node("192.0.2.2", 80))

    assert headers == {"Cookie": "session=final"}
    assert client._node_auth_headers[("192.0.2.2", 80)] == {
        "Cookie": "session=final"
    }


def test_direct_mesh_firmware_update_closes_all_responses() -> None:
    """Direct mesh update fallback releases version, staging, and commit responses."""
    responses = [
        FakeResponse(200, json_data={"ndw": {"components": "base, wifi"}}),
        FakeResponse(204),
        FakeResponse(204),
    ]
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._session = FakeSession(responses.copy())

    async def fake_node_auth(_node_ip: str, _port: int) -> dict[str, str]:
        return {"Cookie": "session=node"}

    client._authenticate_to_node = fake_node_auth

    assert asyncio.run(client.async_start_node_firmware_update("192.0.2.2")) is True
    assert all(response.closed for response in responses)


def test_direct_mesh_firmware_update_clears_stale_cached_node_cookie() -> None:
    """A 401 during direct node update invalidates the cached node cookie."""
    responses = [
        FakeResponse(200, json_data={"ndw": {"components": ""}}),
        FakeResponse(401, text="auth expired"),
    ]
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, port=80)
    client._session = FakeSession(responses.copy())
    client._node_auth_headers[("192.0.2.2", 80)] = {"Cookie": "session=stale"}

    with pytest.raises(HomeAssistantError):
        asyncio.run(client.async_start_node_firmware_update("192.0.2.2"))

    assert ("192.0.2.2", 80) not in client._node_auth_headers
    assert all(response.closed for response in responses)
