"""Transport primitives for the Keenetic API client."""

from __future__ import annotations

from typing import Any, Optional, Dict

import aiohttp
import asyncio
import base64
import logging

from ..const import DOMAIN
from .constants import RCI_ROOT, _SENSITIVE_NAMES
from .errors import KeeneticApiError, KeeneticAuthError
from .helpers import (
    _cookie_header_from_response,
    _is_endpoint_missing,
    _payload_summary,
    _response_summary,
)
from .target import normalize_connection_target

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.transport")


class _Transport:

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 100,
        ssl: bool = False,
        request_timeout: int = 15,
        use_challenge_auth: bool = False,
    ) -> None:
        target = normalize_connection_target(host, port, ssl)

        self._host = target.host
        self._username = username
        self._password = password
        self._port = target.port
        self._ssl = target.ssl
        self._request_timeout = request_timeout
        self._use_challenge_auth = use_challenge_auth

        self._base = target.base_url

        self._session: Optional[aiohttp.ClientSession] = None
        self._auth_header: Optional[Dict[str, str]] = None
        self._authenticated: bool = False
        self._node_auth_headers: dict[tuple[str, int], Dict[str, str]] = {}

        # NOTE: __repr__ is overridden below so that any accidental log of
        # repr(client) / f"{client}" never exposes the password or username.

        # Capability caches. None -> unknown, False -> endpoint not on this
        # device/firmware (skip future calls to avoid router-side log spam),
        # True -> endpoint works. Pattern mirrors `_mws_member_supported`.
        self._mws_member_supported: bool | None = None
        self._crypto_map_supported: bool | None = None
        self._dns_proxy_supported: bool | None = None
        self._ping_check_supported: bool | None = None
        self._ndns_supported: bool | None = None
        self._ipsec_diagnostics_supported: bool | None = None
        # Hotspot host RCI subpaths that have responded "not found" — skip
        # them on subsequent polls so we stop spamming the router log.
        self._hotspot_subpath_skip: set[str] = set()
        # Serialise authentication refreshes so concurrent RCI calls do not
        # race on `_auth_header` / `_authenticated`.
        self._auth_lock: asyncio.Lock = asyncio.Lock()

    def __repr__(self) -> str:
        """Redacted repr — never expose username/password in logs or tracebacks."""
        return (
            f"KeeneticClient(host={self._host!r}, port={self._port}, "
            f"ssl={self._ssl}, username='<redacted>', password='<redacted>', "
            f"challenge_auth={self._use_challenge_auth})"
        )

    __str__ = __repr__

    def _basic_auth_headers(self) -> Dict[str, str]:
        """Return Basic auth headers without exposing credentials to logs."""
        auth_string = base64.b64encode(
            f"{self._username}:{self._password}".encode()
        ).decode()
        return {"Authorization": f"Basic {auth_string}"}

    async def async_start(self, session: aiohttp.ClientSession) -> None:
        """Attach an aiohttp session and authenticate."""
        self._session = session
        if self._use_challenge_auth:
            await self._async_authenticate_challenge()
        else:
            await self._async_authenticate()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Dict[str, Any] | None = None,
        json: Any | None = None,
        allow_text: bool = False,
    ) -> Any:
        """Perform a raw HTTP request to Keenetic."""
        if self._session is None:
            raise KeeneticApiError("ClientSession is not set")

        await self._ensure_auth()

        url = f"{self._base}{path}"
        headers: Dict[str, str] = dict(self._auth_header or {})

        _LOGGER.debug(
            "Keenetic request: %s %s params=%s json=%s",
            method,
            url,
            params,
            _payload_summary(json),
        )

        try:
            async with asyncio.timeout(self._request_timeout):
                resp = await self._session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                )
                async with resp:
                    # Auth cookies can expire. Retry once after a fresh
                    # handshake before surfacing a real auth failure to HA.
                    if resp.status == 401:
                        await resp.read()
                        self._authenticated = False
                        await self._ensure_auth()
                        retry_headers: Dict[str, str] = dict(self._auth_header or {})
                        resp = await self._session.request(
                            method,
                            url,
                            params=params,
                            json=json,
                            headers=retry_headers,
                        )
                        async with resp:
                            return await self._handle_response(
                                resp, path, allow_text=allow_text
                            )

                    return await self._handle_response(
                        resp, path, allow_text=allow_text
                    )
        except asyncio.TimeoutError as err:
            raise KeeneticApiError(f"Timeout for {path}") from err
        except aiohttp.ClientError as err:
            raise KeeneticApiError(f"Connection error: {err}") from err

    async def _handle_response(
        self,
        resp: aiohttp.ClientResponse,
        path: str,
        *,
        allow_text: bool = False,
    ) -> Any:
        """Normalize an aiohttp response into JSON/text or a domain error."""
        if resp.status == 401:
            text = await resp.text()
            self._authenticated = False
            raise KeeneticAuthError(
                f"Authentication rejected for {path}: {_response_summary(text)}"
            )

        if resp.status >= 400:
            text = await resp.text()
            if resp.status == 502:
                raise KeeneticApiError(
                    "HTTP error 502 for "
                    f"{path}: KeenDNS protected web app was reached, but its "
                    "internal published application/upstream is unavailable or "
                    f"misconfigured: {_response_summary(text)}"
                )
            raise KeeneticApiError(
                f"HTTP error {resp.status} for {path}: {_response_summary(text)}"
            )

        if allow_text:
            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype:
                return await resp.json()
            return await resp.text()

        return await resp.json()

    async def _rci_get(
        self,
        subpath: str,
        *,
        params: Dict[str, Any] | None = None,
    ) -> Any:
        """GET /rci/<subpath>."""
        path = f"{RCI_ROOT}/{subpath.lstrip('/')}"
        return await self._request("GET", path, params=params)

    async def _rci_post(
        self,
        subpath: str,
        json: Any,
        *,
        allow_text: bool = False,
    ) -> Any:
        """POST /rci/<subpath>."""
        path = f"{RCI_ROOT}/{subpath.lstrip('/')}"
        return await self._request("POST", path, json=json, allow_text=allow_text)

    async def _rci_parse(self, command: str) -> Any:
        """Execute a CLI-like command via /rci/parse."""
        # JSON body sadece string: "interface Wireguard0 up"
        return await self._rci_post("parse", command, allow_text=True)
