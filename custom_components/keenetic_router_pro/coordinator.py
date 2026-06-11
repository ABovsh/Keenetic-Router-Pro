"""DataUpdateCoordinator for Keenetic Router Pro."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KeeneticClient
from .const import DOMAIN, FAST_SCAN_INTERVAL
from .coordinator_parts.derived import (
    build_clients_by_mac,
    counter_rate_bytes_per_second,
    mesh_associations,
    order_wan_interfaces,
    real_client_macs,
)
from .coordinator_parts.fetching import (
    FetchFailure,
    critical_failures_to_exception,
    ok_or_default,
)
from .coordinator_parts.oom import advance_oom_state
from .coordinator_parts.payloads import (
    dict_or_empty,
    list_or_empty,
    merge_clients_with_neighbours,
)
from .coordinator_parts.refresh import build_batch_tree, refresh_plan
from .utils import coerce_int, first_present, normalize_mac


_OOM_STORE_VERSION = 1
_advance_oom_state = advance_oom_state
_dict_or_empty = dict_or_empty
_list_or_empty = list_or_empty
_counter_rate_bytes_per_second = counter_rate_bytes_per_second
_merge_clients_with_neighbours = merge_clients_with_neighbours
_mesh_associations = mesh_associations

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


def _first_stat_int(stats: dict[str, Any], *keys: str) -> int | None:
    """Return the first usable integer stat, or None for absent/garbage values.

    Booleans are rejected (``int(False) == 0`` would look like a counter
    reset and fabricate a throughput spike on the next real sample).
    """
    value = first_present(stats, *keys, default=None)
    if value is None or isinstance(value, bool):
        return None
    return coerce_int(value)


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
        # Per-config-entry persistent OOM tracker. The Store key is
        # derived from the API host so two routers on the same HA
        # instance keep their counters independent. Schema:
        #   {"last_seen_iso": "2026-05-27T17:33:48", "last_seen_count": 1, "total": 42}
        host = getattr(client, "host", None) or "unknown"
        self._oom_store: Store = Store(
            hass,
            _OOM_STORE_VERSION,
            f"{DOMAIN}_vici_oom_{host}.json",
        )
        self._oom_state: dict[str, Any] = {
            "last_seen_iso": None,
            "last_seen_count": 0,
            "total": 0,
        }
        self._oom_state_loaded = False

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all router data, serializing access to client tick caches."""
        update_lock = getattr(self, "_update_lock", None)
        if update_lock is None:
            update_lock = asyncio.Lock()
            self._update_lock = update_lock
        async with update_lock:
            return await self._async_update_data_unlocked()

    async def _async_update_data_unlocked(self) -> dict[str, Any]:
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
        # Reset any tick cache from a prior refresh; stage 0 below will
        # repopulate it from a fresh composite POST.
        self.client.clear_tick_cache()

        async def _bounded(coro):
            async with sem:
                return await coro

        _prev = self.data or {}
        _prev_sys = _prev.get("system") or {}

        async def _resolve(value: Any) -> Any:
            return value

        # Collected per-tick so we can emit a single warning instead of
        # silently defaulting every failing fetch at debug level.
        failed_fetches: list[FetchFailure] = []

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
            result = ok_or_default(
                name,
                value,
                default,
                failed_fetches,
                silent=silent,
            )
            if isinstance(value, BaseException) and not isinstance(
                value, asyncio.CancelledError
            ):
                _LOGGER.debug("Coordinator fetch %s failed: %s", name, value)
            return result
 
        plan = refresh_plan(
            first_refresh=self.data is None,
            refresh_count=self._refresh_count,
        )
        first_refresh = plan.first_refresh
        medium_refresh = plan.medium_refresh
        slow_refresh = plan.slow_refresh
        very_slow_refresh = plan.very_slow_refresh
        # Site-to-site IPsec state now comes from ``show/ipsec`` (stroke
        # path), which is OOM-safe — verified by burst-testing 90+
        # rapid calls produce zero ``IpSec::Vici::Stats: out of memory``
        # events. Polled on the same ``slow_refresh`` cadence as WAN
        # traffic stats so throughput graphs have matching resolution.
        ipsec_status_refresh = plan.ipsec_status_refresh

        # Precompute the cached fallbacks for skipped slow-tick fetches
        # outside the gather() call so the fast tick doesn't rebuild
        # these dicts every time. On a non-slow tick the previous
        # version info is reused verbatim; on a slow tick these are
        # discarded and the live RCI fetch result is used instead.
        if not very_slow_refresh:
            _cached_version = {
                k: _prev_sys.get(k) for k in _VERSION_CACHE_KEYS if k in _prev_sys
            }
            _cached_version_available = {
                "title": _prev_sys.get("release-available"),
                "sandbox": _prev_sys.get("fw-update-sandbox"),
                "update-available": _prev_sys.get("fw-update-available", False),
            }

        # ---------- Stage 0: prefetch composite RCI tree ----------
        # One POST /rci/ replaces up to a dozen GETs on routers whose
        # firmware supports the composite endpoint (confirmed on KN-1811
        # 5.00.C.x). On routers that don't support it, ``prefetch_tick``
        # returns False and stage-1 falls back to per-call GETs as before.
        # The cache is read by ``_rci_get`` (params-less only) and cleared
        # in the ``finally`` block below.
        try:
            if self.client._rci_batch_supported is not False:
                batch_tree = build_batch_tree(plan)
                try:
                    await self.client.prefetch_tick(batch_tree)
                except asyncio.CancelledError:
                    raise
                except Exception as err:  # noqa: BLE001 — best-effort prefetch
                    _LOGGER.debug("RCI prefetch failed (continuing per-call): %s", err)
                    self.client.clear_tick_cache()

            # ---------- Stage 1: independent fetches ----------
            # Mesh nodes intentionally moved to stage 1.5 below so we can
            # share the already-fetched ``clients`` list with the mesh
            # fallback path (avoids a duplicate ``show/ip/hotspot`` round-trip
            # on routers that have extenders).
            # Ping check status is a configuration-shaped payload (which WAN
            # has a ping profile attached) that changes only when the user
            # edits the profile on the router. Moving it to the slow tier
            # removes one round-trip per fast tick. The override of
            # ``internet_access`` further below still applies — it just uses
            # the cached snapshot until the next slow tick refreshes it.
            (
                system,
                version,
                version_available,
                interfaces,
                clients,
                ip_neighbours,
                host_policies,
                policies,
                ndns_info,
                ping_check_status,
                crypto_maps,
                dns_proxy,
                ipsec_diagnostics,
            ) = await asyncio.gather(
                _bounded(self.client.async_get_system_info()),
                _bounded(self.client.async_get_current_version_info()) if very_slow_refresh else _resolve(_cached_version),
                _bounded(self.client.async_get_available_version_info()) if very_slow_refresh else _resolve(_cached_version_available),
                _bounded(self.client.async_get_interfaces()),
                _bounded(self.client.async_get_clients()),
                _bounded(self.client.async_get_ip_neighbours()),
                _bounded(self.client.async_get_host_policies()) if slow_refresh else _resolve(_prev.get("host_policies", {})),
                _bounded(self.client.async_get_policies()) if very_slow_refresh else _resolve(_prev.get("policies", {})),
                _bounded(self.client.async_get_ndns_info()) if very_slow_refresh else _resolve(_prev.get("ndns", {})),
                _bounded(self.client.async_get_ping_check_status()) if medium_refresh else _resolve(_prev.get("ping_check_status", {})),
                _bounded(self.client.async_get_ipsec_status()) if ipsec_status_refresh else _resolve(_prev.get("crypto_maps", {})),
                _bounded(self.client.async_get_dns_proxy_status()) if very_slow_refresh else _resolve(_prev.get("dns_proxy", {})),
                _bounded(self.client.async_get_ipsec_diagnostics()) if very_slow_refresh else _resolve(_prev.get("ipsec_diagnostics", {})),
                return_exceptions=True,
            )

            clients_stale = False
            if isinstance(clients, asyncio.CancelledError):
                raise clients
            if isinstance(clients, BaseException):
                previous_clients = _prev.get("clients", [])
                if previous_clients:
                    failed_fetches.append(FetchFailure("clients", clients))
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

            # Stage 1.5: mesh nodes — needs ``clients`` already fetched so
            # the fallback path can reuse it instead of re-fetching.
            if slow_refresh:
                try:
                    mesh_nodes = await _bounded(
                        self.client.async_get_mesh_nodes(clients=clients)
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
            ip_neighbours = _ok("ip_neighbours", ip_neighbours, [], silent=True)
            clients = merge_clients_with_neighbours(clients, ip_neighbours)
            # On a transient mesh fetch failure keep the previous snapshot:
            # an empty default would flap every mesh entity unavailable, and
            # the API layer now raises instead of returning MAC-keyed
            # fallback nodes (which used to flip mesh unique_ids).
            mesh_nodes = _ok("mesh_nodes", mesh_nodes, _prev.get("mesh_nodes", []))
            host_policies = dict_or_empty(_ok("host_policies", host_policies, {}))
            policies = dict_or_empty(
                _ok("policies", policies, _prev.get("policies", {}))
            )
            ndns_info = dict_or_empty(_ok("ndns_info", ndns_info, {}))
            ping_check_status = dict_or_empty(
                _ok("ping_check_status", ping_check_status, {})
            )
            # Crypto maps: not every router/firmware has the IPsec component,
            # so this endpoint may be unavailable. Mark the fetch as silent
            # so an absent endpoint doesn't produce a warning on every tick —
            # the api layer already debug-logs the reason. When the slow tier
            # is skipped, keep the previous snapshot instead of rebuilding it.
            if slow_refresh:
                # On a transient fetch failure keep the previous snapshot —
                # an empty default would flap every IPsec entity unavailable
                # and wipe tunnel state for one tick.
                crypto_maps = {
                    name: dict(cmap)
                    for name, cmap in dict_or_empty(
                        _ok(
                            "crypto_maps",
                            crypto_maps,
                            _prev.get("crypto_maps", {}),
                            silent=True,
                        )
                    ).items()
                    if isinstance(cmap, dict)
                }
            else:
                crypto_maps = _prev.get("crypto_maps", {})
            # DNS proxy is diagnostic-only and intentionally slow-cadence;
            # routers without the endpoint should not warn every refresh.
            dns_proxy = dict_or_empty(
                _ok("dns_proxy", dns_proxy, {}, silent=True)
            )
            # IPsec diagnostics read recent router log lines on the same
            # very-slow cadence as DNS diagnostics. Missing log access is
            # non-critical and should not affect normal polling.
            ipsec_diagnostics = dict_or_empty(
                _ok(
                    "ipsec_diagnostics",
                    ipsec_diagnostics,
                    _prev.get("ipsec_diagnostics", {}),
                    silent=True,
                )
            )
            # Monotonic OOM counter — persists across HA restarts via Store,
            # dedups against the per-event router timestamp so each OOM is
            # counted exactly once even when polling windows overlap. The
            # `total` value powers the TOTAL_INCREASING sensor, which lets
            # HA Statistics derive per-hour / per-day rates and produce
            # bar-chart "when did problems happen" graphs.
            if not getattr(self, "_oom_state_loaded", False):
                if not hasattr(self, "_oom_state"):
                    self._oom_state = {
                        "last_seen_iso": None,
                        "last_seen_count": 0,
                        "total": 0,
                    }
                store = getattr(self, "_oom_store", None)
                stored = None
                if store is not None:
                    try:
                        stored = await store.async_load()
                    except (OSError, ValueError, TypeError) as err:
                        _LOGGER.debug("OOM Store load failed: %s — starting from 0", err)
                if isinstance(stored, dict):
                    self._oom_state = {
                        "last_seen_iso": stored.get("last_seen_iso"),
                        "last_seen_count": coerce_int(
                            stored.get("last_seen_count"), 0
                        ),
                        "total": coerce_int(stored.get("total"), 0),
                    }
                self._oom_state_loaded = True

            events = (
                ipsec_diagnostics.get("events")
                if very_slow_refresh and ipsec_diagnostics
                else None
            )
            if events:
                next_oom_state = advance_oom_state(self._oom_state, events)
                if next_oom_state != self._oom_state:
                    self._oom_state = next_oom_state
                    store = getattr(self, "_oom_store", None)
                    if store is not None:
                        try:
                            await store.async_save(self._oom_state)
                        except (OSError, TypeError) as err:
                            _LOGGER.debug("OOM Store save failed: %s", err)

            # Copy before mutating: on non-very-slow ticks this is the SAME
            # dict object as the currently-published self.data snapshot.
            ipsec_diagnostics = dict(ipsec_diagnostics)
            ipsec_diagnostics["oom_total"] = self._oom_state["total"]
            ipsec_diagnostics["oom_last_seen"] = self._oom_state.get("last_seen_iso")

            # Fail-fast on critical fetches. If the router is unreachable,
            # auth has expired, or the RCI surface is down, ``system_info``
            # and ``interfaces`` are the two calls that MUST succeed — every
            # downstream computation depends on them. Letting them default
            # to ``{}`` / ``[]`` would produce a ghost-mode tick where every
            # entity silently shows "zero/empty" instead of ``unavailable``,
            # masking real outages. Raise ``UpdateFailed`` so HA marks the
            # coordinator as failed and retries on the next tick.
            critical_failures_to_exception(failed_fetches)

            # An HTTP-200 with an empty/garbled critical payload must fail
            # the tick like an exception would — publishing it would flip
            # every WAN/Wi-Fi/port entity to empty/down while staying
            # "available" (ghost-mode update).
            if not system or not interfaces:
                raise UpdateFailed(
                    "Critical router payload empty "
                    f"(system={bool(system)}, interfaces={bool(interfaces)})"
                )

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
            # Fast ticks reuse the last published stage-2 snapshot instead of
            # fanning out extra router calls. Medium ticks refresh WAN and
            # interface stats, while the slow/very-slow tiers continue to
            # govern the larger diagnostic groups.
            iface_list = self.client._normalize_interfaces(interfaces)
            iface_fp = tuple(
                (i.get("id"), i.get("type"), i.get("link"), i.get("state"))
                for i in iface_list
            )
            if medium_refresh:
                # WAN interface set rarely changes between ticks (links flap but
                # the *set* of WAN-eligible interfaces is stable). Build a cheap
                # fingerprint of the interface payload and reuse the previously
                # derived WAN list when the fingerprint matches.
                cached_wan = _prev.get("wan_interfaces")
                cached_fp = _prev.get("_iface_fingerprint")
                if (
                    not first_refresh
                    and cached_wan is not None
                    and cached_fp == iface_fp
                ):
                    wan_interfaces = [dict(w) for w in cached_wan]
                else:
                    try:
                        wan_interfaces = await _bounded(
                            self.client.async_get_wan_interfaces(
                                interfaces=interfaces,
                                iface_list=iface_list,
                            )
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as err:  # noqa: BLE001
                        wan_interfaces = err
                (
                    wifi,
                    wireguard,
                    vpn_tunnels,
                    wan_status,
                    traffic_stats,
                    port_info,
                    interface_stats,
                ) = await asyncio.gather(
                    _bounded(
                        self.client.async_get_wifi_networks(
                            interfaces=interfaces, iface_list=iface_list
                        )
                    ),
                    _bounded(
                        self.client.async_get_wireguard_status(
                            interfaces=interfaces, iface_list=iface_list
                        )
                    ),
                    _bounded(
                        self.client.async_get_vpn_tunnels(
                            interfaces=interfaces, iface_list=iface_list
                        )
                    ),
                    _bounded(
                        self.client.async_get_wan_status(
                            interfaces=interfaces, iface_list=iface_list
                        )
                    ),
                    _bounded(
                        self.client.async_get_traffic_stats(
                            interfaces=interfaces, iface_list=iface_list
                        )
                    ),
                    _bounded(self.client.async_get_port_info(interfaces=interfaces)),
                    _bounded(
                        self.client.async_get_all_interface_stats(
                            interfaces=interfaces,
                            iface_list=iface_list,
                            wan_interfaces=wan_interfaces
                            if isinstance(wan_interfaces, list)
                            else [],
                        )
                    ),
                    return_exceptions=True,
                )
            else:
                wan_interfaces = _prev.get("wan_interfaces", [])
                wifi = _prev.get("wifi", [])
                wireguard = _prev.get("wireguard", [])
                vpn_tunnels = _prev.get("vpn_tunnels", [])
                wan_status = _prev.get("wan_status", {})
                traffic_stats = _prev.get("traffic_stats", {})
                port_info = _prev.get("port_info", [])
                interface_stats = _prev.get("interface_stats", {})

            interface_stats_failed = isinstance(interface_stats, BaseException)
            # On a stage-2 task exception keep the previous snapshot — an
            # empty default would make every existing entity of that family
            # unavailable for the tick (same policy as mesh/crypto_maps).
            wifi = _ok("wifi", wifi, _prev.get("wifi", []))
            wireguard = _ok("wireguard", wireguard, _prev.get("wireguard", []))
            vpn_tunnels = _ok("vpn_tunnels", vpn_tunnels, _prev.get("vpn_tunnels", []))
            wan_status = dict_or_empty(
                _ok("wan_status", wan_status, _prev.get("wan_status", {}))
            )
            wan_interfaces = _ok(
                "wan_interfaces", wan_interfaces, _prev.get("wan_interfaces", [])
            )
            traffic_stats = dict_or_empty(
                _ok("traffic_stats", traffic_stats, _prev.get("traffic_stats", {}))
            )
            port_info = list_or_empty(
                _ok("port_info", port_info, _prev.get("port_info", []))
            )
            interface_stats = dict_or_empty(_ok("interface_stats", interface_stats, {}))
            if interface_stats_failed:
                interface_stats = _prev.get("interface_stats", {})
                wan_interfaces = _prev.get("wan_interfaces", wan_interfaces)

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
                    ", ".join(failure.name for failure in failed_fetches),
                )

            wan_stats_fresh = medium_refresh and not interface_stats_failed

            # ---------- WAN enrichment (CPU-only, runs on already-fetched
            # data — logic unchanged from the sequential implementation) ----------
            #
            # We reuse the already-fetched ``interface_stats`` (show/interface/stat
            # for every interface) instead of firing extra RCI calls. Throughput
            # is computed as a delta against the previous coordinator tick.
            if wan_stats_fresh:
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
                        wan["rx_throughput"] = counter_rate_bytes_per_second(
                            rx_bytes,
                            prev.get("rx_bytes"),
                            dt,
                        )
                        wan["tx_throughput"] = counter_rate_bytes_per_second(
                            tx_bytes,
                            prev.get("tx_bytes"),
                            dt,
                        )
                    else:
                        wan["rx_throughput"] = 0.0
                        wan["tx_throughput"] = 0.0

                wan_interfaces = order_wan_interfaces(wan_interfaces)
                wan_by_id = {
                    w.get("id"): w
                    for w in wan_interfaces
                    if isinstance(w, dict) and w.get("id")
                }
            else:
                wan_by_id = _prev.get("wan_by_id", {})

            # ---------- Crypto map (site-to-site IPsec) enrichment ----------
            # Same delta pattern as the WAN block above. Counters reset to
            # zero whenever a phase2 SA rekeys or the tunnel bounces — the
            # negative-delta clamp keeps throughput sensors from spiking
            # to absurd negative values on those events.
            if slow_refresh:
                prev_cmap_by_name: dict[str, dict[str, Any]] = {}
                if self.data:
                    for pname, pentry in (self.data.get("crypto_maps") or {}).items():
                        if isinstance(pentry, dict):
                            prev_cmap_by_name[pname] = dict(pentry)

                now_ts = asyncio.get_running_loop().time()
                for cmap_name, cmap in crypto_maps.items():
                    prev_cmap = prev_cmap_by_name.get(cmap_name)
                    if not ipsec_status_refresh and prev_cmap:
                        cmap["_sample_ts"] = prev_cmap.get("_sample_ts")
                        cmap["rx_throughput"] = prev_cmap.get("rx_throughput", 0.0)
                        cmap["tx_throughput"] = prev_cmap.get("tx_throughput", 0.0)
                        continue

                    cmap["_sample_ts"] = now_ts
                    if prev_cmap and prev_cmap.get("_sample_ts"):
                        dt = now_ts - float(prev_cmap.get("_sample_ts") or 0)
                        # Pass raw values: counter_rate rejects None/bool
                        # samples itself; pre-coercing None to 0 would
                        # fabricate a reset + spike pair.
                        cmap["rx_throughput"] = counter_rate_bytes_per_second(
                            cmap.get("rx_bytes"),
                            prev_cmap.get("rx_bytes"),
                            dt,
                        )
                        cmap["tx_throughput"] = counter_rate_bytes_per_second(
                            cmap.get("tx_bytes"),
                            prev_cmap.get("tx_bytes"),
                            dt,
                        )
                    else:
                        cmap["rx_throughput"] = 0.0
                        cmap["tx_throughput"] = 0.0

            if slow_refresh:
                mesh_associations_data = mesh_associations(mesh_nodes)
                mesh_nodes_by_cid = {
                    (n.get("cid") or n.get("id")): n
                    for n in (mesh_nodes if isinstance(mesh_nodes, list) else [])
                    if isinstance(n, dict) and (n.get("cid") or n.get("id"))
                }
            else:
                mesh_associations_data = _prev.get("mesh_associations", {})
                mesh_nodes_by_cid = _prev.get("mesh_nodes_by_cid", {})
 
            # ---------- New-client detection ----------
            # Build the MAC->client index once and reuse it for both the
            # new-MAC diff and the output dict. Previously this walked
            # ``clients`` three times and called ``normalize_mac`` 2N+ times
            # per tick; one walk now suffices and the index is the same
            # object that ends up in the coordinator data dict.
            clients_by_mac = build_clients_by_mac(clients)
            # Neighbour-only ghosts (ARP/ND records with no hotspot client)
            # stay in the index for enrichment but never count as "new
            # devices" — a host that was merely pinged is not a connection.
            current_macs = real_client_macs(clients_by_mac)

            previous_by_mac = (
                self.data.get("clients_by_mac", {}) if self.data else {}
            )
            if isinstance(previous_by_mac, dict) and previous_by_mac:
                previous_macs = {
                    mac
                    for mac in (normalize_mac(key) for key in previous_by_mac.keys())
                    if mac
                }
            else:
                previous_macs = {
                    normalize_mac(c.get("mac"))
                    for c in (self.data.get("clients", []) if self.data else [])
                    if isinstance(c, dict) and c.get("mac")
                }
                previous_macs.discard("")
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
                "clients_by_mac": clients_by_mac,
                "wan_status": wan_status,
                "wan_interfaces": wan_interfaces,
                "wan_by_id": wan_by_id,
                "mesh_nodes": mesh_nodes,
                "mesh_associations": mesh_associations_data,
                "mesh_nodes_by_cid": mesh_nodes_by_cid,
                "interface_stats": interface_stats,
                "client_stats": client_stats,
                "ndns": ndns_info,
                "host_policies": host_policies,
                "policies": policies,
                "port_info": port_info,
                "ping_check_status": ping_check_status,
                "_iface_fingerprint": iface_fp,
                "crypto_maps": crypto_maps,
                "dns_proxy": dns_proxy,
                "ipsec_diagnostics": ipsec_diagnostics,
                "new_clients": new_macs,
            }
        finally:
            self.client.clear_tick_cache()
