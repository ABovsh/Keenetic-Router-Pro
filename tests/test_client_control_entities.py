"""Tests for tracked-client policy and presence entities."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.keenetic_router_pro.device_tracker import KeeneticClientTracker
from custom_components.keenetic_router_pro.select import (
    KeeneticClientPolicySelect,
    async_setup_entry as async_setup_select_entry,
)


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router", data={})


def _coordinator() -> SimpleNamespace:
    async def async_request_refresh() -> None:
        calls.append("refresh")

    calls: list[str] = []
    return SimpleNamespace(
        data={
            "clients_by_mac": {
                "aa:bb:cc:dd:ee:ff": {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "ip": "192.0.2.40",
                    "name": "Kitchen tablet",
                    "interface": {"name": "Main AP"},
                    "ssid": "Main",
                    "link": "up",
                }
            },
            "host_policies": {
                "aa:bb:cc:dd:ee:ff": {
                    "policy": "Policy1",
                    "access": "permit",
                    "registered": True,
                }
            },
        },
        async_request_refresh=async_request_refresh,
        refresh_calls=calls,
    )


def test_client_policy_select_maps_options_and_refreshes_after_change() -> None:
    """Policy select maps display names to router policy ids and refreshes state."""
    entry = _entry()
    coordinator = _coordinator()
    api_calls = []

    async def async_set_client_policy(mac: str, policy: str) -> None:
        api_calls.append((mac, policy))

    entity = KeeneticClientPolicySelect(
        coordinator=coordinator,
        entry=entry,
        api_client=SimpleNamespace(async_set_client_policy=async_set_client_policy),
        mac="aa:bb:cc:dd:ee:ff",
        label="Tablet",
        initial_ip="192.0.2.10",
        policies={"Policy1": "VPN", "Policy2": "Smart Home"},
    )

    assert entity.unique_id == "entry_123_client_aa:bb:cc:dd:ee:ff_policy"
    assert entity.options == ["Default", "Smart Home", "VPN", "Deny (Blocked)"]
    assert entity.current_option == "VPN"
    assert entity.available is True
    assert entity.extra_state_attributes["client_name"] == "Kitchen tablet"

    asyncio.run(entity.async_select_option("Smart Home"))
    asyncio.run(entity.async_select_option("Deny (Blocked)"))
    asyncio.run(entity.async_select_option("Default"))

    assert api_calls == [
        ("aa:bb:cc:dd:ee:ff", "Policy2"),
        ("aa:bb:cc:dd:ee:ff", "deny"),
        ("aa:bb:cc:dd:ee:ff", "default"),
    ]
    assert coordinator.refresh_calls == ["refresh", "refresh", "refresh"]

    coordinator.data["host_policies"]["aa:bb:cc:dd:ee:ff"] = {"access": "deny"}

    assert entity.current_option == "Deny (Blocked)"


def test_client_policy_setup_deduplicates_same_mac_variants() -> None:
    """One client tracked on one router should create one policy select."""
    coordinator = _coordinator()

    class ApiClient:
        async def async_get_policies(self):
            return {"Policy1": "VPN"}

    entry = SimpleNamespace(
        entry_id="entry_123",
        title="Router",
        data={
            "tracked_clients": [
                {"mac": "AA-BB-CC-DD-EE-FF", "name": "Phone"},
                {"mac": "aa:bb:cc:dd:ee:ff", "name": "Phone duplicate"},
            ]
        },
        runtime_data=SimpleNamespace(coordinator=coordinator, client=ApiClient()),
    )
    added = []

    asyncio.run(async_setup_select_entry(SimpleNamespace(), entry, added.extend))

    assert len(added) == 1
    assert added[0].unique_id == "entry_123_client_aa:bb:cc:dd:ee:ff_policy"


def test_client_tracker_uses_router_link_for_all_clients() -> None:
    """Presence tracking is based on the router's own client link state."""
    entry = _entry()
    coordinator = _coordinator()

    tracker = KeeneticClientTracker(
        coordinator=coordinator,
        entry=entry,
        mac="aa:bb:cc:dd:ee:ff",
        label="Kitchen tablet",
        initial_ip="192.0.2.10",
    )

    assert tracker.unique_id == "entry_123_client_aa:bb:cc:dd:ee:ff"
    assert tracker.mac_address == "aa:bb:cc:dd:ee:ff"
    assert tracker.ip_address == "192.0.2.40"
    assert tracker.hostname == "Kitchen tablet"
    assert tracker.is_connected is True
    assert tracker.extra_state_attributes["tracking_method"] == "router_link"
    assert tracker.extra_state_attributes["presence_source"] == "link"

    apple_tracker = KeeneticClientTracker(
        coordinator=coordinator,
        entry=entry,
        mac="aa:bb:cc:dd:ee:ff",
        label="Anton iPhone",
        initial_ip=None,
    )

    assert apple_tracker.is_connected is True
    assert apple_tracker.extra_state_attributes["tracking_method"] == "router_link"


