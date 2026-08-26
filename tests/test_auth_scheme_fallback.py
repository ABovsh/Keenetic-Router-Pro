"""KeeneticOS 5.2 (NDM-4515) reworked the HTTP auth suite.

The router's own web port now answers Basic with a bare 401 and advertises
``x-ndw2-interactive`` / ``x-ndw4-interactive`` instead, while the KeenDNS
``rci.`` alias still speaks only Basic. A config entry created before the
update carries the wrong ``use_challenge_auth`` flag for one of those hosts,
so the client must try the other scheme once before declaring bad credentials.
"""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import pytest

from custom_components.keenetic_router_pro.api import KeeneticAuthError, KeeneticClient

from test_api_lifecycle import FakeResponse, FakeSession

CHALLENGE_401 = {
    "X-NDM-Challenge": "challenge",
    "X-NDM-Realm": "Keenetic Viva",
    "Set-Cookie": "__Host-Http-f1ChrnIhAZTIL2y=abc; Path=/; Max-Age=20",
}


def _client(*, challenge: bool, responses: list[FakeResponse]) -> KeeneticClient:
    client = KeeneticClient(
        TEST_HOST,
        TEST_USERNAME,
        TEST_PASSWORD,
        use_challenge_auth=challenge,
    )
    client._session = FakeSession(responses)
    return client


@pytest.mark.asyncio
async def test_basic_rejected_falls_back_to_challenge() -> None:
    """5.2 web port: Basic 401s, NDW2 succeeds — no reauth prompt."""
    client = _client(
        challenge=False,
        responses=[
            FakeResponse(401, text="401 Authorization Required"),  # Basic GET /rci/
            FakeResponse(401, headers=CHALLENGE_401),  # NDW2 GET /auth
            FakeResponse(200, text="ok"),  # NDW2 POST /auth
        ],
    )

    await client._ensure_auth()

    assert client._authenticated is True
    assert client._use_challenge_auth is True
    assert client._auth_header == {"Cookie": "__Host-Http-f1ChrnIhAZTIL2y=abc"}


@pytest.mark.asyncio
async def test_challenge_unsupported_falls_back_to_basic() -> None:
    """KeenDNS rci. alias: no X-NDM-Challenge, Basic succeeds."""
    client = _client(
        challenge=True,
        responses=[
            FakeResponse(401, headers={}),  # GET /auth, no challenge header
            FakeResponse(200, text="{}"),  # Basic GET /rci/
        ],
    )

    await client._ensure_auth()

    assert client._authenticated is True
    assert client._use_challenge_auth is False
    assert "Authorization" in (client._auth_header or {})


@pytest.mark.asyncio
async def test_both_schemes_rejected_still_raises_auth_error() -> None:
    """A genuinely wrong password must still reach HA's reauth flow."""
    client = _client(
        challenge=False,
        responses=[
            FakeResponse(401, text="denied"),  # Basic
            FakeResponse(401, headers=CHALLENGE_401),  # NDW2 GET /auth
            FakeResponse(403, text="denied"),  # NDW2 POST /auth
        ],
    )

    with pytest.raises(KeeneticAuthError):
        await client._ensure_auth()

    assert client._authenticated is False
    assert client._use_challenge_auth is False


@pytest.mark.asyncio
async def test_setup_path_also_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setup and the config flow go through async_start, not _ensure_auth."""
    client = _client(
        challenge=False,
        responses=[
            FakeResponse(401, text="401 Authorization Required"),
            FakeResponse(401, headers=CHALLENGE_401),
            FakeResponse(200, text="ok"),
        ],
    )
    session = client._session

    await client.async_start(session)

    assert client._authenticated is True
    assert client._use_challenge_auth is True


@pytest.mark.asyncio
async def test_challenge_get_403_falls_back_to_basic() -> None:
    """A router with no /auth endpoint 403s the unauthenticated GET.

    No credentials are sent on that request, so 403 means "this host does not
    speak challenge auth", not "wrong password" — it must reach the fallback
    instead of failing setup outright.
    """
    client = _client(
        challenge=True,
        responses=[
            FakeResponse(403, text="403 Forbidden"),  # GET /auth
            FakeResponse(200, text="{}"),  # Basic GET /rci/
        ],
    )

    await client._ensure_auth()

    assert client._authenticated is True
    assert client._use_challenge_auth is False
