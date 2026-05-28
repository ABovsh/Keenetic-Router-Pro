"""WAN parser helper tests."""

from __future__ import annotations

from custom_components.keenetic_router_pro.api.parsers.wan import (
    derive_wan_enabled,
    derive_wan_internet_access,
    extract_wan_ip,
    is_ranked_wan_interface,
)


def test_extract_wan_ip_accepts_known_address_shapes() -> None:
    assert extract_wan_ip({"address": "198.51.100.1/32"}) == "198.51.100.1"
    assert (
        extract_wan_ip({"global-address": [{"address": "198.51.100.2/24"}]})
        == "198.51.100.2"
    )
    assert extract_wan_ip({"address": [{"ip": "198.51.100.3/24"}]}) == "198.51.100.3"
    assert extract_wan_ip({"ip-address": "198.51.100.4/24"}) == "198.51.100.4"


def test_extract_wan_ip_can_prefer_global_address_for_status_summary() -> None:
    iface = {
        "address": "10.0.0.2/24",
        "global-address": [{"address": "198.51.100.5/24"}],
    }

    assert extract_wan_ip(iface) == "10.0.0.2"
    assert extract_wan_ip(iface, prefer_global_address=True) == "198.51.100.5"


def test_ranked_wan_detection_matches_existing_rules() -> None:
    assert is_ranked_wan_interface({"role": ["inet"]}) is True
    assert is_ranked_wan_interface({"role": "wan"}) is True
    assert is_ranked_wan_interface({"global": True, "priority": 10}) is True
    assert is_ranked_wan_interface({"global": True}) is False


def test_derive_enabled_and_internet_access_preserve_pending_state() -> None:
    iface = {
        "state": "up",
        "global": True,
        "summary": {"layer": {"conf": "running", "ipv4": "pending"}},
    }

    assert derive_wan_enabled(iface) is True
    assert derive_wan_internet_access(iface) is None


def test_derive_internet_access_preserves_fail_override() -> None:
    iface = {
        "state": "up",
        "global": True,
        "address": "198.51.100.6/32",
        "fail": "yes",
    }

    assert derive_wan_internet_access(iface) is False
