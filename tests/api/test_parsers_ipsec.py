"""IPsec parser helper tests."""

from __future__ import annotations

from custom_components.keenetic_router_pro.api.parsers.ipsec import (
    parse_ipsec_statusall,
    parse_ipsec_vici_diagnostics,
)


def test_parse_ipsec_statusall_established_tunnel() -> None:
    text = """
Security Associations (1 up, 0 connecting):
  Office[1]: ESTABLISHED 12 minutes ago, 192.0.2.10[local]...198.51.100.10[remote]
  Office[1]: IKEv2 SPIs: abc_i def_r*, rekeying in 30 minutes
  Office{2}: INSTALLED, TUNNEL, reqid 1, ESP in UDP SPIs: ccc_i ddd_o
  Office{2}: AES_GCM_16_256, 100 bytes_i (2 pkts, 1s ago), 200 bytes_o (3 pkts, 2s ago)
"""
    result = parse_ipsec_statusall(text)

    assert result["Office"]["connected"] is True
    assert result["Office"]["state"] == "PHASE2_ESTABLISHED"
    assert result["Office"]["rx_bytes"] == 100
    assert result["Office"]["tx_bytes"] == 200


def test_parse_ipsec_vici_diagnostics_entries_preserve_events() -> None:
    result = parse_ipsec_vici_diagnostics(
        [],
        entries=[
            {
                "time": "May 27 17:33:48",
                "message": "IpSec::Vici::Stats: out of memory [0xcffe02a7].",
            },
            {"time": "May 27 17:34:48", "message": "ordinary message"},
        ],
    )

    assert result["status"] == "warning"
    assert result["vici_out_of_memory_count"] == 1
    assert result["last_error_code"] == "0xcffe02a7"
    assert result["events"] == [
        (
            "May 27 17:33:48",
            "IpSec::Vici::Stats: out of memory [0xcffe02a7].",
        )
    ]
