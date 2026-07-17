"""Direct tests for coordinator helper modules."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.keenetic_router_pro.api import (
    KeeneticApiError,
    KeeneticAuthError,
)
from custom_components.keenetic_router_pro.coordinator_parts.derived import (
    build_clients_by_mac,
    counter_rate_bytes_per_second,
    mesh_associations,
    order_wan_interfaces,
)
from custom_components.keenetic_router_pro.coordinator_parts.enrichment import (
    enrich_crypto_maps,
    enrich_wan_interfaces,
)
from custom_components.keenetic_router_pro.coordinator_parts.fetching import (
    CRITICAL_FETCH_GRACE_TICKS,
    FetchFailure,
    critical_failures_to_exception,
    evaluate_critical_failures,
    next_backoff_interval,
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
            "ip": {"neighbour": {}, "hotspot": {}},
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
    # show/ip/hotspot is prefetched on every tick (fast, medium, slow, very-slow).
    for tree in (fast, medium, slow, very_slow):
        assert "hotspot" in tree["show"]["ip"]


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


def test_evaluate_critical_failures_ok_resets_streak() -> None:
    """No critical failure clears any prior transient streak."""
    decision = evaluate_critical_failures([], have_previous_data=True, streak=2)
    assert decision.action == "ok"
    assert decision.streak == 0


def test_evaluate_critical_failures_auth_fails_immediately() -> None:
    """Auth rejection on a critical fetch never enters the grace window."""
    failures = [FetchFailure("system_info", KeeneticAuthError("bad auth"))]
    decision = evaluate_critical_failures(
        failures, have_previous_data=True, streak=0
    )
    assert decision.action == "auth"


def test_evaluate_critical_failures_tolerates_transient_timeout() -> None:
    """A single timeout with last-known data is tolerated, not fatal."""
    failures = [
        FetchFailure("system_info", KeeneticApiError("Timeout for /rci/show/system"))
    ]
    decision = evaluate_critical_failures(
        failures, have_previous_data=True, streak=0, grace_ticks=3
    )
    assert decision.action == "tolerate"
    assert decision.streak == 1


def test_evaluate_critical_failures_fails_after_grace_exhausted() -> None:
    """Once the grace window is spent the coordinator must fail for real."""
    failures = [
        FetchFailure("system_info", KeeneticApiError("Timeout for /rci/show/system"))
    ]
    decision = evaluate_critical_failures(
        failures, have_previous_data=True, streak=3, grace_ticks=3
    )
    assert decision.action == "fail"
    assert decision.streak == 4


def test_evaluate_critical_failures_no_previous_data_fails_immediately() -> None:
    """Without a snapshot to keep, there is nothing to tolerate."""
    failures = [FetchFailure("interfaces", RuntimeError("boom"))]
    decision = evaluate_critical_failures(
        failures, have_previous_data=False, streak=0
    )
    assert decision.action == "fail"
    assert decision.streak == 1


def test_critical_fetch_grace_ticks_is_positive() -> None:
    """The shipped grace window must allow at least one tolerated tick."""
    assert CRITICAL_FETCH_GRACE_TICKS >= 1


# --- next_backoff_interval (adaptive poll backoff) ---


def test_next_backoff_interval_first_failed_tick_is_60s() -> None:
    """The first UpdateFailed tick after grace exhaustion stretches to 60s."""
    assert next_backoff_interval(1) == 60


def test_next_backoff_interval_second_failed_tick_caps_at_120s() -> None:
    """The second consecutive failed tick stretches further, to the 120s cap."""
    assert next_backoff_interval(2) == 120


def test_next_backoff_interval_stays_capped_for_further_failed_ticks() -> None:
    """A long-running outage does not keep stretching past the 120s cap."""
    assert next_backoff_interval(3) == 120
    assert next_backoff_interval(50) == 120


def test_next_backoff_interval_zero_or_negative_treated_as_first() -> None:
    """A defensive floor: non-positive counts still return the first-step value."""
    assert next_backoff_interval(0) == 60


# --- enrich_wan_interfaces ---


def test_enrich_wan_interfaces_computes_throughput_delta() -> None:
    """Throughput is a delta against the previous tick's byte counters."""
    wan_interfaces = [{"id": "PPPoE0", "defaultgw": True, "priority": 100}]
    interface_stats = {"PPPoE0": {"rxbytes": 2000, "txbytes": 3000}}
    prev = [{"id": "PPPoE0", "rx_bytes": 1000, "tx_bytes": 1000, "_sample_ts": 100.0}]

    ordered, wan_by_id = enrich_wan_interfaces(
        wan_interfaces, interface_stats, {}, prev, now_ts=110.0
    )

    assert ordered[0]["rx_throughput"] == pytest.approx(100.0)
    assert ordered[0]["tx_throughput"] == pytest.approx(200.0)
    assert wan_by_id["PPPoE0"] is ordered[0]


def test_enrich_wan_interfaces_first_sample_has_zero_throughput() -> None:
    """No previous sample -> throughput is 0.0, not a spike."""
    wan_interfaces = [{"id": "PPPoE0", "defaultgw": True, "priority": 100}]
    interface_stats = {"PPPoE0": {"rxbytes": 2000, "txbytes": 3000}}

    ordered, _ = enrich_wan_interfaces(
        wan_interfaces, interface_stats, {}, [], now_ts=110.0
    )

    assert ordered[0]["rx_throughput"] == 0.0
    assert ordered[0]["tx_throughput"] == 0.0


