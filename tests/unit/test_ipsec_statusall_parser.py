"""Tests for the strongSwan ``ipsec statusall`` text parser.

The parser replaces the OOM-leaking ``show/crypto/map`` Vici call as
the source of site-to-site IPsec data. These golden tests pin the
normalized output shape so downstream sensors keep working.
"""

from __future__ import annotations

from custom_components.keenetic_router_pro.api.domains.vpn import VpnMixin


_REAL_ESTABLISHED = """Status of IKE charon daemon (strongSwan 6.0.1, Linux 4.9-ndm-5, aarch64):
  uptime: 6 days, since May 21 13:00:01 2026
Listening IP addresses:
  100.64.0.22
  178.158.225.76
Connections:
 site-2-site:  %any...%any  IKEv2, dpddelay=30s
 site-2-site:   local:  [178.158.225.76] uses pre-shared key authentication
 site-2-site:   remote: [yahny.keenetic.pro] uses pre-shared key authentication
 site-2-site:   child:  192.168.10.0/24 192.168.1.0/24 === 192.168.3.0/24 TUNNEL, dpdaction=start
Security Associations (1 up, 0 connecting):
 site-2-site[141]: ESTABLISHED 10 hours ago, 178.158.225.76[178.158.225.76]...62.122.70.6[yahny.keenetic.pro]
 site-2-site[141]: IKEv2 SPIs: 0ef6ad945e249aba_i f9e56bba03571271_r*, rekeying in 13 hours
 site-2-site[141]: IKE proposal: AES_GCM_16=128/PRF_HMAC_SHA2_384/ECP_256
 site-2-site{25}:  INSTALLED, TUNNEL, reqid 1, ESP in UDP SPIs: c0cc00a5_i c3767893_o
 site-2-site{25}:  AES_GCM_16_128/ECP_256, 33511272 bytes_i (43830 pkts, 0s ago), 6676269 bytes_o (33971 pkts, 0s ago), rekeying in 5 hours
 site-2-site{25}:   192.168.1.0/24 192.168.10.0/24 === 192.168.3.0/24
"""


def test_parser_handles_fully_established_tunnel() -> None:
    result = VpnMixin._parse_ipsec_statusall(_REAL_ESTABLISHED)
    assert "site-2-site" in result
    t = result["site-2-site"]

    assert t["connected"] is True
    assert t["state"] == "PHASE2_ESTABLISHED"
    assert t["ike_state"] == "ESTABLISHED"
    assert t["local_endpoint"] == "178.158.225.76"
    assert t["remote_endpoint"] == "62.122.70.6"
    assert t["mode"] == "tunnel"
    assert t["rx_bytes"] == 33511272
    assert t["tx_bytes"] == 6676269
    assert t["rx_packets"] == 43830
    assert t["tx_packets"] == 33971

    p1 = t["phase1"]
    assert p1["ike_version"] == "2"
    assert p1["proposal"] == "AES_GCM_16=128/PRF_HMAC_SHA2_384/ECP_256"
    assert p1["unique_id"] == 141

    assert len(t["phase2_sa_list"]) == 1
    sa = t["phase2_sa_list"][0]
    assert sa["unique_id"] == 25
    assert sa["sa_state"] == "INSTALLED"
    assert sa["mode"] == "TUNNEL"
    assert sa["protocol"] == "ESP"
    assert sa["encapsulation"] is True
    assert sa["in_bytes"] == "33511272"
    assert sa["out_bytes"] == "6676269"


def test_parser_handles_configured_but_no_sa() -> None:
    """Tunnel loaded by strongSwan but no SA negotiated yet."""
    text = """Status of IKE charon daemon:
Connections:
 site-2-site:  %any...%any  IKEv2, dpddelay=30s
 site-2-site:   child:  10.0.0.0/24 === 10.1.0.0/24 TUNNEL, dpdaction=restart
Security Associations (0 up, 0 connecting):
"""
    result = VpnMixin._parse_ipsec_statusall(text)
    assert "site-2-site" in result
    t = result["site-2-site"]
    assert t["enabled"] is True
    assert t["state"] == "UNDEFINED"
    assert t["connected"] is False
    assert t["ike_state"] == "UNDEFINED"
    assert t["rx_bytes"] == 0
    assert t["tx_bytes"] == 0


def test_parser_handles_phase1_only() -> None:
    """IKE up but phase2 SA negotiation has not completed."""
    text = """Connections:
 demo:  %any...%any  IKEv2
Security Associations (1 up, 0 connecting):
 demo[7]: ESTABLISHED 1 minute ago, 1.2.3.4[id-l]...5.6.7.8[id-r]
 demo[7]: IKEv2 SPIs: aaaa_i bbbb_r*, rekeying in 24 hours
 demo[7]: IKE proposal: AES_GCM_16=128
"""
    result = VpnMixin._parse_ipsec_statusall(text)
    t = result["demo"]
    assert t["ike_state"] == "ESTABLISHED"
    assert t["state"] == "PHASE1_ONLY"
    assert t["connected"] is False
    assert t["phase2_sa_list"] == []


