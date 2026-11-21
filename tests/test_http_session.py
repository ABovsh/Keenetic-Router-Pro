"""The integration signs in on its own HTTP session, which keeps no cookies.

Router auth sends its session cookie explicitly. A cookie-keeping session
would also replay the cookie a previous sign-in left behind (setup, then the
config entry; or reauth, then reload). The router answers that challenge
request with 200 and no challenge, and the sign-in is reported as rejected
credentials.
"""

from __future__ import annotations

import hashlib
import secrets
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web

from custom_components.keenetic_router_pro import config_flow, http_session
import custom_components.keenetic_router_pro as integration
from custom_components.keenetic_router_pro.api import KeeneticAuthError, KeeneticClient

_USER = "admin"
_PASSWORD = "router-password"
_REALM = "Keenetic Test"
_COOKIE = "__Host-Http-session"


async def _start_router() -> tuple[web.AppRunner, int]:
    challenges: dict[str, str] = {}
    signed_in: set[str] = set()

    async def get_auth(request: web.Request) -> web.Response:
        if request.cookies.get(_COOKIE) in signed_in:
            return web.Response(status=200)
        sid, challenge = secrets.token_hex(8), secrets.token_hex(8)
        challenges[sid] = challenge
        response = web.Response(
            status=401,
            headers={"X-NDM-Challenge": challenge, "X-NDM-Realm": _REALM},
        )
        response.set_cookie(_COOKIE, sid)
        return response

    async def post_auth(request: web.Request) -> web.Response:
        sid = request.cookies.get(_COOKIE, "")
        body = await request.json()
        ha1 = hashlib.md5(f"{_USER}:{_REALM}:{_PASSWORD}".encode()).hexdigest()
        expected = hashlib.sha256((challenges.pop(sid, "") + ha1).encode()).hexdigest()
        if body.get("login") != _USER or body.get("password") != expected:
            return web.Response(status=401)
        rotated = secrets.token_hex(8)
        signed_in.add(rotated)
        response = web.Response(status=200)
        response.set_cookie(_COOKIE, rotated)
        return response

    async def anything_else(_request: web.Request) -> web.Response:
        return web.Response(status=401)

    app = web.Application()
    app.router.add_get("/auth", get_auth)
    app.router.add_post("/auth", post_auth)
    app.router.add_route("*", "/{tail:.*}", anything_else)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port


async def _sign_in(session: aiohttp.ClientSession, port: int) -> None:
    client = KeeneticClient(
        "127.0.0.1", _USER, _PASSWORD, port=port, ssl=False, use_challenge_auth=True
    )
    client._session = session
    await client._ensure_auth()


async def test_cookie_keeping_session_rejects_the_second_sign_in() -> None:
    """The hazard: a replayed cookie turns a valid sign-in into an auth error."""
    runner, port = await _start_router()
    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            await _sign_in(session, port)
            with pytest.raises(KeeneticAuthError):
                await _sign_in(session, port)
    finally:
        await runner.cleanup()


async def test_integration_session_signs_in_repeatedly(monkeypatch) -> None:
    created: list[dict] = []

    def _create(_hass, **kwargs):
        created.append(kwargs)
        return aiohttp.ClientSession(**kwargs)

    monkeypatch.setattr(http_session, "async_create_clientsession", _create)
    hass = SimpleNamespace(data={})
    runner, port = await _start_router()
    session = http_session.async_get_keenetic_session(hass)
    try:
        await _sign_in(session, port)
        await _sign_in(session, port)
        await _sign_in(http_session.async_get_keenetic_session(hass), port)
    finally:
        await session.close()
        await runner.cleanup()

    assert len(created) == 1
    assert isinstance(created[0]["cookie_jar"], aiohttp.DummyCookieJar)


async def test_closed_integration_session_is_replaced(monkeypatch) -> None:
    sessions: list[aiohttp.ClientSession] = []

    def _create(_hass, **kwargs):
        sessions.append(aiohttp.ClientSession(**kwargs))
        return sessions[-1]

    monkeypatch.setattr(http_session, "async_create_clientsession", _create)
    hass = SimpleNamespace(data={})
    first = http_session.async_get_keenetic_session(hass)
    await first.close()
    second = http_session.async_get_keenetic_session(hass)
    await second.close()

    assert first is not second
    assert len(sessions) == 2


def test_setup_and_config_flow_use_the_integration_session() -> None:
    for module in (integration, config_flow):
        assert module.async_get_keenetic_session is http_session.async_get_keenetic_session
        assert not hasattr(module, "async_get_clientsession")
