"""Direct tests for coordinator helper modules."""

from __future__ import annotations

from datetime import datetime

from custom_components.keenetic_router_pro.coordinator_parts.oom import (
    advance_oom_state,
    parse_keenetic_log_ts,
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
