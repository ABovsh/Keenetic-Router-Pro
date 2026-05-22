"""Regression tests for 1.7.27 hardening pass.

Covers the dict-shape guards added across api/domains/* so that one
malformed entry returned by the router cannot torpedo an entire fetch.
"""

from __future__ import annotations

import asyncio

import pytest

from custom_components.keenetic_router_pro.api.domains.dns import _redact_doh_uri
from custom_components.keenetic_router_pro.api.domains.vpn import VpnMixin
from custom_components.keenetic_router_pro.api.domains.wifi import WifiMixin
from custom_components.keenetic_router_pro.coordinator import (
    _dict_or_empty,
    _list_or_empty,
)


def _run(coro):
    return asyncio.run(coro)


def test_list_or_empty_returns_list_payload() -> None:
    assert _list_or_empty([1, 2]) == [1, 2]


@pytest.mark.parametrize("bad", [{}, None, "oops", 0, 42, object()])
def test_list_or_empty_rejects_non_list(bad) -> None:
    assert _list_or_empty(bad) == []


@pytest.mark.parametrize("bad", [[], None, "oops", 0, 42, object()])
def test_dict_or_empty_rejects_non_dict(bad) -> None:
    assert _dict_or_empty(bad) == {}


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("https://dns.google/dns-query", "https://dns.google/"),
        ("https://dns.nextdns.io/abc123secret", "https://dns.nextdns.io/"),
        ("https://user:pass@dns.example.com/path?x=1", "https://dns.example.com/"),
        ("https://dns.example.com:8443/q", "https://dns.example.com:8443/"),
        ("", ""),
        (None, ""),
    ],
)
def test_redact_doh_uri_strips_secrets(uri, expected) -> None:
    assert _redact_doh_uri(uri) == expected


# --- VPN tunnel parser must survive malformed firmware shapes ---


class _Vpn(VpnMixin):
    """Concrete VpnMixin without other mixin requirements."""


@pytest.mark.parametrize(
    "iface_list",
    [
        [{"type": "wireguard", "id": "Wg0", "wireguard": "not-a-dict",
          "state": "up", "summary": "not-a-dict"}],
        [{"type": "wireguard", "id": "Wg0",
          "wireguard": {"peer": ["not-a-dict"]}, "state": "up"}],
        [{"type": "wireguard", "id": "Wg0", "wireguard": {"peer": "bogus"},
          "state": "up"}],
    ],
)
def test_wireguard_parser_tolerates_malformed_shapes(iface_list) -> None:
    vpn = _Vpn()
    result = _run(vpn.async_get_wireguard_status(iface_list=iface_list))
    assert isinstance(result, dict)
    assert "profiles" in result


def test_vpn_tunnels_tolerates_numeric_id_and_bad_summary() -> None:
    vpn = _Vpn()
    iface_list = [
        {"type": "openvpn", "id": 123, "state": "up", "summary": "bad"},
    ]
    result = _run(vpn.async_get_vpn_tunnels(iface_list=iface_list))
    assert "123" in result["profiles"]


def test_vpn_tunnels_tolerates_string_id_and_bad_layer() -> None:
    vpn = _Vpn()
    iface_list = [
        {"type": "ipsec", "id": "ipsec0", "summary": {"layer": "bad"},
         "state": "down"},
    ]
    result = _run(vpn.async_get_vpn_tunnels(iface_list=iface_list))
    assert "ipsec0" in result["profiles"]


# --- Wi-Fi parser must coerce numeric raw_id and skip non-list traits ---


class _Wifi(WifiMixin):
    pass


def test_wifi_parser_tolerates_numeric_id_and_bad_traits() -> None:
    wifi = _Wifi()
    iface_list = [
        {"type": "accesspoint", "id": 1, "ssid": "OK", "traits": "not-a-list"},
        {"type": "accesspoint", "id": "AccessPoint0", "ssid": "OK2", "traits": ["wifi", "accesspoint"]},
    ]
    result = _run(wifi.async_get_wifi_networks(iface_list=iface_list))
    assert isinstance(result, list)
