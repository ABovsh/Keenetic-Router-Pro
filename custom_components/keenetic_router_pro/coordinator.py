"""DataUpdateCoordinator for Keenetic Router Pro."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KeeneticAuthError, KeeneticClient
from .const import DOMAIN, FAST_SCAN_INTERVAL, DEFAULT_PING_INTERVAL

import logging

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.coordinator")

_VERSION_CACHE_KEYS = (
    "title",
    "release",
    "sandbox",
    "arch",
    "description",
    "model",
    "device",
    "hw_id",
    "ndw",
    "ndw4",
    "ndm",
    "bsp",
)


def _to_int(value: Any) -> int:
    """Best-effort integer coercion for loosely typed Keenetic RCI values."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _first_stat_int(stats: dict[str, Any], *keys: str) -> int:
    """Return the first non-empty integer stat from several firmware key names."""
    for key in keys:
        value = stats.get(key)
        if value not in (None, ""):
            return _to_int(value)
    return 0


def _counter_rate_bytes_per_second(
    current: int,
    previous: Any,
    elapsed_seconds: float,
) -> float:
    """Calculate a monotonic byte-counter rate, clamping resets to zero."""
    if elapsed_seconds <= 0:
        return 0.0
    delta = current - _to_int(previous)
    if delta < 0:
        return 0.0
    return max(0.0, delta / elapsed_seconds)


# ICMP ping via icmplib (mirrors the Home Assistant Ping integration).
try:
    from icmplib import async_ping, SocketPermissionError
    ICMPLIB_AVAILABLE = True
except ImportError:
    ICMPLIB_AVAILABLE = False
    _LOGGER.warning("icmplib not available, ping-based tracking will not work")


class KeeneticCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches all router data on each tick."""

    def __init__(self, hass: HomeAssistant, client: KeeneticClient) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="keenetic_router_pro",
            update_interval=timedelta(seconds=FAST_SCAN_INTERVAL),
        )
        self.client = client
        self._refresh_count = 0

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all router data with bounded, staged parallelism.
 
        The Keenetic RCI endpoint is a single HTTP surface served by a
        modest router CPU, so we cap concurrency at 4 in-flight calls
        with a semaphore. Calls are split into dependency stages:
 
          * Stage 1 has no dependencies and runs first.
          * Stage 2 needs ``interfaces`` from stage 1.
          * Stage 3 performs CPU-only WAN enrichment from already fetched
            interface statistics.
 
        Within each stage we use ``asyncio.gather`` with
        ``return_exceptions=True`` so a single failing endpoint can no
        longer kill the whole update tick — failed fetches are
        normalised to safe defaults of the same shape the downstream
        code expects, and the next tick simply retries them.
        """
        sem = asyncio.Semaphore(4)
 
        async def _bounded(coro):
            async with sem:
                return await coro

        _prev = self.data or {}
        _prev_sys = _prev.get("system") or {}

        async def _resolve(value: Any) -> Any:
            return value

        # Collected per-tick so we can emit a single warning instead of
        # silently defaulting every failing fetch at debug level.
        failed_fetches: list[tuple[str, BaseException]] = []

        def _ok(name, value, default, silent: bool = False):
            """Replace failed fetches with a safe default of the right shape.

            Failures are recorded in ``failed_fetches`` so the tick can
            emit a single aggregated warning at the end of stage 2, and
            so critical fetches can be checked for failure explicitly.

            Pass ``silent=True`` for endpoints that legitimately may
            not exist on all firmwares (e.g. optional components like
            IPsec site-to-site). The default is still substituted but
            the failure is not added to the warning aggregate — the
            api layer is expected to debug-log the reason itself.
            """
            if isinstance(value, BaseException):
                if not silent:
                    failed_fetches.append((name, value))
                _LOGGER.debug("Coordinator fetch %s failed: %s", name, value)
                return default
            return value
 
        first_refresh = self.data is None
        slow_refresh = first_refresh or self._refresh_count % 6 == 0
        very_slow_refresh = first_refresh or self._refresh_count % 30 == 0

        # Precompute the cached fallbacks for skipped slow-tick fetches
        # outside the gather() call so the fast tick doesn't rebuild
        # these dicts every time. On a non-slow tick the previous
        # version info is reused verbatim; on a slow tick these are
        # discarded and the live RCI fetch result is used instead.
        if not slow_refresh:
            _cached_version = {
                k: _prev_sys.get(k) for k in _VERSION_CACHE_KEYS if k in _prev_sys
            }
        if not very_slow_refresh:
            _cached_version_available = {
                "title": _prev_sys.get("release-available"),
                "sandbox": _prev_sys.get("fw-update-sandbox"),
                "update-available": _prev_sys.get("fw-update-available", False),
            }

        # ---------- Stage 1: independent fetches ----------
        (
            system,
            version,
            version_available,
            interfaces,
            clients,
            mesh_nodes,
            host_policies,
            ndns_info,
            ping_check_status,
            crypto_maps,
            dns_proxy,
        ) = await asyncio.gather(
            _bounded(self.client.async_get_system_info()),
            _bounded(self.client.async_get_current_version_info()) if slow_refresh else _resolve(_cached_version),
            _bounded(self.client.async_get_available_version_info()) if very_slow_refresh else _resolve(_cached_version_available),
            _bounded(self.client.async_get_interfaces()),
            _bounded(self.client.async_get_clients()),
            _bounded(self.client.async_get_mesh_nodes()) if slow_refresh else _resolve(_prev.get("mesh_nodes", [])),
            _bounded(self.client.async_get_host_policies()) if slow_refresh else _resolve(_prev.get("host_policies", {})),
            _bounded(self.client.async_get_ndns_info()) if very_slow_refresh else _resolve(_prev.get("ndns", {})),
            _bounded(self.client.async_get_ping_check_status()),
            _bounded(self.client.async_get_crypto_maps()) if slow_refresh else _resolve(_prev.get("crypto_maps", {})),
            _bounded(self.client.async_get_dns_proxy_status()) if very_slow_refresh else _resolve(_prev.get("dns_proxy", {})),
            return_exceptions=True,
        )
 
        system = _ok("system_info", system, {})
        version = _ok("current_version", version, {})
        version_available = _ok("available_version", version_available, {})
        interfaces = _ok("interfaces", interfaces, [])
        clients = _ok("clients", clients, [])
        mesh_nodes = _ok("mesh_nodes", mesh_nodes, [])
        host_policies = _ok("host_policies", host_policies, {})
        ndns_info = _ok("ndns_info", ndns_info, {})
        ping_check_status = _ok("ping_check_status", ping_check_status, {})
        # Crypto maps: not every router/firmware has the IPsec component,
        # so this endpoint may be unavailable. Mark the fetch as silent
        # so an absent endpoint doesn't produce a warning on every tick —
        # the api layer already debug-logs the reason.
        crypto_maps = _ok(
            "crypto_maps", crypto_maps, {}, silent=True
        )
        # DNS proxy is diagnostic-only and intentionally slow-cadence;
        # routers without the endpoint should not warn every refresh.
        dns_proxy = _ok("dns_proxy", dns_proxy, {}, silent=True)

        # Fail-fast on critical fetches. If the router is unreachable,
        # auth has expired, or the RCI surface is down, ``system_info``
        # and ``interfaces`` are the two calls that MUST succeed — every
        # downstream computation depends on them. Letting them default
        # to ``{}`` / ``[]`` would produce a ghost-mode tick where every
        # entity silently shows "zero/empty" instead of ``unavailable``,
        # masking real outages. Raise ``UpdateFailed`` so HA marks the
        # coordinator as failed and retries on the next tick.
        critical_failures = [
            (name, err) for name, err in failed_fetches
            if name in ("system_info", "interfaces")
        ]
        if critical_failures:
            if any(isinstance(err, KeeneticAuthError) for _, err in critical_failures):
                raise ConfigEntryAuthFailed("Keenetic credentials were rejected")
            details = ", ".join(f"{n}: {e!r}" for n, e in critical_failures)
            raise UpdateFailed(f"Critical router fetch failed ({details})")

        client_stats = self.client.summarize_client_stats(clients)
 
        merged_system = {**system, **version}
        merged_system["release-available"] = (
            version_available.get("title") or version_available.get("release")
        )
        merged_system["fw-update-sandbox"] = version_available.get("sandbox")
        merged_system["fw-update-available"] = version_available.get(
            "update-available", False
        )
 
        # ---------- Stage 2: depends on stage-1 `interfaces` ----------
        # All of these accept a pre-fetched ``interfaces=`` argument so
        # we don't re-query the router for the same data once per call.
        (
            wifi,
            wireguard,
            vpn_tunnels,
            wan_status,
            wan_interfaces,
            traffic_stats,
            port_info,
            interface_stats,
        ) = await asyncio.gather(
            _bounded(self.client.async_get_wifi_networks(interfaces=interfaces)),
            _bounded(self.client.async_get_wireguard_status(interfaces=interfaces)),
            _bounded(self.client.async_get_vpn_tunnels(interfaces=interfaces)),
            _bounded(self.client.async_get_wan_status(interfaces=interfaces)),
            _bounded(self.client.async_get_wan_interfaces(interfaces=interfaces)),
            _bounded(self.client.async_get_traffic_stats(interfaces=interfaces)),
            _bounded(self.client.async_get_port_info(interfaces=interfaces)),
            _bounded(self.client.async_get_all_interface_stats(interfaces=interfaces)),
            return_exceptions=True,
        )
 
        wifi = _ok("wifi", wifi, [])
        wireguard = _ok("wireguard", wireguard, [])
        vpn_tunnels = _ok("vpn_tunnels", vpn_tunnels, [])
        wan_status = _ok("wan_status", wan_status, {})
        wan_interfaces = _ok("wan_interfaces", wan_interfaces, [])
        traffic_stats = _ok("traffic_stats", traffic_stats, {})
        port_info = _ok("port_info", port_info, {})
        interface_stats = _ok("interface_stats", interface_stats, {})

        # Emit a single aggregated warning per tick for any non-critical
        # fetches that fell back to defaults. Keeping this above debug
        # ensures a user whose router is mostly-working-but-flaky sees
        # *something* in the default log level instead of silently
        # getting empty data for the affected entities.
        if failed_fetches:
            _LOGGER.warning(
                "Keenetic coordinator: %d fetch(es) failed this tick and "
                "fell back to defaults: %s",
                len(failed_fetches),
                ", ".join(name for name, _ in failed_fetches),
            )
 
        # ---------- WAN enrichment (CPU-only, runs on already-fetched
        # data — logic unchanged from the sequential implementation) ----------
        #
        # We reuse the already-fetched ``interface_stats`` (show/interface/stat
        # for every interface) instead of firing extra RCI calls. Throughput
        # is computed as a delta against the previous coordinator tick.
        prev_wan_by_id: dict[str, dict[str, Any]] = {}
        if self.data:
            for prev in self.data.get("wan_interfaces", []) or []:
                pid = prev.get("id")
                if pid:
                    prev_wan_by_id[pid] = prev
        now_ts = asyncio.get_running_loop().time()
 
        for wan in wan_interfaces:
            wan_id = wan.get("id")
            stats = (interface_stats or {}).get(wan_id) or {}
            rx_bytes = _first_stat_int(
                stats,
                "rxbytes",
                "rx-bytes",
                "rx_bytes",
            )
            tx_bytes = _first_stat_int(
                stats,
                "txbytes",
                "tx-bytes",
                "tx_bytes",
            )
            wan["rx_bytes"] = rx_bytes
            wan["tx_bytes"] = tx_bytes
            wan["rx_packets"] = _first_stat_int(
                stats,
                "rxpackets",
                "rx-packets",
            )
            wan["tx_packets"] = _first_stat_int(
                stats,
                "txpackets",
                "tx-packets",
            )
            wan["rx_speed_raw"] = _first_stat_int(
                stats,
                "rxspeed",
                "rx-speed",
                "rx_rate",
            )
            wan["tx_speed_raw"] = _first_stat_int(
                stats,
                "txspeed",
                "tx-speed",
                "tx_rate",
            )
            wan["stats_interface"] = stats.get("interface_name") or wan_id
            wan["stats_timestamp"] = stats.get("timestamp")
            wan["_sample_ts"] = now_ts
 
            # --- Authoritative ping-check override ---
            # When the router itself reports a ping-check result for
            # this WAN, trust it over the heuristic. Three cases:
            #   passing=True  -> internet_access=True (ping check ok)
            #   passing=False -> internet_access=False (real outage,
            #                    the case the feature request is about)
            #   passing=None  -> no real profile attached / mixed state
            #                    -> keep the heuristic value from api.py
            pc = ping_check_status.get(wan_id)
            if pc is not None:
                wan["ping_check"] = pc
                passing = pc.get("passing")
                if passing is True or passing is False:
                    wan["internet_access"] = passing
                    wan["internet_access_source"] = "ping_check"
                else:
                    wan["internet_access_source"] = "heuristic"
            else:
                wan["ping_check"] = None
                wan["internet_access_source"] = "heuristic"
 
            prev = prev_wan_by_id.get(wan_id)
            if prev and prev.get("_sample_ts"):
                dt = now_ts - float(prev.get("_sample_ts") or 0)
                wan["rx_throughput"] = _counter_rate_bytes_per_second(
                    rx_bytes,
                    prev.get("rx_bytes"),
                    dt,
                )
                wan["tx_throughput"] = _counter_rate_bytes_per_second(
                    tx_bytes,
                    prev.get("tx_bytes"),
                    dt,
                )
            else:
                wan["rx_throughput"] = 0.0
                wan["tx_throughput"] = 0.0

        # ---------- Crypto map (site-to-site IPsec) enrichment ----------
        # Same delta pattern as the WAN block above. Counters reset to
        # zero whenever a phase2 SA rekeys or the tunnel bounces — the
        # negative-delta clamp keeps throughput sensors from spiking
        # to absurd negative values on those events.
        prev_cmap_by_name: dict[str, dict[str, Any]] = {}
        if self.data:
            for pname, pentry in (self.data.get("crypto_maps") or {}).items():
                if isinstance(pentry, dict):
                    prev_cmap_by_name[pname] = pentry

        for cmap_name, cmap in crypto_maps.items():
            cmap["_sample_ts"] = now_ts

            prev_cmap = prev_cmap_by_name.get(cmap_name)
            if prev_cmap and prev_cmap.get("_sample_ts"):
                dt = now_ts - float(prev_cmap.get("_sample_ts") or 0)
                cmap["rx_throughput"] = _counter_rate_bytes_per_second(
                    cmap["rx_bytes"],
                    prev_cmap.get("rx_bytes"),
                    dt,
                )
                cmap["tx_throughput"] = _counter_rate_bytes_per_second(
                    cmap["tx_bytes"],
                    prev_cmap.get("tx_bytes"),
                    dt,
                )
            else:
                cmap["rx_throughput"] = 0.0
                cmap["tx_throughput"] = 0.0

        # Role labels: the interface with ``defaultgw: true`` is the
        # Default connection. The rest are Backup connection 1..N
        # ordered by priority descending (higher Keenetic priority =
        # next in line for failover).
        default_idx: int | None = None
        for i, wan in enumerate(wan_interfaces):
            if wan.get("defaultgw"):
                default_idx = i
                break
 
        def _prio_key(w: dict[str, Any]) -> int:
            p = w.get("priority")
            return -int(p) if isinstance(p, (int, float)) else 0
 
        if default_idx is not None:
            default = wan_interfaces[default_idx]
            backups = [
                w for i, w in enumerate(wan_interfaces) if i != default_idx
            ]
            backups.sort(key=_prio_key)
            ordered = [default] + backups
        else:
            ordered = sorted(wan_interfaces, key=_prio_key)
 
        for position, wan in enumerate(ordered):
            if position == 0 and (wan.get("defaultgw") or default_idx is None):
                wan["role_label"] = "Default connection"
                wan["role_index"] = 0
            else:
                idx = position if default_idx is None else position
                wan["role_label"] = f"Backup connection {idx}"
                wan["role_index"] = idx
        wan_interfaces = ordered
 
        # ---------- New-client detection (unchanged) ----------
        previous_clients = self.data.get("clients", []) if self.data else []
        previous_macs = {
            str(c.get("mac") or "").lower()
            for c in previous_clients
            if c.get("mac")
        }
        current_macs = {
            str(c.get("mac") or "").lower() for c in clients if c.get("mac")
        }
        new_macs = current_macs - previous_macs
 
        self._refresh_count += 1
        return {
            "system": merged_system,
            "traffic_stats": traffic_stats,
            "interfaces": interfaces,
            "wifi": wifi,
            "wireguard": wireguard,
            "vpn_tunnels": vpn_tunnels,
            "clients": clients,
            "wan_status": wan_status,
            "wan_interfaces": wan_interfaces,
            "mesh_nodes": mesh_nodes,
            "interface_stats": interface_stats,
            "client_stats": client_stats,
            "ndns": ndns_info,
            "host_policies": host_policies,
            "port_info": port_info,
            "crypto_maps": crypto_maps,
            "dns_proxy": dns_proxy,
            "new_clients": new_macs,
        }


