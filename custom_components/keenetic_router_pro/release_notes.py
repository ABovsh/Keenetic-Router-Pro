"""Resolve per-model firmware changelog deep links on support.keenetic.*.

The retired help.keenetic.com portal now redirects every article to the
generic support homepage. The current support site organizes release notes
per model at ``https://support.keenetic.<tld>/<family>/<hw-id>/<lang>/<id>-latest-<channel>-release.html``
where ``<id>`` is a model-specific article number that cannot be derived
offline. This module discovers it at runtime by fetching the model's
help-center index page (which the router's own web UI links to) and parsing
the release-notes article links out of it.
"""

from __future__ import annotations

import asyncio
import logging
import re

import aiohttp

from .const import DOMAIN

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.release_notes")

KEENETIC_SUPPORT_URL = "https://support.keenetic.com/"

_FETCH_TIMEOUT = 10  # seconds per index-page fetch

_CHANNEL_PAGES = {
    "stable": "latest-main-release",
    "preview": "latest-preview-release",
    "draft": "latest-development-release",
    "dev": "latest-development-release",
}
# When the desired channel page is absent on the model page (e.g. LTS-only
# models), fall back through the remaining pages in a sane order.
_PAGE_FALLBACK_ORDER = (
    "latest-main-release",
    "latest-lts-release",
    "latest-preview-release",
    "latest-development-release",
)

_ARTICLE_RE = re.compile(
    r'href="(?P<lang>[a-z]{2})/(?P<article>\d+-latest-'
    r'(?:main|lts|preview|development)-release\.html)"'
)
_SLUG_ALLOWED_RE = re.compile(r"[^a-z0-9-]+")
_REGION_RE = re.compile(r"^[A-Za-z]{2,3}$")

_FETCH_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, OSError)

# Article numbers are stable per model; cache successful lookups for the
# lifetime of the process so opening the update dialog stays instant.
_cache: dict[tuple[str, str, str | None, str | None, str], str] = {}


def clear_cache() -> None:
    """Reset the resolver cache (test hook)."""
    _cache.clear()


def _slug(value: str) -> str | None:
    slug = _SLUG_ALLOWED_RE.sub("", value.strip().lower().replace(" ", "-"))
    return slug or None


def family_slug(device: str | None, model: str | None) -> str | None:
    """Return the support-site family slug, e.g. 'titan' or 'hero-4g'.

    ``show/version`` exposes the family as ``device`` ("Titan"); mesh members
    only carry ``model`` ("Giga (KN-1011)") whose prefix is the family name.
    """
    if device:
        return _slug(device)
    if model:
        return _slug(model.split("(", 1)[0])
    return None


def model_slug(hw_id: str | None) -> str | None:
    """Return the support-site model slug, e.g. 'kn-1812'."""
    return _slug(hw_id) if hw_id else None


def channel_page(channel: str | None) -> str:
    """Map a firmware sandbox/channel name to its release-notes page slug."""
    if channel and "lts" in channel.lower():
        return "latest-lts-release"
    return _CHANNEL_PAGES.get((channel or "").lower(), "latest-main-release")


def _pick_article(html: str, page: str, lang: str) -> str | None:
    """Pick the best '<lang>/<id>-<page>.html' article path from index HTML."""
    by_page: dict[str, dict[str, str]] = {}
    for match in _ARTICLE_RE.finditer(html):
        article = match.group("article")
        page_name = article.split("-", 1)[1].removesuffix(".html")
        by_page.setdefault(page_name, {})[match.group("lang")] = article

    for candidate in (page, *_PAGE_FALLBACK_ORDER):
        langs = by_page.get(candidate)
        if not langs:
            continue
        if lang in langs:
            found_lang = lang
        elif "en" in langs:
            found_lang = "en"
        else:
            found_lang = next(iter(langs))
        return f"{found_lang}/{langs[found_lang]}"
    return None


async def async_resolve_release_url(
    session: aiohttp.ClientSession,
    *,
    model: str | None,
    hw_id: str | None,
    device: str | None = None,
    region: str | None = None,
    channel: str | None = None,
    lang: str = "en",
) -> str | None:
    """Resolve the model-specific changelog URL, or None if it can't be found.

    Tries the region support domain first (the international .com site uses
    different family names for some regions' models), then falls back to .com.
    Only successful lookups are cached.
    """
    family = family_slug(device, model)
    hw = model_slug(hw_id)
    if not family or not hw or session is None:
        return None

    page = channel_page(channel)
    cache_key = (family, hw, region, channel, lang)
    if cache_key in _cache:
        return _cache[cache_key]

    domains = []
    if region and _REGION_RE.match(region):
        domains.append(f"https://support.keenetic.{region.lower()}")
    if "https://support.keenetic.com" not in domains:
        domains.append("https://support.keenetic.com")

    for domain in domains:
        index_url = f"{domain}/{family}/{hw}/"
        try:
            async with asyncio.timeout(_FETCH_TIMEOUT):
                resp = await session.get(index_url)
                async with resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text()
        except _FETCH_ERRORS as err:
            _LOGGER.debug(
                "Release-notes index fetch failed for %s: %s",
                index_url,
                type(err).__name__,
            )
            continue

        article_path = _pick_article(html, page, lang)
        url = f"{index_url}{article_path}" if article_path else index_url
        _cache[cache_key] = url
        return url

    return None