def test_parser_handles_empty_or_invalid_input() -> None:
    assert VpnMixin._parse_ipsec_statusall("") == {}
    assert VpnMixin._parse_ipsec_statusall(None) == {}  # type: ignore[arg-type]
    assert VpnMixin._parse_ipsec_statusall("garbage text\nno markers") == {}


def test_parser_handles_multiple_tunnels() -> None:
    text = """Connections:
 alpha:  %any...%any  IKEv2
 beta:  1.1.1.1...2.2.2.2  IKEv2
Security Associations (2 up, 0 connecting):
 alpha[1]: ESTABLISHED 5 minutes ago, 10.0.0.1[a]...20.0.0.1[b]
 alpha[1]: IKEv2 SPIs: 11_i 22_r*, rekeying in 4 hours
 alpha{1}:  INSTALLED, TUNNEL, reqid 1, ESP in UDP SPIs: aa_i bb_o
 alpha{1}:  AES_GCM_16/ECP_256, 1000 bytes_i (5 pkts, 0s ago), 2000 bytes_o (10 pkts, 0s ago), rekeying in 2 hours
 beta[2]: ESTABLISHED 1 hour ago, 1.1.1.1[c]...2.2.2.2[d]
 beta[2]: IKEv2 SPIs: cc_i dd_r*, rekeying in 8 hours
 beta{2}:  INSTALLED, TUNNEL, reqid 2, ESP in UDP SPIs: ee_i ff_o
 beta{2}:  AES_GCM_16/ECP_256, 500 bytes_i (3 pkts, 0s ago), 700 bytes_o (4 pkts, 0s ago), rekeying in 3 hours
"""
    result = VpnMixin._parse_ipsec_statusall(text)
    assert set(result.keys()) == {"alpha", "beta"}
    assert result["alpha"]["rx_bytes"] == 1000
    assert result["alpha"]["tx_bytes"] == 2000
    assert result["beta"]["rx_bytes"] == 500
    assert result["beta"]["tx_bytes"] == 700
    assert result["alpha"]["connected"] is True
    assert result["beta"]["connected"] is True


def test_parser_handles_rekeying_tunnel_and_disabled_reauth_line() -> None:
    text = """Connections:
 branch-vpn:  %any...%any  IKEv2
 branch-vpn:   child:  10.0.0.0/24 === 10.1.0.0/24 TUNNEL, dpdaction=restart
Security Associations (1 up, 0 connecting):
 branch-vpn[9]: REKEYING 4 seconds ago, 198.51.100.10[left-id]...203.0.113.20[right-id]
 branch-vpn[9]: IKEv2 SPIs: aa11_i bb22_r, reauthentication disabled
 branch-vpn{31}:  REKEYING, TUNNEL, reqid 3, ESP SPIs: cc33_i dd44_o
 branch-vpn{31}:  AES_CBC_256/HMAC_SHA2_256_128, 42 bytes_i, 84 bytes_o
"""
    result = VpnMixin._parse_ipsec_statusall(text)
    t = result["branch-vpn"]

    assert t["ike_state"] == "REKEYING"
    assert t["state"] == "REKEYING"
    assert t["connected"] is False
    assert t["mode"] == "tunnel"
    assert t["rx_bytes"] == 42
    assert t["tx_bytes"] == 84
    assert t["phase1"]["local_spi"] == "aa11"
    assert t["phase1"]["remote_spi"] == "bb22"


def test_parser_keeps_installed_child_connected_during_ike_rekey() -> None:
    text = """Connections:
 branch-vpn:  %any...%any  IKEv2
Security Associations (1 up, 0 connecting):
 branch-vpn[9]: REKEYING 4 seconds ago, 198.51.100.10[left-id]...203.0.113.20[right-id]
 branch-vpn[9]: IKEv2 SPIs: aa11_i bb22_r, reauthentication disabled
 branch-vpn{31}:  INSTALLED, TUNNEL, reqid 3, ESP SPIs: cc33_i dd44_o
 branch-vpn{31}:  AES_CBC_256/HMAC_SHA2_256_128, 42 bytes_i, 84 bytes_o
"""
    result = VpnMixin._parse_ipsec_statusall(text)
    t = result["branch-vpn"]

    assert t["ike_state"] == "REKEYING"
    assert t["state"] == "PHASE2_ESTABLISHED"
    assert t["connected"] is True
