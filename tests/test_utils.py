"""Unit tests for utils helpers used by the integration."""

from __future__ import annotations

import pytest

from custom_components.keenetic_router_pro.utils import (
    client_display_name,
    coerce_bool,
    coerce_float,
    coerce_int,
    coerce_seconds,
    find_client_by_mac,
    find_mesh_node,
    iter_new_items,
    iter_tracked_clients,
    is_client_online,
    normalize_mac,
    parse_memory_fraction,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"),
        ("AA-BB-CC-DD-EE-FF", "aa:bb:cc:dd:ee:ff"),
        ("aabb.ccdd.eeff", "aa:bb:cc:dd:ee:ff"),
        ("aabbccddeeff", "aa:bb:cc:dd:ee:ff"),
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


def test_find_mesh_node_uses_index_hit() -> None:
    indexed = {"cid": "node-1", "name": "Kitchen"}
    data = {
        "mesh_nodes_by_cid": {"node-1": indexed},
        "mesh_nodes": [{"cid": "node-1", "name": "Fallback"}],
    }

    assert find_mesh_node(data, "node-1") is indexed


def test_find_mesh_node_linear_fallback() -> None:
    node = {"id": "node-2", "name": "Office"}
    data = {"mesh_nodes": ["bad", node]}

    assert find_mesh_node(data, "node-2") is node


def test_find_mesh_node_returns_none_for_missing_values() -> None:
    assert find_mesh_node({}, "node-1") is None
    assert find_mesh_node({"mesh_nodes": [{"cid": "node-1"}]}, "node-2") is None
    assert find_mesh_node({"mesh_nodes": [{"cid": "node-1"}]}, "") is None


def test_iter_new_items_skips_invalid_seen_and_missing_ids() -> None:
    coordinator = type(
        "Coordinator",
        (),
        {"data": {"items": ["bad", {}, {"id": "seen"}, {"id": "new"}]}},
    )()
    known = {"seen"}

    assert list(iter_new_items(coordinator, "items", known)) == [{"id": "new"}]


def test_iter_new_items_uses_fallback_id_keys() -> None:
    coordinator = type("Coordinator", (), {"data": {"nodes": [{"cid": "node-1"}]}})()

    assert list(iter_new_items(coordinator, "nodes", set(), ("cid", "id"))) == [
        {"cid": "node-1"}
    ]


def test_iter_tracked_clients_normalizes_and_deduplicates_macs() -> None:
    entry = type(
        "Entry",
        (),
        {
            "data": {
                "tracked_clients": [
                    "bad",
                    {"mac": "AA-BB-CC-DD-EE-FF", "name": "Laptop", "ip": "192.0.2.10"},
                    {"mac": "aa:bb:cc:dd:ee:ff", "name": "Duplicate"},
                    {"mac": "", "name": "Missing"},
                ]
            }
        },
    )()

    assert list(iter_tracked_clients(entry)) == [
        ("aa:bb:cc:dd:ee:ff", "Laptop", "192.0.2.10")
    ]


def test_iter_tracked_clients_defaults_label_to_uppercase_mac() -> None:
    entry = type(
        "Entry",
        (),
        {"data": {"tracked_clients": [{"mac": "aa:bb:cc:dd:ee:ff"}]}},
    )()

    assert list(iter_tracked_clients(entry)) == [
        ("aa:bb:cc:dd:ee:ff", "AA:BB:CC:DD:EE:FF", None)
    ]


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
    ("client", "fallback", "expected"),
    [
        ({"hostname": "phone.local", "name": "Phone - WiFi"}, "fallback", "phone.local"),
        ({"hostname": "", "name": "Phone - WiFi"}, "fallback", "Phone"),
        ({"hostname": "", "name": ""}, "fallback", "fallback"),
        (None, "fallback", "fallback"),
    ],
)
def test_client_display_name_branches(
    client: dict[str, object] | None, fallback: str, expected: str
) -> None:
    assert client_display_name(client, fallback) == expected


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
    ("client", "expected"),
    [
        ({"link": "up", "active": False}, True),
        ({"active": "yes"}, True),
        ({"active": "yes", "neighbour-expired": "yes"}, False),
        ({"active": "no"}, False),
        (None, False),
        ({"link": "Up"}, True),
        ({"link": "UP"}, True),
        ({"link": " up "}, True),
    ],
)
def test_is_client_online_contract(
    client: dict[str, object] | None, expected: bool
) -> None:
    assert is_client_online(client) is expected


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


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        ("12.5", None, 12.5),
        (12, None, 12.0),
        ("", None, None),
        (None, -1.0, -1.0),
        ("not-a-number", 7.5, 7.5),
    ],
)
def test_coerce_float_handles_loose_rci_values(
    raw: object, default: float | None, expected: float | None
) -> None:
    assert coerce_float(raw, default) == expected


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", float("nan"), float("inf"), float("-inf")])
def test_coerce_float_rejects_non_finite_values(raw: object) -> None:
    """NaN/inf must never reach the HA recorder — they break long-term stats."""
    assert coerce_float(raw, default=None) is None
    assert coerce_float(raw, default=0.0) == pytest.approx(0.0)


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", float("inf"), float("nan")])
def test_coerce_seconds_rejects_non_finite_values(raw: object) -> None:
    """``int(float('inf'))`` raises OverflowError; treat as missing."""
    assert coerce_seconds(raw, default=0) == 0
    assert coerce_seconds(raw, default=None) is None


def test_parse_memory_fraction_clamps_into_range() -> None:
    """Transient firmware payloads must not produce <0% or >100%."""
    assert parse_memory_fraction("1100/1000") == pytest.approx(100.0)
    assert parse_memory_fraction("-100/1000") == pytest.approx(0.0)
