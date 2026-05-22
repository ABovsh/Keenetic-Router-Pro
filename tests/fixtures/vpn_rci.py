"""Canned VPN RCI payloads for coordinator tests."""

from __future__ import annotations

WIREGUARD_STATUS = {"profiles": {"Wireguard0": {"enabled": True}}}

VPN_TUNNELS = {"profiles": {"Wireguard0": {"enabled": True}}}

CRYPTO_MAPS = {
    "SITE": {
        "rx_bytes": 1000,
        "tx_bytes": 2000,
        "connected": True,
    }
}

IPSEC_DIAGNOSTICS = {"status": "ok"}

IPSEC_PHASE_STATES = (
    "UNDEFINED",
    "CONNECTING",
    "PHASE1_ONLY",
    "PHASE2_ESTABLISHED",
    "DOWN",
)


def crypto_map_for_phase(state: str) -> dict:
    """Return a normalized crypto-map fixture for one IPsec phase state."""
    return {
        "SITE": {
            "name": "SITE",
            "state": state,
            "ike_state": state,
            "connected": state == "PHASE2_ESTABLISHED",
            "remote_peer": "198.51.100.1",
        }
    }
