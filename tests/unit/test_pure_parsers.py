"""Golden tests for pure api.py parser/helper behavior."""

from __future__ import annotations

from tests.conftest import TEST_HOST, TEST_HOST_ALT, TEST_PASSWORD, TEST_USERNAME

import asyncio
from unittest.mock import AsyncMock

import pytest

from custom_components.keenetic_router_pro.api import (
    KeeneticApiError,
    KeeneticClient,
)
from custom_components.keenetic_router_pro.utils import coerce_bool, first_present

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
        _validate_cli_arg,
        iface_label,
    )
except ModuleNotFoundError:
    from custom_components.keenetic_router_pro.api import (
        _dict_items,
        _is_endpoint_missing,
        _nested_dict_items,
        _payload_summary,
        _response_summary,
        _to_int,
        _validate_cli_arg,
    )

    def _normalize_interfaces(raw):
        return KeeneticClient("host", "user", "pass")._normalize_interfaces(raw)

    _extract_parse_messages = KeeneticClient._extract_parse_messages
    iface_label = lambda iface, iface_id=None: str(  # noqa: E731
        iface.get("description")
        or iface.get("interface-name")
        or iface_id
        or iface.get("id")
    )


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


def test_first_present_returns_first_non_empty_value() -> None:
    data = {"a": None, "b": "", "c": 0, "d": "value"}

    assert first_present(data, "a", "b", "c", "d") == 0
    assert first_present(data, "missing", "d") == "value"
    assert first_present(data, "a", "b", default="fallback") == "fallback"


def test_parse_dns_proxy_stat_edge_values() -> None:
    stat = """
    8.8.8.8 53 2 4 1 5ms 6ms 10
    not enough columns
    2001:db8::53 853 11 10 1 13ms 15ms 40
    """

    assert KeeneticClient._parse_dns_proxy_stat(None) == []
    assert KeeneticClient._parse_dns_proxy_stat(stat) == [
        {
            "ip": "8.8.8.8",
            "port": 53,
            "sent": 2,
            "answered": 4,
            "nxdomain": 1,
            "failed": 0,
            "median_ms": 5,
            "average_ms": 6,
            "rank": 10,
        },
        {
            "ip": "2001:db8::53",
            "port": 853,
            "sent": 11,
            "answered": 10,
            "nxdomain": 1,
            "failed": 0,
            "median_ms": 13,
            "average_ms": 15,
            "rank": 40,
        },
    ]


def test_dns_proxy_status_normalizes_malformed_and_single_doh_payloads() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(
        return_value={
            "proxy-status": [
                {
                    "proxy-name": "main",
                    "proxy-config": "server https://dns.example/dns-query",
                    "proxy-stat": "1.1.1.1 53 3 1 0 7ms 9ms 50\nbad",
                    "proxy-https": {
                        "server-https": {"uri": "https://dns.example/dns-query"}
                    },
                },
                "bad",
                {"proxy-name": "empty", "proxy-stat": ""},
            ]
        }
    )

    status = asyncio.run(client.async_get_dns_proxy_status())

    assert status["status"] == "degraded"
    assert status["proxy_count"] == 2
    assert status["doh_server_count"] == 1
    assert status["dns_server_count"] == 1
    assert status["requests_sent"] == 3
    assert status["failed_requests"] == 2
    assert status["client_path_uses_doh"] is True


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


def test_parse_ipsec_vici_diagnostics_empty_and_malformed_values() -> None:
    assert KeeneticClient._parse_ipsec_vici_diagnostics([]) == {
        "status": "ok",
        "vici_out_of_memory_count": 0,
        "last_vici_out_of_memory": None,
        "last_error_code": None,
        "recent_matches": [],
        "scanned_log_lines": 0,
    }
    assert KeeneticClient._parse_ipsec_vici_diagnostics([None, 7, {"msg": "x"}]) == {
        "status": "ok",
        "vici_out_of_memory_count": 0,
        "last_vici_out_of_memory": None,
        "last_error_code": None,
        "recent_matches": [],
        "scanned_log_lines": 3,
    }


def test_async_get_crypto_maps_normalizes_single_phase2_sa_and_placeholders() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(
        return_value={
            "crypto_map": {
                "Office": {
                    "config": {
                        "enabled": "yes",
                        "remote_peer": " 198.51.100.10 ",
                        "mode": " tunnel ",
                        "crypto_ipsec_profile_name": " profile0 ",
                    },
                    "status": {
                        "state": "PHASE2_ESTABLISHED",
                        "ike_state": " IKE_ESTABLISHED ",
                        "via": " PPPoE0 ",
                        "local-endpoint-address": "0.0.0.0",
                        "remote-endpoint-address": "::",
                        "phase2_sa_list": {
                            "phase2_sa": {
                                "in_bytes": "10",
                                "out_bytes": "bad",
                                "in_packets": "3",
                                "out_packets": None,
                            }
                        },
                    },
                },
                "bad": "ignored",
            }
        }
    )

    maps = asyncio.run(client.async_get_crypto_maps())

    assert maps["Office"]["enabled"] is True
    assert maps["Office"]["connected"] is True
    assert maps["Office"]["remote_peer"] == "198.51.100.10"
    assert maps["Office"]["mode"] == "tunnel"
    assert maps["Office"]["ipsec_profile_name"] == "profile0"
    assert maps["Office"]["local_endpoint"] is None
    assert maps["Office"]["remote_endpoint"] is None
    assert maps["Office"]["rx_bytes"] == 10
    assert maps["Office"]["tx_bytes"] == 0
    assert maps["Office"]["rx_packets"] == 3
    assert maps["Office"]["tx_packets"] == 0
    assert maps["Office"]["phase2_sa_list"] == [
        {
            "in_bytes": "10",
            "out_bytes": "bad",
            "in_packets": "3",
            "out_packets": None,
        }
    ]
    assert "bad" not in maps


