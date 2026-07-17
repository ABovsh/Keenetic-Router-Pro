"""Network domain methods for KeeneticClient."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

import aiohttp

from ...const import DOMAIN, LINK_STATE_UP
from ...utils import first_present
from ..errors import KeeneticApiError
from ..helpers import _normalize_interfaces, _validate_cli_arg, iface_label

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.network")


class NetworkMixin:
    async def async_ping_ip(
        self, ip_address: str, timeout_seconds: float = 2.0
    ) -> bool:
        """Ping an IP address using the router's ping functionality.

        Returns True if the host is reachable, False otherwise.
        """
        try:
            ip_address = _validate_cli_arg(ip_address, "IP address")

            async with asyncio.timeout(timeout_seconds):
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

        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            _LOGGER.debug("Ping to %s failed: %s", ip_address, err)
            return False

    async def async_ping_multiple(
        self,
        ip_addresses: List[str],
        timeout_seconds: float = 2.0
    ) -> Dict[str, bool]:
        """Ping multiple IP addresses concurrently.

        Returns a dict mapping IP address to reachability status.
        """
        if not ip_addresses:
            return {}

        tasks = [self.async_ping_ip(ip, timeout_seconds) for ip in ip_addresses]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        ping_results: Dict[str, bool] = {}
        for ip, result in zip(ip_addresses, results):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                ping_results[ip] = False
            else:
                ping_results[ip] = bool(result)

        return ping_results

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

            label = iface.get("label") or iface_label(iface, iface_id)
            if label in seen_labels:
                continue
            seen_labels.add(label)

            entry: Dict[str, Any] = {
                "label": label,
                "appearance": iface.get("type"),
                "link": iface.get("link", "unknown"),
            }
            if iface.get("link") == LINK_STATE_UP:
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
                label = port_data.get("label") or iface_label(port_data)
                if label and label not in seen_labels:
                    seen_labels.add(label)
                    entry = {
                        "label": label,
                        "appearance": port_data.get("type"),
                        "link": port_data.get("link", "unknown"),
                    }
                    if port_data.get("link") == LINK_STATE_UP:
                        entry["speed"] = port_data.get("speed")
                        entry["duplex"] = port_data.get("duplex")
                    ports.append(entry)
            else:
                # Dict of port dicts (keyed by port number)
                for port_key, port_val in port_data.items():
                    if not isinstance(port_val, dict):
                        continue
                    label = port_val.get("label") or iface_label(port_val, port_key)
                    if label in seen_labels:
                        continue
                    seen_labels.add(label)
                    entry = {
                        "label": label,
                        "appearance": port_val.get("type"),
                        "link": port_val.get("link", "unknown"),
                    }
                    if port_val.get("link") == LINK_STATE_UP:
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
        """Return statistics (traffic, speed) for a specific interface.

        We prefer the cheaper GET ``show/interface/stat`` once we've seen
        it succeed; ``_rci_parse`` (CLI parse mode) is heavier on the
        router. After the first successful GET, ``_iface_stat_get_only``
        latches True and we skip the parse-mode attempt for the rest of
        the session. The parse-mode fallback is still used on the very
        first call (or after auth/recovery) so older firmwares that only
        expose the parse path keep working.
        """
        safe_name = _validate_cli_arg(name, "interface name")
        get_only = getattr(self, "_iface_stat_get_only", None)
        if get_only is not True:
            try:
                data = await self._rci_parse(f"show interface {safe_name} stat")
                if isinstance(data, dict):
                    stat_keys = {"rxbytes", "txbytes", "rxspeed", "txspeed"}
                    if stat_keys.intersection(data):
                        return data
            except asyncio.CancelledError:
                raise
            except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
                _LOGGER.debug(
                    "Parse-style interface stat failed for %s: %s; trying GET fallback",
                    safe_name,
                    err,
                )
        result = await self._rci_get(
            "show/interface/stat", params={"name": safe_name}
        ) or {}
        if isinstance(result, dict) and result:
            self._iface_stat_get_only = True
        return result

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

    async def async_get_traffic_stats(
        self,
        interfaces: Dict[str, Any] | None = None,
        iface_list: List[Dict[str, Any]] | None = None,
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
            if iface_list is None:
                if interfaces is None:
                    interfaces = await self.async_get_interfaces()
                iface_list = _normalize_interfaces(interfaces)
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

                if state == LINK_STATE_UP and any(k in name_joined for k in WAN_KEYWORDS):
                    stats["total_rx"] = first_present(
                        iface,
                        "rxbytes",
                        "rx-bytes",
                        "bytes-rx",
                        "rx",
                        default=0,
                    )
                    stats["total_tx"] = first_present(
                        iface,
                        "txbytes",
                        "tx-bytes",
                        "bytes-tx",
                        "tx",
                        default=0,
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

        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            _LOGGER.debug("Error getting traffic stats: %s", err)

        return stats

    async def async_get_all_interface_stats(
        self,
        interfaces: Dict[str, Any] | None = None,
        iface_list: List[Dict[str, Any]] | None = None,
        wan_interfaces: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Get traffic statistics for all interfaces.

        Returns dict mapping interface name to stats (rxbytes, txbytes, etc.)
        """
        if iface_list is None:
            if interfaces is None:
                interfaces = await self.async_get_interfaces()
            iface_list = _normalize_interfaces(interfaces)

        if wan_interfaces is None:
            wan_interfaces = await self.async_get_wan_interfaces(
                interfaces=interfaces, iface_list=iface_list
            )
        wan_ids = {str(wan.get("id")) for wan in wan_interfaces if wan.get("id")}

        targets: List[Dict[str, Any]] = []
        for iface in iface_list:
            iface_name = iface.get("id") or iface.get("interface-name")
            if not iface_name:
                continue
            iface_type = str(iface.get("type") or "").lower()
            if iface_name not in wan_ids and iface_type in (
                "bridge",
                "vlan",
                "accesspoint",
            ):
                continue
            targets.append({
                "name": iface_name,
                "type": iface_type,
                "link": iface.get("link"),
                "state": iface.get("state"),
            })

        raw_stats: Dict[str, Any] = {}

        batch_supported = getattr(self, "_rci_batch_supported", None)
        if batch_supported is not False and len(targets) >= 2:
            batch_stats = await self._try_batch_interface_stats(targets)
            if batch_stats is not None:
                raw_stats = batch_stats

        missing_targets = [t for t in targets if t["name"] not in raw_stats]

        if missing_targets:
            sem = asyncio.Semaphore(4)

            async def _bounded_interface_stat(name: str) -> Dict[str, Any]:
                async with sem:
                    return await self.async_get_interface_stat(name)

            results = await asyncio.gather(
                *(_bounded_interface_stat(t["name"]) for t in missing_targets),
                return_exceptions=True,
            )

            for target, stats in zip(missing_targets, results):
                if isinstance(stats, asyncio.CancelledError):
                    raise stats
                if isinstance(stats, Exception):
                    _LOGGER.debug(
                        "Failed to get stats for %s: %s", target["name"], stats
                    )
                    continue
                if not stats:
                    continue
                if not isinstance(stats, dict):
                    continue
                raw_stats[target["name"]] = stats

        all_stats: Dict[str, Dict[str, Any]] = {}
        for target in targets:
            stats = raw_stats.get(target["name"])
            if not stats or not isinstance(stats, dict):
                continue
            stats = dict(stats)
            stats["interface_name"] = target["name"]
            stats["interface_type"] = target["type"]
            stats["link"] = target["link"]
            stats["state"] = target["state"]
            all_stats[target["name"]] = stats

        return all_stats

    @staticmethod
    def _is_stat_error_record(stat: Any) -> bool:
        """Return True if a batch stat entry is not usable interface stat data."""
        if not isinstance(stat, dict) or not stat:
            return True
        status = stat.get("status")
        if isinstance(status, list) and status:
            first = status[0]
            if isinstance(first, dict) and first.get("status") == "error":
                return True
        return False

    async def _try_batch_interface_stats(
        self, targets: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]] | None:
        """Attempt a single composite POST for all interface stats.

        Returns a dict of usable name -> stat dict for entries that came
        back clean from the batch. Interfaces that are missing/error in
        the batch response are simply absent from the result, so the
        caller falls back to per-call fetches for just those. Returns
        None if the overall response shape is unusable, so the caller
        falls back to the fan-out path entirely.

        Positional matching is safe: the RCI composite response mirrors
        the request tree, so the ``stat`` array comes back in request
        order and an unknown interface yields an in-place error record,
        not an omission (verified live on KN-1812 5.x, 2026-07-17, incl.
        a reversed-order and a bogus-name probe). Stat records carry no
        self-identifying field, so identity matching is not possible.
        """
        tree = {
            "show": {
                "interface": {
                    "stat": [{"name": t["name"]} for t in targets],
                }
            }
        }
        # _rci_batch is deliberately non-raising (returns None on failure);
        # only CancelledError propagates, and it should.
        result = await self._rci_batch(tree)
        if result is None:
            return None

        stat_list = (
            result.get("show", {}).get("interface", {}).get("stat")
            if isinstance(result, dict)
            else None
        )
        if not isinstance(stat_list, list) or len(stat_list) != len(targets):
            return None

        batch_stats: Dict[str, Dict[str, Any]] = {}
        for target, stat in zip(targets, stat_list):
            if self._is_stat_error_record(stat):
                continue
            batch_stats[target["name"]] = stat
        return batch_stats
