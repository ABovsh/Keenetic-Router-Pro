"""Golden tests for pure api.py parser/helper behavior."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_HOST_ALT, TEST_PASSWORD, TEST_USERNAME

import asyncio

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



def test_first_present_returns_first_non_empty_value() -> None:
    data = {"a": None, "b": "", "c": 0, "d": "value"}

    assert first_present(data, "a", "b", "c", "d") == 0
    assert first_present(data, "missing", "d") == "value"
    assert first_present(data, "a", "b", default="fallback") == "fallback"



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
    # Up + global but no usable address (ipv4 pending) = no internet (False),
    # not None/unavailable.
    assert wans[1]["internet_access"] is False


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
