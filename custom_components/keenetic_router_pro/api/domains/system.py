"""System domain methods for KeeneticClient."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Dict

import aiohttp

from homeassistant.exceptions import HomeAssistantError

from ...const import CAPABILITY_BOOT_GRACE_S, DOMAIN, RCI_SHOW_VERSION
from ...utils import (
    bracket_host,
    coerce_bool,
    mask_identifier,
    normalize_mesh_node_address,
)
from ..constants import RCI_ROOT, _RCI_PARSE_ERROR_RE
from ..errors import KeeneticApiError, KeeneticAuthError
from ..helpers import (
    _cookie_header_from_response,
    _extract_parse_messages,
    _is_endpoint_missing,
    _to_int,
    _validate_cli_arg,
)

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.system")
_FIRMWARE_AUTH_REJECTED = "Firmware update authentication rejected"


class _FirmwareUpdateRejected(KeeneticApiError):
    """The firmware command returned a definite refusal."""


def _firmware_endpoint_missing(err: KeeneticApiError) -> bool:
    return err.status == 405 or _is_endpoint_missing(err)


def _firmware_staging_payload(version: Any) -> list[dict[str, Any]]:
    ndw = version.get("ndw") if isinstance(version, dict) else None
    components = ndw.get("components") if isinstance(ndw, dict) else None
    if not isinstance(components, str):
        return []
    install = [{"component": name} for part in components.split(",") if (name := part.strip())]
    return [{"components": {"install": install}}] if install else []


def _firmware_refused(result: Any) -> bool:
    if result is False:
        return True
    if isinstance(result, list):
        return any(_firmware_refused(item) for item in result)
    if isinstance(result, dict):
        if result.get("error") or result.get("status") is False or result.get("result") is False:
            return True
        for key in ("status", "result"):
            status = str(result.get(key, "")).strip().lower()
            if status in {"failure", "refused", "rejected"} or _RCI_PARSE_ERROR_RE.search(status):
                return True
    return any(_RCI_PARSE_ERROR_RE.search(message) for message in _extract_parse_messages(result))


class SystemMixin:
    async def async_get_system_info(self) -> Dict[str, Any]:
        """Return basic system info: hostname, version, cpu, memory, uptime, etc."""
        data = await self._rci_get("show/system")
        # A list/str payload would crash every dict consumer downstream.
        if not isinstance(data, dict):
            return {}
        self._reprobe_capabilities_after_boot(data.get("uptime"))
        return data

    def _reprobe_capabilities_after_boot(self, raw_uptime: Any) -> None:
        """Drop capability latches that a booting router may have caused.

        Runtime endpoints can answer "not found" for a moment while KeeneticOS
        starts. A latch taken then would hold for the whole session, e.g.
        reading clients from the config tree where every host looks offline.
        """
        if raw_uptime is None:
            return
        try:
            uptime = int(raw_uptime)
        except (TypeError, ValueError):
            return
        previous = self._last_seen_uptime
        self._last_seen_uptime = uptime
        rebooted = previous is not None and uptime < previous
        if rebooted:
            _LOGGER.info("Router uptime went back; re-probing capability endpoints")
        if rebooted or uptime < CAPABILITY_BOOT_GRACE_S:
            self.reset_capability_caches()


    async def async_get_current_version_info(self) -> Dict[str, Any]:
        """Return version info"""
        data = await self._rci_get(RCI_SHOW_VERSION)
        if not isinstance(data, dict):
            return {}

        identity = data.get("release") or data.get("title")
        if identity:
            previous = self._last_seen_fw_version
            if previous is not None and previous != identity:
                _LOGGER.info(
                    "Firmware version changed from %s to %s — "
                    "re-probing router capability endpoints",
                    previous,
                    identity,
                )
                self.reset_capability_caches()
            self._last_seen_fw_version = identity

        return data


    async def async_get_available_version_info(self) -> Dict[str, Any]:
        """Return version info"""
        data = await self._rci_get("components/check-update")
        return data if isinstance(data, dict) else {}


    async def async_reboot(self) -> None:
        """Reboot the router via 'system reboot' command."""
        cmd = "system reboot"
        _LOGGER.warning("Sending router reboot command via RCI parse")
        await self._rci_parse(cmd)


    async def async_check_firmware_update(self) -> Dict[str, Any]:
        """Check for available firmware update via /rci/show/version."""
        try:
            data = await self._rci_get(RCI_SHOW_VERSION)
            if not data:
                return {}

            current = data.get("title") or data.get("release")
            available = data.get("fw-available") or data.get("release-available")

            has_update = bool(
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
        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            _LOGGER.debug("Error checking firmware update: %s", type(err).__name__)
            return {}



    async def async_start_firmware_update(self) -> bool:  # NOSONAR python:S3516 — interface-required success signal, raises on failure
        """Start a firmware update on the controller alone."""
        try:
            version = await self.async_get_current_version_info()
            staging = _firmware_staging_payload(version)
            if staging:
                await self._post_firmware_command("", staging)
                await self._dispatch_firmware_update("components/commit", {"reason": "manual"})
                return True
        except KeeneticAuthError as err:
            raise HomeAssistantError(_FIRMWARE_AUTH_REJECTED) from err
        except KeeneticApiError as err:
            if not _firmware_endpoint_missing(err):
                raise HomeAssistantError("Router did not accept the firmware update command") from err
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise HomeAssistantError("Could not stage the router firmware update") from err

        try:
            await self._dispatch_firmware_update("system/update", {"confirm": True})
        except KeeneticApiError as err:
            if _firmware_endpoint_missing(err):
                raise HomeAssistantError(
                    "No compatible firmware update endpoint found on this router"
                ) from err
            raise HomeAssistantError("Router did not accept the firmware update command") from err
        return True

    async def async_start_node_firmware_update(  # NOSONAR python:S3516 — interface-required success signal, raises on failure
        self, node_ip: str, node_name: str = "", node_cid: str | None = None
    ) -> bool:
        """Start a mesh member update via MWS or, if rejected, via the node."""
        if not self._session or not node_ip:
            raise HomeAssistantError("Cannot connect to mesh node")

        label = mask_identifier(node_name or node_ip)
        if node_cid:
            # Invalid tokens have never been dispatched and can use the direct API.
            try:
                member = _validate_cli_arg(node_cid, "mesh node cid")
            except KeeneticApiError:
                _LOGGER.debug("Invalid MWS member token for node %s", label)
            else:
                try:
                    await self._dispatch_firmware_update("parse", f"mws member {member} update start")
                    return True
                except _FirmwareUpdateRejected:
                    _LOGGER.debug("MWS rejected the update for node %s, trying direct RCI", label)

        direct_address = normalize_mesh_node_address(node_ip)
        if direct_address is None:
            raise HomeAssistantError("Mesh node address is not a valid literal IP")
        node_ip = direct_address
        scheme = "https" if self._ssl else "http"

        for port in dict.fromkeys((self._port, 80)):
            node_headers = await self._authenticate_to_node(node_ip, port)
            if not node_headers:
                continue

            base = f"{scheme}://{bracket_host(node_ip)}:{port}"
            auth_key = (node_ip, port)
            node = (base, node_headers, auth_key)
            try:
                staging = await self._read_node_firmware_staging(*node)
            except _FirmwareUpdateRejected:
                staging = []
            except (KeeneticApiError, aiohttp.ClientError, TimeoutError, ValueError) as err:
                _LOGGER.debug("Could not read firmware components on node %s: %s", label, type(err).__name__)
                continue

            if staging:
                try:
                    await self._post_firmware_command("", staging, node=node)
                except _FirmwareUpdateRejected:
                    _LOGGER.debug("Node %s rejected component staging, trying system/update", label)
                except (KeeneticApiError, aiohttp.ClientError, TimeoutError, ValueError) as err:
                    raise HomeAssistantError(f"Could not stage firmware on node {label}") from err
                else:
                    try:
                        await self._dispatch_firmware_update(
                            "components/commit", {"reason": "manual"}, node=node
                        )
                        return True
                    except _FirmwareUpdateRejected:
                        _LOGGER.debug("Node %s rejected commit, trying system/update", label)

            try:
                await self._dispatch_firmware_update("system/update", {"confirm": True}, node=node)
            except _FirmwareUpdateRejected:
                break
            return True

        raise HomeAssistantError(f"Could not start firmware update on node {label}")

    async def _read_node_firmware_staging(
        self, base: str, headers: dict[str, str], auth_key: tuple[str, int]
    ) -> list[dict[str, Any]]:
        """Read the direct node's component list before dispatching an update."""
        async with (
            asyncio.timeout(self._request_timeout),
            await self._session.get(f"{base}{RCI_ROOT}/{RCI_SHOW_VERSION}", headers=headers, allow_redirects=False) as response,
        ):
            if response.status == 401:
                self._node_auth_headers.pop(auth_key, None)
                raise KeeneticAuthError("Node firmware version authentication rejected")
            if response.status != 200:
                await self._check_firmware_response(response)
                raise KeeneticApiError("Node firmware version unavailable")
            return _firmware_staging_payload(await response.json())

    async def _dispatch_firmware_update(
        self,
        endpoint: str,
        payload: dict[str, Any] | str,
        *,
        node: tuple[str, dict[str, str], tuple[str, int]] | None = None,
    ) -> None:
        """Start outcome polling if the install command may have run."""
        try:
            await self._post_firmware_command(endpoint, payload, node=node)
        except _FirmwareUpdateRejected:
            raise
        except asyncio.CancelledError:
            # HA cancellation cancels the task itself. A cancelled HTTP reply
            # alone can also mean the router rebooted while sending its reply.
            task = asyncio.current_task()
            if task is None or task.cancelling():
                raise
        except (KeeneticApiError, aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Firmware dispatch reply lost at %s: %s", endpoint, type(err).__name__)

    async def _post_firmware_command(
        self,
        endpoint: str,
        payload: Any,
        *,
        node: tuple[str, dict[str, str], tuple[str, int]] | None = None,
    ) -> None:
        """Retry only when the HTTP auth layer rejects a command before execution."""
        if self._session is None:
            raise HomeAssistantError("Cannot connect to the device for a firmware update")
        if node is None:
            base, auth_key = self._base, None
            headers = await self._firmware_auth_headers(auth_key)
        else:
            base, headers, auth_key = node
            # Staging may have refreshed the cookie before the next command.
            headers = self._node_auth_headers.get(auth_key, headers)

        url = f"{base}{RCI_ROOT}/{endpoint}"
        for retry in (False, True):
            async with (
                asyncio.timeout(self._request_timeout),
                await self._session.post(url, json=payload, headers=headers, allow_redirects=False) as response,
            ):
                if response.status != 401:
                    await self._check_firmware_response(response)
                    return
                if auth_key is None:
                    self._authenticated = False
                else:
                    self._node_auth_headers.pop(auth_key, None)

            if retry:
                raise HomeAssistantError(_FIRMWARE_AUTH_REJECTED)
            headers = await self._firmware_auth_headers(auth_key)

    async def _firmware_auth_headers(self, auth_key: tuple[str, int] | None) -> dict[str, str]:
        """Get credentials before sending or retrying a firmware command."""
        try:
            if auth_key is None:
                await self._ensure_auth()
                return dict(self._auth_header or {})
            headers = await self._authenticate_to_node(*auth_key)
        except (KeeneticApiError, aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(_FIRMWARE_AUTH_REJECTED) from err
        if not headers:
            raise HomeAssistantError(_FIRMWARE_AUTH_REJECTED)
        return headers

    async def _check_firmware_response(self, response: aiohttp.ClientResponse) -> None:
        """Distinguish an explicit firmware refusal from an inconclusive reply."""
        status = response.status
        if status in (404, 405):
            raise _FirmwareUpdateRejected(status=status)
        if 400 <= status < 500 and status not in (408, 429) and (await response.text()).strip():
            raise _FirmwareUpdateRejected(status=status)
        if not 200 <= status < 300:
            raise KeeneticApiError("Inconclusive firmware command response")
        if status == 204:
            return
        if "application/json" in response.headers.get("Content-Type", ""):
            result = await response.json()
        else:
            result = await response.text()
        if _firmware_refused(result):
            raise _FirmwareUpdateRejected("RCI rejected the firmware command")

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
        auth_url = f"{scheme}://{bracket_host(node_ip)}:{port}/auth"

        try:
            # Step 1: GET /auth to get challenge
            async with asyncio.timeout(self._request_timeout):
                get_resp = await self._session.get(
                    auth_url, allow_redirects=False
                )

            async with asyncio.timeout(self._request_timeout), get_resp:
                challenge = get_resp.headers.get("X-NDM-Challenge")
                realm = get_resp.headers.get("X-NDM-Realm", "")

                if not challenge:
                    _LOGGER.debug(
                        "Node %s did not return a challenge header",
                        mask_identifier(node_ip),
                    )
                    await get_resp.read()
                    return None

                # Step 2: Compute hash
                ha1 = hashlib.md5(
                    f"{self._username}:{realm}:{self._password}".encode(),
                    usedforsecurity=False,
                ).hexdigest()
                response_hash = hashlib.sha256(
                    (challenge + ha1).encode()
                ).hexdigest()

                # Extract session cookie
                session_cookie = _cookie_header_from_response(get_resp)

            # Step 3: POST /auth with credentials
            post_headers: Dict[str, str] = {}
            if session_cookie:
                post_headers["Cookie"] = session_cookie

            async with asyncio.timeout(self._request_timeout):
                post_resp = await self._session.post(
                    auth_url,
                    json={"login": self._username, "password": response_hash},
                    headers=post_headers,
                    allow_redirects=False,
                )

            async with asyncio.timeout(self._request_timeout), post_resp:
                await post_resp.read()
                if post_resp.status in (200, 204):
                    _LOGGER.debug(
                        "Challenge auth to node %s:%s succeeded",
                        mask_identifier(node_ip),
                        port,
                    )
                    session_cookie = (
                        _cookie_header_from_response(post_resp) or session_cookie
                    )
                    headers = {"Cookie": session_cookie} if session_cookie else {}
                    self._node_auth_headers[(node_ip, port)] = headers
                    return dict(headers)

                _LOGGER.debug(
                    "Challenge auth to node %s:%s returned status %s",
                    mask_identifier(node_ip),
                    port,
                    post_resp.status,
                )
                return None

        except asyncio.TimeoutError:
            _LOGGER.debug(
                "Timeout during auth to node %s:%s",
                mask_identifier(node_ip),
                port,
            )
            return None
        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, ValueError, TypeError, KeyError) as err:
            _LOGGER.debug(
                "Auth to node %s:%s failed: %s",
                mask_identifier(node_ip),
                port,
                type(err).__name__,
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
                "in_progress": coerce_bool(data.get("in-progress", False)),
                "progress_percent": _to_int(data.get("progress", 0)),
                "stage": data.get("stage"),
                "eta_seconds": data.get("eta"),
            }
        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            _LOGGER.debug("firmware progress fetch failed: %s", type(err).__name__)
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
        if self._ndns_supported is False:
            return {}
        try:
            data = await self._rci_get("show/ndns")
            if not data:
                return {}
            self._ndns_supported = True

            # Ensure we always return a dict
            result = dict(data) if isinstance(data, dict) else {}

            # Parse tunnel information if present. Build NEW dicts — the
            # payload may be a shared tick-cache subtree served by
            # reference from _rci_get, and in-place coercion would corrupt
            # it for every other reader in the same refresh.
            if "ttp" in result and isinstance(result["ttp"], dict):
                ttp = dict(result["ttp"])
                result["ttp"] = ttp
                # Ensure tunnel list is properly formatted
                if "tunnel" in ttp and isinstance(ttp["tunnel"], list):
                    tunnels = []
                    for tunnel in ttp["tunnel"]:
                        if isinstance(tunnel, dict):
                            tunnel = dict(tunnel)
                            # Convert string numbers to int where appropriate
                            for key in ["uptime", "idle", "timeout", "linger"]:
                                if key in tunnel and tunnel[key] is not None:
                                    try:
                                        tunnel[key] = int(tunnel[key])
                                    except (ValueError, TypeError):
                                        pass
                            tunnels.append(tunnel)
                    ttp["tunnel"] = tunnels
                elif isinstance(ttp.get("tunnel"), dict):
                    # Single-tunnel collapse: same coercion on a copied dict.
                    tunnel = dict(ttp["tunnel"])
                    for key in ["uptime", "idle", "timeout", "linger"]:
                        if key in tunnel and tunnel[key] is not None:
                            try:
                                tunnel[key] = int(tunnel[key])
                            except (ValueError, TypeError):
                                pass
                    ttp["tunnel"] = tunnel

            _LOGGER.debug(
                "NDNS info retrieved (keys: %s)",
                sorted(result.keys()) if isinstance(result, dict) else type(result).__name__,
            )
            return result

        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            if _is_endpoint_missing(err):
                self._ndns_supported = False
            _LOGGER.debug("Error getting NDNS info: %s", type(err).__name__)
            return {}
