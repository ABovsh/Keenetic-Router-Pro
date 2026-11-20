"""HTTP session shared by every Keenetic config entry and flow."""

from __future__ import annotations

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import DOMAIN

_SESSION_KEY = f"{DOMAIN}_http_session"


def async_get_keenetic_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Return the integration's HTTP session, which stores no cookies.

    Router auth sends its session cookie explicitly. Home Assistant's shared
    session keeps a cookie jar, so the cookie from a finished setup or reauth
    would ride on the next challenge request; the router then answers without
    a challenge and the sign-in is reported as rejected credentials.
    """
    session = hass.data.get(_SESSION_KEY)
    if session is None or session.closed:
        session = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
        hass.data[_SESSION_KEY] = session
    return session
