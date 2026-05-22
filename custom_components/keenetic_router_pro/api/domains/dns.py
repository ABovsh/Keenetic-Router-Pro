"""DNS domain methods for KeeneticClient."""

from __future__ import annotations

import logging
import asyncio
from typing import Any, Dict, List

import aiohttp
from urllib.parse import urlsplit, urlunsplit

from ...const import DOMAIN
from ..constants import _DNS_PROXY_STAT_RE
from ..errors import KeeneticApiError
from ..helpers import _dict_items, _is_endpoint_missing


def _redact_doh_uri(value: Any) -> str:
    """Strip userinfo, query, and any path beyond the first segment from a DoH URI.

    The path of provider-personalized URIs (e.g. NextDNS) embeds a private
    configuration ID that should not appear in HA state or diagnostics.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return ""
    if port:
        host = f"{host}:{port}"
    return urlunsplit((parts.scheme, host, "/", "", ""))

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.dns")


class DnsMixin:
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
        if self._dns_proxy_supported is False:
            return {}
        try:
            data = await self._rci_get("show/dns-proxy") or {}
            proxy_status = data.get("proxy-status") or []
            if not isinstance(proxy_status, list):
                return {}
            self._dns_proxy_supported = True

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
                proxy_https = proxy.get("proxy-https") or {}
                if not isinstance(proxy_https, dict):
                    proxy_https = {}
                https_servers_raw = proxy_https.get("server-https") or []
                # Firmware may collapse a single DoH upstream to a dict;
                # `_dict_items` flattens both shapes to a list of dicts.
                https_servers = _dict_items(https_servers_raw)

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
                            _redact_doh_uri(server.get("uri"))
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
        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            if _is_endpoint_missing(err):
                self._dns_proxy_supported = False
            _LOGGER.debug("Error getting DNS proxy status: %s", err)
            return {}
