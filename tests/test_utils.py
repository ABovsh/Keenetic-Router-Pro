"""Unit tests for utils helpers used by the integration."""

from __future__ import annotations

import pytest

from custom_components.keenetic_router_pro.utils import (
    coerce_bool,
    coerce_int,
    coerce_seconds,
    find_client_by_mac,
    normalize_mac,
    parse_memory_fraction,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"),
        ("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"),
        ("", ""),
        (None, ""),
        (0, ""),  # falsy non-string
    ],
)
def test_normalize_mac(raw: object, expected: str) -> None:
    assert normalize_mac(raw) == expected


def test_find_client_by_mac_returns_match_case_insensitive() -> None:
    clients = [
        {"mac": "AA:BB:CC:00:00:01", "name": "phone"},
        {"mac": "aa:bb:cc:00:00:02", "name": "laptop"},
    ]
    assert find_client_by_mac(clients, "aa:bb:cc:00:00:02")["name"] == "laptop"
    assert find_client_by_mac(clients, "AA:BB:CC:00:00:01")["name"] == "phone"


def test_find_client_by_mac_returns_none_for_misses_and_empty_inputs() -> None:
    assert find_client_by_mac([], "aa:bb:cc:00:00:01") is None
    assert find_client_by_mac(None, "aa:bb:cc:00:00:01") is None
    assert find_client_by_mac([{"mac": "aa:bb:cc:00:00:01"}], "") is None
    assert find_client_by_mac([{"mac": "aa:bb:cc:00:00:01"}], "ff:ff:ff:ff:ff:ff") is None


def test_find_client_by_mac_skips_non_dict_entries() -> None:
    """Defensive: router payloads occasionally contain stray strings."""
    clients = ["not-a-client", {"mac": "aa:bb:cc:00:00:01", "name": "ok"}]
    assert find_client_by_mac(clients, "aa:bb:cc:00:00:01")["name"] == "ok"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("512/1024", 50.0),
        ("256/1024", 25.0),
        ("0/1024", 0.0),
        ("1024/1024", 100.0),
        ("not a fraction", None),
        ("", None),
        (None, None),
        (1024, None),  # not a string
        ("512/0", None),  # zero total
        ("abc/def", None),
    ],
)
def test_parse_memory_fraction(raw: object, expected: float | None) -> None:
    assert parse_memory_fraction(raw) == expected


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        ("123", 0, 123),
        ("123.7", 0, 123),
        (123, 0, 123),
        ("", 0, 0),
        ("unknown", 0, 0),
        ("Unknown", 0, 0),
        (None, 0, 0),
        ("not-a-number", 0, 0),
        ("", None, None),
    ],
)
def test_coerce_seconds(raw: object, default: int | None, expected: int | None) -> None:
    assert coerce_seconds(raw, default) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        ("yes", True),
        ("up", True),
        ("online", True),
        ("no", False),
        ("down", False),
        ("false", False),
        ("", False),
        (None, False),
    ],
)
def test_coerce_bool_matches_keenetic_payload_values(raw: object, expected: bool) -> None:
    assert coerce_bool(raw) is expected


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        ("123", 0, 123),
        (123, 0, 123),
        ("", -1, -1),
        (None, -1, -1),
        ("not-a-number", 7, 7),
    ],
)
def test_coerce_int_handles_loose_rci_values(
    raw: object, default: int, expected: int
) -> None:
    assert coerce_int(raw, default) == expected
