"""DataUpdateCoordinator for Keenetic Router Pro."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KeeneticClient
from .api.helpers import _is_endpoint_missing, _normalize_interfaces
from .const import DOMAIN, FAST_SCAN_INTERVAL
from .coordinator_parts.derived import (
    carry_firmware_available,
    counter_rate_bytes_per_second,
    mesh_associations,
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
    RefreshPlan,
    Source,
    build_batch_tree,
    refresh_plan,
    unsupported_batch_paths,
)
from .coordinator_parts.snapshot import (
    _InterfaceData,
    _PrimaryData,
    _assemble_snapshot,
    _merge_system,
)


_dict_or_empty = dict_or_empty
_list_or_empty = list_or_empty
_counter_rate_bytes_per_second = counter_rate_bytes_per_second
_merge_clients_with_neighbours = merge_clients_with_neighbours
_mesh_associations = mesh_associations

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.coordinator")


_Bounded = Callable[[Coroutine[Any, Any, Any]], Awaitable[Any]]

_SWITCH_READ_FAILURES = {
    Source.WIFI: {"wifi", "clients", "ip_neighbours", "mesh_nodes"},
    Source.WAN_STATUS: {
        "wan_interfaces", "wan_status", "ping_check_status", "traffic_stats", "wireguard",
        "vpn_tunnels",
    },
    Source.VPN: {"wan_interfaces", "wan_status", "traffic_stats", "wireguard", "vpn_tunnels"},
    Source.IPSEC: {"crypto_maps"},
}


@dataclass(slots=True)
class _SwitchConfirmation:
    """A switch write awaiting a confirming read."""

    generation: int
    attempts: int = 0


@dataclass(slots=True)
class _PrimaryReads:
    """Independent reads before the mesh and payload resolution stages."""

    system: Any
    version: Any
    version_available: Any
    interfaces: Any
    clients: Any
    ip_neighbours: Any
    host_policies: Any
    policies: Any
    ndns_info: Any
    ping_check_status: Any
    crypto_maps: Any
    clients_stale: bool = False


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
        # ``async_request_refresh`` by default. Uses the same pending/token
        # pair as the host-policies request.
        self._full_refresh_pending = False
        self._full_refresh_token = 0
        self._switch_refreshes: dict[Source, _SwitchConfirmation] = {}
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

    async def async_request_switch_refresh(self, source: Source) -> None:
        """Arm a confirmation after a switch command, then request a refresh."""
        previous = self._switch_refreshes.get(source)
        self._switch_refreshes[source] = _SwitchConfirmation(
            previous.generation + 1 if previous else 1
        )
        await self.async_request_refresh(full=False)

    def _finish_switch_confirmations(
        self,
        pending: tuple[tuple[Source, int], ...],
        confirmed: set[Source],
    ) -> None:
        """Acknowledge matching generations or count a failed attempt."""
        for source, generation in pending:
            request = self._switch_refreshes.get(source)
            if request is None or request.generation != generation:
                continue
            if source in confirmed:
                del self._switch_refreshes[source]
                continue
            request.attempts += 1
            if request.attempts >= OPTIONAL_FETCH_GRACE_ATTEMPTS:
                del self._switch_refreshes[source]
                _LOGGER.debug(
                    "Switch confirmation for %s expired after %d attempts", source, request.attempts
                )

    async def async_request_refresh(self, *, full: bool = True) -> None:
        """Run HA's debounced refresh, by default with every tier armed.

        Callers that already requested their confirmation sources pass
        ``full=False``. The tick also runs whichever tiers are scheduled.
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
        """Plan, read, and publish one tick while the update lock is held."""
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
                switches=tuple(
                    (source, request.generation)
                    for source, request in getattr(self, "_switch_refreshes", {}).items()
                ),
            ),
        )
        due = plan.due
        pending = plan.pending
        sem = asyncio.Semaphore(4)
        # Start this refresh's tick scope: stage 0 below repopulates the
        # prefetch cache, and concurrent stage-1 reads of one endpoint share
        # a single request until the ``finally`` block ends the scope.
        self.client.begin_tick()

        async def bounded(coro: Coroutine[Any, Any, Any]) -> Any:
            async with sem:
                return await coro

        previous = self.data or {}
        previous_sys = previous.get("system") or {}

        # Collected per-tick so we can emit a single warning instead of
        # silently defaulting every failing fetch at debug level.
        failed_fetches: list[FetchFailure] = []
        confirmed_switches: set[Source] = set()

        def ok(name: str, value: Any, default: Any, *, silent: bool = False) -> Any:
            result = ok_or_default(
                name,
                value,
                default,
                failed_fetches,
                silent=silent,
            )
            if isinstance(value, BaseException) and not isinstance(value, asyncio.CancelledError):
                _LOGGER.debug("Coordinator fetch %s failed: %s", name, value)
            return result

        try:
            reads = await self._read_primary(
                plan, previous, previous_sys, bounded, failed_fetches, ok
            )
            mesh_nodes = await self._read_mesh(due, reads.clients, previous, bounded)
            primary = self._resolve_primary(due, reads, mesh_nodes, previous, previous_sys, ok)

            critical_decision = evaluate_critical_failures(
                failed_fetches,
                have_previous_data=self.data is not None,
                streak=getattr(self, "_critical_fail_streak", 0),
            )
            self._critical_fail_streak = critical_decision.streak
            if critical_decision.action == "auth":
                raise ConfigEntryAuthFailed(critical_decision.message)
            if critical_decision.action == "fail":
                # Back off after the grace window to reduce load during an outage.
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
            if not primary.system or not primary.interfaces:
                raise UpdateFailed(
                    "Critical router payload empty "
                    f"(system={bool(primary.system)}, interfaces={bool(primary.interfaces)})"
                )

            client_stats = self.client.summarize_client_stats(primary.clients)
            merged_system = _merge_system(primary, previous_sys, due)
            links = await self._read_interfaces(due, primary.interfaces, previous, bounded, ok)

            if failed_fetches:
                _LOGGER.warning(
                    "Keenetic coordinator: %d fetch(es) failed this tick and "
                    "fell back to defaults: %s",
                    len(failed_fetches),
                    ", ".join(failure.name for failure in failed_fetches),
                )

            # Carried WAN values alone are insufficient to detect a fresh outage.
            wan_observation_fresh = (
                critical_decision.action == "ok"
                and not links.wan_interfaces_failed
                and not primary.ping_check_failed
            )
            snapshot = _assemble_snapshot(
                primary,
                links,
                due=due,
                previous=self.data or {},
                cached=previous,
                merged_system=merged_system,
                client_stats=client_stats,
                wan_observation_fresh=wan_observation_fresh,
                clock=asyncio.get_running_loop().time,
            )

            # A tick that reaches this point did not raise UpdateFailed, so
            # any backoff stretch applied during a prior outage is over —
            # restore the normal fast poll cadence.
            if getattr(self, "_critical_fail_backoff_count", 0):
                self._critical_fail_backoff_count = 0
                self.update_interval = timedelta(seconds=FAST_SCAN_INTERVAL)

            if primary.host_policies_failed:
                self._host_policies_refresh_pending = True
            elif (
                Source.HOST_POLICIES in due
                and pending.host_policies is not None
                and getattr(self, "_host_policies_refresh_token", 0) == pending.host_policies
            ):
                # A newer request that arrived mid-tick keeps the flag armed
                # so its confirming fetch runs on the very next tick.
                self._host_policies_refresh_pending = False

            if pending.full is not None and self._full_refresh_token == pending.full:
                self._full_refresh_pending = False

            if Source.CHECK_UPDATE in due:
                self._update_check_retry_token = (
                    pending.update_check_token if primary.available_version_failed else None
                )
                if (
                    not primary.available_version_failed
                    and pending.update_check_pending
                    and self._update_check_token == pending.update_check_token
                ):
                    self._update_check_pending = False

            if pending.mesh is not None and self._mesh_refresh_token == pending.mesh:
                self._mesh_refresh_pending = False

            if pending.switches and critical_decision.action == "ok":
                # These optional failures are silent in the fetch log.
                failed = {failure.name for failure in failed_fetches}
                if primary.crypto_maps_failed:
                    failed.add("crypto_maps")
                if isinstance(reads.ip_neighbours, BaseException) and not _is_endpoint_missing(
                    reads.ip_neighbours
                ):
                    failed.add("ip_neighbours")
                confirmed_switches = {
                    source for source, _generation in pending.switches
                    if _SWITCH_READ_FAILURES[source].isdisjoint(failed)
                }

            self._refresh_count += 1
            return snapshot
        finally:
            self.client.clear_tick_cache()
            self._finish_switch_confirmations(pending.switches, confirmed_switches)

    async def _read_primary(
        self,
        plan: RefreshPlan,
        previous: dict[str, Any],
        previous_system: dict[str, Any],
        bounded: _Bounded,
        failed_fetches: list[FetchFailure],
        ok: Callable[..., Any],
    ) -> _PrimaryReads:
        """Prefetch composite data, then read independent sources."""
        due = plan.due
        switches = {source for source, _generation in plan.pending.switches}
        # A successful crypto-map command supersedes a cached endpoint absence.
        if Source.IPSEC in switches and self.client._crypto_map_supported is False:
            self.client._crypto_map_supported = None
        if Source.VERSION not in due:
            cached_version = {
                key: previous_system.get(key)
                for key in _VERSION_CACHE_KEYS
                if key in previous_system
            }

        async def cached(value: Any) -> Any:
            return value

        # Readers still run independently when composite prefetch is unavailable.
        if self.client._rci_batch_supported is not False:
            batch_tree = build_batch_tree(
                plan,
                unsupported=unsupported_batch_paths(self.client),
                include_mesh=getattr(self.client, "_mws_member_supported", None) is True,
            )
            try:
                await self.client.prefetch_tick(batch_tree)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 — best-effort prefetch
                _LOGGER.debug("RCI prefetch failed (continuing per-call): %s", err)
                self.client.begin_tick()

        values = await asyncio.gather(
            bounded(self.client.async_get_system_info()),
            bounded(self.client.async_get_current_version_info())
            if Source.VERSION in due
            else cached(cached_version),
            bounded(self.client.async_get_available_version_info())
            if Source.CHECK_UPDATE in due
            else cached(None),
            bounded(self.client.async_get_interfaces()),
            bounded(self.client.async_get_clients())
            if Source.HOSTS in due
            else cached(previous.get("clients", [])),
            bounded(self.client.async_get_ip_neighbours()),
            bounded(self.client.async_get_host_policies())
            if Source.HOST_POLICIES in due
            else cached(previous.get("host_policies", {})),
            bounded(self.client.async_get_policies())
            if Source.POLICIES in due
            else cached(previous.get("policies", {})),
            bounded(self.client.async_get_ndns_info())
            if Source.NDNS in due
            else cached(previous.get("ndns", {})),
            bounded(
                self.client.async_get_ping_check_status(raise_on_failure=True)
                if Source.WAN_STATUS in switches
                else self.client.async_get_ping_check_status()
            ),
            bounded(self.client.async_get_ipsec_status())
            if Source.IPSEC in due
            else cached(previous.get("crypto_maps", {})),
            return_exceptions=True,
        )
        reads = _PrimaryReads(*values)
        # Resolve hosts now so mesh can reuse the hotspot payload.
        clients = reads.clients
        if isinstance(clients, asyncio.CancelledError):
            raise clients
        if isinstance(clients, BaseException):
            previous_clients = previous.get("clients", [])
            if previous_clients:
                failed_fetches.append(FetchFailure("clients", clients))
                _LOGGER.debug(
                    "Coordinator fetch clients failed; preserving previous client snapshot: %s",
                    clients,
                )
                reads.clients = previous_clients
                reads.clients_stale = True
                return reads
        reads.clients = ok("clients", clients, [])
        return reads

    async def _read_mesh(
        self,
        due: frozenset[Source],
        clients: list[dict[str, Any]],
        previous: dict[str, Any],
        bounded: _Bounded,
    ) -> Any:
        """Read mesh nodes using the pre-neighbour-merge hotspot snapshot."""
        if Source.MESH_MEMBERS not in due:
            return previous.get("mesh_nodes", [])
        try:
            return await bounded(self.client.async_get_mesh_nodes(clients=clients))
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 — resolved with other tick reads
            return err

    def _resolve_primary(
        self,
        due: frozenset[Source],
        reads: _PrimaryReads,
        mesh_nodes: Any,
        previous: dict[str, Any],
        previous_system: dict[str, Any],
        ok: Callable[..., Any],
    ) -> _PrimaryData:
        """Resolve failures and record freshness before the dependent reads."""
        mesh_due = Source.MESH_MEMBERS in due
        ipsec_due = Source.IPSEC in due
        mesh_nodes_failed = mesh_due and isinstance(mesh_nodes, BaseException)

        # Critical fetches default to the previous snapshot, not an empty
        # shape, so a tolerated transient failure (see the grace-window
        # check below) keeps the last-known values instead of blanking
        # every entity for one tick. ``previous_system`` is the previously
        # published merged system dict; ``version`` re-merges on top below.
        system = ok("system_info", reads.system, dict(previous_system))
        version = ok("current_version", reads.version, {})
        available_version_failed = isinstance(reads.version_available, BaseException)
        version_available = ok("available_version", reads.version_available, None)
        interfaces = ok("interfaces", reads.interfaces, dict(previous.get("interfaces", {})))
        ip_neighbours = ok("ip_neighbours", reads.ip_neighbours, [], silent=True)
        clients = merge_clients_with_neighbours(reads.clients, ip_neighbours)
        # On a transient mesh fetch failure keep the previous snapshot:
        # an empty default would flap every mesh entity unavailable, and
        # the API layer now raises instead of returning MAC-keyed
        # fallback nodes (which used to flip mesh unique_ids).
        mesh_nodes = ok("mesh_nodes", mesh_nodes, previous.get("mesh_nodes", []))
        if mesh_due and not mesh_nodes_failed and isinstance(mesh_nodes, list):
            mesh_nodes = carry_firmware_available(mesh_nodes, previous.get("mesh_nodes"))
        mesh_nodes_fresh = self._source_is_fresh(
            "mesh_nodes",
            attempted=mesh_due,
            failed=mesh_nodes_failed,
        )
        # On a transient fetch failure keep the previous snapshot — an
        # empty default would flip every policy select to "Default"
        # until the next slow-tier refetch.
        host_policies_failed = isinstance(reads.host_policies, BaseException)
        host_policies = dict_or_empty(
            ok("host_policies", reads.host_policies, previous.get("host_policies", {}))
        )
        policies = dict_or_empty(ok("policies", reads.policies, previous.get("policies", {})))
        ndns_info = dict_or_empty(ok("ndns_info", reads.ndns_info, {}))
        # Read every tick now: one failed read must not drop every WAN
        # back to the link heuristic, so keep the last answer.
        ping_check_failed = isinstance(reads.ping_check_status, BaseException)
        ping_check_status = dict_or_empty(
            ok(
                "ping_check_status",
                reads.ping_check_status,
                previous.get("ping_check_status", {}),
            )
        )
        # Crypto maps: not every router/firmware has the IPsec component,
        # so this endpoint may be unavailable. Mark the fetch as silent
        # so an absent endpoint doesn't produce a warning on every tick —
        # the api layer already debug-logs the reason. When the slow tier
        # is skipped, keep the previous snapshot instead of rebuilding it.
        crypto_maps_failed = ipsec_due and isinstance(reads.crypto_maps, BaseException)
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
                    ok(
                        "crypto_maps",
                        reads.crypto_maps,
                        previous.get("crypto_maps", {}),
                        silent=True,
                    )
                ).items()
                if isinstance(cmap, dict)
            }
        else:
            crypto_maps = previous.get("crypto_maps", {})
        return _PrimaryData(
            system=system,
            version=version,
            version_available=version_available,
            interfaces=interfaces,
            clients=clients,
            clients_stale=reads.clients_stale,
            mesh_nodes=mesh_nodes,
            mesh_nodes_fresh=mesh_nodes_fresh,
            host_policies=host_policies,
            policies=policies,
            ndns=ndns_info,
            ping_check_status=ping_check_status,
            ping_check_failed=ping_check_failed,
            crypto_maps=crypto_maps,
            crypto_maps_fresh=crypto_maps_fresh,
            crypto_maps_failed=crypto_maps_failed,
            host_policies_failed=host_policies_failed,
            available_version_failed=available_version_failed,
        )

    async def _read_interfaces(
        self,
        due: frozenset[Source],
        interfaces: dict[str, Any],
        previous: dict[str, Any],
        bounded: _Bounded,
        ok: Callable[..., Any],
    ) -> _InterfaceData:
        """Read interface projections and statistics with one normalized interface list."""
        interface_stats_due = Source.INTERFACE_STATS in due
        iface_list = _normalize_interfaces(interfaces)
        iface_fp = tuple(
            (i.get("id"), i.get("type"), i.get("link"), i.get("state")) for i in iface_list
        )
        # Link, address and uptime can change between statistics samples.
        # Rebuild WAN state even if the interface fingerprint is unchanged.
        try:
            wan_interfaces = await bounded(
                self.client.async_get_wan_interfaces(
                    interfaces=interfaces,
                    iface_list=iface_list,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            wan_interfaces = err

        reads: dict[str, Coroutine[Any, Any, Any]] = {}
        if Source.WIFI in due:
            reads["wifi"] = self.client.async_get_wifi_networks(
                interfaces=interfaces, iface_list=iface_list
            )
        if Source.VPN in due:
            reads["wireguard"] = self.client.async_get_wireguard_status(
                interfaces=interfaces, iface_list=iface_list
            )
            reads["vpn_tunnels"] = self.client.async_get_vpn_tunnels(
                interfaces=interfaces, iface_list=iface_list
            )
        if Source.WAN_STATUS in due:
            reads["wan_status"] = self.client.async_get_wan_status(
                interfaces=interfaces, iface_list=iface_list
            )
            reads["traffic_stats"] = self.client.async_get_traffic_stats(
                interfaces=interfaces, iface_list=iface_list
            )
        if interface_stats_due:
            reads["port_info"] = self.client.async_get_port_info(interfaces=interfaces)
            reads["interface_stats"] = self.client.async_get_all_interface_stats(
                interfaces=interfaces,
                iface_list=iface_list,
                wan_interfaces=wan_interfaces if isinstance(wan_interfaces, list) else [],
            )
        results = {}
        if reads:
            values = await asyncio.gather(
                *(bounded(read) for read in reads.values()), return_exceptions=True
            )
            results = dict(zip(reads, values))

        def resolve(key: str, default: Any) -> Any:
            prior = previous.get(key, default)
            return ok(key, results.get(key, prior), prior)

        interface_stats = results.get("interface_stats", previous.get("interface_stats", {}))

        interface_stats_failed = isinstance(interface_stats, BaseException)
        interface_stats_fresh = self._source_is_fresh(
            "interface_stats",
            attempted=interface_stats_due,
            failed=interface_stats_failed,
        )
        # On a stage-2 task exception keep the previous snapshot — an
        # empty default would make every existing entity of that family
        # unavailable for the tick (same policy as mesh/crypto_maps).
        wifi = resolve("wifi", [])
        wireguard = resolve("wireguard", [])
        vpn_tunnels = resolve("vpn_tunnels", [])
        wan_status = dict_or_empty(resolve("wan_status", {}))
        wan_interfaces_failed = isinstance(wan_interfaces, BaseException)
        wan_interfaces = ok("wan_interfaces", wan_interfaces, previous.get("wan_interfaces", []))
        traffic_stats = dict_or_empty(resolve("traffic_stats", {}))
        port_info = list_or_empty(resolve("port_info", []))
        interface_stats = dict_or_empty(ok("interface_stats", interface_stats, {}))
        if interface_stats_failed:
            interface_stats = previous.get("interface_stats", {})
        return _InterfaceData(
            fingerprint=iface_fp,
            wifi=wifi,
            wireguard=wireguard,
            vpn_tunnels=vpn_tunnels,
            wan_status=wan_status,
            wan_interfaces=wan_interfaces,
            wan_interfaces_failed=wan_interfaces_failed,
            traffic_stats=traffic_stats,
            port_info=port_info,
            interface_stats=interface_stats,
            interface_stats_fresh=interface_stats_fresh,
            interface_stats_failed=interface_stats_failed,
        )
