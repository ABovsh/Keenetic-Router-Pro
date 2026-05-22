"""Device tracker Home/Away transition tests."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.keenetic_router_pro.device_tracker import (
    KeeneticClientTracker,
    async_setup_entry,
)

MAC = "aa:bb:cc:dd:ee:ff"


def _client(last_seen: int, *, active: bool) -> dict:
    return {
        "mac": MAC,
        "ip": "192.0.2.40",
        "name": "Phone",
        "active": active,
        "last-seen": last_seen,
        "last-seen-source": "neighbour",
    }


def test_tracker_stays_home_when_last_seen_is_recent_and_active(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory(
        {"clients_by_mac": {MAC: _client(5, active=True)}}
    )
    tracker = KeeneticClientTracker(coordinator, keenetic_entry, MAC, "Phone")

    coordinator.data = {"clients_by_mac": {MAC: _client(30, active=True)}}
    tracker.async_write_ha_state()

    assert tracker.is_connected is True


def test_tracker_transitions_away_when_last_seen_is_old_and_inactive(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory(
        {"clients_by_mac": {MAC: _client(5, active=True)}}
    )
    tracker = KeeneticClientTracker(coordinator, keenetic_entry, MAC, "Phone")

    coordinator.data = {"clients_by_mac": {MAC: _client(900, active=False)}}
    tracker.async_write_ha_state()

    assert tracker.is_connected is False


async def test_tracker_setup_adds_one_entity_per_unique_normalized_mac(
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory({})
    entry = SimpleNamespace(
        entry_id="entry_123",
        title="Router",
        data={
            "tracked_clients": [
                {"mac": "AA-BB-CC-DD-EE-FF", "name": "Phone", "ip": "192.0.2.40"},
                {"mac": "aa:bb:cc:dd:ee:ff", "name": "Duplicate"},
                {"mac": ""},
                "bad",
                {"mac": "11:22:33:44:55:66"},
            ]
        },
        runtime_data=SimpleNamespace(coordinator=coordinator),
    )
    added = []

    await async_setup_entry(SimpleNamespace(), entry, added.extend)

    assert [entity.unique_id for entity in added] == [
        "entry_123_client_aa:bb:cc:dd:ee:ff",
        "entry_123_client_11:22:33:44:55:66",
    ]


async def test_tracker_setup_without_tracked_clients_adds_no_entities(
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory({})
    entry = SimpleNamespace(
        entry_id="entry_123",
        title="Router",
        data={},
        runtime_data=SimpleNamespace(coordinator=coordinator),
    )
    added = []

    await async_setup_entry(SimpleNamespace(), entry, added.extend)

    assert added == []


def test_tracker_update_handler_writes_state(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory({"clients_by_mac": {MAC: _client(5, active=True)}})
    tracker = KeeneticClientTracker(coordinator, keenetic_entry, MAC, "Phone")
    calls = []
    tracker.async_write_ha_state = lambda: calls.append("write")

    tracker._handle_coordinator_update()

    assert calls == ["write"]


def test_tracker_hostname_falls_back_to_hostname_field(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    client = _client(5, active=True)
    client["name"] = " "
    client["hostname"] = "phone.local"
    coordinator = keenetic_coordinator_factory({"clients_by_mac": {MAC: client}})
    tracker = KeeneticClientTracker(coordinator, keenetic_entry, MAC, "Phone")

    assert tracker.hostname == "phone.local"


def test_tracker_missing_client_uses_initial_ip_attribute(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory({"clients_by_mac": {}})
    tracker = KeeneticClientTracker(
        coordinator,
        keenetic_entry,
        MAC,
        "Phone",
        initial_ip="192.0.2.99",
    )

    assert tracker.extra_state_attributes["ip"] == "192.0.2.99"
