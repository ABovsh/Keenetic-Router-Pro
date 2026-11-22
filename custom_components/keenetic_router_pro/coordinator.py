"""DataUpdateCoordinator for Keenetic Router Pro."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KeeneticClient
from .api.helpers import _normalize_interfaces
from .const import DOMAIN, FAST_SCAN_INTERVAL
from .utils import is_client_online
from .coordinator_parts.derived import (
    build_clients_by_mac,
    carry_firmware_available,
    counter_rate_bytes_per_second,
    mesh_associations,
    real_client_macs,
)
from .coordinator_parts.enrichment import (
    carry_wan_samples,
    enrich_crypto_maps,
    enrich_wan_interfaces,
)
from .coordinator_parts.fetching import (
    FetchFailure,
    OPTIONAL_FETCH_GRACE_ATTEMPTS,
    evaluate_critical_failures,
    next_backoff_interval,
    ok_or_default,
)
from .coordinator_parts.payloads import (
    dict_or_empty,
    list_or_empty,
    merge_clients_with_neighbours,
)
from .coordinator_parts.refresh import (
    PendingRefreshes,
    Source,
    build_batch_tree,
    refresh_plan,
    unsupported_batch_paths,
)
from .utils import normalize_mac


_dict_or_empty = dict_or_empty
_list_or_empty = list_or_empty
_counter_rate_bytes_per_second = counter_rate_bytes_per_second
_merge_clients_with_neighbours = merge_clients_with_neighbours
_mesh_associations = mesh_associations

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.coordinator")

# ``show/system`` fields behind the CPU-load and RAM sensors. Republished on the medium tier only (see _async_update_data_unlocked).
_GAUGE_KEYS = (
    "cpu_load",
    "cpuload",
    "cpu",
    "cpu-utilization",
    "memory",
    "mem",
    "memtotal",
    "memfree",
)

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
        # Set from the config entry by __init__/async_update_listener. When a
        # user wants no client data at all, ``show/ip/hotspot`` — the largest
        # payload of the tick by a wide margin — is not worth fetching.
        self.needs_client_data = True
        # Policy assignments and labels exist only for the policy selectors,
        # one per tracked client. Setup clears this for an entry without any.
        self.needs_policies = True
        # One-shot request to fetch host policies on the next tick even if
        # the slow tier is skipped. Set by write-entities (policy select)
        # right after a router-side change so the UI doesn't wait up to a
        # full slow-tier interval (~3 min) to confirm the new value.
        self._host_policies_refresh_pending = False
        # Bumped on every request; the tick snapshots it at start and only
        # clears the pending flag if no NEWER request arrived while the
        # fetch was in flight (two rapid policy changes within one tick).
        self._host_policies_refresh_token = 0
        # One-shot request to run EVERY fetch tier on the next tick, armed by
        # ``async_request_refresh`` — the confirming refresh each write entity
        # fires after a router command. The medium/slow/very-slow tiers carry
        # their previous snapshot forward on a fast tick, so without this the
        # switch the user just flipped reads its old value back out of the
        # cache and snaps back in the UI until the next tier tick (up to ~3
        # min). Uses the same pending/token pair as the host-policies request.
        self._full_refresh_pending = False
        self._full_refresh_token = 0
        # Post-install firmware checks, armed by ``request_update_check``.
        self._update_check_pending = False
        self._update_check_token = 0
        # A failed check defers its generation to the scheduled very-slow
        # tier. A newer explicit request can still run immediately.
        self._update_check_retry_token: int | None = None
        # One-shot mesh read (membership and node firmware) for a mesh
        # firmware install polling its progress. Same pending/token pair.
        self._mesh_refresh_pending = False
        self._mesh_refresh_token = 0
        # Consecutive transient critical-fetch failures tolerated so far. A
        # one-off ``system_info`` / ``interfaces`` timeout keeps the last-known
        # snapshot instead of flipping every entity unavailable; reset on the
        # first clean tick. See ``evaluate_critical_failures``.
        self._critical_fail_streak = 0
        # Number of consecutive UpdateFailed ticks since the grace window
        # (CRITICAL_FETCH_GRACE_TICKS) was exhausted — drives the adaptive
        # poll backoff during a sustained outage. Reset to 0 on any tick that
        # doesn't raise UpdateFailed. See ``next_backoff_interval``.
        self._critical_fail_backoff_count = 0
        # Optional fetch families can fail while the coordinator itself stays
        # healthy. Keep a short grace window, then mark only that source stale.
        self._source_failure_streaks: dict[str, int] = {}

    def _source_is_fresh(self, name: str, *, attempted: bool, failed: bool) -> bool:
        """Track freshness for a tiered optional source by attempted refreshes."""
        key = f"{name}_fresh"
        if not attempted:
            return bool((self.data or {}).get(key, True))
        streaks = getattr(self, "_source_failure_streaks", None)
        if streaks is None:
            streaks = self._source_failure_streaks = {}
        if not failed:
            streaks[name] = 0
            return True
        streak = streaks.get(name, 0) + 1
        streaks[name] = streak
        return self.data is not None and streak <= OPTIONAL_FETCH_GRACE_ATTEMPTS

    def request_host_policies_refresh(self) -> None:
        """Force a live host_policies fetch on the next refresh tick."""
        self._host_policies_refresh_pending = True
        self._host_policies_refresh_token += 1

    def request_update_check(self) -> None:
        """Force one firmware update check on the next tick.

        Only the update entities call this, right after installing firmware —
        the generic write-confirming refresh must NOT, or a mesh-node install
        (which polls for a refresh every ~10s) would re-run the stateful
        ``components/check-update`` endpoint dozens of times per install.
        """
        self._update_check_pending = True
        self._update_check_token += 1

    def request_mesh_refresh(self) -> None:
        """Read mesh membership and node firmware on the next tick."""
        self._mesh_refresh_pending = True
        self._mesh_refresh_token += 1

    def request_full_refresh(self) -> None:
        """Force every fetch tier to run on the next refresh tick."""
        self._full_refresh_pending = True
        self._full_refresh_token += 1

    async def async_request_refresh(self, *, full: bool = True) -> None:
        """Run HA's debounced refresh, by default with every tier armed.

        Every caller in this integration is a write entity confirming a
        command it just sent to the router, and the values most of them read
        (Wi-Fi/VPN/WAN state, crypto maps, firmware version) live outside the
        fast tier. A caller that already requested the sources it changed
        (``request_host_policies_refresh``, ``request_mesh_refresh``) passes
        ``full=False``; the tick then also runs whatever tiers are due.
        """
        if full:
            self.request_full_refresh()
        await super().async_request_refresh()

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
        plan = refresh_plan(
            first_refresh=self.data is None,
            refresh_count=self._refresh_count,
            needs_clients=getattr(self, "needs_client_data", True),
            needs_policies=getattr(self, "needs_policies", True),
            pending=PendingRefreshes(
                host_policies=(
                    getattr(self, "_host_policies_refresh_token", 0)
                    if getattr(self, "_host_policies_refresh_pending", False)
                    else None
                ),
                full=(
                    getattr(self, "_full_refresh_token", 0)
                    if getattr(self, "_full_refresh_pending", False)
                    else None
                ),
                mesh=(
                    getattr(self, "_mesh_refresh_token", 0)
                    if getattr(self, "_mesh_refresh_pending", False)
                    else None
                ),
                update_check_pending=getattr(self, "_update_check_pending", False),
                update_check_token=getattr(self, "_update_check_token", 0),
                update_check_retry_token=getattr(self, "_update_check_retry_token", None),
            ),
        )
        due = plan.due
        pending = plan.pending
        sem = asyncio.Semaphore(4)
        # Start this refresh's tick scope: stage 0 below repopulates the
        # prefetch cache, and concurrent stage-1 reads of one endpoint share
        # a single request until the ``finally`` block ends the scope.
        self.client.begin_tick()

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

        clients_reused = Source.HOSTS not in due
        mesh_due = Source.MESH_MEMBERS in due
        ipsec_due = Source.IPSEC in due
        interface_stats_due = Source.INTERFACE_STATS in due
        if Source.VERSION not in due:
            _cached_version = {
                k: _prev_sys.get(k) for k in _VERSION_CACHE_KEYS if k in _prev_sys
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
                batch_tree = build_batch_tree(
                    plan,
                    unsupported=unsupported_batch_paths(self.client),
                    include_mesh=getattr(self.client, "_mws_member_supported", None)
                    is True,
                )
                try:
                    await self.client.prefetch_tick(batch_tree)
                except asyncio.CancelledError:
                    raise
                except Exception as err:  # noqa: BLE001 — best-effort prefetch
                    _LOGGER.debug("RCI prefetch failed (continuing per-call): %s", err)
                    self.client.begin_tick()

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
                policies,
                ndns_info,
                ping_check_status,
                crypto_maps,
            ) = await asyncio.gather(
                _bounded(self.client.async_get_system_info()),
                _bounded(self.client.async_get_current_version_info())
                if Source.VERSION in due else _resolve(_cached_version),
                _bounded(self.client.async_get_available_version_info())
                if Source.CHECK_UPDATE in due else _resolve(None),
                _bounded(self.client.async_get_interfaces()),
                _bounded(self.client.async_get_clients())
                if not clients_reused else _resolve(_prev.get("clients", [])),
                _bounded(self.client.async_get_ip_neighbours()),
                _bounded(self.client.async_get_host_policies())
                if Source.HOST_POLICIES in due else _resolve(_prev.get("host_policies", {})),
                _bounded(self.client.async_get_policies())
                if Source.POLICIES in due else _resolve(_prev.get("policies", {})),
                _bounded(self.client.async_get_ndns_info())
                if Source.NDNS in due else _resolve(_prev.get("ndns", {})),
                _bounded(self.client.async_get_ping_check_status()),
                _bounded(self.client.async_get_ipsec_status())
                if ipsec_due else _resolve(_prev.get("crypto_maps", {})),
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
            if mesh_due:
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
            mesh_nodes_failed = mesh_due and isinstance(mesh_nodes, BaseException)

            # Critical fetches default to the previous snapshot, not an empty
            # shape, so a tolerated transient failure (see the grace-window
            # check below) keeps the last-known values instead of blanking
            # every entity for one tick. ``_prev_sys`` is the previously
            # published merged system dict; ``version`` re-merges on top below.
            system = _ok("system_info", system, dict(_prev_sys))
            version = _ok("current_version", version, {})
            available_version_failed = isinstance(version_available, BaseException)
            version_available = _ok("available_version", version_available, None)
            interfaces = _ok(
                "interfaces", interfaces, dict(_prev.get("interfaces", {}))
            )
            ip_neighbours = _ok("ip_neighbours", ip_neighbours, [], silent=True)
            clients = merge_clients_with_neighbours(clients, ip_neighbours)
            # On a transient mesh fetch failure keep the previous snapshot:
            # an empty default would flap every mesh entity unavailable, and
            # the API layer now raises instead of returning MAC-keyed
            # fallback nodes (which used to flip mesh unique_ids).
            mesh_nodes = _ok("mesh_nodes", mesh_nodes, _prev.get("mesh_nodes", []))
            if mesh_due and not mesh_nodes_failed and isinstance(mesh_nodes, list):
                mesh_nodes = carry_firmware_available(
                    mesh_nodes, _prev.get("mesh_nodes")
                )
            mesh_nodes_fresh = self._source_is_fresh(
                "mesh_nodes",
                attempted=mesh_due,
                failed=mesh_nodes_failed,
            )
            # On a transient fetch failure keep the previous snapshot — an
            # empty default would flip every policy select to "Default"
            # until the next slow-tier refetch.
            host_policies_failed = isinstance(host_policies, BaseException)
            host_policies = dict_or_empty(
                _ok("host_policies", host_policies, _prev.get("host_policies", {}))
            )
            policies = dict_or_empty(
                _ok("policies", policies, _prev.get("policies", {}))
            )
            ndns_info = dict_or_empty(_ok("ndns_info", ndns_info, {}))
            # Read every tick now: one failed read must not drop every WAN
            # back to the link heuristic, so keep the last answer.
            ping_check_failed = isinstance(ping_check_status, BaseException)
            ping_check_status = dict_or_empty(
                _ok(
                    "ping_check_status",
                    ping_check_status,
                    _prev.get("ping_check_status", {}),
                )
            )
            # Crypto maps: not every router/firmware has the IPsec component,
            # so this endpoint may be unavailable. Mark the fetch as silent
            # so an absent endpoint doesn't produce a warning on every tick —
            # the api layer already debug-logs the reason. When the slow tier
            # is skipped, keep the previous snapshot instead of rebuilding it.
            crypto_maps_failed = ipsec_due and isinstance(crypto_maps, BaseException)
            crypto_maps_fresh = self._source_is_fresh(
                "crypto_maps",
                attempted=ipsec_due,
                failed=crypto_maps_failed,
            )
            if ipsec_due:
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

            # Handle critical fetch failures. If the router is unreachable,
            # auth has expired, or the RCI surface is down, ``system_info``
            # and ``interfaces`` are the two calls that MUST succeed — every
            # downstream computation depends on them. A single transient drop
            # (e.g. a router-side ``/rci/show/system`` timeout), however, is
            # tolerated for a few ticks: we keep the last-known snapshot (the
            # ``_ok`` defaults above already substituted it) rather than flip
            # every entity unavailable. Auth failures bypass the grace window;
            # a sustained outage exhausts it and then raises ``UpdateFailed``.
            critical_decision = evaluate_critical_failures(
                failed_fetches,
                have_previous_data=self.data is not None,
                streak=getattr(self, "_critical_fail_streak", 0),
            )
            self._critical_fail_streak = critical_decision.streak
            if critical_decision.action == "auth":
                raise ConfigEntryAuthFailed(critical_decision.message)
            if critical_decision.action == "fail":
                # Sustained outage: the grace window is exhausted and this
                # tick is about to raise UpdateFailed. Stretch the poll
                # interval so we don't keep hammering a confirmed-down
                # router every FAST_SCAN_INTERVAL; HA's DataUpdateCoordinator
                # reschedules from ``self.update_interval`` on its next pass.
                self._critical_fail_backoff_count = (
                    getattr(self, "_critical_fail_backoff_count", 0) + 1
                )
                self.update_interval = timedelta(
                    seconds=next_backoff_interval(self._critical_fail_backoff_count)
                )
                raise UpdateFailed(critical_decision.message)
            if critical_decision.action == "tolerate":
                _LOGGER.warning(critical_decision.message)

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
            # Router gauges (CPU load, RAM, NAT table) jitter on every fast
            # tick and were the integration's top recorder writers. They ride
            # along in the fast ``show/system`` payload, so there is nothing to
            # save on the wire — publish them on the medium cadence instead.
            if Source.SYSTEM_GAUGES not in due and _prev_sys:
                for gauge_key in _GAUGE_KEYS:
                    if gauge_key in _prev_sys:
                        merged_system[gauge_key] = _prev_sys[gauge_key]
                    else:
                        merged_system.pop(gauge_key, None)
            if version_available is None:
                merged_system["release-available"] = _prev_sys.get("release-available")
                merged_system["fw-update-sandbox"] = _prev_sys.get("fw-update-sandbox")
                merged_system["fw-update-available"] = _prev_sys.get("fw-update-available")
            else:
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
            iface_list = _normalize_interfaces(interfaces)
            iface_fp = tuple(
                (i.get("id"), i.get("type"), i.get("link"), i.get("state"))
                for i in iface_list
            )
            # Rebuild the per-WAN payload EVERY tick: link, address, default
            # route and ping check are what users alert on, and ``interfaces``
            # is fetched every tick anyway. This is a
            # CPU-only transform of the already-fetched ``iface_list``
            # (``async_get_wan_interfaces`` makes no RCI call when
            # ``iface_list`` is passed), so it is cheap. It must NOT be
            # cached on the ``(id, type, link, state)`` interface
            # fingerprint: that fingerprint excludes the volatile
            # ``uptime`` / ``ip`` fields, so reusing the cached list froze
            # every WAN-interface uptime sensor at its value from the last
            # link flap until the next flap. ``iface_fp`` is still emitted
            # below for diagnostics/contract consumers.
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
            if interface_stats_due:
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
                wifi = _prev.get("wifi", [])
                wireguard = _prev.get("wireguard", [])
                vpn_tunnels = _prev.get("vpn_tunnels", [])
                wan_status = _prev.get("wan_status", {})
                traffic_stats = _prev.get("traffic_stats", {})
                port_info = _prev.get("port_info", [])
                interface_stats = _prev.get("interface_stats", {})

            interface_stats_failed = isinstance(interface_stats, BaseException)
            interface_stats_fresh = self._source_is_fresh(
                "interface_stats",
                attempted=interface_stats_due,
                failed=interface_stats_failed,
            )
            # On a stage-2 task exception keep the previous snapshot — an
            # empty default would make every existing entity of that family
            # unavailable for the tick (same policy as mesh/crypto_maps).
            wifi = _ok("wifi", wifi, _prev.get("wifi", []))
            wireguard = _ok("wireguard", wireguard, _prev.get("wireguard", []))
            vpn_tunnels = _ok("vpn_tunnels", vpn_tunnels, _prev.get("vpn_tunnels", []))
            wan_status = dict_or_empty(
                _ok("wan_status", wan_status, _prev.get("wan_status", {}))
            )
            wan_interfaces_failed = isinstance(wan_interfaces, BaseException)
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

            wan_stats_fresh = interface_stats_due and not interface_stats_failed

            # ---------- WAN enrichment (CPU-only, runs on already-fetched
            # data — logic unchanged from the sequential implementation) ----------
            #
            # We reuse the already-fetched ``interface_stats`` (show/interface/stat
            # for every interface) instead of firing extra RCI calls. Throughput
            # is computed as a delta against the previous coordinator tick.
            if wan_stats_fresh:
                now_ts = asyncio.get_running_loop().time()
                wan_interfaces, wan_by_id = enrich_wan_interfaces(
                    wan_interfaces,
                    interface_stats,
                    ping_check_status,
                    self.data.get("wan_interfaces", []) if self.data else [],
                    now_ts,
                )
            else:
                # Fresh state, last counter sample: counters and rates are
                # only re-read on the medium tier.
                wan_interfaces, wan_by_id = carry_wan_samples(
                    wan_interfaces,
                    ping_check_status,
                    self.data.get("wan_interfaces", []) if self.data else [],
                )

            # ---------- Crypto map (site-to-site IPsec) enrichment ----------
            # Same delta pattern as the WAN block above. Counters reset to
            # zero whenever a phase2 SA rekeys or the tunnel bounces — the
            # negative-delta clamp keeps throughput sensors from spiking
            # to absurd negative values on those events.
            if ipsec_due:
                now_ts = asyncio.get_running_loop().time()
                # A failed read re-publishes the previous snapshot, which has
                # no new sample: carry its rates over instead of computing 0.
                enrich_crypto_maps(
                    crypto_maps,
                    self.data.get("crypto_maps") if self.data else None,
                    now_ts,
                    not crypto_maps_failed,
                )

            if mesh_due:
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

            # ---------- Connect / disconnect / failover events ----------
            # Automations otherwise have to trigger on entity state and poll
            # their way to "who just left". These diffs cost one set operation
            # a tick and nothing at all while the network is quiet.
            online_macs = {
                mac
                for mac, client in clients_by_mac.items()
                if isinstance(client, dict) and is_client_online(client)
            }
            previous_online = (
                self.data.get("online_clients", set()) if self.data else set()
            )
            if not isinstance(previous_online, set):
                previous_online = set()
            # On the very first tick every client looks like an arrival; the
            # integration has simply never seen this network before.
            first_tick = not self.data
            if clients_stale or clients_reused:
                # The hotspot table is the PREVIOUS tick's snapshot, re-merged
                # with this tick's neighbour data — diffing that invents
                # arrivals and departures the router never reported.
                # ClientEntity.available and the device tracker already refuse
                # to trust a stale tick. Keeping the previous online set also
                # stops the next healthy tick from firing a phantom reconnect.
                online_macs = set(previous_online)
                connected_macs: set[str] = set()
                disconnected_macs: set[str] = set()
            else:
                connected_macs = set() if first_tick else online_macs - previous_online
                disconnected_macs = set() if first_tick else previous_online - online_macs

            active_wan = next(
                (
                    wan_id
                    for wan_id, wan in wan_by_id.items()
                    if isinstance(wan, dict) and wan.get("defaultgw")
                ),
                None,
            )
            previous_wan = self.data.get("active_wan") if self.data else None
            wan_failover = (
                {"from": previous_wan, "to": active_wan}
                if not first_tick and active_wan != previous_wan
                else None
            )

            # A tick that reaches this point did not raise UpdateFailed, so
            # any backoff stretch applied during a prior outage is over —
            # restore the normal fast poll cadence.
            if getattr(self, "_critical_fail_backoff_count", 0):
                self._critical_fail_backoff_count = 0
                self.update_interval = timedelta(seconds=FAST_SCAN_INTERVAL)

            if host_policies_failed:
                self._host_policies_refresh_pending = True
            elif (
                Source.HOST_POLICIES in due
                and pending.host_policies is not None
                and getattr(self, "_host_policies_refresh_token", 0) == pending.host_policies
            ):
                # A newer request that arrived mid-tick keeps the flag armed
                # so its confirming fetch runs on the very next tick.
                self._host_policies_refresh_pending = False

            if (
                pending.full is not None
                and self._full_refresh_token == pending.full
            ):
                self._full_refresh_pending = False

            if Source.CHECK_UPDATE in due:
                self._update_check_retry_token = (
                    pending.update_check_token if available_version_failed else None
                )
                if (
                    not available_version_failed
                    and pending.update_check_pending
                    and self._update_check_token == pending.update_check_token
                ):
                    self._update_check_pending = False

            if (
                pending.mesh is not None
                and self._mesh_refresh_token == pending.mesh
            ):
                self._mesh_refresh_pending = False

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
                # Preserved data keeps dashboards stable, but is not evidence
                # that a provider is still down during an unreadable poll.
                "wan_observation_fresh": (
                    critical_decision.action == "ok"
                    and not wan_interfaces_failed
                    and not ping_check_failed
                ),
                "wan_by_id": wan_by_id,
                "mesh_nodes": mesh_nodes,
                "mesh_nodes_fresh": mesh_nodes_fresh,
                "mesh_associations": mesh_associations_data,
                "mesh_nodes_by_cid": mesh_nodes_by_cid,
                "interface_stats": interface_stats,
                "interface_stats_fresh": interface_stats_fresh,
                "client_stats": client_stats,
                "ndns": ndns_info,
                "host_policies": host_policies,
                "policies": policies,
                "port_info": port_info,
                "ping_check_status": ping_check_status,
                "_iface_fingerprint": iface_fp,
                "crypto_maps": crypto_maps,
                "crypto_maps_fresh": crypto_maps_fresh,
                "new_clients": new_macs,
                "online_clients": online_macs,
                "connected_clients": connected_macs,
                "disconnected_clients": disconnected_macs,
                "active_wan": active_wan,
                "wan_failover": wan_failover,
            }
        finally:
            self.client.clear_tick_cache()
