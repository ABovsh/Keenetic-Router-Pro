"""Unit tests for coordinator normalization helpers."""

from __future__ import annotations

from custom_components.keenetic_router_pro.coordinator import (
    _counter_rate_bytes_per_second,
    _first_stat_int,
    _to_int,
)


def test_to_int_handles_router_strings_and_bad_values() -> None:
    """Keenetic RCI may return numeric counters as strings or invalid blanks."""
    assert _to_int("42") == 42
    assert _to_int(7) == 7
    assert _to_int(None) == 0
    assert _to_int("not-a-number") == 0


def test_first_stat_int_uses_first_non_empty_firmware_alias() -> None:
    """Stats aliases preserve the first populated firmware-specific key."""
    stats = {
        "rxbytes": "",
        "rx-bytes": "125",
        "rx_bytes": "999",
    }

    assert _first_stat_int(stats, "rxbytes", "rx-bytes", "rx_bytes") == 125
    assert _first_stat_int(stats, "missing") == 0


def test_counter_rate_clamps_reset_and_invalid_time() -> None:
    """Counter resets and zero/negative intervals produce stable zero rates."""
    assert _counter_rate_bytes_per_second(1500, "500", 10.0) == 100.0
    assert _counter_rate_bytes_per_second(100, 500, 10.0) == 0.0
    assert _counter_rate_bytes_per_second(1500, 500, 0.0) == 0.0
