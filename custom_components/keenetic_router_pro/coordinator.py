"""DataUpdateCoordinator for Keenetic Router Pro."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KeeneticAuthError, KeeneticClient
from .const import DOMAIN, FAST_SCAN_INTERVAL
from .utils import coerce_int, first_present, is_client_online, normalize_mac, usable_ip

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


def _first_stat_int(stats: dict[str, Any], *keys: str) -> int:
    """Return the first non-empty integer stat from several firmware key names."""
    return coerce_int(first_present(stats, *keys, default=0))


def _mesh_associations(mesh_nodes: Any) -> dict[str, Any]:
    """Return total and per-node mesh client association counts."""
    by_node: dict[str, int] = {}
    total = 0
    for node in mesh_nodes or []:
        if not isinstance(node, dict):
            continue
        node_id = node.get("cid") or node.get("id")
        if not node_id:
            continue
        count = coerce_int(node.get("associations"), 0)
        by_node[str(node_id)] = count
        total += count
    return {"total": total, "by_node": by_node}


def _dict_or_empty(value: Any) -> dict[str, Any]:
    """Return a dict payload, or an empty dict for malformed endpoint data."""
    return value if isinstance(value, dict) else {}


def _list_or_empty(value: Any) -> list[Any]:
    """Return a list payload, or an empty list for malformed endpoint data."""
    return value if isinstance(value, list) else []


def _counter_rate_bytes_per_second(
    current: int,
    previous: Any,
    elapsed_seconds: float,
) -> float:
    """Calculate a monotonic byte-counter rate, clamping resets to zero."""
    if elapsed_seconds <= 0:
        return 0.0
    delta = current - coerce_int(previous)
    if delta < 0:
        return 0.0
    return max(0.0, delta / elapsed_seconds)


def _merge_clients_with_neighbours(
    clients: list[dict[str, Any]],
    neighbours: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach IP-neighbour discovery data to hotspot client records."""
    neighbours_by_mac = {
        normalize_mac(neighbour.get("mac")): neighbour
        for neighbour in neighbours
        if isinstance(neighbour, dict) and neighbour.get("mac")
    }
    merged: list[dict[str, Any]] = []
    seen_macs: set[str] = set()

    for client in clients:
        if not isinstance(client, dict):
            continue
        mac = normalize_mac(client.get("mac"))
        if not mac:
            merged.append(client)
            continue
        seen_macs.add(mac)
        neighbour = neighbours_by_mac.get(mac)
        if not neighbour:
            merged.append(client)
            continue

        item = dict(client)
        item["neighbour"] = neighbour
        if (
            not is_client_online(item)
            and neighbour.get("last-seen") not in (None, "")
        ):
            item["last-seen"] = neighbour.get("last-seen")
            item["last-seen-source"] = "neighbour"
        elif item.get("last-seen") in (None, "", 0, "0"):
            item["last-seen"] = neighbour.get("last-seen")
            item.setdefault("last-seen-source", "neighbour")
        else:
            item.setdefault("last-seen-source", "hotspot")
        if item.get("first-seen") in (None, ""):
            item["first-seen"] = neighbour.get("first-seen")
            item.setdefault("first-seen-source", "neighbour")
        else:
            item.setdefault("first-seen-source", "hotspot")
        if usable_ip(item.get("ip")) is None and neighbour.get("address-family") == "ipv4":
            item["ip"] = neighbour.get("address")
        item["neighbour-expired"] = neighbour.get("expired")
        item["neighbour-wireless"] = neighbour.get("wireless")
        item["neighbour-leasetime"] = neighbour.get("leasetime")
        merged.append(item)

    for mac, neighbour in neighbours_by_mac.items():
        if mac in seen_macs:
            continue
        merged.append(
            {
                "mac": mac,
                "via": neighbour.get("via"),
                "ip": neighbour.get("address")
                if neighbour.get("address-family") == "ipv4"
                else None,
                "active": False,
                "last-seen": neighbour.get("last-seen"),
                "last-seen-source": "neighbour",
                "first-seen": neighbour.get("first-seen"),
                "first-seen-source": "neighbour",
                "neighbour": neighbour,
                "neighbour-expired": neighbour.get("expired"),
                "neighbour-wireless": neighbour.get("wireless"),
                "neighbour-leasetime": neighbour.get("leasetime"),
            }
        )

    return merged


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
        # Mesh nodes intentionally moved to stage 1.5 below so we can
        # share the already-fetched ``clients`` list with the mesh
        # fallback path (avoids a duplicate ``show/ip/hotspot`` round-trip
        # on routers that have extenders).
        (
            system,
            version,
            version_available,
            interfaces,
            clients,
            ip_neighbours,
            host_policies,
            ndns_info,
            ping_check_status,
            crypto_maps,
            dns_proxy,
            ipsec_diagnostics,
        ) = await asyncio.gather(
            _bounded(self.client.async_get_system_info()),
            _bounded(self.client.async_get_current_version_info()) if slow_refresh else _resolve(_cached_version),
            _bounded(self.client.async_get_available_version_info()) if very_slow_refresh else _resolve(_cached_version_available),
            _bounded(self.client.async_get_interfaces()),
            _bounded(self.client.async_get_clients()),
            _bounded(self.client.async_get_ip_neighbours()),
            _bounded(self.client.async_get_host_policies()) if slow_refresh else _resolve(_prev.get("host_policies", {})),
            _bounded(self.client.async_get_ndns_info()) if very_slow_refresh else _resolve(_prev.get("ndns", {})),
            _bounded(self.client.async_get_ping_check_status()),
            _bounded(self.client.async_get_crypto_maps()) if very_slow_refresh else _resolve(_prev.get("crypto_maps", {})),
            _bounded(self.client.async_get_dns_proxy_status()) if very_slow_refresh else _resolve(_prev.get("dns_proxy", {})),
            _bounded(self.client.async_get_ipsec_diagnostics()) if very_slow_refresh else _resolve(_prev.get("ipsec_diagnostics", {})),
            return_exceptions=True,
        )

        # Stage 1.5: mesh nodes — needs ``clients`` already fetched so
        # the fallback path can reuse it instead of re-fetching.
        if slow_refresh:
            _resolved_clients = clients if isinstance(clients, list) else []
            try:
                mesh_nodes = await _bounded(
                    self.client.async_get_mesh_nodes(clients=_resolved_clients)
                )
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                mesh_nodes = err
        else:
            mesh_nodes = _prev.get("mesh_nodes", [])
 
        system = _ok("system_info", system, {})
        version = _ok("current_version", version, {})
        version_available = _ok("available_version", version_available, {})
        interfaces = _ok("interfaces", interfaces, [])
        clients_stale = False
        if isinstance(clients, BaseException):
            previous_clients = _prev.get("clients", [])
            if previous_clients:
                failed_fetches.append(("clients", clients))
                _LOGGER.debug(
                    "Coordinator fetch clients failed; preserving previous client snapshot: %s",
                    clients,
                )
                clients = previous_clients
                clients_stale = True
            else:
                clients = _ok("clients", clients, [])
        else:
            clients = _ok("clients", clients, [])
        ip_neighbours = _ok("ip_neighbours", ip_neighbours, [], silent=True)
        clients = _merge_clients_with_neighbours(clients, ip_neighbours)
        mesh_nodes = _ok("mesh_nodes", mesh_nodes, [])
        host_policies = _dict_or_empty(_ok("host_policies", host_policies, {}))
        ndns_info = _dict_or_empty(_ok("ndns_info", ndns_info, {}))
        ping_check_status = _dict_or_empty(
            _ok("ping_check_status", ping_check_status, {})
        )
        # Crypto maps: not every router/firmware has the IPsec component,
        # so this endpoint may be unavailable. Mark the fetch as silent
        # so an absent endpoint doesn't produce a warning on every tick —
        # the api layer already debug-logs the reason.
        crypto_maps = {
            name: dict(cmap)
            for name, cmap in _dict_or_empty(
                _ok("crypto_maps", crypto_maps, {}, silent=True)
            ).items()
            if isinstance(cmap, dict)
        }
        # DNS proxy is diagnostic-only and intentionally slow-cadence;
        # routers without the endpoint should not warn every refresh.
        dns_proxy = _dict_or_empty(
            _ok("dns_proxy", dns_proxy, {}, silent=True)
        )
        # IPsec diagnostics read recent router log lines on the same
        # very-slow cadence as DNS diagnostics. Missing log access is
        # non-critical and should not affect normal polling.
        ipsec_diagnostics = _dict_or_empty(
            _ok("ipsec_diagnostics", ipsec_diagnostics, {}, silent=True)
        )

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
        # Normalize once and share the result so each stage-2 call skips
        # a redundant O(N) walk over the same payload.
        iface_list = self.client._normalize_interfaces(interfaces)
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
            _bounded(self.client.async_get_wifi_networks(interfaces=interfaces, iface_list=iface_list)),
            _bounded(self.client.async_get_wireguard_status(interfaces=interfaces, iface_list=iface_list)),
            _bounded(self.client.async_get_vpn_tunnels(interfaces=interfaces, iface_list=iface_list)),
            _bounded(self.client.async_get_wan_status(interfaces=interfaces, iface_list=iface_list)),
            _bounded(self.client.async_get_wan_interfaces(interfaces=interfaces, iface_list=iface_list)),
            _bounded(self.client.async_get_traffic_stats(interfaces=interfaces, iface_list=iface_list)),
            _bounded(self.client.async_get_port_info(interfaces=interfaces)),
            _bounded(self.client.async_get_all_interface_stats(interfaces=interfaces, iface_list=iface_list)),
            return_exceptions=True,
        )
 
        wifi = _ok("wifi", wifi, [])
        wireguard = _ok("wireguard", wireguard, [])
        vpn_tunnels = _ok("vpn_tunnels", vpn_tunnels, [])
        wan_status = _dict_or_empty(_ok("wan_status", wan_status, {}))
        wan_interfaces = _ok("wan_interfaces", wan_interfaces, [])
        traffic_stats = _dict_or_empty(_ok("traffic_stats", traffic_stats, {}))
        port_info = _list_or_empty(_ok("port_info", port_info, []))
        interface_stats = _dict_or_empty(_ok("interface_stats", interface_stats, {}))

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
                    prev_cmap_by_name[pname] = dict(pentry)

        for cmap_name, cmap in crypto_maps.items():
            prev_cmap = prev_cmap_by_name.get(cmap_name)
            if not very_slow_refresh and prev_cmap:
                cmap["_sample_ts"] = prev_cmap.get("_sample_ts")
                cmap["rx_throughput"] = prev_cmap.get("rx_throughput", 0.0)
                cmap["tx_throughput"] = prev_cmap.get("tx_throughput", 0.0)
                continue

            cmap["_sample_ts"] = now_ts
            if prev_cmap and prev_cmap.get("_sample_ts"):
                dt = now_ts - float(prev_cmap.get("_sample_ts") or 0)
                cmap["rx_throughput"] = _counter_rate_bytes_per_second(
                    coerce_int(cmap.get("rx_bytes")),
                    prev_cmap.get("rx_bytes"),
                    dt,
                )
                cmap["tx_throughput"] = _counter_rate_bytes_per_second(
                    coerce_int(cmap.get("tx_bytes")),
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
            return -coerce_int(p)
 
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
                wan["role_label"] = f"Backup connection {position}"
                wan["role_index"] = position
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
            "clients_stale": clients_stale,
            "clients_by_mac": {
                normalize_mac(c.get("mac")): c
                for c in clients
                if isinstance(c, dict) and c.get("mac")
            },
            "wan_status": wan_status,
            "wan_interfaces": wan_interfaces,
            "wan_by_id": {
                w.get("id"): w
                for w in wan_interfaces
                if isinstance(w, dict) and w.get("id")
            },
            "mesh_nodes": mesh_nodes,
            "mesh_associations": _mesh_associations(mesh_nodes),
            "mesh_nodes_by_cid": {
                (n.get("cid") or n.get("id")): n
                for n in (mesh_nodes if isinstance(mesh_nodes, list) else [])
                if isinstance(n, dict) and (n.get("cid") or n.get("id"))
            },
            "interface_stats": interface_stats,
            "client_stats": client_stats,
            "ndns": ndns_info,
            "host_policies": host_policies,
            "port_info": port_info,
            "crypto_maps": crypto_maps,
            "dns_proxy": dns_proxy,
            "ipsec_diagnostics": ipsec_diagnostics,
            "new_clients": new_macs,
        }
