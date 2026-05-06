"""Low-level async API client for Keenetic Router Pro integration (Basic Auth to /rci)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Dict, List
from urllib.parse import urlparse
from homeassistant.exceptions import HomeAssistantError

import aiohttp
import async_timeout
import asyncio
import base64
import hashlib
import logging
import re

from .const import DOMAIN

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api")

RCI_ROOT = "/rci"
_SENSITIVE_NAMES = frozenset(
    {"authorization", "cookie", "key", "pass", "password", "psk", "secret"}
)
_SENSITIVE_RESPONSE_RE = re.compile(
    r'(?i)("?(?:authorization|cookie|key|pass|password|psk|secret)"?\s*[:=]\s*)'
    r'("[^"]*"|\'[^\']*\'|[^,\s;}\]]+)'
)
_DNS_PROXY_STAT_RE = re.compile(
    r"^\s*(?P<ip>\S+)\s+"
    r"(?P<port>\d+)\s+"
    r"(?P<sent>\d+)\s+"
    r"(?P<answered>\d+)\s+"
    r"(?P<nxdomain>\d+)\s+"
    r"(?P<median>\d+)ms\s+"
    r"(?P<average>\d+)ms\s+"
    r"(?P<rank>\d+)\s*$"
)


class KeeneticApiError(Exception):
    """Base API error."""


class KeeneticAuthError(KeeneticApiError):
    """Authentication failed."""


@dataclass(frozen=True)
class KeeneticConnectionTarget:
    """Normalized Keenetic HTTP target."""

    host: str
    port: int
    ssl: bool

    @property
    def base_url(self) -> str:
        """Return the normalized base URL for API requests."""
        scheme = "https" if self.ssl else "http"
        return f"{scheme}://{self.host}:{self.port}"


def normalize_connection_target(host: str, port: int, ssl: bool) -> KeeneticConnectionTarget:
    """Normalize host/port/SSL input from config flows and existing entries.

    ``host`` may be a bare host name/IP or a full URL with an optional port.
    Paths, query strings and fragments are rejected because the integration
    appends its own ``/rci/...`` paths.
    """
    raw_host = str(host or "").strip()
    if not raw_host:
        raise KeeneticApiError("Host is required")

    parsed = urlparse(raw_host if "://" in raw_host else f"//{raw_host}")
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        raise KeeneticApiError(f"Unsupported URL scheme: {parsed.scheme}")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise KeeneticApiError("Host must not include a path, query string or fragment")

    normalized_host = parsed.hostname or raw_host
    normalized_host = normalized_host.strip()
    if not normalized_host:
        raise KeeneticApiError("Host is required")
    if any(ch.isspace() for ch in normalized_host):
        raise KeeneticApiError("Host must not contain whitespace")

    normalized_ssl = parsed.scheme == "https" if parsed.scheme else bool(ssl)
    try:
        url_port = parsed.port
    except ValueError as err:
        raise KeeneticApiError("Port must be between 1 and 65535") from err
    try:
        normalized_port = url_port if url_port is not None else int(port)
    except (TypeError, ValueError) as err:
        raise KeeneticApiError("Port must be between 1 and 65535") from err
    if not 1 <= normalized_port <= 65535:
        raise KeeneticApiError("Port must be between 1 and 65535")

    return KeeneticConnectionTarget(
        host=normalized_host,
        port=normalized_port,
        ssl=normalized_ssl,
    )


def _validate_cli_arg(value: str, label: str) -> str:
    """Return a safe Keenetic CLI token or raise for command injection input."""
    candidate = str(value).strip()
    if not candidate:
        raise KeeneticApiError(f"Empty {label}")
    if any(ch in candidate for ch in ("\r", "\n", ";")):
        raise KeeneticApiError(f"Unsafe {label}")
    return candidate


def _response_summary(text: str, limit: int = 240) -> str:
    """Return a short, single-line response excerpt with obvious secrets redacted."""
    summary = " ".join(str(text).split())
    if not summary:
        return "<empty>"
    summary = _SENSITIVE_RESPONSE_RE.sub(r"\1<redacted>", summary)
    if len(summary) > limit:
        return f"{summary[:limit]}..."
    return summary


def _payload_summary(payload: Any) -> Any:
    """Return a compact, non-secret representation of an outgoing JSON payload."""
    if payload is None:
        return None
    if isinstance(payload, dict):
        return {
            str(key): "<redacted>"
            if str(key).lower() in _SENSITIVE_NAMES
            else type(value).__name__
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return f"list[{len(payload)}]"
    return type(payload).__name__


def _dict_items(value: Any) -> List[Dict[str, Any]]:
    """Return dict entries from a Keenetic list/dict payload."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    return []


def _nested_dict_items(data: Any, *keys: str) -> List[Dict[str, Any]]:
    """Return dict entries from a list payload or first matching nested key."""
    if isinstance(data, list):
        return _dict_items(data)
    if not isinstance(data, dict):
        return []

    for key in keys:
        items = _dict_items(data.get(key))
        if items:
            return items
    return []


