"""KeeneticOS 5.1.9 (NDM-4882) closed the unauthenticated RCI on port 79.

Every path that ends at ``127.0.0.1:79`` — a ``Ext → 79`` port forward or a
KeenDNS ``ip http proxy`` with ``upstream http 127.0.0.1 79`` — now answers
403 with an ``X-Detail`` header (``0x1237, rci-local only`` or
``0x1867, invalid query string``). That is not a wrong password: re-entering
it in the reauth form can never succeed. The client must say so, and setup
must point the user at Reconfigure (router web address + challenge auth).
"""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from typing import Any

import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError

from custom_components.keenetic_router_pro.api import (
    KeeneticAuthError,
    KeeneticClient,
    KeeneticRciBlockedError,
)

from test_api_lifecycle import FakeResponse, FakeSession
from test_integration_lifecycle import _FakeClient, _FakeEntry, _hass

RCI_LOCAL_ONLY = {"X-Detail": "0x1237, rci-local only"}
INVALID_QUERY = {"X-Detail": "0x1867, invalid query string"}


def _client(*, challenge: bool, responses: list[FakeResponse]) -> KeeneticClient:
    client = KeeneticClient(
        TEST_HOST,
        TEST_USERNAME,
        TEST_PASSWORD,
        use_challenge_auth=challenge,
    )
    client._session = FakeSession(responses)
    return client


@pytest.mark.parametrize("headers", [RCI_LOCAL_ONLY, INVALID_QUERY])
def test_basic_port_79_lockdown_is_not_a_credential_error(
    headers: dict[str, str],
) -> None:
    """Basic leg 403 + lockdown detail → blocked error, never reauth."""
    client = _client(
        challenge=False,
        responses=[
            FakeResponse(403, headers=headers),  # Basic GET /rci/
            FakeResponse(403, headers=RCI_LOCAL_ONLY),  # fallback GET /auth
        ],
    )

    with pytest.raises(KeeneticRciBlockedError) as err:
        asyncio.run(client._ensure_auth())

    assert not isinstance(err.value, KeeneticAuthError)
    assert client._authenticated is False
    assert client._use_challenge_auth is False


def test_challenge_port_79_lockdown_is_not_a_credential_error() -> None:
    """Challenge GET /auth refused by the lockdown → blocked error, flag kept."""
    client = _client(
        challenge=True,
        responses=[
            FakeResponse(403, headers=RCI_LOCAL_ONLY),  # GET /auth
            FakeResponse(403, headers=RCI_LOCAL_ONLY),  # fallback Basic GET /rci/
        ],
    )

    with pytest.raises(KeeneticRciBlockedError):
        asyncio.run(client._ensure_auth())

    assert client._use_challenge_auth is True


def test_plain_403_still_means_rejected_credentials() -> None:
    """A 403 without the lockdown detail keeps the reauth contract."""
    client = _client(
        challenge=False,
        responses=[
            FakeResponse(403, text="forbidden"),  # Basic GET /rci/
            FakeResponse(401, headers={}),  # GET /auth, no challenge
        ],
    )

    with pytest.raises(KeeneticAuthError):
        asyncio.run(client._ensure_auth())


def test_setup_reports_lockdown_as_repair_not_reauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup raises ConfigEntryError and raises a dedicated Repair issue."""
    import custom_components.keenetic_router_pro as integration

    created: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        integration.ir,
        "async_create_issue",
        lambda _hass, _domain, issue_id, **kw: created.append((issue_id, kw)),
    )
    _FakeClient.instances.clear()
    monkeypatch.setattr(
        _FakeClient, "start_error", KeeneticRciBlockedError("rci-local only")
    )
    monkeypatch.setattr(integration, "KeeneticClient", _FakeClient)
    monkeypatch.setattr(integration, "async_get_clientsession", lambda hass: None)

    entry = _FakeEntry(
        {"host": TEST_HOST, "username": TEST_USERNAME, "password": TEST_PASSWORD}
    )

    with pytest.raises(ConfigEntryError) as err:
        asyncio.run(integration.async_setup_entry(_hass(), entry))

    assert not isinstance(err.value, ConfigEntryAuthFailed)
    assert [issue_id for issue_id, _ in created] == [
        f"{integration.ISSUE_RCI_BLOCKED}_{entry.entry_id}"
    ]
    assert created[0][1]["translation_key"] == integration.ISSUE_RCI_BLOCKED
    assert created[0][1]["translation_placeholders"] == {
        "title": entry.title,
        "host": TEST_HOST,
    }


def test_successful_setup_clears_lockdown_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After Reconfigure, the stale lockdown Repair must disappear."""
    import custom_components.keenetic_router_pro as integration

    deleted: list[str] = []
    real_delete = integration.ir.async_delete_issue

    def _delete(hass: Any, domain: str, issue_id: str) -> None:
        deleted.append(issue_id)
        real_delete(hass, domain, issue_id)

    monkeypatch.setattr(integration.ir, "async_delete_issue", _delete)
    entry = _FakeEntry({})

    integration._async_delete_rci_blocked_issue(_hass(), entry)

    assert deleted == [f"{integration.ISSUE_RCI_BLOCKED}_{entry.entry_id}"]
