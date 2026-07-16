"""Regression tests: the 401 re-auth retry must get its own fresh timeout window.

Previously a single ``asyncio.timeout(self._request_timeout)`` wrapped the
initial attempt, the re-auth call, and the retry request together. A slow
first response could starve the retry even though the retry itself would
have completed comfortably within the configured timeout.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

import pytest
from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient


class _Response:
    def __init__(
        self,
        payload: Any = None,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        text: str = "ok",
        delay: float = 0.0,
    ) -> None:
        self.payload = payload if payload is not None else {}
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self._text = text
        self.delay = delay
        self.closed = False

    async def __aenter__(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    async def json(self):
        return self.payload

    async def text(self) -> str:
        return self._text

    async def read(self) -> bytes:
        return self._text.encode()


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = deque(responses)
        self.seen: list[_Response] = []

    async def request(self, *_args, **_kwargs):
        response = self.responses.popleft()
        self.seen.append(response)
        return response

    async def get(self, *_args, **_kwargs):
        return await self.request()

    async def post(self, *_args, **_kwargs):
        return await self.request()

    @property
    def open_responses(self) -> list[_Response]:
        return [response for response in self.seen if not response.closed]


async def test_slow_first_attempt_does_not_starve_401_retry() -> None:
    """A near-timeout first attempt must not doom a fast retry.

    request_timeout=1s. First attempt sleeps 0.9s then reports 401. The
    retry only needs 0.3s and must succeed because it gets its own fresh
    timeout window, not the leftover ~0.1s from the shared deadline.
    """
    client = KeeneticClient(
        TEST_HOST, TEST_USERNAME, TEST_PASSWORD, request_timeout=1
    )
    session = _Session(
        [
            _Response({}),  # initial auth (async_start)
            _Response({}, status=401, delay=0.9),  # slow first attempt
            _Response({}),  # re-auth
            _Response({"hostname": "router"}, delay=0.3),  # fast retry
        ]
    )
    await client.async_start(session)

    result = await client.async_get_system_info()

    assert result == {"hostname": "router"}
    assert session.open_responses == []


async def test_retry_exceeding_its_own_fresh_window_still_times_out() -> None:
    """The retry is not exempt from timeouts — it just gets its own window."""
    client = KeeneticClient(
        TEST_HOST, TEST_USERNAME, TEST_PASSWORD, request_timeout=1
    )
    session = _Session(
        [
            _Response({}),  # initial auth (async_start)
            _Response({}, status=401),  # fast first attempt -> 401
            _Response({}),  # re-auth
            _Response({"hostname": "router"}, delay=1.5),  # retry too slow
        ]
    )
    await client.async_start(session)

    with pytest.raises(KeeneticApiError):
        await client.async_get_system_info()
