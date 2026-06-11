"""System domain methods for KeeneticClient."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Dict

import aiohttp

from homeassistant.exceptions import HomeAssistantError

from ...const import DOMAIN, RCI_SHOW_VERSION
from ...utils import bracket_host, coerce_bool, mask_identifier
from ..constants import RCI_ROOT
from ..errors import KeeneticApiError
from ..helpers import (
    _cookie_header_from_response,
    _extract_parse_messages,
    _is_endpoint_missing,
    _response_summary,
    _to_int,
    _validate_cli_arg,
)

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.system")


class SystemMixin:
    async def async_get_system_info(self) -> Dict[str, Any]:
        """Return basic system info: hostname, version, cpu, memory, uptime, etc."""
        data = await self._rci_get("show/system")
        return data or {}


    async def async_get_current_version_info(self) -> Dict[str, Any]:
        """Return version info"""
        data = await self._rci_get(RCI_SHOW_VERSION)
        return data or {}
    

    async def async_get_available_version_info(self) -> Dict[str, Any]:
        """Return version info"""
        data = await self._rci_get("components/check-update")
        return data or {}


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
        """Start firmware update for the controller (main router) ONLY.

        Tries endpoints in order:
        1. /rci/components stage + commit (KeeneticOS 5.x)
        2. /rci/system/update (older firmware)
        Does NOT use mws/update/start as that triggers a mesh-wide update.
        """
        # Try KeeneticOS 5.x: stage components then commit
        try:
            version_data = await self._rci_get(RCI_SHOW_VERSION)
            ndw_components = ""
            if isinstance(version_data, dict):
                ndw = version_data.get("ndw")
                if isinstance(ndw, dict):
                    ndw_components = ndw.get("components", "")

            if ndw_components:
                current_components = [
                    c.strip() for c in ndw_components.split(",") if c.strip()
                ]
                install_list = [{"component": c} for c in current_components]
                payload = [{"components": {"install": install_list}}]

                _LOGGER.debug("Staging component update on controller")
                await self._request("POST", f"{RCI_ROOT}/", json=payload)

                _LOGGER.debug("Committing component update on controller")
                await self._rci_post("components/commit", {"reason": "manual"}, allow_text=True)
                _LOGGER.info("Controller firmware update started via components/commit")
                return True
        except KeeneticApiError as err:
            if "404" not in str(err):
                raise HomeAssistantError(f"Failed to start update: {err}") from err
            _LOGGER.debug("Components update not available, trying system/update")

        # Try system/update (older firmware)
        try:
            result = await self._rci_post("system/update", {"confirm": True}, allow_text=True)
            if isinstance(result, dict):
                status = result.get("status") or result.get("result")
                if status in ("started", "ok", True, "accepted"):
                    _LOGGER.info("Controller firmware update started via system/update")
                    return True
            if isinstance(result, str) and result.strip().lower() in {
                "started",
                "ok",
                "accepted",
            }:
                _LOGGER.info("Controller firmware update started via system/update")
                return True
        except KeeneticApiError as err:
            if "404" not in str(err):
                raise HomeAssistantError(f"Failed to start update: {err}") from err
            _LOGGER.debug("system/update returned 404")

        msg = "No compatible firmware update endpoint found on this router"
        _LOGGER.error(msg)
        raise HomeAssistantError(msg)


    async def async_start_node_firmware_update(  # NOSONAR python:S3516 — interface-required success signal, raises on failure
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
                    mask_identifier(label),
                    mask_identifier(member),
                )
                parse_result = await self._rci_parse(
                    f"mws member {member} update start"
                )
                messages = _extract_parse_messages(parse_result)
                error_marker = next(
                    (
                        msg
                        for msg in messages
                        if any(
                            tok in msg.lower()
                            for tok in ("error", "failed", "unknown", "invalid")
                        )
                    ),
                    None,
                )
                if error_marker:
                    _LOGGER.warning(
                        "Controller MWS update for node %s reported: %s",
                        mask_identifier(label),
                        error_marker,
                    )
                    raise KeeneticApiError(error_marker)
                return True
            except asyncio.CancelledError:
                raise
            except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
                _LOGGER.warning(
                    "Controller MWS update command failed for node %s: %s. "
                    "Trying direct node update fallback.",
                    mask_identifier(label),
                    type(err).__name__,
                )

        scheme = "https" if self._ssl else "http"

        # Try controller's port first, then default port 80
        ports_to_try = [self._port]
        if self._port != 80:
            ports_to_try.append(80)

        for port in ports_to_try:
            base = f"{scheme}://{bracket_host(node_ip)}:{port}"

            # Always do challenge auth with mesh nodes
            node_headers = await self._authenticate_to_node(node_ip, port)
            if not node_headers:
                _LOGGER.debug(
                    "Could not authenticate to node %s on port %s",
                    mask_identifier(label),
                    port,
                )
                continue

            # KeeneticOS 5.x: two-step update via components
            # Step 1: Get current components from show/version
            try:
                url = f"{base}{RCI_ROOT}/show/version"
                async with asyncio.timeout(self._request_timeout):
                    resp = await self._session.get(url, headers=node_headers)
                async with resp:
                    if resp.status == 200:
                        version_data = await resp.json()
                        ndw_components = ""
                        if isinstance(version_data, dict):
                            ndw = version_data.get("ndw")
                            if isinstance(ndw, dict):
                                ndw_components = ndw.get("components", "")
                    elif resp.status == 401:
                        _LOGGER.debug(
                            "Auth rejected on node %s port %s",
                            mask_identifier(label),
                            port,
                        )
                        self._node_auth_headers.pop((node_ip, port), None)
                        continue
                    else:
                        ndw_components = ""

                    if ndw_components:
                        current_components = [
                            c.strip() for c in ndw_components.split(",") if c.strip()
                        ]
                        _LOGGER.debug(
                            "Node %s has %d components: %s",
                            mask_identifier(label),
                            len(current_components),
                            current_components,
                        )

                        # Step 2: POST component list to /rci/
                        install_list = [
                            {"component": c} for c in current_components
                        ]
                        payload = [{"components": {"install": install_list}}]

                        url = f"{base}{RCI_ROOT}/"
                        _LOGGER.info(
                            "Staging component update on node %s",
                            mask_identifier(label),
                        )
                        async with asyncio.timeout(self._request_timeout):
                            resp = await self._session.post(
                                url,
                                json=payload,
                                headers=node_headers,
                            )
                        async with resp:
                            if resp.status == 401:
                                _LOGGER.debug(
                                    "Auth rejected while staging update on node "
                                    "%s port %s",
                                    mask_identifier(label),
                                    port,
                                )
                                self._node_auth_headers.pop((node_ip, port), None)
                                continue
                            if resp.status not in (200, 204):
                                text = await resp.text()
                                _LOGGER.warning(
                                    "Node %s component staging returned %s: %s",
                                    mask_identifier(label),
                                    resp.status,
                                    _response_summary(text),
                                )
                                continue

                        # Step 3: Commit
                        url = f"{base}{RCI_ROOT}/components/commit"
                        _LOGGER.info(
                            "Committing update on node %s",
                            mask_identifier(label),
                        )
                        async with asyncio.timeout(self._request_timeout):
                            resp = await self._session.post(
                                url,
                                json={"reason": "manual"},
                                headers=node_headers,
                            )
                        async with resp:
                            if resp.status in (200, 204):
                                _LOGGER.info(
                                    "Node %s firmware update started via "
                                    "components/commit",
                                    mask_identifier(label),
                                )
                                return True
                            if resp.status == 401:
                                _LOGGER.debug(
                                    "Auth rejected while committing update on node "
                                    "%s port %s",
                                    mask_identifier(label),
                                    port,
                                )
                                self._node_auth_headers.pop((node_ip, port), None)
                                continue

                            text = await resp.text()
                            _LOGGER.warning(
                                "Node %s commit returned %s: %s",
                                mask_identifier(label),
                                resp.status,
                                _response_summary(text),
                            )
                    else:
                        _LOGGER.debug(
                            "Node %s has no ndw.components in version info",
                            mask_identifier(label),
                        )
            except asyncio.TimeoutError:
                _LOGGER.debug(
                    "Timeout connecting to node %s port %s",
                    mask_identifier(label),
                    port,
                )
                continue
            except asyncio.CancelledError:
                raise
            except (KeeneticApiError, aiohttp.ClientError, ValueError, TypeError, KeyError) as err:  # NOSONAR python:S1045 — aiohttp.ClientError overlaps with TimeoutError only via ServerTimeoutError; we want TimeoutError handled separately above for clarity
                _LOGGER.debug(
                    "Components update on node %s failed: %s",
                    mask_identifier(label),
                    type(err).__name__,
                )

            # Fallback: POST /rci/system/update (older firmware)
            try:
                url = f"{base}{RCI_ROOT}/system/update"
                _LOGGER.info(
                    "Attempting direct update on node %s",
                    mask_identifier(label),
                )
                async with asyncio.timeout(self._request_timeout):
                    resp = await self._session.post(
                        url,
                        json={"confirm": True},
                        headers=node_headers,
                    )
                async with resp:
                    if resp.status in (200, 204):
                        _LOGGER.info(
                            "Node %s firmware update started via system/update",
                            mask_identifier(label),
                        )
                        return True
                    if resp.status == 401:
                        _LOGGER.debug(
                            "Auth rejected on node %s port %s during system/update",
                            mask_identifier(label),
                            port,
                        )
                        self._node_auth_headers.pop((node_ip, port), None)
                        continue
                    if resp.status != 404:
                        text = await resp.text()
                        _LOGGER.debug(
                            "Node %s system/update returned %s: %s",
                            mask_identifier(label),
                            resp.status,
                            _response_summary(text),
                        )
            except asyncio.TimeoutError:
                _LOGGER.debug(
                    "Timeout on system/update for node %s",
                    mask_identifier(label),
                )
            except asyncio.CancelledError:
                raise
            except (KeeneticApiError, aiohttp.ClientError, ValueError, TypeError, KeyError) as err:
                _LOGGER.debug(
                    "system/update on node %s failed: %s",
                    mask_identifier(label),
                    type(err).__name__,
                )

        msg = f"Could not start firmware update on node {label}"
        _LOGGER.error(
            "Could not start firmware update on node %s",
            mask_identifier(label),
        )
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
        auth_url = f"{scheme}://{bracket_host(node_ip)}:{port}/auth"

        try:
            # Step 1: GET /auth to get challenge
            async with asyncio.timeout(self._request_timeout):
                get_resp = await self._session.get(
                    auth_url, allow_redirects=False
                )

            async with get_resp:
                challenge = get_resp.headers.get("X-NDM-Challenge")
                realm = get_resp.headers.get("X-NDM-Realm", "")

                if not challenge:
                    _LOGGER.debug(
                        "Node %s did not return challenge header, "
                        "using basic auth fallback",
                        mask_identifier(node_ip),
                    )
                    await get_resp.read()
                    # Do NOT cache the Basic fallback: a transient error
                    # page without the challenge header would otherwise
                    # latch an unusable header for the whole session on a
                    # challenge-auth node.
                    return dict(self._basic_auth_headers())

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
                )

            async with post_resp:
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