def test_async_get_crypto_maps_rejects_malformed_payloads() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(return_value=None)
    assert asyncio.run(client.async_get_crypto_maps()) == {}

    client._rci_get = AsyncMock(return_value={"crypto_map": []})
    assert asyncio.run(client.async_get_crypto_maps()) == {}


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
            "ip": TEST_HOST_ALT,
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
                "ip": TEST_HOST_ALT,
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


def test_summarize_client_stats_exhaustive_edge_values() -> None:
    clients = [
        {"mac": "AA", "active": "yes", "interface": None},
        {"mac": "BB", "active": "0", "interface": {"id": "AP0"}},
        {"mac": "CC", "link": "up", "interface": {"name": "AP Name"}},
        {"mac": "DD", "link": "down", "ssid": "Guest"},
        {"mac": "EE", "system-mode": "repeater", "hostname": "Repeater"},
    ]

    summary = KeeneticClient.summarize_client_stats(clients)

    assert summary["connected"] == 2
    assert summary["disconnected"] == 2
    assert summary["total"] == 4
    assert summary["per_ap"] == {"Unknown": 1, "AP Name": 1}
    assert summary["extender_count"] == 1
    assert summary["extenders"][0]["name"] == "Repeater"
    assert summary["extenders"][0]["mode"] == "repeater"


def test_async_get_wan_status_prefers_pppoe_and_normalizes_interface_dict() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    async def fake_interfaces():
        return {
            "GigabitEthernet0": {
                "state": "up",
                "security-level": "public",
                "global-address": [{"address": "198.51.100.20/24"}],
            },
            "PPPoE0": {
                "type": "PPPoE",
                "state": "up",
                "address": "203.0.113.10/32",
                "via": "GigabitEthernet0",
            },
        }

    client.async_get_interfaces = fake_interfaces  # type: ignore[assignment]

    status = asyncio.run(client.async_get_wan_status())

    assert status["status"] == "connected"
    assert status["type"] == "pppoe"
    assert status["interface"] == "PPPoE0"
    assert status["ip"] == "203.0.113.10"


def test_async_get_wan_status_reports_link_up_without_ip() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    status = asyncio.run(
        client.async_get_wan_status(
            iface_list=[
                {
                    "id": "ISP",
                    "state": "up",
                    "security-level": "public",
                    "description": "Broadband",
                }
            ]
        )
    )

    assert status["status"] == "link_up"
    assert status["ip"] is None
    assert status["interface"] == "ISP"


def test_async_get_wan_interfaces_normalizes_multi_interface_payload() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    interfaces = {
        "Bridge0": {"role": ["home"], "global": True},
        "PPPoE0": {
            "type": "PPPoE",
            "state": "up",
            "role": "inet",
            "global": True,
            "defaultgw": True,
            "priority": 100,
            "address": "203.0.113.10/32",
            "summary": {"layer": {"conf": "running", "ipv4": "running"}},
        },
        "Wireguard0": {
            "type": "Wireguard",
            "state": "up",
            "global": True,
            "priority": 10,
            "summary": {"layer": {"conf": "disabled", "ipv4": "pending"}},
        },
        "GigabitEthernet0": {
            "type": "Ethernet",
            "state": "up",
            "global": False,
            "via": "PPPoE0",
        },
    }

    wans = asyncio.run(client.async_get_wan_interfaces(interfaces=interfaces))

    assert [wan["id"] for wan in wans] == ["PPPoE0", "Wireguard0"]
    assert wans[0]["ip"] == "203.0.113.10"
    assert wans[0]["role"] == ["inet"]
    assert wans[0]["enabled"] is True
    assert wans[0]["internet_access"] is True
    assert wans[1]["enabled"] is False
    assert wans[1]["internet_access"] is None


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
    assert [coerce_bool(True), coerce_bool("yes"), coerce_bool("off"), coerce_bool(2), coerce_bool(0), coerce_bool([])] == [
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
    assert _payload_summary({"username": TEST_USERNAME, "data": [1], "count": 3}) == {
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
    assert _is_endpoint_missing(RuntimeError("Not found")) is True
    assert _is_endpoint_missing(RuntimeError("HTTP 404")) is True
    assert _is_endpoint_missing(RuntimeError("Forbidden")) is False


def test_iface_label_prefers_description_before_interface_name() -> None:
    assert iface_label({"description": "ISP", "interface-name": "Ppp0"}, "id") == "ISP"


def test_iface_label_falls_back_to_iface_id_before_payload_id() -> None:
    assert iface_label({"id": 7}, "GigabitEthernet0") == "GigabitEthernet0"


@pytest.mark.parametrize("value", ["GigabitEthernet0", TEST_HOST])
def test_validate_cli_arg_accepts_golden_values(value: str) -> None:
    assert _validate_cli_arg(value, "token") == value


@pytest.mark.parametrize("value", ["bad value", " trim", "", None, "semi;colon"])
def test_validate_cli_arg_rejects_golden_values(value: str | None) -> None:
    with pytest.raises(KeeneticApiError):
        _validate_cli_arg(value, "token")
