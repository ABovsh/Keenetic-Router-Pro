"""Refresh cadence and RCI batch planning for coordinator ticks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RefreshPlan:
    """Coordinator cadence flags for one refresh tick."""

    first_refresh: bool
    slow_refresh: bool
    very_slow_refresh: bool
    ipsec_status_refresh: bool


def refresh_plan(*, first_refresh: bool, refresh_count: int) -> RefreshPlan:
    """Return cadence flags matching the coordinator's existing modulo rules."""
    slow_refresh = first_refresh or refresh_count % 6 == 0
    very_slow_refresh = first_refresh or refresh_count % 30 == 0
    return RefreshPlan(
        first_refresh=first_refresh,
        slow_refresh=slow_refresh,
        very_slow_refresh=very_slow_refresh,
        ipsec_status_refresh=slow_refresh,
    )


def build_batch_tree(plan: RefreshPlan) -> dict[str, Any]:
    """Build the composite RCI tree requested at the start of a tick."""
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
    if plan.slow_refresh:
        add("show/version")
        add("show/ping-check")
        add("show/ipsec")
    if plan.very_slow_refresh:
        add("components/check-update")
        add("show/ndns")
        add("show/dns-proxy")
    return batch_tree
