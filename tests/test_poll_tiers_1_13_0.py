"""Flat 60 s poll with time-meaningful refresh tiers (1.13.0).

The idle backoff stretched the tick to 90-120 s, and because the refresh tiers
are counted in TICKS (``refresh_count % N``) that stretch multiplied through
every tier: Wi-Fi, VPN, WAN throughput and the CPU/RAM/conntrack gauges all ride
the medium tier, so at a 120 s tick they refreshed once every six minutes, mesh
and IPsec once every twelve, firmware and DNS state once an hour.

Worse, the fingerprint that decided "idle" carried ``any(throughput)`` as a
*component* rather than as a gate, so a router moving traffic non-stop produced
a stable ``True`` and was classified idle forever — the exact opposite of the
behaviour its own comment described.

With the 1.12.0 counter deadbands doing the noise suppression, the poll cadence
no longer has to. The backoff is deleted outright: a flat 60 s tick makes the
tick-counted tiers mean a fixed number of seconds again, and the fingerprint
that produced the inversion goes with it. The outage backoff
(``next_backoff_interval``) is a different mechanism and stays.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_coordinator_stages import StageFixtureClient, _coordinator, _updated_data

from custom_components.keenetic_router_pro import coordinator_parts
from custom_components.keenetic_router_pro.const import FAST_SCAN_INTERVAL
from custom_components.keenetic_router_pro.coordinator_parts.refresh import refresh_plan


def test_fast_tier_is_a_flat_sixty_seconds() -> None:
    assert FAST_SCAN_INTERVAL == 60


def test_the_idle_backoff_is_gone() -> None:
    """The ladder and its inverted fingerprint are deleted, not re-tuned."""
    assert not hasattr(coordinator_parts.refresh, "idle_poll_interval")


@pytest.mark.parametrize(
    ("count", "medium", "slow", "very_slow"),
    [
        (1, False, False, False),
        (2, True, False, False),   # medium every 2 ticks -> 2 min
        (3, False, True, False),   # slow every 3 ticks   -> 3 min
        (4, True, False, False),
        (6, True, True, False),
        (15, False, True, True),   # very slow every 15   -> 15 min
        (30, True, True, True),
    ],
)
def test_refresh_tiers_land_on_fixed_wall_clock_intervals(
    count: int, medium: bool, slow: bool, very_slow: bool
) -> None:
    plan = refresh_plan(first_refresh=False, refresh_count=count)
    assert plan.medium_refresh is medium
    assert plan.slow_refresh is slow
    assert plan.very_slow_refresh is very_slow
    assert plan.ipsec_status_refresh is slow


def test_update_check_still_runs_once_a_day_at_the_new_tick() -> None:
    """2880 ticks was 24 h at 30 s; at 60 s the divisor has to halve."""
    assert refresh_plan(first_refresh=False, refresh_count=1440).update_check_refresh
    assert not refresh_plan(first_refresh=False, refresh_count=2880 - 1).update_check_refresh
    assert not refresh_plan(first_refresh=False, refresh_count=720).update_check_refresh


async def test_quiet_ticks_no_longer_stretch_the_poll_interval() -> None:
    """A router nothing happens on keeps the flat tick, not a 120 s one.

    61 ticks is past every rung of the old ladder (20 -> 60 s, 40 -> 90 s,
    60 -> 120 s), and the fixture router is quiet throughout, so the old code
    would have ended this loop at 120 s.
    """
    coordinator = _coordinator(StageFixtureClient())
    coordinator.update_interval = timedelta(seconds=FAST_SCAN_INTERVAL)

    for _ in range(61):
        await _updated_data(coordinator)

    assert coordinator.update_interval == timedelta(seconds=FAST_SCAN_INTERVAL)


async def test_outage_backoff_still_stretches_and_still_restores() -> None:
    """Deleting the idle ladder must not take the outage backoff with it."""
    from homeassistant.helpers.update_coordinator import UpdateFailed

    client = StageFixtureClient()
    state = {"fail": True}
    original = client.async_get_system_info

    async def flaky() -> dict:
        if state["fail"]:
            raise RuntimeError("router offline")
        return await original()

    client.async_get_system_info = flaky  # type: ignore[method-assign]
    coordinator = _coordinator(client)
    coordinator.update_interval = timedelta(seconds=FAST_SCAN_INTERVAL)

    for expected in (60, 120, 120):
        with pytest.raises(UpdateFailed):
            await _updated_data(coordinator)
        assert coordinator.update_interval == timedelta(seconds=expected)

    state["fail"] = False
    await _updated_data(coordinator)
    assert coordinator.update_interval == timedelta(seconds=FAST_SCAN_INTERVAL)
