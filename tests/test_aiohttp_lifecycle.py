"""aiohttp response lifecycle regression tests."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

from collections import deque
from typing import Any

import pytest

from custom_components.keenetic_router_pro.api import KeeneticClient


class _Response:
    def __init__(self, payload: Any = None, *, status: int = 200, headers: dict[str, str] | None = None, text: str = "ok") -> None:
        self.payload = payload if payload is not None else {}
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self._text = text
        self.closed = False
        self.read_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    async def json(self):
        return self.payload

    async def text(self) -> str:
        return self._text

    async def read(self) -> bytes:
        self.read_called = True
        return self._text.encode()


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = deque(responses)
        self.seen: list[_Response] = []

    async def request(self, *_args, **_kwargs):
        return self._next()

    async def get(self, *_args, **_kwargs):
        return self._next()

    async def post(self, *_args, **_kwargs):
        return self._next()

    def _next(self) -> _Response:
        response = self.responses.popleft()
        self.seen.append(response)
        return response

    @property
    def open_responses(self) -> list[_Response]:
        return [response for response in self.seen if not response.closed]


async def _started_client(*responses: _Response) -> tuple[KeeneticClient, _Session]:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    session = _Session([_Response({}), *responses])
    await client.async_start(session)  # auth response must be closed too
    assert session.open_responses == []
    return client, session


@pytest.mark.parametrize(
    ("method_name", "responses", "args"),
    [
        ("async_get_system_info", [_Response({"hostname": "router"})], ()),
        ("async_get_clients", [_Response({"host": [{"mac": "aa:bb:cc:00:00:01"}]})], ()),
        ("async_get_dns_proxy_status", [_Response({"proxy-status": []})], ()),
        ("async_get_ping_check_status", [_Response({"pingcheck": []})], ()),
        ("async_start_firmware_update", [_Response({"ndw": {"components": "base"}}), _Response({}), _Response({}, status=204)], ()),
    ],
)
async def test_public_methods_release_transport_responses(
    method_name: str,
    responses: list[_Response],
    args: tuple[object, ...],
) -> None:
    client, session = await _started_client(*responses)

    await getattr(client, method_name)(*args)

    assert session.open_responses == []


async def test_401_retry_releases_original_and_retry_response() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    session = _Session([
        _Response({}),  # initial auth
        _Response({}, status=401),
        _Response({}),  # retry auth
        _Response({"hostname": "router"}),
    ])
    await client.async_start(session)

    assert await client.async_get_system_info() == {"hostname": "router"}
    assert session.open_responses == []


async def test_start_node_firmware_update_releases_node_responses() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    session = _Session([
        _Response({}),  # initial auth
        _Response({}, headers={}),  # node auth GET without challenge -> basic fallback
        _Response({"ndw": {"components": "base"}}),
        _Response({}, status=204),
        _Response({}, status=204),
    ])
    await client.async_start(session)

    assert await client.async_start_node_firmware_update("192.0.2.2", "Kitchen") is True
    assert session.open_responses == []