class KeeneticClient:

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

        # Mesh/Wi-Fi System (MWS) capability cache:
        # None  -> unknown (not checked yet)
        # False -> endpoint missing on this device/firmware (avoid router log spam)
        # True  -> endpoint works
        self._mws_member_supported: bool | None = None

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

    async def _async_authenticate(self) -> None:
        """Perform Basic auth against /rci/, like original ha_keenetic."""
        if self._session is None:
            raise KeeneticAuthError("ClientSession is not set")

        headers = self._basic_auth_headers()
        url = f"{self._base}{RCI_ROOT}/"

        _LOGGER.debug("Authenticating to Keenetic via %s", url)

        try:
            async with async_timeout.timeout(self._request_timeout):
                resp = await self._session.get(url, headers=headers)
                async with resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise KeeneticAuthError(
                            f"Auth failed (status {resp.status}): "
                            f"{_response_summary(text)}"
                        )
        except asyncio.TimeoutError as err:
            raise KeeneticAuthError("Auth connection timed out") from err
        except aiohttp.ClientError as err:
            raise KeeneticAuthError(f"Auth connection failed: {err}") from err

        self._auth_header = headers
        self._authenticated = True
        _LOGGER.debug(
            "Authenticated to Keenetic router at %s:%s",
            self._host,
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
        _LOGGER.debug("NDW2 challenge auth: GET %s", auth_url)
        try:
            async with async_timeout.timeout(self._request_timeout):
                get_resp = await self._session.get(auth_url, allow_redirects=False)
        except asyncio.TimeoutError as err:
            raise KeeneticAuthError("Challenge GET timed out") from err
        except aiohttp.ClientError as err:
            raise KeeneticAuthError(f"Challenge GET failed: {err}") from err

        _LOGGER.debug(
            "NDW2 challenge GET response: status=%s has_challenge=%s has_cookie=%s",
            get_resp.status,
            bool(get_resp.headers.get("X-NDM-Challenge")),
            bool(get_resp.headers.get("Set-Cookie")),
        )

        if get_resp.status not in (200, 401):
            text = await get_resp.text()
            raise KeeneticAuthError(
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

        # Extract session cookie from Set-Cookie header
        session_cookie: str | None = None
        # Extract session cookie manually — HA's shared CookieJar(unsafe=False)
        # silently ignores cookies from bare IP addresses.
        raw_cookie = get_resp.headers.get("Set-Cookie", "")
        if raw_cookie:
            cookie_kv = raw_cookie.split(";")[0].strip()
            if "=" in cookie_kv:
                session_cookie = cookie_kv

        # --- Step 2: Compute NDW2 hashes ---
        # ha1      = md5(username:realm:password)   [hex digest]
        # response = sha256(challenge + ha1)         [hex digest]
        ha1 = hashlib.md5(
            f"{self._username}:{realm}:{self._password}".encode()
        ).hexdigest()
        response_hash = hashlib.sha256((challenge + ha1).encode()).hexdigest()

        # --- Step 3: POST /auth with credentials + explicit Cookie header ---
        payload = {"login": self._username, "password": response_hash}
        post_headers: Dict[str, str] = {}
        if session_cookie:
            post_headers["Cookie"] = session_cookie

        _LOGGER.debug("NDW2 challenge: POST %s payload_login_set=%s", auth_url, bool(self._username))

        try:
            async with async_timeout.timeout(self._request_timeout):
                post_resp = await self._session.post(
                    auth_url,
                    json=payload,
                    headers=post_headers,
                )
        except asyncio.TimeoutError as err:
            raise KeeneticAuthError("Challenge POST timed out") from err
        except aiohttp.ClientError as err:
            raise KeeneticAuthError(f"Challenge POST failed: {err}") from err

        post_text = await post_resp.text()
        _LOGGER.debug(
            "NDW2 challenge POST response: status=%s body_length=%s",
            post_resp.status,
            len(post_text),
        )

        if post_resp.status == 401:
            raise KeeneticAuthError(
                "Challenge auth rejected. Check the username, password and "
                "challenge-auth setting."
            )
        if post_resp.status not in (200, 204):
            raise KeeneticAuthError(
                "Challenge auth failed "
                f"(status={post_resp.status}, body={_response_summary(post_text)!r})"
            )

        # Store cookie in _auth_header so every subsequent RCI request includes it.
        self._auth_header = {"Cookie": session_cookie} if session_cookie else {}
        self._authenticated = True

        _LOGGER.debug(
            "Authenticated to Keenetic router at %s:%s (NDW2 challenge OK)",
            self._host,
            self._port,
        )

    async def _ensure_auth(self) -> None:
        """Ensure we are authenticated before making an RCI call."""
        if not self._authenticated:
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
            async with async_timeout.timeout(self._request_timeout):
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

    def _normalize_interfaces(self, raw: Any) -> List[Dict[str, Any]]:
        """Raw /rci/show/interface çıktısını evrensel listeye çevir.

        Dict anahtarları (ör. "ISP", "GigabitEthernet0") interface'in adıdır.
        Kaybolmaması için, içeride "id" yoksa anahtar adı enjekte edilir.
        """
        if isinstance(raw, dict):
            # {"GigabitEthernet0": {...}, "WifiMaster0/AccessPoint0": {...}}
            result: List[Dict[str, Any]] = []
            for key, val in raw.items():
                if not isinstance(val, dict):
                    continue
                if "id" not in val:
                    val = {**val, "id": key}
                result.append(val)
            return result
        if isinstance(raw, list):
            # [ {...}, {...} ]
            return _dict_items(raw)
        return []

    async def async_ping_ip(self, ip_address: str, timeout: float = 2.0) -> bool:
        """Ping an IP address using the router's ping functionality.
        
        Returns True if the host is reachable, False otherwise.
        """
        try:
            ip_address = _validate_cli_arg(ip_address, "IP address")

            result = await self._rci_parse(f"ip ping {ip_address} count 1")

            if result is None:
                return False

            result_str = str(result).lower()

            if "1 received" in result_str or "bytes from" in result_str:
                return True

            # Check for failure patterns
            if "0 received" in result_str or "100% packet loss" in result_str:
                return False

            if "timeout" not in result_str and "unreachable" not in result_str:
                return True

            return False

        except Exception as err:
            _LOGGER.debug("Ping to %s failed: %s", ip_address, err)
            return False

    async def async_ping_multiple(
        self, 
        ip_addresses: List[str], 
        timeout: float = 2.0
    ) -> Dict[str, bool]:
        """Ping multiple IP addresses concurrently.
        
        Returns a dict mapping IP address to reachability status.
        """
        if not ip_addresses:
            return {}

        tasks = [self.async_ping_ip(ip, timeout) for ip in ip_addresses]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        ping_results: Dict[str, bool] = {}
        for ip, result in zip(ip_addresses, results):
            if isinstance(result, Exception):
                ping_results[ip] = False
            else:
                ping_results[ip] = bool(result)

        return ping_results

    async def async_get_system_info(self) -> Dict[str, Any]:
        """Return basic system info: hostname, version, cpu, memory, uptime, etc."""
        data = await self._rci_get("show/system")
        return data or {}

    async def async_get_current_version_info(self) -> Dict[str, Any]:
        """Return version info"""
        data = await self._rci_get("show/version")
        return data or {}
    
    async def async_get_available_version_info(self) -> Dict[str, Any]:
        """Return version info"""
        data = await self._rci_get("components/check-update")
        return data or {}

    async def async_get_port_info(self, interfaces: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        """Return physical port information for the main router.
        
        Ports are found in show/interface as top-level entries with type "Port".
        Example keys: "0", "1", "2", "3", "4" with label and link status.
        
        Also checks GigabitEthernet*.port nested dicts as fallback.
        """
        if interfaces is None:
            interfaces = await self.async_get_interfaces()
        
        if not interfaces or not isinstance(interfaces, dict):
            return []
        
        ports: List[Dict[str, Any]] = []
        seen_labels: set = set()
        
        # Method 1: Top-level Port entries (keys like "0", "1", "2", "3", "4")
        for iface_id, iface in interfaces.items():
            if not isinstance(iface, dict):
                continue
            if iface.get("type") != "Port":
                continue
            
            label = iface.get("label") or iface.get("interface-name") or iface_id
            if label in seen_labels:
                continue
            seen_labels.add(label)
            
            entry: Dict[str, Any] = {
                "label": label,
                "appearance": iface.get("type"),
                "link": iface.get("link", "unknown"),
            }
            if iface.get("link") == "up":
                entry["speed"] = iface.get("speed")
                entry["duplex"] = iface.get("duplex")
            ports.append(entry)
        
        if ports:
            # Sort by label for consistent ordering
            ports.sort(key=lambda p: str(p.get("label", "")))
            _LOGGER.debug("Found %d main router ports from top-level Port entries", len(ports))
            return ports
        
        # Method 2: Nested port dicts inside GigabitEthernet interfaces
        for iface_id, iface in interfaces.items():
            if not isinstance(iface, dict):
                continue
            if iface.get("type") != "GigabitEthernet":
                continue
            
            port_data = iface.get("port")
            if not port_data or not isinstance(port_data, dict):
                continue
            
            # port can be a single dict (GigabitEthernet1) or dict of dicts (GigabitEthernet0)
            if "label" in port_data:
                # Single port dict
                label = port_data.get("label") or port_data.get("interface-name")
                if label and label not in seen_labels:
                    seen_labels.add(label)
                    entry = {
                        "label": label,
                        "appearance": port_data.get("type"),
                        "link": port_data.get("link", "unknown"),
                    }
                    if port_data.get("link") == "up":
                        entry["speed"] = port_data.get("speed")
                        entry["duplex"] = port_data.get("duplex")
                    ports.append(entry)
            else:
                # Dict of port dicts (keyed by port number)
                for port_key, port_val in port_data.items():
                    if not isinstance(port_val, dict):
                        continue
                    label = port_val.get("label") or port_val.get("interface-name") or port_key
                    if label in seen_labels:
                        continue
                    seen_labels.add(label)
                    entry = {
                        "label": label,
                        "appearance": port_val.get("type"),
                        "link": port_val.get("link", "unknown"),
                    }
                    if port_val.get("link") == "up":
                        entry["speed"] = port_val.get("speed")
                        entry["duplex"] = port_val.get("duplex")
                    ports.append(entry)
        
        if ports:
            ports.sort(key=lambda p: str(p.get("label", "")))
            _LOGGER.debug("Found %d main router ports from nested GigabitEthernet port data", len(ports))
        else:
            _LOGGER.warning("No physical ports found for main router")
        
        return ports

    async def async_get_interfaces(self) -> Dict[str, Any]:
        """Return raw interfaces dictionary from /rci/show/interface."""
        data = await self._rci_get("show/interface")
        return data or {}

    async def async_get_interface_stat(self, name: str) -> Dict[str, Any]:
        """Return statistics (traffic, speed) for a specific interface."""
        safe_name = _validate_cli_arg(name, "interface name")
        try:
            data = await self._rci_parse(f"show interface {safe_name} stat")
            if isinstance(data, dict):
                stat_keys = {"rxbytes", "txbytes", "rxspeed", "txspeed"}
                if stat_keys.intersection(data):
                    return data
        except Exception as err:
            _LOGGER.debug(
                "Parse-style interface stat failed for %s: %s; trying GET fallback",
                safe_name,
                err,
            )
        return await self._rci_get(
            "show/interface/stat", params={"name": safe_name}
        ) or {}

    async def async_get_clients(self) -> List[Dict[str, Any]]:

        last_data: Any = None

        for subpath in ("show/ip/hotspot/host", "ip/hotspot/host"):
            try:
                data = await self._rci_get(subpath)
                last_data = data
            except Exception:
                continue

            items = _nested_dict_items(data, "hosts", "host", "items")

            if items:
                return items

        _LOGGER.debug(
            "No clients parsed from hotspot host response type=%s",
            type(last_data).__name__,
        )
        return []


    async def async_get_wireguard_status(
        self, interfaces: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        """Return WireGuard interfaces and their status."""
        if interfaces is None:
            interfaces = await self.async_get_interfaces()
        iface_list = self._normalize_interfaces(interfaces)

        profiles: Dict[str, Any] = {}

        for item in iface_list:
            itype = (item.get("type") or "").lower()
            traits = [t.lower() for t in item.get("traits", []) if isinstance(t, str)]
            name = (
                item.get("id")
                or item.get("interface-name")
                or item.get("name")
                or item.get("ifname")
            )
            if not name:
                continue

            is_wg = itype == "wireguard" or "wireguard" in "".join(traits)
            if not is_wg:
                continue

            wg_info = item.get("wireguard") or {}
            description = item.get("description") or name 

            remote = None
            rx_val = wg_info.get("rxbytes") or item.get("rxbytes")
            tx_val = wg_info.get("txbytes") or item.get("txbytes")

            peer = wg_info.get("peer")

            if isinstance(peer, list) and peer:
                p = peer[0]
                if remote is None:
                    remote = p.get("remote-endpoint-address")
                if rx_val is None:
                    rx_val = p.get("rxbytes")
                if tx_val is None:
                    tx_val = p.get("txbytes")
            elif isinstance(peer, dict):
                if remote is None:
                    remote = peer.get("remote-endpoint-address")
                if rx_val is None:
                    rx_val = peer.get("rxbytes")
                if tx_val is None:
                    tx_val = peer.get("txbytes")

            profiles[name] = {

                "label": description,
                "enabled": str(item.get("state", "")).lower() == "up",
                "state": item.get("state"),
                "address": item.get("address"),
                "remote": remote,
                "uptime": item.get("uptime"),
                "rx": rx_val,
                "tx": tx_val,
                "rxbytes": rx_val,
                "txbytes": tx_val,
            }

        return {"profiles": profiles}


    async def async_get_wifi_networks(
        self, interfaces: Dict[str, Any] | None = None
    ) -> List[Dict[str, Any]]:


        if interfaces is None:
            interfaces = await self.async_get_interfaces()
        iface_list = self._normalize_interfaces(interfaces)

        bridge_labels: Dict[str, str] = {}
        for item in iface_list:
            itype = (item.get("type") or "").lower()
            if itype != "bridge":
                continue

            bid = item.get("id") or item.get("interface-name")
            if not bid:
                continue

            label = (
                item.get("interface-name")
                or item.get("description")
                or bid
            )
            bridge_labels[str(bid)] = str(label)

        ap_items: List[Dict[str, Any]] = []
        for item in iface_list:
            raw_id = (
                item.get("id")
                or item.get("interface-name")
                or item.get("name")
                or item.get("ifname")
            )
            if not raw_id:
                continue

            itype = (item.get("type") or "").lower()
            traits = [t.lower() for t in item.get("traits", []) if isinstance(t, str)]
            id_lower = raw_id.lower()

            is_ap = (
                "accesspoint" in id_lower
                or itype == "accesspoint"
                or ("wifi" in "".join(traits) and "accesspoint" in "".join(traits))
            )
            if not is_ap:
                continue

            ssid = (item.get("ssid") or "").strip()
            group = str(item.get("group") or "").strip()
            if not ssid and not group:
                continue

            clone = dict(item)
            clone["__id"] = raw_id
            ap_items.append(clone)

        groups: Dict[str, Dict[str, Any]] = {}
        for item in ap_items:
            raw_id = item["__id"]
            ssid = (item.get("ssid") or "").strip()
            group = str(item.get("group") or "").strip()
            base_id = raw_id.split("/")[0]

            group_key = group or ssid or base_id

            g = groups.setdefault(
                group_key,
                {
                    "ssid": "",
                    "group": group,
                    "aps": [],
                },
            )

            # A real broadcast SSID from any AP in the group always wins.
            # This matters because Keenetic omits the `ssid` field on
            # disabled APs: on dual-band networks the 2.4 GHz AP may come
            # first with no SSID, and if we let a bridge-label fallback
            # latch in here, we would never pick up the real SSID from
            # the 5 GHz AP that arrives later.
            if ssid:
                g["ssid"] = ssid

            g["aps"].append(item)

        # Second pass: any group that still has no real SSID (e.g. every
        # AP in the group is disabled and the firmware stripped the field
        # from all of them) falls back to the bridge label or group id,
        # so the entry at least has *some* logical name for display.
        for g in groups.values():
            if g["ssid"]:
                continue
            grp = g["group"]
            if grp and grp in bridge_labels:
                g["ssid"] = bridge_labels[grp]
            elif grp:
                g["ssid"] = grp

        wifi_networks: List[Dict[str, Any]] = []

        for g in groups.values():
            logical_name = (g["ssid"] or "").strip()
            group = g["group"]

            if not logical_name:
                if group and group in bridge_labels:
                    logical_name = bridge_labels[group]
                elif group:
                    logical_name = group
                else:
                    logical_name = "Wi-Fi"

            per_band: Dict[str, Dict[str, Any]] = {}

            for ap in g["aps"]:
                raw_id = ap["__id"]
                band = str(ap.get("band") or "").strip()

                if not band:
                    base_id = raw_id.split("/")[0].lower()
                    chan = str(ap.get("channel") or "")
                    if "wifimaster0" in base_id:
                        band = "2.4"
                    elif "wifimaster1" in base_id:
                        band = "5"
                    elif chan:
                        try:
                            ch = int(chan)
                            band = "2.4" if 1 <= ch <= 14 else "5"
                        except ValueError:
                            pass

                if band:
                    b_lower = band.lower()
                    if "2.4" in b_lower or b_lower == "2":
                        band_label = "2.4 GHz"
                    elif "5" in b_lower:
                        band_label = "5 GHz"
                    else:
                        band_label = band
                else:
                    band_label = ""

                key = band_label or "default"
                if key in per_band:
                    continue
                per_band[key] = ap

            for band_label, ap in per_band.items():
                raw_id = ap["__id"]
                state = str(ap.get("state", "")).lower()
                enabled = state == "up"

                vis_name = logical_name
                if band_label:
                    vis_name = f"{logical_name} {band_label}"

                net: Dict[str, Any] = {
                    "id": raw_id,          
                    "name": vis_name,      
                    "ssid": logical_name,
                    "band": band_label,
                    "enabled": enabled,
                    "state": ap.get("state"),
                    "group": group or None,
                    "channel": ap.get("channel"),
                    "tx_power": ap.get("tx-power") or ap.get("tx_power"),
                }

                for k in list(net.keys()):
                    if any(
                        pat in k.lower()
                        for pat in ("password", "pass", "psk", "wpa", "key", "secret")
                    ):
                        net.pop(k, None)

                wifi_networks.append(net)

        return wifi_networks




    async def async_set_wifi_enabled(self, interface_name: str, enabled: bool) -> None:
        """Enable or disable a Wi-Fi interface via RCI parse."""
        interface_name = _validate_cli_arg(interface_name, "interface name")
        cmd = f"interface {interface_name} {'up' if enabled else 'down'}"
        _LOGGER.debug("Set Wi-Fi %s enabled=%s via: %s", interface_name, enabled, cmd)
        await self._rci_parse(cmd)

    async def async_set_wireguard_enabled(self, interface_name: str, enabled: bool) -> None:
        """Enable or disable a WireGuard interface via RCI parse.

        Kept for backwards compatibility; delegates to the generic
        async_set_interface_enabled which works for any interface type
        (WireGuard, OpenVPN, SSTP, IPsec, ...).
        """
        await self.async_set_interface_enabled(interface_name, enabled)

    async def async_set_interface_enabled(self, interface_name: str, enabled: bool) -> None:
        """Enable or disable any interface via RCI 'interface X up/down'."""
        interface_name = _validate_cli_arg(interface_name, "interface name")
        cmd = f"interface {interface_name} {'up' if enabled else 'down'}"
        _LOGGER.debug(
            "Set interface %s enabled=%s via: %s",
            interface_name,
            enabled,
            cmd,
        )
        await self._rci_parse(cmd)

    async def async_reboot(self) -> None:
        """Reboot the router via 'system reboot' command."""
        cmd = "system reboot"
        _LOGGER.warning("Sending router reboot command via RCI parse")
        await self._rci_parse(cmd)

    async def async_get_vpn_tunnels(
        self, interfaces: Dict[str, Any] | None = None
    ) -> dict[str, dict[str, Any]]:
        """Auto-discover VPN-like interfaces (WireGuard, OpenVPN, IPsec, ...).

        Returns:
            {
              "profiles": {
                 "Wireguard0": {...},
                 "Wireguard1": {...},
                 "OpenVpn0": {...},
                 ...
              }
            }
        """
        if interfaces is None:
            interfaces = await self.async_get_interfaces()
        iface_list = self._normalize_interfaces(interfaces)

        VPN_TYPES = {
            "wireguard",
            "openvpn",
            "ipsec",
            "l2tp",
            "pptp",
            "sstp",
            "zerotier",
            "tor",
        }

        profiles: dict[str, dict[str, Any]] = {}

        for item in iface_list:
            itype = str(item.get("type") or "").lower()
            if itype not in VPN_TYPES:
                continue

            iface_id = (
                item.get("id")
                or item.get("interface-name")
                or item.get("name")
            )
            if not iface_id:
                continue

            label = (
                item.get("description")
                or item.get("interface-name")
                or iface_id
            )

            state = str(item.get("state") or "").lower()
            summary = item.get("summary") or {}
            layer = summary.get("layer") or {}
            conf = str(layer.get("conf") or "").lower()

            enabled = not (conf == "disabled" or state == "down")

            profiles[str(iface_id)] = {
                "id": iface_id,
                "type": item.get("type") or itype,
                "label": str(label),
                "enabled": enabled,
                "state": item.get("state"),
            }

        return {"profiles": profiles}

    async def async_get_wan_status(
        self, interfaces: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        """Get WAN interface status including external IP address.

        PPPoE bağlantısı varsa oradan, yoksa WAN interface'inden IP alır.

        Durum mantığı:
          - "connected"  → interface up VE IP mevcut
          - "link_up"    → interface up AMA IP yok (ISP sorunu vb.)
          - "down"       → interface bulunamadı veya down
        """
        if interfaces is None:
            interfaces = await self.async_get_interfaces()
        iface_list = self._normalize_interfaces(interfaces)

        # ---------- yardımcı: interface'den IP çıkar ----------
        def _extract_ip(iface: Dict[str, Any]) -> str | None:
            """Try every known Keenetic address field/format."""
            # 1) global-address (Keenetic 4.x+)
            gaddr = iface.get("global-address")
            if isinstance(gaddr, list) and gaddr:
                first = gaddr[0]
                if isinstance(first, dict):
                    ip = first.get("address") or first.get("ip")
                    if ip:
                        return str(ip).split("/")[0]
                elif isinstance(first, str):
                    return first.split("/")[0]

            # 2) address alanı
            address = iface.get("address")
            if isinstance(address, list) and address:
                first = address[0]
                if isinstance(first, dict):
                    ip = first.get("address") or first.get("ip")
                    if ip:
                        return str(ip).split("/")[0]
                elif isinstance(first, str):
                    return first.split("/")[0]
            elif isinstance(address, str) and address:
                return address.split("/")[0]

            # 3) doğrudan ip / ipv4 alanı
            for key in ("ip", "ipv4", "ip-address"):
                val = iface.get(key)
                if val and isinstance(val, str):
                    return val.split("/")[0]

            return None

        # ---------- yardımcı: sonuç oluştur ----------
        def _build_result(
            iface: Dict[str, Any], wan_type: str
        ) -> Dict[str, Any]:
            wan_ip = _extract_ip(iface)
            link_state = str(iface.get("state") or "").lower()
            status = "connected" if (link_state == "up" and wan_ip) else (
                "link_up" if link_state == "up" else "down"
            )
            return {
                "status": status,
                "ip": wan_ip,
                "interface": iface.get("id") or iface.get("interface-name"),
                "uptime": iface.get("uptime"),
                "gateway": (
                    iface.get("gateway")
                    or iface.get("remote")
                    or iface.get("default-gateway")
                ),
                "type": wan_type,
                "link": link_state,
            }

        # ---------- yardımcı: WAN keyword eşleşmesi ----------
        WAN_KEYWORDS = ("wan", "internet", "isp", "broadband")

        def _is_wan_iface(iface: Dict[str, Any]) -> bool:
            """Interface'in WAN olup olmadığını birden fazla ipucuyla belirle."""
            # security-level: public → Keenetic'te WAN demek
            sec = str(iface.get("security-level") or "").lower()
            if sec == "public":
                return True
            # role: inet
            role = str(iface.get("role") or "").lower()
            if role in ("inet", "internet", "wan"):
                return True
            # İsim tabanlı arama
            name_fields = [
                iface.get("name"),
                iface.get("ifname"),
                iface.get("id"),
                iface.get("interface-name"),
                iface.get("description"),
                iface.get("type"),
            ]
            name_joined = " ".join(str(v) for v in name_fields if v).lower()
            return any(k in name_joined for k in WAN_KEYWORDS)

        # ========== 1) PPPoE (öncelikli) ==========
        for iface in iface_list:
            itype = str(iface.get("type") or "").lower()
            state = str(iface.get("state") or "").lower()
            if itype == "pppoe" and state == "up":
                return _build_result(iface, "pppoe")

        # ========== 2) WAN interface (state == "up") ==========
        for iface in iface_list:
            state = str(iface.get("state") or "").lower()
            if state == "up" and _is_wan_iface(iface):
                return _build_result(iface, "ethernet")

        # ========== 3) WAN interface (state != "up" — link_up/down) ==========
        for iface in iface_list:
            if _is_wan_iface(iface):
                return _build_result(iface, "ethernet")

        return {"status": "down", "ip": None, "link": "down"}

    async def async_get_wan_interfaces(
        self, interfaces: Dict[str, Any] | None = None
    ) -> List[Dict[str, Any]]:
        """Return per-uplink info for every configured WAN interface.

        Enumerates *all* uplink-capable interfaces Keenetic knows about —
        not just the currently active one — so Home Assistant can expose
        a full picture of the multi-WAN / failover configuration.

        WAN detection logic (derived from real show/interface output):
          - `global: true` — interface has a routable, "public-facing" role
          - `priority` is set — interface participates in Keenetic's
            uplink priority ordering (this is what puts an interface into
            the "Connection priorities" list in the web UI)
          - `role` contains "inet" — explicit uplink tag
          Any interface matching (`global=true` AND `priority` is set),
          OR with `role` containing "inet", is treated as a WAN.

          Interfaces that are merely carriers for a PPPoE/VLAN (e.g. the
          raw GigabitEthernet1 below PPPoE0) are *not* WANs — they have
          `global: false` and no `priority`, so they fail the filter
          naturally. They show up as `via` / `underlying` on the WAN that
          rides on top of them.

        Each entry in the returned list contains:
            id                 interface id (PPPoE0, Wireguard0, ...)
            description        human-readable description from the router
                               UI ("Telekom", "Zurich"), falls back to id
            interface_name     the "interface-name" field (e.g. "ISP")
            type               interface type (PPPoE / Wireguard / ...)
            link_state         "up" / "down"
            enabled            bool — True when the interface is configured
                               up (summary.layer.conf != "disabled")
            global             bool — has a global (public) role
            defaultgw          bool — currently the default gateway
            priority           int — Keenetic uplink priority (higher wins)
            role               list[str] — e.g. ["inet"]
            security_level     "public" / "private" / "protected"
            ip                 current public IP, if any
            mask               subnet mask, if any
            uptime             seconds since the session came up
            underlying         id of the physical/logical interface this
                               session rides on (PPPoE `via`), if any
            remote             remote peer address (PPPoE/tunnel)
            mac                L2 address if applicable
            internet_access    bool — best-effort ping-check / reachability
                               heuristic (see _derive_internet_access)
            summary_layers     nested summary.layer dict (conf/link/ipv4/...)
            raw                the untouched interface dict, for consumers
                               that want a field we didn't pull out
        """
        if interfaces is None:
            interfaces = await self.async_get_interfaces()
        iface_list = self._normalize_interfaces(interfaces)

        def _is_wan(iface: Dict[str, Any]) -> bool:
            # Explicit uplink role is the strongest signal.
            role = iface.get("role")
            if isinstance(role, list) and any(
                str(r).lower() in ("inet", "internet", "wan") for r in role
            ):
                return True
            if isinstance(role, str) and role.lower() in ("inet", "internet", "wan"):
                return True

            # Otherwise: global + priority is how Keenetic marks an
            # interface as a ranked uplink. Both conditions must hold —
            # `global: true` alone catches LAN bridges in some configs,
            # and `priority` alone catches non-uplink routing tweaks.
            is_global = bool(iface.get("global"))
            has_priority = iface.get("priority") is not None
            return is_global and has_priority

        def _extract_ip(iface: Dict[str, Any]) -> str | None:
            # PPPoE/static: flat "address" string. Ethernet WANs in some
            # firmware versions use global-address/address lists.
            addr = iface.get("address")
            if isinstance(addr, str) and addr:
                return addr.split("/")[0]
            gaddr = iface.get("global-address")
            if isinstance(gaddr, list) and gaddr:
                first = gaddr[0]
                if isinstance(first, dict):
                    v = first.get("address") or first.get("ip")
                    if v:
                        return str(v).split("/")[0]
                elif isinstance(first, str):
                    return first.split("/")[0]
            if isinstance(addr, list) and addr:
                first = addr[0]
                if isinstance(first, dict):
                    v = first.get("address") or first.get("ip")
                    if v:
                        return str(v).split("/")[0]
                elif isinstance(first, str):
                    return first.split("/")[0]
            return None

        def _derive_enabled(iface: Dict[str, Any]) -> bool:
            # summary.layer.conf == "disabled" means the interface is
            # toggled off in the config — matches the UI toggle exactly.
            summary = iface.get("summary") or {}
            layer = summary.get("layer") or {}
            conf = str(layer.get("conf") or "").lower()
            if conf == "disabled":
                return False
            if conf == "running":
                return True
            # Fallback: if we don't have a summary, assume enabled unless
            # state says otherwise.
            return True

        def _derive_internet_access(iface: Dict[str, Any]) -> bool | None:
            """Best-effort ping-check / reachability indicator.

            Keenetic's raw show/interface output on this firmware does
            *not* expose the ping-check result as a distinct field — the
            red "NO INTERNET ACCESS (PING CHECK)" badge in the web UI is
            computed client-side from a different RCI call that's not
            uniformly available across firmware versions.

            As a pragmatic substitute we use:
                up  = state=="up" AND global AND has routable IP
                     AND summary.layer.ipv4 in {"running"}
                down = state != "up" OR global is false OR no IP
                unknown (None) = state up but no public IP yet (pending)

            This matches the user-visible "this WAN is actually usable"
            meaning for the common case (PPPoE up with IP, WG tunnel up
            with handshake) without false-positiving on carrier
            interfaces or half-initialised uplinks.
            """
            state = str(iface.get("state") or "").lower()
            if state != "up":
                return False
            if not iface.get("global"):
                return False
            ip = _extract_ip(iface)
            if not ip:
                summary = iface.get("summary") or {}
                layer = summary.get("layer") or {}
                if str(layer.get("ipv4") or "").lower() == "pending":
                    return None
                return False
            # Extra guard: PPPoE exposes `fail` when the last session
            # attempt failed.
            fail = str(iface.get("fail") or "").lower()
            if fail in ("yes", "true"):
                return False
            return True

        wans: List[Dict[str, Any]] = []
        for iface in iface_list:
            if not _is_wan(iface):
                continue
            iface_id = iface.get("id") or iface.get("interface-name")
            if not iface_id:
                continue

            role = iface.get("role")
            if isinstance(role, str):
                role_list = [role]
            elif isinstance(role, list):
                role_list = [str(r) for r in role]
            else:
                role_list = []

            wans.append({
                "id": iface_id,
                "description": iface.get("description") or iface.get("interface-name") or iface_id,
                "interface_name": iface.get("interface-name"),
                "type": iface.get("type"),
                "link_state": str(iface.get("state") or "down").lower(),
                "enabled": _derive_enabled(iface),
                "global": bool(iface.get("global")),
                "defaultgw": bool(iface.get("defaultgw")),
                "priority": iface.get("priority"),
                "role": role_list,
                "security_level": iface.get("security-level"),
                "ip": _extract_ip(iface),
                "mask": iface.get("mask"),
                "uptime": iface.get("uptime"),
                "underlying": iface.get("via"),
                "remote": iface.get("remote"),
                "mac": iface.get("mac"),
                "internet_access": _derive_internet_access(iface),
                "summary_layers": (iface.get("summary") or {}).get("layer") or {},
                "raw": iface,
            })

        return wans

    async def async_get_ping_check_status(self) -> Dict[str, Any]:
        """Return the router's ping-check results per interface.

        This is the authoritative "is the internet actually reachable
        through this WAN" signal — the same data that drives the red
        "NO INTERNET ACCESS (PING CHECK)" badge in the Keenetic web UI
        and that the router itself uses to decide when to fail over to
        a backup uplink.

        Endpoint: rci/show/ping-check
        Example response:
            {
              "pingcheck": [
                {
                  "profile": "default",
                  "host": ["captive.keenetic.net"],
                  "port": 80,
                  "update-interval": 30,
                  "max-fails": 3,
                  "mode": "icmp",
                  "interface": {
                    "PPPoE0": {
                      "successcount": 7,
                      "failcount": 0,
                      "status": "pass",
                      "ipcache": [
                        {"host": "captive.keenetic.net",
                         "addresses": ["135.181.129.158", "..."]}
                      ]
                    }
                  }
                }
              ]
            }

        Returns a flat dict keyed by interface id:
            {
              "PPPoE0": {
                "status": "pass",                 # "pass" | "fail"
                "success_count": 7,
                "fail_count": 0,
                "profile": "default",             # winning profile name
                "check_hosts": ["captive.keenetic.net"],
                "check_addresses": ["135.181.129.158", ...],
                "check_port": 80,
                "check_mode": "icmp",
                "update_interval": 30,
                "max_fails": 3,
                "all_profiles": [                 # every profile touching
                  {"profile": "...", "status": "...", ...}   # this iface
                ],
              }
            }

        A router may have multiple profiles bound to the same interface.

        IMPORTANT: profiles named `_WEBADMIN_<InterfaceId>` are NOT
        transient — current Keenetic firmware persists user-enabled
        Ping Check configurations under that name when the user toggles
        "Check the Availability of the Internet (Ping Check)" in the
        web UI. They have real `update-interval`, `max-fails`, real
        check hosts and live counters, and they ARE the authoritative
        ping-check signal for that WAN.

        We instead identify *truly* transient profiles by their target
        address: one-off connection tests target IANA documentation /
        TEST-NET ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24).
        Those are the only profiles we ignore.

        When multiple authoritative profiles report on the same interface,
        the aggregate status is "fail" if any profile is failing (matches
        how Keenetic itself treats the WAN as unusable for routing).
        """
        data = await self._rci_get("show/ping-check") or {}
        raw_profiles = data.get("pingcheck") or []
        if not isinstance(raw_profiles, list):
            return {}

        # Collect per-interface observations from every profile that
        # actually has results (profile without `interface` block is
        # just a definition with nothing attached yet).
        observations: Dict[str, List[Dict[str, Any]]] = {}
        for profile_entry in raw_profiles:
            if not isinstance(profile_entry, dict):
                continue
            iface_map = profile_entry.get("interface")
            if not isinstance(iface_map, dict) or not iface_map:
                continue

            profile_name = str(profile_entry.get("profile") or "")
            host = profile_entry.get("host")
            if isinstance(host, str):
                hosts = [host]
            elif isinstance(host, list):
                hosts = [str(h) for h in host if h]
            else:
                hosts = []

            for iface_id, iface_result in iface_map.items():
                if not isinstance(iface_result, dict):
                    continue
                ipcache = iface_result.get("ipcache") or []
                addresses: List[str] = []
                cache_hosts: List[str] = []
                if isinstance(ipcache, list):
                    for entry in ipcache:
                        if not isinstance(entry, dict):
                            continue
                        h = entry.get("host")
                        if h:
                            cache_hosts.append(str(h))
                        addrs = entry.get("addresses") or []
                        if isinstance(addrs, list):
                            addresses.extend(str(a) for a in addrs if a)

                # Prefer ipcache hosts over profile-level host list when
                # both exist (ipcache reflects what the router actually
                # resolved and probed).
                effective_hosts = cache_hosts or hosts

                observation = {
                    "profile": profile_name,
                    "status": str(iface_result.get("status") or "").lower() or None,
                    "success_count": iface_result.get("successcount"),
                    "fail_count": iface_result.get("failcount"),
                    "check_hosts": effective_hosts,
                    "check_addresses": addresses,
                    "check_port": profile_entry.get("port"),
                    "check_mode": profile_entry.get("mode"),
                    "update_interval": profile_entry.get("update-interval"),
                    "max_fails": profile_entry.get("max-fails"),
                }
                observations.setdefault(iface_id, []).append(observation)

        # Per interface, pick "authoritative" profiles and aggregate.
        #
        # We only ignore profiles whose check targets fall entirely
        # inside IANA TEST-NET / documentation ranges, because those
        # are the one-off connection tests the web UI fires when the
        # user clicks "test connection" — they intentionally target
        # unroutable addresses and would otherwise produce permanent
        # false "fail" results.
        #
        # We do NOT filter by profile name. In particular,
        # `_WEBADMIN_<InterfaceId>` profiles are persistent, real,
        # user-enabled Ping Check configurations created from the
        # router's web UI — they are the authoritative ping-check
        # signal for that WAN and MUST be honoured.
        def _is_test_net_only(observation: Dict[str, Any]) -> bool:
            addrs = observation.get("check_addresses") or []
            hosts = observation.get("check_hosts") or []
            candidates = [str(x) for x in (list(addrs) + list(hosts)) if x]
            if not candidates:
                return False
            test_net_prefixes = ("192.0.2.", "198.51.100.", "203.0.113.")
            return all(c.startswith(test_net_prefixes) for c in candidates)

        result: Dict[str, Any] = {}
        for iface_id, obs_list in observations.items():
            real = [o for o in obs_list if not _is_test_net_only(o)]

            if not real:
                # Only TEST-NET probe profiles exist — don't trust them,
                # fall back to the link+IP heuristic downstream.
                result[iface_id] = {
                    "status": None,
                    "passing": None,
                    "profile": None,
                    "success_count": None,
                    "fail_count": None,
                    "check_hosts": [],
                    "check_addresses": [],
                    "check_port": None,
                    "check_mode": None,
                    "update_interval": None,
                    "max_fails": None,
                    "all_profiles": obs_list,
                    "ignored_profiles": [o.get("profile") for o in obs_list],
                }
                continue

            effective = real

            # Aggregate status: any "fail" wins, all "pass" -> "pass",
            # otherwise whatever the last-seen status is (typically a
            # profile in "pending"/"checking" state that's newly added).
            statuses = [o.get("status") for o in effective if o.get("status")]
            if not statuses:
                agg_status: str | None = None
                agg_bool: bool | None = None
            elif any(s == "fail" for s in statuses):
                agg_status = "fail"
                agg_bool = False
            elif all(s == "pass" for s in statuses):
                agg_status = "pass"
                agg_bool = True
            else:
                # Mixed or unknown state — surface as None so the
                # sensor goes "unavailable" rather than lying.
                agg_status = statuses[-1]
                agg_bool = None

            # The "winning" profile is the first fail (if any), else the
            # first pass — gives the most useful single-profile summary
            # for attribute display.
            primary: Dict[str, Any] | None = None
            for o in effective:
                if o.get("status") == "fail":
                    primary = o
                    break
            if primary is None:
                for o in effective:
                    if o.get("status") == "pass":
                        primary = o
                        break
            if primary is None and effective:
                primary = effective[0]

            flat: Dict[str, Any] = {
                "status": agg_status,
                "passing": agg_bool,
                "profile": (primary or {}).get("profile"),
                "success_count": (primary or {}).get("success_count"),
                "fail_count": (primary or {}).get("fail_count"),
                "check_hosts": (primary or {}).get("check_hosts") or [],
                "check_addresses": (primary or {}).get("check_addresses") or [],
                "check_port": (primary or {}).get("check_port"),
                "check_mode": (primary or {}).get("check_mode"),
                "update_interval": (primary or {}).get("update_interval"),
                "max_fails": (primary or {}).get("max_fails"),
                "all_profiles": obs_list,
                "ignored_profiles": [
                    o.get("profile") for o in obs_list if o not in effective
                ],
            }
            result[iface_id] = flat

        return result

    @staticmethod
    def _parse_dns_proxy_stat(stat_text: str) -> List[Dict[str, Any]]:
        """Parse the DNS Servers table from `show dns-proxy` statistics."""
        servers: List[Dict[str, Any]] = []
        for line in str(stat_text or "").splitlines():
            match = _DNS_PROXY_STAT_RE.match(line)
            if not match:
                continue
            sent = int(match.group("sent"))
            answered = int(match.group("answered"))
            nxdomain = int(match.group("nxdomain"))
            servers.append(
                {
                    "ip": match.group("ip"),
                    "port": int(match.group("port")),
                    "sent": sent,
                    "answered": answered,
                    "nxdomain": nxdomain,
                    "failed": max(0, sent - answered - nxdomain),
                    "median_ms": int(match.group("median")),
                    "average_ms": int(match.group("average")),
                    "rank": int(match.group("rank")),
                }
            )
        return servers

    async def async_get_dns_proxy_status(self) -> Dict[str, Any]:
        """Return a lightweight health summary for Keenetic DNS/DoH proxy.

        This intentionally reads the router's generated DNS proxy state
        instead of scraping logs. It is useful for detecting the class of
        outage where raw IP connectivity still works but DoH proxy requests
        are timing out or all encrypted upstreams stop answering.
        """
        try:
            data = await self._rci_get("show/dns-proxy") or {}
            proxy_status = data.get("proxy-status") or []
            if not isinstance(proxy_status, list):
                return {}

            proxies: List[Dict[str, Any]] = []
            total_servers = 0
            active_servers = 0
            failed_requests = 0
            sent_requests = 0
            do_h_servers = 0

            for proxy in proxy_status:
                if not isinstance(proxy, dict):
                    continue

                name = str(proxy.get("proxy-name") or "Unknown")
                config = str(proxy.get("proxy-config") or "")
                stat = str(proxy.get("proxy-stat") or "")
                stat_servers = self._parse_dns_proxy_stat(stat)
                https_servers = (
                    (proxy.get("proxy-https") or {}).get("server-https") or []
                )
                if not isinstance(https_servers, list):
                    https_servers = []

                proxy_sent = sum(int(s["sent"]) for s in stat_servers)
                proxy_failed = sum(int(s["failed"]) for s in stat_servers)
                proxy_active = sum(1 for s in stat_servers if int(s["answered"]) > 0)
                proxy_doh = sum(
                    1
                    for server in https_servers
                    if isinstance(server, dict) and server.get("uri")
                )

                total_servers += len(stat_servers)
                active_servers += proxy_active
                failed_requests += proxy_failed
                sent_requests += proxy_sent
                do_h_servers += proxy_doh

                proxies.append(
                    {
                        "name": name,
                        "doh_servers": proxy_doh,
                        "configured_doh_uris": [
                            str(server.get("uri"))
                            for server in https_servers
                            if isinstance(server, dict) and server.get("uri")
                        ],
                        "client_path_uses_doh": "https://" in config,
                        "servers": stat_servers,
                        "requests_sent": proxy_sent,
                        "failed_requests": proxy_failed,
                        "active_servers": proxy_active,
                    }
                )

            if not proxies:
                status = "unknown"
            elif total_servers == 0:
                status = "unknown"
            elif active_servers == 0 and sent_requests > 0:
                status = "down"
            elif failed_requests > 0:
                status = "degraded"
            else:
                status = "ok"

            return {
                "status": status,
                "proxy_count": len(proxies),
                "doh_server_count": do_h_servers,
                "dns_server_count": total_servers,
                "active_dns_server_count": active_servers,
                "requests_sent": sent_requests,
                "failed_requests": failed_requests,
                "client_path_uses_doh": any(
                    bool(proxy.get("client_path_uses_doh")) for proxy in proxies
                ),
                "proxies": proxies,
            }
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Error getting DNS proxy status: %s", err)
            return {}

    async def async_get_crypto_maps(self) -> Dict[str, Dict[str, Any]]:
        """Return site-to-site IPsec tunnels (`crypto map` entries).

        Endpoint: rci/show/crypto/map

        Site-to-site IPsec tunnels do NOT appear as virtual interfaces
        in /rci/show/interface, so they need their own data path and
        their own entity model — they can't piggyback on the existing
        per-WAN / per-VPN-client plumbing that other VPN types use.

        The router response looks like (tunnel that never came up):
            {
              "crypto_map": {
                "TEST": {
                  "config": {
                    "remote_peer": "192.0.2.1",
                    "enabled": "yes",              # NOTE: string, not bool
                    "crypto_ipsec_profile_name": "TEST",
                    "mode": "tunnel"
                  },
                  "status": {
                    "primary_peer": true,
                    "initiator": true,
                    "ike_state": "UNDEFINED",
                    "state": "UNDEFINED",
                    "via": "PPPoE0",
                    "local-endpoint-address": "78.188.13.104",
                    "remote-endpoint-address": "192.0.2.1"
                  }
                }
              }
            }

        A fully established tunnel additionally has `status.phase1`
        (dict) and `status.phase2_sa_list.phase2_sa` (list of SA dicts
        with in_bytes / out_bytes counters). We treat those as optional
        because the router only populates them once SA negotiation has
        actually happened.

        We normalise to:
            {
              "<name>": {
                "name": "TEST",
                "enabled": True,                   # config.enabled == "yes"
                "remote_peer": "192.0.2.1",
                "mode": "tunnel",
                "ipsec_profile_name": "TEST",
                "state": "UNDEFINED",              # status.state
                "ike_state": "UNDEFINED",          # status.phase1.ike_state
                                                   #   or status.ike_state
                "connected": False,                # state == PHASE2_ESTABLISHED
                "via": "PPPoE0" or None,
                "local_endpoint": "78.188.13.104" or None,
                "remote_endpoint": "192.0.2.1" or None,
                "rx_bytes": 1506697,               # sum across phase2 SAs
                "tx_bytes": 129642,                # sum across phase2 SAs
                "rx_packets": 2950,
                "tx_packets": 2360,
                "phase1": {...} or None,           # raw, for v2 sensors
                "phase2_sa_list": [...] or [],     # raw, normalised to list
                "raw_status": {...},               # raw status for diag
                "raw_config": {...},
              }
            }
        """
        try:
            data = await self._rci_get("show/crypto/map")
        except Exception as err:
            _LOGGER.debug("show/crypto/map unavailable: %s", err)
            return {}

        if not isinstance(data, dict):
            return {}
        raw_maps = data.get("crypto_map") or {}

        if not isinstance(raw_maps, dict):
            return {}

        def _clean_addr(v: Any) -> str | None:
            """Reject '0.0.0.0' / empty / None placeholders."""
            if v is None:
                return None
            s = str(v).strip()
            if not s or s == "0.0.0.0" or s == "::":
                return None
            return s

        def _clean_str(v: Any) -> str | None:
            if v is None:
                return None
            s = str(v).strip()
            return s or None

        def _to_int(v: Any) -> int:
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0

        def _as_list(v: Any) -> List[Any]:
            """Keenetic sometimes collapses single-entry lists to a
            dict. Normalise to a real list so downstream code can
            always iterate."""
            if v is None:
                return []
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                return [v]
            return []

        result: Dict[str, Dict[str, Any]] = {}
        for name, entry in raw_maps.items():
            if not isinstance(entry, dict):
                continue

            config = entry.get("config") or {}
            status = entry.get("status") or {}
            if not isinstance(config, dict):
                config = {}
            if not isinstance(status, dict):
                status = {}

            # phase1 may live either under status.phase1 (when router
            # has negotiated) or — on some firmwares — the ike_state
            # field alone is promoted to status.ike_state with no
            # phase1 block. Handle both.
            phase1 = status.get("phase1")
            if not isinstance(phase1, dict):
                phase1 = None

            ike_state = None
            if phase1:
                ike_state = _clean_str(phase1.get("ike_state"))
            if not ike_state:
                ike_state = _clean_str(status.get("ike_state"))

            # phase2 SA list — present only when SAs have been set up.
            p2_wrapper = status.get("phase2_sa_list") or {}
            if not isinstance(p2_wrapper, dict):
                p2_wrapper = {}
            phase2_sa_list = _as_list(p2_wrapper.get("phase2_sa"))

            rx_bytes = 0
            tx_bytes = 0
            rx_packets = 0
            tx_packets = 0
            for sa in phase2_sa_list:
                if not isinstance(sa, dict):
                    continue
                rx_bytes += _to_int(sa.get("in_bytes"))
                tx_bytes += _to_int(sa.get("out_bytes"))
                rx_packets += _to_int(sa.get("in_packets"))
                tx_packets += _to_int(sa.get("out_packets"))

            state = _clean_str(status.get("state"))
            connected = state == "PHASE2_ESTABLISHED"
            local_endpoint = _clean_addr(status.get("local-endpoint-address"))
            remote_endpoint = _clean_addr(status.get("remote-endpoint-address"))

            result[name] = {
                "name": name,
                "enabled": str(config.get("enabled", "")).lower() == "yes",
                "remote_peer": _clean_str(config.get("remote_peer")),
                "mode": _clean_str(config.get("mode")),
                "ipsec_profile_name": _clean_str(
                    config.get("crypto_ipsec_profile_name")
                ),
                "state": state,
                "ike_state": ike_state,
                "connected": connected,
                "via": _clean_str(status.get("via")),
                "local_endpoint": local_endpoint,
                "remote_endpoint": remote_endpoint,
                "rx_bytes": rx_bytes,
                "tx_bytes": tx_bytes,
                "rx_packets": rx_packets,
                "tx_packets": tx_packets,
                "phase1": phase1,
                "phase2_sa_list": phase2_sa_list,
                "raw_config": config,
                "raw_status": status,
            }

        return result

    async def async_set_crypto_map_enabled(
        self, name: str, enabled: bool
    ) -> None:
        """Enable or disable a site-to-site IPsec `crypto map` entry.

        Unlike VPN-client interfaces (which are toggled via
        `interface X up/down`), site-to-site tunnels live under the
        `crypto map <name>` configuration sub-mode. The CLI pattern is:

            crypto map <name>
              enable     (or: no enable)

        We send this as a single RCI parse call with an embedded
        newline. Changes are runtime-only until persisted, so we
        follow up with `system configuration save` so the toggle
        survives a reboot — matching the user's expectation that a
        Home Assistant switch toggle is permanent.
        """
        name = _validate_cli_arg(name, "crypto map name")
        verb = "enable" if enabled else "no enable"
        cmd = f"crypto map {name}\n{verb}"
        _LOGGER.debug(
            "Set crypto map %s enabled=%s via: %r", name, enabled, cmd
        )
        await self._rci_parse(cmd)
        # Persist so the change survives a reboot. Without this the
        # toggle is lost on the next router restart and the user sees
        # the switch "flip back" with no obvious reason.
        try:
            await self._rci_parse("system configuration save")
        except Exception as err:
            _LOGGER.warning(
                "crypto map %s toggled to enabled=%s but "
                "'system configuration save' failed: %s — change will "
                "be lost on reboot",
                name,
                enabled,
                err,
            )


    async def async_get_mesh_nodes(self) -> List[Dict[str, Any]]:
        """Get mesh/extender nodes status from mws/member endpoint.

        Bu endpoint tüm mesh üyelerini detaylı bilgileriyle döndürür.

        NOT:
        Bazı Keenetic modellerinde/firmware'lerinde Wi-Fi System (MWS) controller yoktur.
        Bu durumda show/mws/member çağrısı router loguna:
            Core::Scgi::ThreadPool: not found: "member" (http/rci)
        şeklinde spam basar.

        Çözüm:
        1) Önce client listesinde extender/repeater var mı bak.
           Yoksa MWS endpoint'ine hiç gitme.
        2) MWS endpoint'i "not found" ise desteklenmiyor diye cache'le, tekrar deneme.
        """
        nodes: List[Dict[str, Any]] = []

        # 1) Önce fallback ile "evde extender var mı?" tespit et
        try:
            fallback_nodes = await self._get_mesh_nodes_from_clients()
        except Exception:
            fallback_nodes = []

        # Extender yoksa MWS endpoint'ine hiç dokunma (log spam sıfır)
        if not fallback_nodes:
            return nodes

        # Daha önce "desteklemiyor" diye cache'lediysek tekrar deneme
        if self._mws_member_supported is False:
            return fallback_nodes

        try:
            data = await self._rci_get("show/mws/member")

            # Endpoint çalıştı
            self._mws_member_supported = True

            if not data or not isinstance(data, list):
                return nodes

            for member in data:
                cid = member.get("cid")
                if not cid:
                    continue

                mac = member.get("mac")
                system_info = member.get("system", {})
                rci_info = member.get("rci", {})

                is_connected = (
                    rci_info.get("errors", 0) == 0 
                    and member.get("internet-available", False)
                )

                ports = member.get("port", [])
                normalized_ports = []
                for port in ports:
                    if isinstance(port, dict):
                        normalized_port = {
                            "label": port.get("label"),
                            "appearance": port.get("appearance"),
                            "link": port.get("link"),
                        }
                        if port.get("link") == "up":
                            normalized_port["speed"] = port.get("speed")
                            normalized_port["duplex"] = port.get("duplex")
                        normalized_ports.append(normalized_port)

                nodes.append({
                    "id": cid,
                    "cid": cid,
                    "mac": mac,
                    "ip": member.get("ip"),
                    "name": member.get("known-host") or member.get("model") or mac,
                    "model": member.get("model"),
                    "mode": member.get("mode"), 
                    "hw_id": member.get("hw_id"),
                    "connected": is_connected,
                    "state": "up" if is_connected else "down",
                    "uptime": system_info.get("uptime"),
                    "cpuload": system_info.get("cpuload"),
                    "memory": system_info.get("memory"),
                    "firmware": member.get("fw"),
                    "firmware_available": member.get("fw-available"),
                    "associations": member.get("associations", 0), 
                    "rci_errors": rci_info.get("errors", 0),
                    "fqdn": member.get("fqdn"),
                    "port": normalized_ports,
                    "backhaul": member.get("backhaul"),
                })

        except Exception as err:
            # "not found" durumunda tekrar denemeyip cache'leyelim
            msg = str(err).lower()
            if ("not found" in msg) or ("404" in msg):
                self._mws_member_supported = False
                return fallback_nodes

            _LOGGER.debug("Error getting mesh nodes from mws/member: %s", err)
            return fallback_nodes

        return nodes

    async def _get_mesh_nodes_from_clients(self) -> List[Dict[str, Any]]:
        """Fallback: Get mesh nodes from client list if mws/member fails."""
        clients = await self.async_get_clients()
        nodes: List[Dict[str, Any]] = []

        for client in clients:
            system_mode = str(client.get("system-mode") or "").lower()
            if system_mode not in ("extender", "repeater"):
                continue

            mac = client.get("mac")
            if not mac:
                continue

            is_active = bool(client.get("active", False))

            nodes.append({
                "id": mac,
                "cid": None, 
                "mac": mac,
                "ip": client.get("ip"),
                "name": client.get("name") or client.get("hostname") or mac,
                "mode": system_mode,
                "connected": is_active,
                "state": "up" if is_active else "down",
                "uptime": client.get("uptime"),
                "firmware": client.get("firmware"),
            })

        return nodes

    async def async_reboot_mesh_node(self, cid: str) -> None:
        """Reboot a specific mesh/extender node by CID (component ID).
        
        Command format: mws member {cid} reboot
        """
        cid = _validate_cli_arg(cid, "mesh node cid")
        _LOGGER.warning("Sending reboot command to mesh node cid=%s", cid)

        cmd = f"mws member {cid} reboot"
        await self._rci_parse(cmd)

    async def async_get_traffic_stats(
        self, interfaces: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        """Get traffic statistics (speed, totals).
        
        Args:
            interfaces: Pre-fetched interfaces data to avoid duplicate API calls.
        """
        stats: Dict[str, Any] = {
            "download_speed": 0.0,  
            "upload_speed": 0.0,    
            "total_rx": 0,          
            "total_tx": 0,          
        }

        try:
            if interfaces is None:
                interfaces = await self.async_get_interfaces()

            iface_list = self._normalize_interfaces(interfaces)
            WAN_KEYWORDS = ("wan", "internet", "pppoe", "isp", "provider")

            for iface in iface_list:
                name_fields = [
                    iface.get("name"),
                    iface.get("ifname"),
                    iface.get("id"),
                    iface.get("interface-name"),
                    iface.get("description"),
                    iface.get("type"),
                ]
                name_joined = " ".join(str(v) for v in name_fields if v).lower()
                state = str(iface.get("state") or "").lower()

                if state == "up" and any(k in name_joined for k in WAN_KEYWORDS):
                    stats["total_rx"] = (
                        iface.get("rxbytes") or 
                        iface.get("rx-bytes") or 
                        iface.get("bytes-rx") or 
                        iface.get("rx") or 
                        0
                    )
                    stats["total_tx"] = (
                        iface.get("txbytes") or 
                        iface.get("tx-bytes") or 
                        iface.get("bytes-tx") or 
                        iface.get("tx") or 
                        0
                    )

                    rx_speed = (
                        iface.get("rx-speed") or 
                        iface.get("rxspeed") or 
                        iface.get("speed-rx") or 
                        iface.get("rx_rate") or 
                        0
                    )
                    tx_speed = (
                        iface.get("tx-speed") or 
                        iface.get("txspeed") or 
                        iface.get("speed-tx") or 
                        iface.get("tx_rate") or 
                        0
                    )

                    stats["download_speed"] = round(float(rx_speed) / 8 / 1024 / 1024, 2)
                    stats["upload_speed"] = round(float(tx_speed) / 8 / 1024 / 1024, 2)

                    _LOGGER.debug(
                        "Traffic stats for %s: rx=%s, tx=%s, rx_speed=%s, tx_speed=%s",
                        name_joined, stats["total_rx"], stats["total_tx"],
                        stats["download_speed"], stats["upload_speed"]
                    )
                    break

        except Exception as err:
            _LOGGER.debug("Error getting traffic stats: %s", err)

        return stats

    async def async_get_all_interface_stats(
        self, interfaces: Dict[str, Any] | None = None
    ) -> Dict[str, Dict[str, Any]]:
        """Get traffic statistics for all interfaces.
        
        Returns dict mapping interface name to stats (rxbytes, txbytes, etc.)
        """
        if interfaces is None:
            interfaces = await self.async_get_interfaces()
        iface_list = self._normalize_interfaces(interfaces)

        all_stats: Dict[str, Dict[str, Any]] = {}
        wan_ids = {
            str(wan.get("id"))
            for wan in await self.async_get_wan_interfaces(interfaces=interfaces)
            if wan.get("id")
        }

        for iface in iface_list:
            iface_name = iface.get("id") or iface.get("interface-name")
            if not iface_name:
                continue

            iface_type = iface.get("type", "").lower()
            if iface_name not in wan_ids and iface_type in (
                "bridge",
                "vlan",
                "accesspoint",
            ):
                continue

            try:
                stats = await self.async_get_interface_stat(iface_name)
                if stats:
                    stats["interface_name"] = iface_name
                    stats["interface_type"] = iface_type
                    stats["link"] = iface.get("link")
                    stats["state"] = iface.get("state")
                    all_stats[iface_name] = stats
            except Exception as err:
                _LOGGER.debug("Failed to get stats for %s: %s", iface_name, err)

        return all_stats

    @staticmethod
    def summarize_client_stats(clients: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Summarize a pre-fetched Keenetic client list."""
        connected = 0
        disconnected = 0
        per_ap: Dict[str, int] = {}
        extenders: List[Dict[str, Any]] = []

        for client in clients:
            system_mode = str(client.get("system-mode") or "").lower()
            if system_mode in ("extender", "repeater"):
                extenders.append({
                    "mac": client.get("mac"),
                    "ip": client.get("ip"),
                    "name": client.get("name") or client.get("hostname") or client.get("mac"),
                    "mode": system_mode,
                    "active": client.get("active", False),
                    "uptime": client.get("uptime"),
                    "firmware": client.get("firmware"),
                    "description": client.get("description"),
                    "http_host": client.get("http-host"),
                })
                continue  

            is_active = False
            if "active" in client:
                value = client.get("active")
                if isinstance(value, bool):
                    is_active = value
                elif isinstance(value, str):
                    is_active = value.lower() in ("true", "yes", "1", "up", "online")
                else:
                    is_active = bool(value)
            elif "link" in client:
                is_active = str(client.get("link") or "").lower() == "up"

            if is_active:
                connected += 1
            else:
                disconnected += 1

            iface = client.get("interface")
            if isinstance(iface, dict):
                ap_name = iface.get("name") or iface.get("id") or "Unknown"
            else:
                ap_name = str(iface) if iface else "Unknown"

            ssid = client.get("ssid")
            if ssid:
                ap_name = str(ssid)

            if is_active:
                per_ap[ap_name] = per_ap.get(ap_name, 0) + 1

        return {
            "connected": connected,
            "disconnected": disconnected,
            "total": connected + disconnected, 
            "per_ap": per_ap,
            "extenders": extenders,
            "extender_count": len(extenders),
        }

    async def async_get_client_stats(self) -> Dict[str, Any]:
        """Get connected/disconnected client counts and per-AP stats.
        
        Extender/repeater cihazları client sayısından çıkarılır.
        """
        return self.summarize_client_stats(await self.async_get_clients())

    async def async_get_policies(self) -> Dict[str, str]:
        """Get available connection policies.
        
        Returns:
            Dict mapping policy_id to description
            e.g. {"Policy0": "VPN", "Policy1": "Smart Home", ...}
        """
        try:
            # Doğru endpoint: GET /rci/ip/policy
            data = await self._rci_get("ip/policy")
            if not data or not isinstance(data, dict):
                return {}

            policies = {}
            for policy_id, policy_data in data.items():
                if isinstance(policy_data, dict):
                    desc = policy_data.get("description") or policy_id
                    policies[policy_id] = str(desc)

            return policies
        except Exception as err:
            _LOGGER.debug("Error getting policies: %s", err)
            return {}

    async def async_get_host_policies(self) -> Dict[str, Dict[str, Any]]:
        """Get policy assignments for all hosts.
        
        Returns:
            Dict mapping MAC to policy info
            e.g. {"aa:bb:cc:dd:ee:ff": {"policy": "Policy1", "access": "permit"}, ...}
        """
        try:
            # Doğru endpoint: GET /rci/ip/hotspot/host
            data = await self._rci_get("ip/hotspot/host")
            if not data:
                return {}

            host_policies = {}
            for host in _nested_dict_items(data, "host", "hosts"):
                mac = str(host.get("mac") or "").lower()
                if mac:
                    host_policies[mac] = {
                        "policy": host.get("policy"), 
                        "access": host.get("access"), 
                    }

            return host_policies
        except Exception as err:
            _LOGGER.debug("Error getting host policies: %s", err)
            return {}

    async def async_set_client_policy(self, mac: str, policy: str) -> None:
        """Set connection policy for a client.
        
        Args:
            mac: Client MAC address
            policy: Policy ID (e.g. "Policy0", "Policy1") or "deny"/"default"
        """
        mac_clean = _validate_cli_arg(mac.lower().replace("-", ":"), "MAC address")
        policy = _validate_cli_arg(policy, "policy")

        if policy.lower() == "deny":
            cmd = f"ip hotspot host {mac_clean} deny"
            _LOGGER.debug("Blocking client %s", mac_clean)
            await self._rci_parse(cmd)
        elif policy.lower() in ("default", "permit", ""):

            cmd = f"no ip hotspot host {mac_clean} policy"
            _LOGGER.debug("Removing policy from client %s", mac_clean)
            await self._rci_parse(cmd)

            cmd = f"ip hotspot host {mac_clean} permit"
            await self._rci_parse(cmd)
        else:
            # Önce erişimi aç (deny durumundaysa permit'e çevir)
            cmd = f"ip hotspot host {mac_clean} permit"
            await self._rci_parse(cmd)

            cmd = f"ip hotspot host {mac_clean} policy {policy}"
            _LOGGER.debug("Setting client %s policy to %s", mac_clean, policy)
            await self._rci_parse(cmd)

        await self._rci_parse("system configuration save")

    async def async_block_client(self, mac: str) -> None:
        """Block a client's internet access."""
        await self.async_set_client_policy(mac, "deny")

    async def async_unblock_client(self, mac: str) -> None:
        """Unblock a client's internet access."""
        await self.async_set_client_policy(mac, "default")

    async def async_check_firmware_update(self) -> Dict[str, Any]:
        """Check for available firmware update via /rci/show/version."""
        try:
            data = await self._rci_get("show/version")
            if not data:
                return {}

            current = data.get("title") or data.get("release")
            available = data.get("fw-available") or data.get("release-available")

            has_update = (
                current and available and 
                current != available and
                data.get("fw-update-sandbox") == "stable"
            )

            return {
                "current": {
                    "title": current,
                    "release": data.get("release"),
                },
                "available": {
                    "title": available,
                    "release": data.get("release-available"),
                } if has_update else None,
                "channel": data.get("fw-update-sandbox"),
                "has_update": has_update,
            }
        except Exception as err:
            _LOGGER.debug("Error checking firmware update: %s", err)
            return {}


    async def async_start_firmware_update(self) -> bool:
        """Start firmware update for the controller (main router) ONLY.

        Tries endpoints in order:
        1. /rci/components stage + commit (KeeneticOS 5.x)
        2. /rci/system/update (older firmware)
        Does NOT use mws/update/start as that triggers a mesh-wide update.
        """
        # Try KeeneticOS 5.x: stage components then commit
        try:
            version_data = await self._rci_get("show/version")
            ndw_components = ""
            if isinstance(version_data, dict):
                ndw_components = version_data.get("ndw", {}).get("components", "")

            if ndw_components:
                current_components = [
                    c.strip() for c in ndw_components.split(",") if c.strip()
                ]
                install_list = [{"component": c} for c in current_components]
                payload = [{"components": {"install": install_list}}]

                _LOGGER.debug("Staging component update on controller")
                await self._request("POST", f"{RCI_ROOT}/", json=payload)

                _LOGGER.debug("Committing component update on controller")
                await self._rci_post("components/commit", {"reason": "manual"})
                _LOGGER.info("Controller firmware update started via components/commit")
                return True
        except KeeneticApiError as err:
            if "404" not in str(err):
                raise HomeAssistantError(f"Failed to start update: {err}") from err
            _LOGGER.debug("Components update not available, trying system/update")

        # Try system/update (older firmware)
        try:
            result = await self._rci_post("system/update", {"confirm": True})
            if isinstance(result, dict):
                status = result.get("status") or result.get("result")
                if status in ("started", "ok", True, "accepted"):
                    _LOGGER.info("Controller firmware update started via system/update")
                    return True
            if result is not None:
                _LOGGER.info("Controller firmware update started via system/update")
                return True
        except KeeneticApiError as err:
            if "404" not in str(err):
                raise HomeAssistantError(f"Failed to start update: {err}") from err
            _LOGGER.debug("system/update returned 404")

        msg = "No compatible firmware update endpoint found on this router"
        _LOGGER.error(msg)
        raise HomeAssistantError(msg)

    async def async_start_node_firmware_update(
        self, node_ip: str, node_name: str = "", node_cid: str | None = None
    ) -> bool:
        """Start firmware update on a specific mesh node by connecting directly.

        Prefer the controller-side MWS command because KeeneticOS manages
        extender updates from the controller:

            mws member <member> update start

        If that path is unavailable, fall back to connecting to the node's own
        RCI API and triggering a local update.

        Args:
            node_ip: IP address of the mesh node.
            node_name: Display name for logging.
            node_cid: Mesh member CID/MAC used by the controller.
        """
        if not self._session or not node_ip:
            raise HomeAssistantError("Cannot connect to mesh node")

        label = node_name or node_ip

        if node_cid:
            try:
                member = _validate_cli_arg(node_cid, "mesh node cid")
                _LOGGER.info(
                    "Starting firmware update for mesh node %s via controller "
                    "MWS member %s",
                    label,
                    member,
                )
                await self._rci_parse(f"mws member {member} update start")
                return True
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Controller MWS update command failed for node %s (%s): %s. "
                    "Trying direct node update fallback.",
                    label,
                    node_cid,
                    err,
                )

        scheme = "https" if self._ssl else "http"

        # Try controller's port first, then default port 80
        ports_to_try = [self._port]
        if self._port != 80:
            ports_to_try.append(80)

        for port in ports_to_try:
            base = f"{scheme}://{node_ip}:{port}"

            # Always do challenge auth with mesh nodes
            node_headers = await self._authenticate_to_node(node_ip, port)
            if not node_headers:
                _LOGGER.debug(
                    "Could not authenticate to node %s on port %s", label, port
                )
                continue

            # KeeneticOS 5.x: two-step update via components
            # Step 1: Get current components from show/version
            try:
                url = f"{base}{RCI_ROOT}/show/version"
                async with async_timeout.timeout(self._request_timeout):
                    resp = await self._session.get(url, headers=node_headers)
                if resp.status == 200:
                    version_data = await resp.json()
                    ndw_components = version_data.get("ndw", {}).get("components", "")
                    if ndw_components:
                        current_components = [
                            c.strip() for c in ndw_components.split(",") if c.strip()
                        ]
                        _LOGGER.debug(
                            "Node %s has %d components: %s",
                            label, len(current_components), current_components,
                        )

                        # Step 2: POST component list to /rci/
                        install_list = [
                            {"component": c} for c in current_components
                        ]
                        payload = [{"components": {"install": install_list}}]

                        url = f"{base}{RCI_ROOT}/"
                        _LOGGER.info(
                            "Staging component update on node %s", label
                        )
                        async with async_timeout.timeout(self._request_timeout):
                            resp = await self._session.post(
                                url,
                                json=payload,
                                headers=node_headers,
                            )
                        if resp.status not in (200, 204):
                            text = await resp.text()
                            _LOGGER.warning(
                                "Node %s component staging returned %s: %s",
                                label, resp.status, _response_summary(text),
                            )

                        # Step 3: Commit
                        url = f"{base}{RCI_ROOT}/components/commit"
                        _LOGGER.info(
                            "Committing update on node %s", label
                        )
                        async with async_timeout.timeout(self._request_timeout):
                            resp = await self._session.post(
                                url,
                                json={"reason": "manual"},
                                headers=node_headers,
                            )
                        if resp.status in (200, 204):
                            _LOGGER.info(
                                "Node %s firmware update started via "
                                "components/commit",
                                label,
                            )
                            return True

                        text = await resp.text()
                        _LOGGER.warning(
                            "Node %s commit returned %s: %s",
                            label, resp.status, _response_summary(text),
                        )
                    else:
                        _LOGGER.debug(
                            "Node %s has no ndw.components in version info",
                            label,
                        )
                elif resp.status == 401:
                    _LOGGER.debug("Auth rejected on node %s port %s", label, port)
                    self._node_auth_headers.pop((node_ip, port), None)
                    continue
            except asyncio.TimeoutError:
                _LOGGER.debug("Timeout connecting to node %s port %s", label, port)
                continue
            except Exception as err:
                _LOGGER.debug(
                    "Components update on node %s failed: %s", label, err
                )

            # Fallback: POST /rci/system/update (older firmware)
            try:
                url = f"{base}{RCI_ROOT}/system/update"
                _LOGGER.info("Attempting update on node %s via %s", label, url)
                async with async_timeout.timeout(self._request_timeout):
                    resp = await self._session.post(
                        url,
                        json={"confirm": True},
                        headers=node_headers,
                    )
                if resp.status in (200, 204):
                    _LOGGER.info(
                        "Node %s firmware update started via system/update", label
                    )
                    return True
                if resp.status != 404:
                    text = await resp.text()
                    _LOGGER.debug(
                        "Node %s system/update returned %s: %s",
                        label, resp.status, _response_summary(text),
                    )
            except asyncio.TimeoutError:
                _LOGGER.debug("Timeout on system/update for node %s", label)
            except Exception as err:
                _LOGGER.debug("system/update on node %s failed: %s", label, err)

        msg = f"Could not start firmware update on node {label}"
        _LOGGER.error(msg)
        raise HomeAssistantError(msg)

    async def _authenticate_to_node(
        self, node_ip: str, port: int | None = None
    ) -> Dict[str, str] | None:
        """Perform NDW2 challenge auth against a specific mesh node.

        Always attempts challenge auth first since mesh nodes typically
        require it, even when the controller uses Basic Auth.

        Returns headers dict with session cookie, or None if auth failed.
        """
        if port is None:
            port = self._port

        cached = self._node_auth_headers.get((node_ip, port))
        if cached:
            return dict(cached)

        scheme = "https" if self._ssl else "http"
        auth_url = f"{scheme}://{node_ip}:{port}/auth"

        try:
            # Step 1: GET /auth to get challenge
            async with async_timeout.timeout(self._request_timeout):
                get_resp = await self._session.get(
                    auth_url, allow_redirects=False
                )

            challenge = get_resp.headers.get("X-NDM-Challenge")
            realm = get_resp.headers.get("X-NDM-Realm", "")

            if not challenge:
                _LOGGER.debug(
                    "Node %s did not return challenge header, "
                    "using basic auth fallback",
                    node_ip,
                )
                headers = self._basic_auth_headers()
                self._node_auth_headers[(node_ip, port)] = headers
                return dict(headers)

            # Step 2: Compute hash
            ha1 = hashlib.md5(
                f"{self._username}:{realm}:{self._password}".encode()
            ).hexdigest()
            response_hash = hashlib.sha256(
                (challenge + ha1).encode()
            ).hexdigest()

            # Extract session cookie
            raw_cookie = get_resp.headers.get("Set-Cookie", "")
            session_cookie = None
            if raw_cookie:
                cookie_kv = raw_cookie.split(";")[0].strip()
                if "=" in cookie_kv:
                    session_cookie = cookie_kv

            # Step 3: POST /auth with credentials
            post_headers: Dict[str, str] = {}
            if session_cookie:
                post_headers["Cookie"] = session_cookie

            async with async_timeout.timeout(self._request_timeout):
                post_resp = await self._session.post(
                    auth_url,
                    json={"login": self._username, "password": response_hash},
                    headers=post_headers,
                )

            if post_resp.status in (200, 204):
                _LOGGER.debug(
                    "Challenge auth to node %s:%s succeeded", node_ip, port
                )
                headers = {"Cookie": session_cookie} if session_cookie else {}
                self._node_auth_headers[(node_ip, port)] = headers
                return dict(headers)

            _LOGGER.debug(
                "Challenge auth to node %s:%s returned status %s",
                node_ip, port, post_resp.status,
            )
            return None

        except asyncio.TimeoutError:
            _LOGGER.debug("Timeout during auth to node %s:%s", node_ip, port)
            return None
        except Exception as err:
            _LOGGER.debug(
                "Auth to node %s:%s failed: %s", node_ip, port, err
            )
            return None


    async def async_get_update_progress(self) -> Dict[str, Any]:
        """Get current update progress (if in progress).
        
        Returns progress info or empty dict if no update running.
        """
        try:
            data = await self._rci_get("system/update/status")
            if not data or not isinstance(data, dict):
                return {}

            return {
                "in_progress": data.get("in-progress", False),
                "progress_percent": data.get("progress", 0),
                "stage": data.get("stage"),
                "eta_seconds": data.get("eta"),
            }
        except Exception:
            return {}
        
    async def async_get_ndns_info(self) -> Dict[str, Any]:
        """Get NDNS (Dynamic DNS) information from /rci/show/ndns.
        
        Returns detailed information about NDNS configuration and tunnels.
        Example response includes:
        - name: Hostname
        - domain: Domain name
        - access: Access type (cloud, etc.)
        - ttp: Tunnel information with tunnel list
        - updated: Last update status
        - address/address6: IP addresses
        """
        try:
            data = await self._rci_get("show/ndns")
            if not data:
                return {}
            
            # Ensure we always return a dict
            result = dict(data) if isinstance(data, dict) else {}
            
            # Parse tunnel information if present
            if "ttp" in result and isinstance(result["ttp"], dict):
                ttp = result["ttp"]
                # Ensure tunnel list is properly formatted
                if "tunnel" in ttp and isinstance(ttp["tunnel"], list):
                    tunnels = []
                    for tunnel in ttp["tunnel"]:
                        if isinstance(tunnel, dict):
                            # Convert string numbers to int where appropriate
                            for key in ["uptime", "idle", "timeout", "linger"]:
                                if key in tunnel and tunnel[key] is not None:
                                    try:
                                        tunnel[key] = int(tunnel[key])
                                    except (ValueError, TypeError):
                                        pass
                            tunnels.append(tunnel)
                    ttp["tunnel"] = tunnels
            
            _LOGGER.debug("NDNS info retrieved: %s", result)
            return result
            
        except Exception as err:
            _LOGGER.debug("Error getting NDNS info: %s", err)
            return {}
