"""Refresh cadence and RCI batch planning for coordinator ticks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RefreshPlan:
    """Coordinator cadence flags for one refresh tick."""

    first_refresh: bool
    medium_refresh: bool
    slow_refresh: bool
    very_slow_refresh: bool
    ipsec_status_refresh: bool
    update_check_refresh: bool


# One firmware check per day. ``components/check-update`` asks Keenetic's
# update service, so running it on the very-slow tier meant 96 external checks
# per router per day — and the endpoint is stateful, returning a different
# shape on back-to-back calls, which made the reported channel/available
# version flap between real values and None.
_UPDATE_CHECK_EVERY = 1440  # ticks (24 h at FAST_SCAN_INTERVAL = 60 s)


def refresh_plan(*, first_refresh: bool, refresh_count: int) -> RefreshPlan:
    """Return explicit runtime-efficiency cadence flags for one tick.

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
    medium_refresh = first_refresh or refresh_count % 2 == 0
    slow_refresh = first_refresh or refresh_count % 3 == 0
    very_slow_refresh = first_refresh or refresh_count % 15 == 0
    return RefreshPlan(
        first_refresh=first_refresh,
        medium_refresh=medium_refresh,
        slow_refresh=slow_refresh,
        very_slow_refresh=very_slow_refresh,
        ipsec_status_refresh=slow_refresh,
        update_check_refresh=first_refresh or refresh_count % _UPDATE_CHECK_EVERY == 0,
    )


def build_batch_tree(
    plan: RefreshPlan, *, needs_clients: bool = True
) -> dict[str, Any]:
    """Build the composite RCI tree requested at the start of a tick.

    ``needs_clients`` drops ``show/ip/hotspot`` — by a wide margin the largest
    payload of the tick — when every entity derived from it is disabled. There
    is no point parsing a hundred hosts nothing will ever read.
    """
    batch_tree: dict[str, Any] = {}

    def add(path: str) -> None:
        node = batch_tree
        parts = path.strip("/").split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node.setdefault(parts[-1], {})

    add("show/system")
    add("show/interface")
    add("show/ip/neighbour")
    if needs_clients:
        add("show/ip/hotspot")
    if plan.medium_refresh:
        add("show/ping-check")
    if plan.ipsec_status_refresh:
        add("show/ipsec")
    if plan.very_slow_refresh:
        add("show/version")
        add("show/ndns")
        add("show/dns-proxy")
    if plan.update_check_refresh:
        add("components/check-update")
    return batch_tree
