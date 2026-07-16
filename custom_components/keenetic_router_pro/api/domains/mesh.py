"""Mesh domain methods for KeeneticClient."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

import aiohttp

from ...const import DOMAIN, FIELD_CONNECTED, LINK_STATE_DOWN, LINK_STATE_UP
from ...utils import coerce_bool
from ..errors import KeeneticApiError
from ..helpers import (
    _dict_items,
    _nested_dict_items,
    _to_int,
    _validate_cli_arg,
)

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.mesh")


class MeshMixin:
    async def async_get_mesh_nodes(
        self, clients: List[Dict[str, Any]] | None = None
    ) -> List[Dict[str, Any]]:
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
            fallback_nodes = await self._get_mesh_nodes_from_clients(clients=clients)
        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            _LOGGER.debug("mesh fallback from clients failed: %s", err)
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

            if not data:
                return fallback_nodes

            members = _nested_dict_items(data, "member", "members", "mws")
            if not members and isinstance(data, list):
                members = _dict_items(data)
            if not members:
                return fallback_nodes

            for member in members:
                cid = member.get("cid")
                if not cid:
                    continue

                mac = member.get("mac")
                system_info = member.get("system") or {}
                if not isinstance(system_info, dict):
                    system_info = {}
                rci_info = member.get("rci") or {}
                if not isinstance(rci_info, dict):
                    rci_info = {}

                errors_count = _to_int(rci_info.get("errors", 0))
                if "internet-available" in member:
                    is_connected = (
                        errors_count == 0
                        and coerce_bool(member.get("internet-available"))
                    )
                else:
                    # Older firmware omits internet-available; require the
                    # member to also have a non-offline link state, otherwise
                    # a node that never reported errors looks "connected".
                    state = str(member.get("state") or "").lower()
                    is_connected = (
                        errors_count == 0
                        and state not in ("", "offline", LINK_STATE_DOWN)
                    )

                ports = _dict_items(member.get("port", []))
                normalized_ports = []
                for port in ports:
                    if isinstance(port, dict):
                        normalized_port = {
                            "label": port.get("label"),
                            "appearance": port.get("appearance"),
                            "link": port.get("link"),
                        }
                        if port.get("link") == LINK_STATE_UP:
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
                    "region": member.get("region"),
                    FIELD_CONNECTED: is_connected,
                    "state": LINK_STATE_UP if is_connected else LINK_STATE_DOWN,
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

        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            # "not found" durumunda tekrar denemeyip cache'leyelim
            msg = str(err).lower()
            if ("not found" in msg) or ("404" in msg):
                self._mws_member_supported = False
                return fallback_nodes

            # Transient failure (timeout/5xx while the controller is busy):
            # do NOT return the MAC-keyed fallback — that would flip every
            # mesh node id from CID to MAC for one tick and change entity
            # unique_ids. Surface the failure; the coordinator keeps the
            # previous snapshot.
            _LOGGER.debug("Error getting mesh nodes from mws/member: %s", err)
            raise

        return nodes or fallback_nodes

    async def _get_mesh_nodes_from_clients(
        self, clients: List[Dict[str, Any]] | None = None
    ) -> List[Dict[str, Any]]:
        """Fallback: Get mesh nodes from client list if mws/member fails.

        Accepts a pre-fetched ``clients`` list so the coordinator can
        avoid an extra ``show/ip/hotspot`` round-trip on mesh routers.
        """
        if clients is None:
            clients = await self.async_get_clients()
        nodes: List[Dict[str, Any]] = []

        for client in clients:
            system_mode = str(client.get("system-mode") or "").lower()
            if system_mode not in ("extender", "repeater"):
                continue

            mac = client.get("mac")
            if not mac:
                continue

            is_active = coerce_bool(client.get("active", False))

            nodes.append({
                "id": mac,
                "cid": None,
                "mac": mac,
                "ip": client.get("ip"),
                "name": client.get("name") or client.get("hostname") or mac,
                "mode": system_mode,
                FIELD_CONNECTED: is_active,
                "state": LINK_STATE_UP if is_active else LINK_STATE_DOWN,
                "uptime": client.get("uptime"),
                "firmware": client.get("firmware"),
            })

        return nodes

    async def async_reboot_mesh_node(self, cid: str) -> None:
        """Reboot a specific mesh/extender node by CID (component ID).

        Command format: mws member {cid} reboot
        """
        cid = _validate_cli_arg(cid, "mesh node cid")
        _LOGGER.warning("Sending reboot command to mesh node")

        cmd = f"mws member {cid} reboot"
        await self._rci_parse(cmd)