def test_client_tracker_uses_active_flag_when_link_is_missing() -> None:
    """Keenetic payloads that expose active=true but no link are still home."""
    entry = _entry()
    coordinator = _coordinator()
    coordinator.data["clients_by_mac"]["aa:bb:cc:dd:ee:ff"].pop("link")
    coordinator.data["clients_by_mac"]["aa:bb:cc:dd:ee:ff"]["active"] = True

    tracker = KeeneticClientTracker(
        coordinator=coordinator,
        entry=entry,
        mac="aa:bb:cc:dd:ee:ff",
        label="Kitchen tablet",
        initial_ip="192.0.2.10",
    )

    assert tracker.is_connected is True
    assert tracker.extra_state_attributes["presence_source"] == "active"


def test_client_tracker_fallback_lookup_normalizes_mac_variants() -> None:
    """Fallback client-list lookup should match the same MAC formats as the index."""
    entry = _entry()
    coordinator = _coordinator()
    target = coordinator.data["clients_by_mac"].pop("aa:bb:cc:dd:ee:ff")
    coordinator.data.pop("clients_by_mac")
    target["mac"] = "AA-BB-CC-DD-EE-FF"
    coordinator.data["clients"] = [target]

    tracker = KeeneticClientTracker(
        coordinator=coordinator,
        entry=entry,
        mac="aa:bb:cc:dd:ee:ff",
        label="Kitchen tablet",
        initial_ip="192.0.2.10",
    )

    assert tracker.is_connected is True
    assert tracker.ip_address == "192.0.2.40"


def test_client_tracker_uses_neighbour_expiry_for_offline_clients() -> None:
    """An expired neighbour record is a clear away signal."""
    entry = _entry()
    coordinator = _coordinator()
    coordinator.data["clients_by_mac"]["aa:bb:cc:dd:ee:ff"].pop("link")
    coordinator.data["clients_by_mac"]["aa:bb:cc:dd:ee:ff"].update(
        {
            "active": False,
            "last-seen": 672,
            "last-seen-source": "neighbour",
            "first-seen": 277668,
            "first-seen-source": "neighbour",
            "neighbour-expired": True,
            "neighbour-wireless": False,
            "neighbour-leasetime": 1122,
        }
    )

    tracker = KeeneticClientTracker(
        coordinator=coordinator,
        entry=entry,
        mac="aa:bb:cc:dd:ee:ff",
        label="Kitchen tablet",
        initial_ip="192.0.2.10",
    )

    attrs = tracker.extra_state_attributes

    assert tracker.is_connected is False
    assert attrs["presence_source"] == "neighbour_expired"
    assert attrs["last_seen_source"] == "neighbour"
    assert attrs["first_seen_source"] == "neighbour"
    assert attrs["neighbour_expired"] is True
    assert attrs["neighbour_leasetime"] == 1122


def test_client_tracker_marks_missing_client_away() -> None:
    """When a client disappears from the router table, it is away."""
    entry = _entry()
    coordinator = _coordinator()
    coordinator.data["clients_by_mac"] = {}

    tracker = KeeneticClientTracker(
        coordinator=coordinator,
        entry=entry,
        mac="aa:bb:cc:dd:ee:ff",
        label="Tablet",
        initial_ip="192.0.2.10",
    )

    assert tracker.is_connected is False
    assert tracker.extra_state_attributes["presence_source"] == "missing"
