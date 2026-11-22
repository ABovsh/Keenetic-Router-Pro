"""The tick plan combines cadence, demand and pending refreshes."""

import pytest

from custom_components.keenetic_router_pro.coordinator_parts.refresh import (
    PendingRefreshes,
    Source,
    refresh_plan,
)

_EVERY_TICK = {Source.SYSTEM, Source.INTERFACES, Source.NEIGHBOURS, Source.PING_CHECK}
_MEDIUM = {Source.INTERFACE_STATS, Source.SYSTEM_GAUGES}
_SLOW = {Source.IPSEC, Source.MESH_MEMBERS, Source.HOSTS, Source.HOST_POLICIES}
_VERY_SLOW = {Source.VERSION, Source.NDNS, Source.POLICIES}


def test_first_refresh_schedules_all_sources() -> None:
    plan = refresh_plan(first_refresh=True, refresh_count=1)

    assert plan.due == frozenset(Source)


@pytest.mark.parametrize(
    ("count", "needs_clients", "needs_policies", "pending", "expected"),
    [
        pytest.param(1, False, False, PendingRefreshes(), set(), id="idle-fast"),
        pytest.param(2, True, False, PendingRefreshes(), _MEDIUM | {Source.HOSTS}, id="medium"),
        pytest.param(
            3, False, False, PendingRefreshes(), _SLOW - {Source.HOST_POLICIES}, id="mesh-demand"
        ),
        pytest.param(
            1,
            False,
            True,
            PendingRefreshes(host_policies=0),
            {Source.HOST_POLICIES},
            id="pending-policy",
        ),
        pytest.param(
            1,
            False,
            False,
            PendingRefreshes(mesh=4, host_policies=2),
            {Source.HOSTS, Source.MESH_MEMBERS},
            id="pending-mesh-without-selectors",
        ),
        pytest.param(
            1,
            True,
            True,
            PendingRefreshes(
                full=2, update_check_pending=True, update_check_token=4, update_check_retry_token=4
            ),
            _MEDIUM | _SLOW | _VERY_SLOW,
            id="full-does-not-retry-firmware",
        ),
        pytest.param(
            15,
            True,
            True,
            PendingRefreshes(update_check_token=4, update_check_retry_token=4),
            _SLOW | _VERY_SLOW | {Source.CHECK_UPDATE},
            id="scheduled-firmware-retry",
        ),
        pytest.param(
            1,
            False,
            False,
            PendingRefreshes(
                update_check_pending=True, update_check_token=5, update_check_retry_token=4
            ),
            {Source.CHECK_UPDATE},
            id="newer-firmware-request",
        ),
        pytest.param(
            1440,
            True,
            True,
            PendingRefreshes(),
            _MEDIUM | _SLOW | _VERY_SLOW | {Source.CHECK_UPDATE},
            id="daily-firmware-check",
        ),
    ],
)
def test_refresh_plan_due(
    count: int,
    needs_clients: bool,
    needs_policies: bool,
    pending: PendingRefreshes,
    expected: set[Source],
) -> None:
    plan = refresh_plan(
        first_refresh=False,
        refresh_count=count,
        needs_clients=needs_clients,
        needs_policies=needs_policies,
        pending=pending,
    )

    assert isinstance(plan.due, frozenset)
    assert plan.due == _EVERY_TICK | expected
    assert plan.pending == pending
