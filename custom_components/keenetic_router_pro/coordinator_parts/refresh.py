"""Refresh cadence and RCI batch planning for coordinator ticks."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from enum import StrEnum
from typing import Any


class Source(StrEnum):
    """Sources and derived reads scheduled by the coordinator."""

    SYSTEM = "system"
    INTERFACES = "interfaces"
    NEIGHBOURS = "neighbours"
    HOSTS = "hosts"
    PING_CHECK = "ping_check"
    INTERFACE_STATS = "interface_stats"
    SYSTEM_GAUGES = "system_gauges"
    HOST_POLICIES = "host_policies"
    POLICIES = "policies"
    VERSION = "version"
    NDNS = "ndns"
    CHECK_UPDATE = "check_update"
    IPSEC = "ipsec"
    MESH_MEMBERS = "mesh_members"


@dataclass(frozen=True)
class PendingRefreshes:
    """Request generations captured at the start of a tick."""

    host_policies: int | None = None
    full: int | None = None
    mesh: int | None = None
    # A scheduled firmware check can fail without an explicit request pending.
    update_check_pending: bool = False
    update_check_token: int = 0
    update_check_retry_token: int | None = None


@dataclass(frozen=True)
class RefreshPlan:
    """Freeze the due sources and request generations for one tick."""

    first_refresh: bool
    medium_refresh: bool
    slow_refresh: bool
    very_slow_refresh: bool
    ipsec_status_refresh: bool
    update_check_refresh: bool
    pending: PendingRefreshes = field(default_factory=PendingRefreshes)
    needs_clients: InitVar[bool] = True
    needs_policies: InitVar[bool] = True
    due: frozenset[Source] = field(init=False)

    def __post_init__(self, needs_clients: bool, needs_policies: bool) -> None:
        due = {Source.SYSTEM, Source.INTERFACES, Source.NEIGHBOURS, Source.PING_CHECK}
        mesh_due = self.slow_refresh or self.pending.mesh is not None
        if mesh_due:
            due.add(Source.MESH_MEMBERS)
        # Mesh discovery and connect/disconnect events need the hotspot read
        # on mesh ticks even if no client entities consume it.
        if needs_clients or mesh_due:
            due.add(Source.HOSTS)
        if self.medium_refresh:
            due.update((Source.INTERFACE_STATS, Source.SYSTEM_GAUGES))
        if needs_policies and (self.slow_refresh or self.pending.host_policies is not None):
            due.add(Source.HOST_POLICIES)
        if self.very_slow_refresh:
            due.update((Source.VERSION, Source.NDNS))
            if needs_policies:
                due.add(Source.POLICIES)
        if self.ipsec_status_refresh:
            due.add(Source.IPSEC)
        if self.update_check_refresh:
            due.add(Source.CHECK_UPDATE)
        object.__setattr__(self, "due", frozenset(due))


# One firmware check per day. ``components/check-update`` asks Keenetic's
# update service, so running it on the very-slow tier meant 96 external checks
# per router per day — and the endpoint is stateful, returning a different
# shape on back-to-back calls, which made the reported channel/available
# version flap between real values and None.
_UPDATE_CHECK_EVERY = 1440  # ticks (24 h at FAST_SCAN_INTERVAL = 60 s)


def refresh_plan(
    *,
    first_refresh: bool,
    refresh_count: int,
    needs_clients: bool = True,
    needs_policies: bool = True,
    pending: PendingRefreshes = PendingRefreshes(),
) -> RefreshPlan:
    """Plan all sources due for one tick from a snapshot of its inputs.

    The divisors are counted in ticks, which only means a fixed number of
    seconds because the tick itself is fixed at ``FAST_SCAN_INTERVAL``. That is
    the whole reason the idle backoff was removed: while the tick stretched to
    120 s these same divisors quietly turned the medium tier into six minutes
    and the very-slow tier into an hour. At 60 s per tick they read as
    2 min / 3 min / 15 min, which is what the tier names are meant to promise.

    Note the medium tier carries far more than its name suggests — the whole of
    coordinator stage 2 (Wi-Fi, WireGuard, VPN tunnels, WAN status, interface
    stats, port info) plus the CPU/RAM/conntrack gauges.
    """
    full_refresh = first_refresh or pending.full is not None
    medium_refresh = full_refresh or refresh_count % 2 == 0
    slow_refresh = full_refresh or refresh_count % 3 == 0
    scheduled_very_slow = first_refresh or refresh_count % 15 == 0
    # A full confirming refresh does not advance the firmware retry cadence.
    retry_token = pending.update_check_retry_token
    update_check_refresh = (
        first_refresh
        or refresh_count % _UPDATE_CHECK_EVERY == 0
        or (pending.update_check_pending and pending.update_check_token != retry_token)
        or (scheduled_very_slow and retry_token is not None)
    )
    return RefreshPlan(
        first_refresh=first_refresh,
        medium_refresh=medium_refresh,
        slow_refresh=slow_refresh,
        very_slow_refresh=full_refresh or scheduled_very_slow,
        ipsec_status_refresh=slow_refresh,
        update_check_refresh=update_check_refresh,
        pending=pending,
        needs_clients=needs_clients,
        needs_policies=needs_policies,
    )


_BATCH_PATHS = {
    Source.SYSTEM: "show/system",
    Source.INTERFACES: "show/interface",
    Source.NEIGHBOURS: "show/ip/neighbour",
    Source.HOSTS: "show/ip/hotspot",
    Source.PING_CHECK: "show/ping-check",
    Source.IPSEC: "show/ipsec",
    Source.MESH_MEMBERS: "show/mws/member",
    Source.VERSION: "show/version",
    Source.NDNS: "show/ndns",
    Source.CHECK_UPDATE: "components/check-update",
}

# Prefetch skips latched paths to avoid "not found" entries in the router log.
_BATCH_LATCHES = {
    Source.PING_CHECK: "_ping_check_supported",
    Source.IPSEC: "_crypto_map_supported",
    Source.NDNS: "_ndns_supported",
}


def unsupported_batch_paths(client: Any) -> frozenset[str]:
    """Return batch paths the router has already answered "not found" for."""
    paths = {
        _BATCH_PATHS[source]
        for source, attr in _BATCH_LATCHES.items()
        if getattr(client, attr, None) is False
    }
    if "show/ip/hotspot/host" in (getattr(client, "_hotspot_subpath_skip", None) or ()):
        paths.add(_BATCH_PATHS[Source.HOSTS])
    return frozenset(paths)


def build_batch_tree(
    plan: RefreshPlan,
    *,
    needs_clients: bool = True,
    unsupported: frozenset[str] = frozenset(),
    include_mesh: bool = False,
    mesh_refresh: bool | None = None,
    update_check: bool | None = None,
) -> dict[str, Any]:
    """Build the composite RCI tree from due sources and capability latches."""
    due = plan.due
    # Keep explicit overrides for callers constructing a batch directly.
    for source, override in (
        (Source.MESH_MEMBERS, mesh_refresh),
        (Source.CHECK_UPDATE, update_check),
    ):
        if override is not None:
            due = due | {source} if override else due - {source}
    if not needs_clients:
        due -= {Source.HOSTS}
    if not include_mesh:
        due -= {Source.MESH_MEMBERS}

    batch_tree: dict[str, Any] = {}
    for source, path in _BATCH_PATHS.items():
        if source not in due or path in unsupported:
            continue
        node = batch_tree
        for part in path.split("/"):
            node = node.setdefault(part, {})
    return batch_tree
