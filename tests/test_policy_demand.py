"""Policy reads follow the policy selectors setup creates.

The select platform creates one policy selector per tracked client. An entry
without tracked clients has no selector, so its coordinator skips both policy
reads while still publishing the (empty) keys.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import custom_components.keenetic_router_pro as integration
from custom_components.keenetic_router_pro.const import CONF_TRACKED_CLIENTS
from tests.test_coordinator_stages import StageFixtureClient, _coordinator, _updated_data

_POLICY_CALLS = ("host_policies", "policies")


async def _run_window(coordinator, client, ticks: int) -> dict:
    data = await _updated_data(coordinator)
    for _ in range(ticks):
        coordinator.data = data
        data = await _updated_data(coordinator)
    return data


async def test_entry_without_selectors_makes_no_policy_reads() -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    coordinator.needs_policies = False

    data = await _run_window(coordinator, client, 15)
    coordinator.data = data
    coordinator._full_refresh_token = 0
    coordinator._host_policies_refresh_token = 0
    coordinator.request_full_refresh()
    coordinator.request_host_policies_refresh()
    data = await _updated_data(coordinator)

    assert {name: client.calls.get(name, 0) for name in _POLICY_CALLS} == {
        "host_policies": 0,
        "policies": 0,
    }
    assert data["host_policies"] == {}
    assert data["policies"] == {}
    assert client.calls["clients"] >= 1


async def test_entry_with_selectors_keeps_the_scheduled_policy_reads() -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    coordinator.needs_policies = True

    await _updated_data(coordinator)
    assert client.calls.get("host_policies", 0) == 1
    assert client.calls.get("policies", 0) == 1


@pytest.mark.parametrize(
    ("tracked", "expected"),
    [
        ([], False),
        ([{"mac": "not-a-mac"}], False),
        ([{"mac": "AA-BB-CC-DD-EE-FF"}, {"mac": "aa:bb:cc:dd:ee:ff"}], True),
    ],
)
def test_policy_demand_matches_the_selector_records(tracked, expected) -> None:
    entry = SimpleNamespace(data={CONF_TRACKED_CLIENTS: tracked}, options={})
    assert integration._needs_policies(entry) is expected
