"""Crypto-map config merge into runtime IPsec status.

Regression coverage for the failure where an administratively *disabled*
site-to-site tunnel disappeared from ``ipsec statusall`` and made its switch
go **unavailable** instead of **off**, stranding recovery automations.
"""

from __future__ import annotations

from custom_components.keenetic_router_pro.api.parsers.ipsec import (
    merge_crypto_map_config,
    parse_ipsec_statusall,
)
from custom_components.keenetic_router_pro.const import FIELD_CONNECTED


_ESTABLISHED = """
Connections:
 s2s:  %any...%any  IKEv2, dpddelay=30s
 s2s:   child:  0.0.0.0/0 === 0.0.0.0/0 TUNNEL, dpdaction=start
Security Associations (1 up, 0 connecting):
 s2s[1]: ESTABLISHED 44 seconds ago, 10.0.0.1[a]...10.0.0.2[b]
 s2s[1]: IKEv2 SPIs: aaaa_i* bbbb_r, rekeying in 23 hours
 s2s{1}:  INSTALLED, TUNNEL, reqid 1, ESP SPIs: cccc_i dddd_o
 s2s{1}:  AES_GCM_16_128, 1280 bytes_i, 888 bytes_o
"""


def test_disabled_map_absent_from_status_becomes_available_off():
    """A configured-but-disabled tunnel must surface as a known, off entry."""
    status = parse_ipsec_statusall("")  # nothing loaded → disabled
    config = {"s2s": {"set-peer": {"remote-ip": "203.0.113.5"}, "enable": False}}

    merged = merge_crypto_map_config(status, config)

    assert "s2s" in merged  # present, so the switch stays *available*
    assert merged["s2s"]["enabled"] is False  # ...and reads *off*, not unavailable
    assert merged["s2s"][FIELD_CONNECTED] is False
    assert merged["s2s"]["state"] == "UNDEFINED"
    assert merged["s2s"]["remote_peer"] == "203.0.113.5"


def test_enabled_running_map_keeps_runtime_status():
    """An enabled tunnel keeps its live status; config only confirms enabled."""
    status = parse_ipsec_statusall(_ESTABLISHED)
    config = {"s2s": {"set-peer": {"remote-ip": "10.0.0.2"}, "enable": True}}

    merged = merge_crypto_map_config(status, config)

    assert merged["s2s"]["enabled"] is True
    assert merged["s2s"][FIELD_CONNECTED] is True
    assert merged["s2s"]["state"] != "UNDEFINED"
    assert merged["s2s"]["rx_bytes"] == 1280


def test_enable_flag_is_authoritative_over_parser_default():
    """parse_ipsec_statusall defaults enabled=True; config must override it."""
    status = parse_ipsec_statusall(_ESTABLISHED)
    assert status["s2s"]["enabled"] is True
    merged = merge_crypto_map_config(status, {"s2s": {"enable": "no"}})
    assert merged["s2s"]["enabled"] is False


def test_string_boolean_enable_coercion():
    merged = merge_crypto_map_config({}, {"s2s": {"enable": "yes"}})
    assert merged["s2s"]["enabled"] is True


def test_peer_any_is_not_treated_as_remote_peer():
    merged = merge_crypto_map_config({}, {"s2s": {"set-peer": {"remote-ip": "any"}}})
    assert merged["s2s"]["remote_peer"] is None


def test_empty_or_invalid_config_is_passthrough():
    status = parse_ipsec_statusall(_ESTABLISHED)
    assert merge_crypto_map_config(status, None) == status
    assert merge_crypto_map_config(status, {}) == status
    # non-dict config entries are skipped, not crashed on
    assert "s2s" in merge_crypto_map_config(status, {"bogus": "x", "s2s": {}})


def test_config_does_not_mutate_input_status():
    status = parse_ipsec_statusall(_ESTABLISHED)
    before = status["s2s"]["enabled"]
    merge_crypto_map_config(status, {"s2s": {"enable": False}})
    assert status["s2s"]["enabled"] == before  # original dict untouched