def test_enrich_wan_interfaces_counter_reset_clamps_rate_zero() -> None:
    """A counter reset (current < previous) must clamp to 0, not go negative."""
    wan_interfaces = [{"id": "PPPoE0", "defaultgw": True, "priority": 100}]
    interface_stats = {"PPPoE0": {"rxbytes": 100, "txbytes": 100}}
    prev = [{"id": "PPPoE0", "rx_bytes": 5000, "tx_bytes": 5000, "_sample_ts": 100.0}]

    ordered, _ = enrich_wan_interfaces(
        wan_interfaces, interface_stats, {}, prev, now_ts=110.0
    )

    assert ordered[0]["rx_throughput"] == 0.0
    assert ordered[0]["tx_throughput"] == 0.0


def test_enrich_wan_interfaces_ping_check_true_overrides_internet_access() -> None:
    """passing=True -> internet_access=True, sourced from ping_check."""
    wan_interfaces = [{"id": "PPPoE0", "internet_access": False}]
    ping_check_status = {"PPPoE0": {"passing": True}}

    ordered, _ = enrich_wan_interfaces(
        wan_interfaces, {}, ping_check_status, [], now_ts=1.0
    )

    assert ordered[0]["internet_access"] is True
    assert ordered[0]["internet_access_source"] == "ping_check"


def test_enrich_wan_interfaces_ping_check_false_overrides_internet_access() -> None:
    """passing=False -> internet_access=False (real outage case)."""
    wan_interfaces = [{"id": "PPPoE0", "internet_access": True}]
    ping_check_status = {"PPPoE0": {"passing": False}}

    ordered, _ = enrich_wan_interfaces(
        wan_interfaces, {}, ping_check_status, [], now_ts=1.0
    )

    assert ordered[0]["internet_access"] is False
    assert ordered[0]["internet_access_source"] == "ping_check"


def test_enrich_wan_interfaces_ping_check_none_keeps_heuristic() -> None:
    """passing=None (mixed/no profile) -> keep the heuristic value untouched."""
    wan_interfaces = [{"id": "PPPoE0", "internet_access": True}]
    ping_check_status = {"PPPoE0": {"passing": None}}

    ordered, _ = enrich_wan_interfaces(
        wan_interfaces, {}, ping_check_status, [], now_ts=1.0
    )

    assert ordered[0]["internet_access"] is True
    assert ordered[0]["internet_access_source"] == "heuristic"


def test_enrich_wan_interfaces_ping_check_absent_keeps_heuristic() -> None:
    """No ping-check entry at all for this WAN -> heuristic source, ping_check None."""
    wan_interfaces = [{"id": "PPPoE0", "internet_access": True}]

    ordered, _ = enrich_wan_interfaces(wan_interfaces, {}, {}, [], now_ts=1.0)

    assert ordered[0]["internet_access"] is True
    assert ordered[0]["internet_access_source"] == "heuristic"
    assert ordered[0]["ping_check"] is None


# --- enrich_crypto_maps ---


def test_enrich_crypto_maps_computes_throughput_delta_on_refresh() -> None:
    """On an ipsec-status-refresh tick, throughput is a delta against previous."""
    crypto_maps = {"SITE": {"rx_bytes": 2000, "tx_bytes": 3000}}
    prev = {"SITE": {"rx_bytes": 1000, "tx_bytes": 1000, "_sample_ts": 100.0}}

    enrich_crypto_maps(crypto_maps, prev, now_ts=110.0, ipsec_status_refresh=True)

    assert crypto_maps["SITE"]["rx_throughput"] == pytest.approx(100.0)
    assert crypto_maps["SITE"]["tx_throughput"] == pytest.approx(200.0)
    assert crypto_maps["SITE"]["_sample_ts"] == 110.0


def test_enrich_crypto_maps_counter_reset_clamps_rate_zero() -> None:
    """A rekeyed/bounced tunnel resets counters — clamp to 0, never negative."""
    crypto_maps = {"SITE": {"rx_bytes": 50, "tx_bytes": 50}}
    prev = {"SITE": {"rx_bytes": 9000, "tx_bytes": 9000, "_sample_ts": 100.0}}

    enrich_crypto_maps(crypto_maps, prev, now_ts=110.0, ipsec_status_refresh=True)

    assert crypto_maps["SITE"]["rx_throughput"] == 0.0
    assert crypto_maps["SITE"]["tx_throughput"] == 0.0


def test_enrich_crypto_maps_non_refresh_tick_preserves_previous_throughput() -> None:
    """When the ipsec status tier is skipped, reuse the previous throughput verbatim."""
    crypto_maps = {"SITE": {"rx_bytes": 2000, "tx_bytes": 3000}}
    prev = {
        "SITE": {
            "rx_bytes": 1000,
            "tx_bytes": 1000,
            "_sample_ts": 100.0,
            "rx_throughput": 42.0,
            "tx_throughput": 84.0,
        }
    }

    enrich_crypto_maps(crypto_maps, prev, now_ts=110.0, ipsec_status_refresh=False)

    assert crypto_maps["SITE"]["rx_throughput"] == 42.0
    assert crypto_maps["SITE"]["tx_throughput"] == 84.0
    assert crypto_maps["SITE"]["_sample_ts"] == 100.0


def test_enrich_crypto_maps_first_sample_has_zero_throughput() -> None:
    """No previous crypto-map snapshot -> throughput is 0.0."""
    crypto_maps = {"SITE": {"rx_bytes": 2000, "tx_bytes": 3000}}

    enrich_crypto_maps(crypto_maps, None, now_ts=110.0, ipsec_status_refresh=True)

    assert crypto_maps["SITE"]["rx_throughput"] == 0.0
    assert crypto_maps["SITE"]["tx_throughput"] == 0.0
