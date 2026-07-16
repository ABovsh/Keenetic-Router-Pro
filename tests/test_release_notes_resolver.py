"""Tests for the per-model release-notes URL resolver."""
from __future__ import annotations

import asyncio

import pytest

from custom_components.keenetic_router_pro.release_notes import (
    KEENETIC_SUPPORT_URL,
    async_resolve_release_url,
    channel_page,
    clear_cache,
    family_slug,
    model_slug,
)

TITAN_HTML = (
    '<a href="en/41380-latest-main-release.html">EN main</a>'
    '<a href="en/41167-latest-preview-release.html">EN preview</a>'
    '<a href="uk/41380-latest-main-release.html">UK main</a>'
)
AIR_LTS_ONLY_HTML = '<a href="en/69623-latest-lts-release.html">EN lts</a>'


class FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def text(self) -> str:
        return self._body


class FakeSession:
    """Maps URL -> (status, body); unknown URLs raise OSError like a dead host."""

    def __init__(self, responses: dict[str, tuple[int, str]]) -> None:
        self._responses = responses
        self.requested: list[str] = []

    async def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.requested.append(url)
        if url not in self._responses:
            raise OSError(f"no route to {url}")
        status, body = self._responses[url]
        return FakeResponse(status, body)


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    clear_cache()


# ---------- slug / channel helpers ----------

def test_family_slug_prefers_device_field() -> None:
    assert family_slug("Titan", "Titan (KN-1812)") == "titan"


def test_family_slug_falls_back_to_model_prefix() -> None:
    assert family_slug(None, "Giga (KN-1011)") == "giga"


def test_family_slug_hyphenates_spaces() -> None:
    assert family_slug(None, "Hero 4G (KN-2310)") == "hero-4g"


def test_family_slug_none_when_unknown() -> None:
    assert family_slug(None, None) is None


def test_model_slug_lowercases_hw_id() -> None:
    assert model_slug("KN-1812") == "kn-1812"


def test_model_slug_none_when_missing() -> None:
    assert model_slug(None) is None


def test_channel_page_mapping() -> None:
    assert channel_page("stable") == "latest-main-release"
    assert channel_page("preview") == "latest-preview-release"
    assert channel_page("draft") == "latest-development-release"
    assert channel_page("lts-4.3") == "latest-lts-release"
    assert channel_page(None) == "latest-main-release"
    assert channel_page("mystery") == "latest-main-release"


# ---------- resolver ----------

def _resolve(session: FakeSession, **kwargs: object) -> str | None:
    defaults: dict[str, object] = {
        "model": "Titan (KN-1812)",
        "hw_id": "KN-1812",
        "device": "Titan",
        "region": "UA",
        "channel": "stable",
    }
    defaults.update(kwargs)
    return asyncio.run(async_resolve_release_url(session, **defaults))


def test_resolves_deep_link_on_region_domain() -> None:
    session = FakeSession(
        {"https://support.keenetic.ua/titan/kn-1812/": (200, TITAN_HTML)}
    )
    assert _resolve(session) == (
        "https://support.keenetic.ua/titan/kn-1812/en/41380-latest-main-release.html"
    )


def test_prefers_requested_language_when_available() -> None:
    session = FakeSession(
        {"https://support.keenetic.ua/titan/kn-1812/": (200, TITAN_HTML)}
    )
    assert _resolve(session, lang="uk") == (
        "https://support.keenetic.ua/titan/kn-1812/uk/41380-latest-main-release.html"
    )


def test_falls_back_to_english_for_unknown_language() -> None:
    session = FakeSession(
        {"https://support.keenetic.ua/titan/kn-1812/": (200, TITAN_HTML)}
    )
    assert _resolve(session, lang="de") == (
        "https://support.keenetic.ua/titan/kn-1812/en/41380-latest-main-release.html"
    )


def test_channel_fallback_uses_available_page() -> None:
    session = FakeSession(
        {"https://support.keenetic.ua/air/kn-1611/": (200, AIR_LTS_ONLY_HTML)}
    )
    assert _resolve(
        session,
        model="Air (KN-1611)",
        hw_id="KN-1611",
        device=None,
    ) == "https://support.keenetic.ua/air/kn-1611/en/69623-latest-lts-release.html"


def test_falls_back_to_com_domain_when_region_dead() -> None:
    session = FakeSession(
        {"https://support.keenetic.com/titan/kn-1812/": (200, TITAN_HTML)}
    )
    assert _resolve(session) == (
        "https://support.keenetic.com/titan/kn-1812/en/41380-latest-main-release.html"
    )


def test_model_page_without_articles_returns_model_page() -> None:
    session = FakeSession(
        {"https://support.keenetic.ua/titan/kn-1812/": (200, "<html>nothing</html>")}
    )
    assert _resolve(session) == "https://support.keenetic.ua/titan/kn-1812/"


def test_returns_none_when_all_domains_fail() -> None:
    assert _resolve(FakeSession({})) is None


def test_returns_none_without_model_identifiers() -> None:
    session = FakeSession({})
    assert _resolve(session, model=None, hw_id=None, device=None) is None
    assert session.requested == []


def test_successful_result_is_cached() -> None:
    responses = {"https://support.keenetic.ua/titan/kn-1812/": (200, TITAN_HTML)}
    session = FakeSession(responses)
    first = _resolve(session)
    second = _resolve(session)
    assert first == second
    assert len(session.requested) == 1


def test_failures_are_not_cached() -> None:
    session = FakeSession({})
    assert _resolve(session) is None
    ok_session = FakeSession(
        {"https://support.keenetic.ua/titan/kn-1812/": (200, TITAN_HTML)}
    )
    assert _resolve(ok_session) is not None


def test_support_url_constant_is_live_domain() -> None:
    assert KEENETIC_SUPPORT_URL == "https://support.keenetic.com/"
