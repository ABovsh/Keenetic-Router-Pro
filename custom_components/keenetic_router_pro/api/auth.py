"""Authentication mixin for the Keenetic API client."""

from __future__ import annotations

from typing import Dict

import aiohttp
import asyncio
import hashlib
import logging

from ..const import DOMAIN
from ..utils import mask_identifier
from .constants import RCI_ROOT
from .errors import KeeneticApiError, KeeneticAuthError
from .helpers import _cookie_header_from_response, _response_summary

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.auth")


class _AuthMixin:

    async def _async_authenticate(self) -> None:
        """Perform Basic auth against /rci/, like original ha_keenetic."""
        if self._session is None:
            raise KeeneticAuthError("ClientSession is not set")

        headers = self._basic_auth_headers()
        url = f"{self._base}{RCI_ROOT}/"

        _LOGGER.debug(
            "Authenticating to Keenetic router %s",
            mask_identifier(self._host),
        )

        # Only an explicit credential rejection (401/403) may raise
        # KeeneticAuthError — that is what HA turns into a reauth flow.
        # Timeouts, connection errors and 5xx mean the router is
        # unreachable/rebooting and must stay KeeneticApiError, otherwise
        # every router outage pops a spurious "re-enter password" repair.
        try:
            async with asyncio.timeout(self._request_timeout):
                resp = await self._session.get(url, headers=headers)
                async with resp:
                    if resp.status in (401, 403):
                        text = await resp.text()
                        raise KeeneticAuthError(
                            f"Auth failed (status {resp.status}): "
                            f"{_response_summary(text)}"
                        )
                    if resp.status != 200:
                        text = await resp.text()
                        raise KeeneticApiError(
                            f"Auth endpoint returned status {resp.status}: "
                            f"{_response_summary(text)}"
                        )
        except asyncio.TimeoutError as err:
            raise KeeneticApiError("Auth connection timed out") from err
        except aiohttp.ClientError as err:
            raise KeeneticApiError(f"Auth connection failed: {err}") from err

        self._auth_header = headers
        self._authenticated = True
        _LOGGER.debug(
            "Authenticated to Keenetic router at %s:%s",
            mask_identifier(self._host),
            self._port,
        )

    async def _async_authenticate_challenge(self) -> None:
        """Perform NDW2 challenge-response auth used by newer Keenetic models (e.g. Hero).

        Handshake:
          1. GET /auth  → 401 with X-NDM-Challenge + X-NDM-Realm headers + Set-Cookie
          2. Compute:
               ha1      = md5(username:realm:password)
               response = sha256(challenge + ha1)
          3. POST /auth  with JSON {login, password: response}  and the session cookie
          4. 200 → authenticated; subsequent requests use only the session cookie.
        """
        if self._session is None:
            raise KeeneticAuthError("ClientSession is not set")

        auth_url = f"{self._base}/auth"

        # --- Step 1: GET /auth to obtain challenge & session cookie ---
        _LOGGER.debug(
            "NDW2 challenge auth: GET /auth on %s",
            mask_identifier(self._host),
        )
        try:
            async with asyncio.timeout(self._request_timeout):
                get_resp = await self._session.get(auth_url, allow_redirects=False)
        except asyncio.TimeoutError as err:
            raise KeeneticApiError("Challenge GET timed out") from err
        except aiohttp.ClientError as err:
            raise KeeneticApiError(f"Challenge GET failed: {err}") from err

        async with get_resp:
            _LOGGER.debug(
                "NDW2 challenge GET response: status=%s has_challenge=%s has_cookie=%s",
                get_resp.status,
                bool(get_resp.headers.get("X-NDM-Challenge")),
                bool(get_resp.headers.get("Set-Cookie")),
            )

            if get_resp.status not in (200, 401):
                # 5xx / odd statuses here mean a sick or rebooting router,
                # not bad credentials — keep it a connectivity error.
                text = await get_resp.text()
                raise KeeneticApiError(
                    f"Unexpected status during challenge GET ({get_resp.status}): "
                    f"{_response_summary(text)}"
                )

            challenge = get_resp.headers.get("X-NDM-Challenge")
            realm = get_resp.headers.get("X-NDM-Realm", "")

            if not challenge:
                raise KeeneticAuthError(
                    "Router did not return X-NDM-Challenge header. "
                    "This model may not support Challenge Auth — "
                    "try disabling 'Challenge Auth' and use Basic Auth instead."
                )

            _LOGGER.debug("NDW2 challenge received for realm=%s", realm)

            # Extract session cookie manually — HA's shared CookieJar(unsafe=False)
            # silently ignores cookies from bare IP addresses.
            session_cookie = _cookie_header_from_response(get_resp)

        # --- Step 2: Compute NDW2 hashes ---
        # ha1      = md5(username:realm:password)   [hex digest]
        # response = sha256(challenge + ha1)         [hex digest]
        ha1 = hashlib.md5(
            f"{self._username}:{realm}:{self._password}".encode(),
            usedforsecurity=False,
        ).hexdigest()
        response_hash = hashlib.sha256((challenge + ha1).encode()).hexdigest()

        # --- Step 3: POST /auth with credentials + explicit Cookie header ---
        payload = {"login": self._username, "password": response_hash}
        post_headers: Dict[str, str] = {}
        if session_cookie:
            post_headers["Cookie"] = session_cookie

        _LOGGER.debug(
            "NDW2 challenge: POST /auth on %s payload_login_set=%s",
            mask_identifier(self._host),
            bool(self._username),
        )

        try:
            async with asyncio.timeout(self._request_timeout):
                post_resp = await self._session.post(
                    auth_url,
                    json=payload,
                    headers=post_headers,
                )
        except asyncio.TimeoutError as err:
            raise KeeneticApiError("Challenge POST timed out") from err
        except aiohttp.ClientError as err:
            raise KeeneticApiError(f"Challenge POST failed: {err}") from err

        async with post_resp:
            post_text = await post_resp.text()
            _LOGGER.debug(
                "NDW2 challenge POST response: status=%s body_length=%s",
                post_resp.status,
                len(post_text),
            )

            if post_resp.status in (401, 403):
                raise KeeneticAuthError(
                    "Challenge auth rejected. Check the username, password and "
                    "challenge-auth setting."
                )
            if post_resp.status not in (200, 204):
                # Non-auth failure: router-side problem, not rejected creds.
                raise KeeneticApiError(
                    "Challenge auth failed "
                    f"(status={post_resp.status}, body={_response_summary(post_text)!r})"
                )
            session_cookie = _cookie_header_from_response(post_resp) or session_cookie

        if not session_cookie:
            # A 200/204 with no session cookie would mark us "authenticated"
            # with an empty auth header — every RCI call then 401s and loops
            # back here. Surface it as a connectivity-class failure instead.
            raise KeeneticApiError(
                "Challenge auth succeeded but the router returned no session cookie"
            )

        # Store cookie in _auth_header so every subsequent RCI request includes it.
        self._auth_header = {"Cookie": session_cookie}
        self._authenticated = True

        _LOGGER.debug(
            "Authenticated to Keenetic router at %s:%s (NDW2 challenge OK)",
            mask_identifier(self._host),
            self._port,
        )

    async def _ensure_auth(self) -> None:
        """Ensure we are authenticated before making an RCI call.

        The lock serialises concurrent refreshes. Without it, every RCI
        call hitting an expired session would trigger its own auth handshake
        in parallel, overwriting ``_auth_header`` mid-flight and producing
        spurious 401s on otherwise valid requests.
        """
        if self._authenticated:
            return
        async with self._auth_lock:
            if self._authenticated:
                return
            if self._use_challenge_auth:
                await self._async_authenticate_challenge()
            else:
                await self._async_authenticate()
