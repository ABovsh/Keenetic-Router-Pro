"""Unit tests for coordinator normalization helpers."""

from __future__ import annotations

import pytest

from custom_components.keenetic_router_pro.coordinator import (
    _counter_rate_bytes_per_second,
    _first_stat_int,
)
from custom_components.keenetic_router_pro.utils import coerce_int


def test_coerce_int_handles_router_strings_and_bad_values() -> None:
    """Keenetic RCI may return numeric counters as strings or invalid blanks."""
    assert coerce_int("42") == 42
    assert coerce_int(7) == 7
    assert coerce_int(None) == 0
    assert coerce_int("not-a-number") == 0


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
    assert _counter_rate_bytes_per_second(1500, "500", 10.0) == pytest.approx(100.0)
    assert _counter_rate_bytes_per_second(100, 500, 10.0) == pytest.approx(0.0)
    assert _counter_rate_bytes_per_second(1500, 500, 0.0) == pytest.approx(0.0)
