"""Clients domain methods for KeeneticClient."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

import aiohttp

from ...const import DOMAIN, FIELD_CONNECTED, LINK_STATE_UP, RCI_HOTSPOT_HOST_PATHS
from ...utils import coerce_bool, normalize_mac
from ..errors import KeeneticApiError
from ..helpers import (
    _dict_items,
    _is_endpoint_missing,
    _nested_dict_items,
    _validate_cli_arg,
)

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.clients")


class ClientsMixin:
    async def async_get_clients(self) -> List[Dict[str, Any]]:
        # Fast path: once we've found a hotspot subpath that returns real
        # data, latch it and try it first on every subsequent call. This
        # skips up to 2 wasted round-trips per coordinator tick on routers
        # whose firmware doesn't expose the canonical first path.
        winner = getattr(self, "_hotspot_subpath_winner", None)
        ordered_paths: List[str]
        if winner and winner not in self._hotspot_subpath_skip:
            ordered_paths = [winner] + [
                p for p in RCI_HOTSPOT_HOST_PATHS if p != winner
            ]
        else:
            ordered_paths = list(RCI_HOTSPOT_HOST_PATHS)

        last_data: Any = None

        for subpath in ordered_paths:
            if subpath in self._hotspot_subpath_skip:
                continue
            try:
                data = await self._rci_get(subpath)
                last_data = data
            except KeeneticApiError as err:
                if _is_endpoint_missing(err):
                    # Latch this subpath off so we stop hitting an endpoint
                    # the firmware does not expose every coordinator tick.
                    self._hotspot_subpath_skip.add(subpath)
                _LOGGER.debug("hotspot subpath %s failed: %s", subpath, err)
                continue

            items = _nested_dict_items(data, "hosts", "host", "items")

            if items:
                self._hotspot_subpath_winner = subpath
                return items

        _LOGGER.debug(
            "No clients parsed from hotspot host response type=%s",
            type(last_data).__name__,
        )
        return []

    async def async_get_ip_neighbours(self) -> List[Dict[str, Any]]:
        """Return the router's discovered IP neighbours."""
        data = await self._rci_get("show/ip/neighbour")
        neighbours = _nested_dict_items(data, "neighbour", "neighbours", "items")
        if not neighbours:
            try:
                data = await self._rci_parse("show ip neighbour")
            except asyncio.CancelledError:
                raise
            except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
                _LOGGER.debug("Parse-style IP neighbour fetch failed: %s", err)
                return []
            neighbours = _nested_dict_items(
                data,
                "neighbour",
                "neighbours",
                "items",
            ) or _dict_items(data)

        return [
            neighbour
            for neighbour in neighbours
            if normalize_mac(neighbour.get("mac"))
            and (
                neighbour.get("address") is not None
                or neighbour.get("last-seen") is not None
                or neighbour.get("first-seen") is not None
            )
        ]

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
                is_active = coerce_bool(value)
            elif "link" in client:
                is_active = str(client.get("link") or "").lower() == LINK_STATE_UP

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
            FIELD_CONNECTED: connected,
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
        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
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
                if not isinstance(host, dict):
                    continue
                mac = normalize_mac(host.get("mac"))
                if mac:
                    host_policies[mac] = {
                        "policy": host.get("policy"), 
                        "access": host.get("access"), 
                    }

            return host_policies
        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
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
            _LOGGER.debug("Blocking client (deny policy)")
            await self._rci_parse(cmd)
        elif policy.lower() in ("default", "permit", ""):

            cmd = f"no ip hotspot host {mac_clean} policy"
            _LOGGER.debug("Removing client policy")
            await self._rci_parse(cmd)

            cmd = f"ip hotspot host {mac_clean} permit"
            await self._rci_parse(cmd)
        else:
            # Önce erişimi aç (deny durumundaysa permit'e çevir)
            cmd = f"ip hotspot host {mac_clean} permit"
            await self._rci_parse(cmd)

            cmd = f"ip hotspot host {mac_clean} policy {policy}"
            _LOGGER.debug("Setting client policy to %s", policy)
            await self._rci_parse(cmd)

        await self._rci_parse("system configuration save")

    async def async_block_client(self, mac: str) -> None:
        """Block a client's internet access."""
        await self.async_set_client_policy(mac, "deny")

    async def async_unblock_client(self, mac: str) -> None:
        """Unblock a client's internet access."""
        await self.async_set_client_policy(mac, "default")
