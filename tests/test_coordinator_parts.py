"""Direct tests for coordinator helper modules."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.keenetic_router_pro.api import KeeneticAuthError
from custom_components.keenetic_router_pro.coordinator_parts.derived import (
    build_clients_by_mac,
    counter_rate_bytes_per_second,
    mesh_associations,
    order_wan_interfaces,
)
from custom_components.keenetic_router_pro.coordinator_parts.fetching import (
    FetchFailure,
    critical_failures_to_exception,
    ok_or_default,
)
from custom_components.keenetic_router_pro.coordinator_parts.oom import (
    advance_oom_state,
    parse_keenetic_log_ts,
)
from custom_components.keenetic_router_pro.coordinator_parts.payloads import (
    dict_or_empty,
    list_or_empty,
    merge_clients_with_neighbours,
)
from custom_components.keenetic_router_pro.coordinator_parts.refresh import (
    RefreshPlan,
    build_batch_tree,
    refresh_plan,
)


def test_parse_keenetic_log_ts_handles_single_and_double_space_days() -> None:
    """Keenetic syslog may pad single-digit days with one or two spaces."""
    now = datetime(2026, 5, 28, 12, 0, 0)

    assert parse_keenetic_log_ts("May 1 12:00:00", now=now) == datetime(
        2026, 5, 1, 12, 0, 0
    )
    assert parse_keenetic_log_ts("May  1 12:00:00", now=now) == datetime(
        2026, 5, 1, 12, 0, 0
    )


def test_advance_oom_state_counts_duplicate_timestamps_once_across_repeated_advancement() -> None:
    """Repeated overlapping windows must not recount the same timestamp bucket."""
    now = datetime(2026, 5, 1, 12, 1, 0)
    events = [
        ("May 1 12:00:00", "IpSec::Vici::Stats: out of memory [0xcffe02a7]"),
        ("May 1 12:00:00", "IpSec::Vici::Stats: out of memory [0xcffe02a7]"),
    ]

    first = advance_oom_state(
        {"last_seen_iso": None, "last_seen_count": 0, "total": 0},
        events,
        now=now,
    )
    second = advance_oom_state(first, events, now=now)

    assert first == {
        "last_seen_iso": "2026-05-01T12:00:00",
        "last_seen_count": 2,
        "total": 2,
    }
    assert second == first


@pytest.mark.parametrize("value", [[], None, "bad", 0, object()])
def test_dict_or_empty_defaults_malformed_payloads(value: object) -> None:
    assert dict_or_empty(value) == {}


@pytest.mark.parametrize("value", [{}, None, "bad", 0, object()])
def test_list_or_empty_defaults_malformed_payloads(value: object) -> None:
    assert list_or_empty(value) == []


def test_dict_or_empty_and_list_or_empty_preserve_valid_payloads() -> None:
    dict_payload = {"ok": True}
    list_payload = [1, 2]

    assert dict_or_empty(dict_payload) is dict_payload
    assert list_or_empty(list_payload) is list_payload


def test_merge_clients_with_neighbours_preserves_neighbour_ip_and_data() -> None:
    client = {"mac": "AA-BB-CC-DD-EE-FF", "ip": None, "active": False}
    neighbour = {
        "mac": "aa:bb:cc:dd:ee:ff",
        "address-family": "ipv4",
        "address": "192.0.2.55",
        "last-seen": 42,
        "first-seen": 10,
        "expired": False,
        "wireless": True,
        "leasetime": 3600,
        "via": "WifiMaster0/AccessPoint0",
    }

    merged = merge_clients_with_neighbours([client], [neighbour])

    assert merged == [
        {
            **client,
            "ip": "192.0.2.55",
            "neighbour": neighbour,
            "last-seen": 42,
            "last-seen-source": "neighbour",
            "first-seen": 10,
            "first-seen-source": "neighbour",
            "neighbour-expired": False,
            "neighbour-wireless": True,
            "neighbour-leasetime": 3600,
        }
    ]


def test_counter_rate_bytes_per_second_clamps_resets_and_computes_rates() -> None:
    assert counter_rate_bytes_per_second(1500, "500", 10.0) == pytest.approx(100.0)
    assert counter_rate_bytes_per_second(100, 500, 10.0) == pytest.approx(0.0)
    assert counter_rate_bytes_per_second(1500, 500, 0.0) == pytest.approx(0.0)


def test_build_clients_by_mac_normalizes_mac_keys() -> None:
    client = {"mac": "AA-BB-CC-DD-EE-FF", "name": "phone"}

    assert build_clients_by_mac(["bad", {"name": "missing"}, client]) == {
        "aa:bb:cc:dd:ee:ff": client
    }


def test_mesh_associations_counts_total_and_by_node() -> None:
    assert mesh_associations(
        [
            {"cid": "node-a", "associations": "2"},
            "bad",
            {"id": "node-b", "associations": "bad"},
            {"associations": 5},
            {"cid": "node-c", "associations": 3},
        ]
    ) == {
        "total": 5,
        "by_node": {"node-a": 2, "node-b": 0, "node-c": 3},
    }


def test_order_wan_interfaces_orders_default_then_backups_and_assigns_role_labels() -> None:
    backup_low = {"id": "BackupLow", "defaultgw": False, "priority": "10"}
    default = {"id": "Default", "defaultgw": True, "priority": "100"}
    backup_high = {"id": "BackupHigh", "defaultgw": False, "priority": "80"}

    ordered = order_wan_interfaces([backup_low, default, backup_high])

    assert [wan["id"] for wan in ordered] == ["Default", "BackupHigh", "BackupLow"]
    assert [wan["role_label"] for wan in ordered] == [
        "Default connection",
        "Backup connection 1",
        "Backup connection 2",
    ]
    assert [wan["role_index"] for wan in ordered] == [0, 1, 2]


def test_refresh_plan_first_refresh_runs_all_tiers() -> None:
    assert refresh_plan(first_refresh=True, refresh_count=17) == RefreshPlan(
        first_refresh=True,
        medium_refresh=True,
        slow_refresh=True,
        very_slow_refresh=True,
        ipsec_status_refresh=True,
    )


@pytest.mark.parametrize(
    ("refresh_count", "medium", "slow", "very_slow"),
    [
        (1, False, False, False),
        (2, False, False, False),
        (3, True, False, False),
        (6, True, True, False),
        (30, True, True, True),
    ],
)
def test_refresh_plan_runtime_efficiency_tiers(
    refresh_count: int,
    medium: bool,
    slow: bool,
    very_slow: bool,
) -> None:
    plan = refresh_plan(first_refresh=False, refresh_count=refresh_count)

    assert plan.first_refresh is False
    assert plan.medium_refresh is medium
    assert plan.slow_refresh is slow
    assert plan.very_slow_refresh is very_slow
    assert plan.ipsec_status_refresh is slow


def test_build_batch_tree_includes_only_active_tier_paths() -> None:
    fast = build_batch_tree(
        RefreshPlan(False, False, False, False, False)
    )
    medium = build_batch_tree(
        RefreshPlan(False, True, False, False, False)
    )
    slow = build_batch_tree(
        RefreshPlan(False, True, True, False, True)
    )
    very_slow = build_batch_tree(
        RefreshPlan(False, True, True, True, True)
    )

    assert fast == {
        "show": {
            "system": {},
            "interface": {},
            "ip": {"neighbour": {}},
        }
    }
    assert "ping-check" not in fast["show"]
    assert "ipsec" not in fast["show"]
    assert "ping-check" in medium["show"]
    assert "ipsec" not in medium["show"]
    assert "ipsec" in slow["show"]
    assert "components" not in slow
    assert very_slow["components"] == {"check-update": {}}
    assert "ndns" in very_slow["show"]
    assert "dns-proxy" in very_slow["show"]


def test_ok_or_default_reraises_cancelled_error() -> None:
    """Fetch cancellation must never be converted to a fallback payload."""
    with pytest.raises(asyncio.CancelledError):
        ok_or_default("clients", asyncio.CancelledError(), [], [])


def test_ok_or_default_records_non_silent_fetch_failure() -> None:
    """Non-silent failures are tracked for aggregate warnings and critical checks."""
    failed_fetches: list[FetchFailure] = []
    error = RuntimeError("boom")

    assert ok_or_default("clients", error, [], failed_fetches) == []
    assert failed_fetches == [FetchFailure("clients", error)]


def test_critical_failures_map_auth_to_config_entry_auth_failed() -> None:
    """Auth failures on critical fetches should start HA reauth."""
    failures = [FetchFailure("system_info", KeeneticAuthError("bad auth"))]

    with pytest.raises(ConfigEntryAuthFailed):
        critical_failures_to_exception(failures)


def test_critical_failures_map_non_auth_to_update_failed() -> None:
    """Non-auth critical failures should mark the coordinator refresh failed."""
    failures = [FetchFailure("interfaces", RuntimeError("boom"))]

    with pytest.raises(UpdateFailed):
        critical_failures_to_exception(failures)


def test_non_critical_failures_do_not_raise() -> None:
    """Optional endpoint failures remain fallback-only."""
    critical_failures_to_exception([FetchFailure("dns_proxy", RuntimeError("boom"))])
