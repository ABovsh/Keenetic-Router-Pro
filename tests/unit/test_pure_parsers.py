"""Golden tests for pure api.py parser/helper behavior."""

from __future__ import annotations

import pytest

from custom_components.keenetic_router_pro.api import (
    KeeneticApiError,
    KeeneticClient,
)

try:
    from custom_components.keenetic_router_pro.api.helpers import (
        _dict_items,
        _extract_parse_messages,
        _is_endpoint_missing,
        _nested_dict_items,
        _normalize_interfaces,
        _payload_summary,
        _response_summary,
        _to_int,
        _truthy,
        _validate_cli_arg,
    )
except ModuleNotFoundError:
    from custom_components.keenetic_router_pro.api import (
        _dict_items,
        _is_endpoint_missing,
        _nested_dict_items,
        _payload_summary,
        _response_summary,
        _to_int,
        _truthy,
        _validate_cli_arg,
    )

    def _normalize_interfaces(raw):
        return KeeneticClient("host", "user", "pass")._normalize_interfaces(raw)

    _extract_parse_messages = KeeneticClient._extract_parse_messages


def test_parse_dns_proxy_stat_golden_values() -> None:
    stat = """
      1.1.1.1 53 10 8 1 12ms 20ms 100
    9.9.9.9 853 5 0 2 0ms 4ms 50
    bad line
      2001:4860:4860::8888 53 3 3 0 7ms 9ms 80
    """

    assert KeeneticClient._parse_dns_proxy_stat(stat) == [
        {
            "ip": "1.1.1.1",
            "port": 53,
            "sent": 10,
            "answered": 8,
            "nxdomain": 1,
            "failed": 1,
            "median_ms": 12,
            "average_ms": 20,
            "rank": 100,
        },
        {
            "ip": "9.9.9.9",
            "port": 853,
            "sent": 5,
            "answered": 0,
            "nxdomain": 2,
            "failed": 3,
            "median_ms": 0,
            "average_ms": 4,
            "rank": 50,
        },
        {
            "ip": "2001:4860:4860::8888",
            "port": 53,
            "sent": 3,
            "answered": 3,
            "nxdomain": 0,
            "failed": 0,
            "median_ms": 7,
            "average_ms": 9,
            "rank": 80,
        },
    ]
    assert KeeneticClient._parse_dns_proxy_stat("") == []
    assert KeeneticClient._parse_dns_proxy_stat("bad line\nalso bad") == []


def test_parse_ipsec_vici_diagnostics_golden_values() -> None:
    lines = [
        "May 1 IpSec::Vici::Stats: out of memory",
        "May 2 IpSec::Vici::Stats: out of memory [ENOMEM]",
        "unrelated",
        "May 3 ipsec::vici::stats: out of memory [LOW]",
    ]

    assert KeeneticClient._parse_ipsec_vici_diagnostics(lines) == {
        "status": "warning",
        "vici_out_of_memory_count": 3,
        "last_vici_out_of_memory": "May 3 ipsec::vici::stats: out of memory [LOW]",
        "last_error_code": "LOW",
        "recent_matches": [
            "May 1 IpSec::Vici::Stats: out of memory",
            "May 2 IpSec::Vici::Stats: out of memory [ENOMEM]",
            "May 3 ipsec::vici::stats: out of memory [LOW]",
        ],
        "scanned_log_lines": 4,
    }
    assert KeeneticClient._parse_ipsec_vici_diagnostics(["unrelated"]) == {
        "status": "ok",
        "vici_out_of_memory_count": 0,
        "last_vici_out_of_memory": None,
        "last_error_code": None,
        "recent_matches": [],
        "scanned_log_lines": 1,
    }