class KeeneticPingCoordinator(DataUpdateCoordinator[dict[str, bool]]):
    """Coordinator for ICMP ping-based presence detection of tracked clients.
    
    Uses real ICMP ping (like Home Assistant's Ping integration) instead of
    router API to detect if a device is actually connected to the network.
    """

    # Ping configuration
    PING_COUNT = 1          # One packet per cycle — sufficient for fast loops.
    PING_TIMEOUT = 1        # Seconds per ping.
    PING_PRIVILEGED = False # Unprivileged mode — no root required.

    @staticmethod
    def _is_valid_ip(ip: Any) -> bool:
        """Is this a usable IPv4 address for presence pinging?

        A client that has left the network often shows up in the router
        API with an IP of "0.0.0.0", an empty string, or None. Pinging
        any of these is meaningless at best and produces *false positive*
        "alive" results at worst (0.0.0.0 can resolve to the local host
        on some Linux kernels, which icmplib then reports as reachable —
        flipping the device tracker back to "home" even though the phone
        is actually miles away). Centralising the check in one place
        prevents stale addresses from leaking into the ping loop.
        """
        if ip is None:
            return False
        s = str(ip).strip()
        if not s:
            return False
        if s in ("0.0.0.0", "::", "::0", "0:0:0:0:0:0:0:0"):
            return False
        # Reject obviously malformed values without pulling in ipaddress
        # on the hot path — we only need to catch the common router
        # placeholders.
        if s.startswith("0."):
            return False
        return True

    def __init__(
        self,
        hass: HomeAssistant,
        client: KeeneticClient,
        tracked_clients: list[dict[str, str]],
        interval: int | None = None,
    ) -> None:
        """Initialize the ping coordinator.
        
        Args:
            hass: Home Assistant instance
            client: Keenetic API client (used for IP updates from router)
            tracked_clients: List of dicts with 'mac', 'ip', 'name' keys
            interval: Ping refresh interval in seconds. Defaults to
                DEFAULT_PING_INTERVAL when not provided. Can be reconfigured
                from the integration's options flow.
        """
        if interval is None or interval <= 0:
            interval = DEFAULT_PING_INTERVAL
        super().__init__(
            hass,
            _LOGGER,
            name="keenetic_router_pro_ping",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self._tracked_clients = tracked_clients
        self._privileged: bool | None = None  # Will be determined on first ping

        # MAC → IP mapping, updated by the main coordinator on each tick.
        self._mac_to_ip: dict[str, str] = {}
        for c in tracked_clients:
            # Defensive: handle both dict and plain string (MAC) formats
            if isinstance(c, dict):
                mac = str(c.get("mac") or "").lower()
                ip = str(c.get("ip") or "")
            else:
                mac = str(c).lower()
                ip = ""
            if mac and self._is_valid_ip(ip):
                self._mac_to_ip[mac] = ip

    def update_client_ip(self, mac: str, ip: str) -> None:
        """Update IP address for a specific client (dynamic IP support).

        Invalid/placeholder addresses like "0.0.0.0" or empty strings
        are rejected *and* remove any previously-cached IP for this
        MAC. Rationale: when a client leaves the network the router
        first reports the stale last-known IP for ~30s and then resets
        it to 0.0.0.0. Without this cleanup, the ping loop would either
        keep pinging the stale address (false negative) or ping
        0.0.0.0 which some kernels happily answer (false positive —
        the device tracker flips back to "home" from kilometres away).
        Dropping the MAC from the ping map causes `_async_update_data`
        to report False for this client until a new valid lease shows
        up, which matches the user's expectation.
        """
        mac_lower = mac.lower()
        if self._is_valid_ip(ip):
            self._mac_to_ip[mac_lower] = str(ip).strip()
        else:
            # Remove stale entry so the ping loop stops reporting on it.
            self._mac_to_ip.pop(mac_lower, None)

    def get_tracked_macs(self) -> set[str]:
        """Return set of tracked MAC addresses."""
        result = set()
        for c in self._tracked_clients:
            if isinstance(c, dict):
                mac = str(c.get("mac") or "").lower()
            else:
                mac = str(c).lower()
            if mac:
                result.add(mac)
        return result

    def get_client_info(self, mac: str) -> dict[str, str] | None:
        """Get client info by MAC address."""
        mac_lower = mac.lower()
        for c in self._tracked_clients:
            if isinstance(c, dict):
                if str(c.get("mac") or "").lower() == mac_lower:
                    return c
            else:
                if str(c).lower() == mac_lower:
                    return {"mac": str(c).lower(), "ip": "", "name": ""}
        return None

    async def _async_ping_host(self, ip: str) -> bool:
        """Ping a single host using ICMP.
        
        Returns True if host is alive, False otherwise.
        """
        # Belt-and-braces: even if a caller somehow slips a placeholder
        # address past the map-level validation, refuse to actually
        # send ICMP to it. icmplib will happily "ping" 0.0.0.0 on some
        # kernels and report the host as alive, which is exactly the
        # false-positive that made device_tracker flip back to "home"
        # after the router cleared the client's lease.
        if not self._is_valid_ip(ip):
            _LOGGER.debug("Refusing to ping invalid address %r", ip)
            return False

        if not ICMPLIB_AVAILABLE:
            _LOGGER.warning("icmplib not available, cannot ping %s", ip)
            return False

        try:
            # İlk denemede privileged mode'u belirle
            if self._privileged is None:
                self._privileged = self.PING_PRIVILEGED

            # Gerçek ICMP ping gönder
            result = await async_ping(
                ip,
                count=self.PING_COUNT,
                timeout=self.PING_TIMEOUT,
                privileged=self._privileged,
            )

            is_alive = result.is_alive
            _LOGGER.debug(
                "Ping %s: alive=%s, packets_sent=%d, packets_received=%d, avg_rtt=%.2fms",
                ip,
                is_alive,
                result.packets_sent,
                result.packets_received,
                result.avg_rtt if result.avg_rtt else 0,
            )
            return is_alive

        except SocketPermissionError:
            # Unprivileged mode çalışmadıysa privileged dene
            if not self._privileged:
                _LOGGER.info(
                    "Unprivileged ICMP ping failed, trying privileged mode for %s", ip
                )
                self._privileged = True
                return await self._async_ping_host(ip)
            _LOGGER.error(
                "ICMP ping requires root privileges. "
                "Run Home Assistant as root or enable unprivileged ping: "
                "sudo sysctl -w net.ipv4.ping_group_range='0 2147483647'"
            )
            return False

        except asyncio.CancelledError:
            # CancelledError must be re-raised so HA can shut down the
            # ping loop cleanly when the coordinator is being torn down.
            _LOGGER.debug("Ping to %s was cancelled", ip)
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Ping to %s failed: %s", ip, err)
            return False

    async def _async_update_data(self) -> dict[str, bool]:
        """Ping all tracked clients using ICMP and return their status.
        
        Returns:
            Dict mapping MAC addresses to their connected status (True/False)
        """
        # Every tracked MAC must appear in the result, even if we have
        # no valid IP for it right now — otherwise the device_tracker
        # entity keeps showing its last (stale) state forever. MACs
        # without a usable address are explicitly reported as False so
        # the tracker correctly flips to "not_home".
        tracked_macs = self.get_tracked_macs()
        mac_status: dict[str, bool] = {mac: False for mac in tracked_macs}

        if not self._mac_to_ip:
            _LOGGER.debug("No IP addresses to ping")
            return mac_status

        if not ICMPLIB_AVAILABLE:
            _LOGGER.error("icmplib not installed, ping tracking disabled")
            return mac_status

        # Tüm ping'leri paralel olarak çalıştır
        tasks = []
        macs = []

        for mac, ip in self._mac_to_ip.items():
            if not self._is_valid_ip(ip):
                # Paranoid: validation happens on write, but double-check
                # on read in case something mutated the map out-of-band.
                continue
            tasks.append(self._async_ping_host(ip))
            macs.append(mac)

        # Tüm ping'leri bekle
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Sonuçları MAC adresine göre eşle
        for mac, result in zip(macs, results):
            if isinstance(result, BaseException):
                _LOGGER.debug("Ping exception for %s: %s", mac, result)
                mac_status[mac] = False
            else:
                mac_status[mac] = bool(result)

        _LOGGER.debug("ICMP Ping results: %s", mac_status)

        return mac_status