def test_summarize_client_stats_golden_values() -> None:
    clients = [
        {
            "mac": "AA:BB",
            "active": True,
            "interface": {"name": "WifiMaster0/AccessPoint0"},
            "ssid": "Main",
        },
        {"mac": "CC:DD", "active": "no", "interface": "GigabitEthernet0"},
        {
            "mac": "EE:FF",
            "link": "up",
            "interface": {"id": "WifiMaster1/AccessPoint0"},
        },
        {
            "mac": "11:22",
            "system-mode": "extender",
            "ip": "192.168.1.2",
            "name": "Node",
            "active": True,
            "uptime": 123,
            "firmware": "4.2",
            "description": "Kitchen",
            "http-host": "node.local",
        },
    ]

    assert KeeneticClient.summarize_client_stats(clients) == {
        "connected": 2,
        "disconnected": 1,
        "total": 3,
        "per_ap": {"Main": 1, "WifiMaster1/AccessPoint0": 1},
        "extenders": [
            {
                "mac": "11:22",
                "ip": "192.168.1.2",
                "name": "Node",
                "mode": "extender",
                "active": True,
                "uptime": 123,
                "firmware": "4.2",
                "description": "Kitchen",
                "http_host": "node.local",
            }
        ],
        "extender_count": 1,
    }
    assert KeeneticClient.summarize_client_stats([]) == {
        "connected": 0,
        "disconnected": 0,
        "total": 0,
        "per_ap": {},
        "extenders": [],
        "extender_count": 0,
    }


def test_normalize_interfaces_golden_values() -> None:
    assert _normalize_interfaces(
        {"ISP": {"state": "up"}, "bad": "x", "LAN": {"id": "Bridge0"}}
    ) == [{"state": "up", "id": "ISP"}, {"id": "Bridge0"}]
    assert _normalize_interfaces([{"id": "A"}, "bad", {"id": "B"}]) == [
        {"id": "A"},
        {"id": "B"},
    ]
    assert _normalize_interfaces("bad") == []


def test_extract_parse_messages_golden_values() -> None:
    assert _extract_parse_messages(
        {
            "message": ["first\nsecond", {"text": "third"}],
            "ignored": {"msg": "ignored because message wins"},
        }
    ) == ["first", "second", "third"]
    assert _extract_parse_messages(
        [
            {"level": "I", "time": "12:00", "module": "Core", "msg": "ready"},
            {"nested": {"event": ["event1", "event2"]}},
        ]
    ) == ["I 12:00 Core ready", "event1", "event2"]
    assert _extract_parse_messages(None) == []


def test_helper_boundaries_golden_values() -> None:
    assert [_to_int("42"), _to_int("bad", 7), _to_int(None, -1)] == [42, 7, -1]
    assert [_truthy(True), _truthy("yes"), _truthy("off"), _truthy(2), _truthy(0), _truthy([])] == [
        True,
        True,
        False,
        True,
        False,
        False,
    ]
    assert _dict_items({"a": {"id": "a"}, "b": "bad"}) == [{"id": "a"}]
    assert _dict_items({"id": "self"}) == [{"id": "self"}]
    assert _dict_items([{"x": 1}, "bad"]) == [{"x": 1}]
    assert _nested_dict_items({"client": {"mac": "aa"}}, "client") == [{"mac": "aa"}]
    assert _nested_dict_items({"items": {"a": {"id": "a"}}}, "items") == [{"id": "a"}]
    assert _nested_dict_items("bad", "items") == []
    assert _payload_summary({"username": "admin", "data": [1], "count": 3}) == {
        "username": "<redacted>",
        "data": "list",
        "count": "int",
    }
    assert _payload_summary([1, 2]) == "list[2]"
    assert _payload_summary("abc") == "str"
    assert _payload_summary(None) is None
    assert (
        _response_summary('user=admin password=secret cookie="abc" tail', limit=200)
        == "user=admin password=<redacted> cookie=<redacted> tail"
    )
    assert _is_endpoint_missing(Exception("Not found")) is True
    assert _is_endpoint_missing(Exception("HTTP 404")) is True
    assert _is_endpoint_missing(Exception("Forbidden")) is False


@pytest.mark.parametrize("value", ["GigabitEthernet0", "192.168.1.1"])
def test_validate_cli_arg_accepts_golden_values(value: str) -> None:
    assert _validate_cli_arg(value, "token") == value


@pytest.mark.parametrize("value", ["bad value", " trim", "", None, "semi;colon"])
def test_validate_cli_arg_rejects_golden_values(value: str | None) -> None:
    with pytest.raises(KeeneticApiError):
        _validate_cli_arg(value, "token")
