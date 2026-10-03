"""Usability round: WAN state every tick, Last Seen, statistics and defaults."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.keenetic_router_pro.coordinator_parts.refresh import (
    build_batch_tree,
    refresh_plan,
)
from tests.test_coordinator_stages import (
    StageFixtureClient,
    _coordinator,
    _updated_data,
)

MAC = "aa:bb:cc:dd:ee:ff"


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router", data={})


# ---------- WAN state is rebuilt on every tick ----------


def test_ping_check_rides_in_every_batch() -> None:
    plan = refresh_plan(first_refresh=False, refresh_count=1)  # fast-only tick

    assert "ping-check" in build_batch_tree(plan)["show"]


async def _primed() -> tuple[StageFixtureClient, object, dict]:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    previous = await _updated_data(coordinator)
    coordinator.data = previous
    coordinator._refresh_count = 1  # next tick is fast-only
    return client, coordinator, previous


async def test_fast_tick_publishes_a_lost_wan_link() -> None:
    client, coordinator, previous = await _primed()
    wan_id = previous["wan_interfaces"][0]["id"]
    for wan in client.wan_interfaces:
        if wan["id"] == wan_id:
            wan["link_state"] = "down"

    data = await _updated_data(coordinator)

    assert data["wan_by_id"][wan_id]["link_state"] == "down"


async def test_fast_tick_publishes_a_failing_ping_check() -> None:
    client, coordinator, previous = await _primed()
    wan_id = previous["wan_interfaces"][0]["id"]
    client.ping_check = {wan_id: {"passing": True, "profile": "p"}}
    coordinator._refresh_count = 2  # medium: the passing check is applied
    coordinator.data = await _updated_data(coordinator)
    coordinator._refresh_count = 5  # fast-only again
    client.ping_check = {wan_id: {"passing": False, "profile": "p"}}

    data = await _updated_data(coordinator)

    assert data["wan_by_id"][wan_id]["internet_access"] is False
    assert data["wan_by_id"][wan_id]["internet_access_source"] == "ping_check"


async def test_fast_tick_follows_a_failover() -> None:
    client, coordinator, previous = await _primed()
    ids = [wan["id"] for wan in client.wan_interfaces]
    assert len(ids) > 1
    for wan in client.wan_interfaces:
        wan["defaultgw"] = wan["id"] == ids[1]

    data = await _updated_data(coordinator)

    assert data["active_wan"] == ids[1]


async def test_fast_tick_still_skips_the_medium_stage_two_reads() -> None:
    client, coordinator, _ = await _primed()
    client.calls.clear()

    await _updated_data(coordinator)

    for skipped in ("wifi", "wireguard", "interface_stats", "traffic_stats"):
        assert client.calls.get(skipped, 0) == 0


# ---------- Last Seen ----------


def _last_seen(client: dict):
    from custom_components.keenetic_router_pro.sensor.client import (
        KeeneticClientLastSeenSensor,
    )

    coordinator = SimpleNamespace(
        data={"clients_by_mac": {MAC: client}}, last_update_success=True
    )
    sensor = KeeneticClientLastSeenSensor(coordinator, _entry(), MAC, "Phone")
    base = datetime(2026, 10, 3, 22, 0).astimezone()
    sensor.clock_offset = 0
    sensor._now = lambda: base + timedelta(seconds=sensor.clock_offset)
    return sensor


def test_last_seen_is_a_timestamp() -> None:
    from homeassistant.components.sensor import SensorDeviceClass

    sensor = _last_seen({"mac": MAC, "active": False, "last-seen": 3600})

    assert sensor._attr_device_class == SensorDeviceClass.TIMESTAMP
    assert isinstance(sensor.native_value, datetime)
    assert sensor.native_value.tzinfo is not None


def test_last_seen_holds_while_the_router_keeps_seeing_a_dozing_client() -> None:
    """A phone in Wi-Fi power-save is "offline" yet seen every few seconds."""
    client = {"mac": MAC, "active": False, "last-seen": 5}
    sensor = _last_seen(client)
    first = sensor.native_value

    published = {first}
    for step in range(1, 9):  # eight polls, one minute apart, still "5 s ago"
        sensor.clock_offset = step * 60
        published.add(sensor.native_value)

    assert published == {first}


def test_last_seen_moves_after_ten_minutes_of_new_sightings() -> None:
    client = {"mac": MAC, "active": False, "last-seen": 5}
    sensor = _last_seen(client)
    first = sensor.native_value

    sensor.clock_offset = 11 * 60

    assert (sensor.native_value - first).total_seconds() == pytest.approx(660, abs=2)


# ---------- Long-term statistics that carried nothing useful ----------


def test_low_value_counters_keep_no_statistics() -> None:
    from custom_components.keenetic_router_pro.sensor.clients import (
        KeeneticDisconnectedClientsSensor,
    )
    from custom_components.keenetic_router_pro.sensor.dns import (
        KeeneticDnsProxyFailedRequestsSensor,
    )
    from custom_components.keenetic_router_pro.sensor.ipsec import (
        KeeneticIpsecViciOomTotalSensor,
    )
    from custom_components.keenetic_router_pro.sensor.traffic import (
        KeeneticLanRxSensor,
        KeeneticLanTxSensor,
    )

    for cls in (
        KeeneticDisconnectedClientsSensor,
        KeeneticDnsProxyFailedRequestsSensor,
        KeeneticIpsecViciOomTotalSensor,
        KeeneticLanRxSensor,
        KeeneticLanTxSensor,
    ):
        assert cls._attr_state_class is None, cls.__name__


def _wg(cls, wan_ids: list[str]):
    coordinator = SimpleNamespace(
        data={
            "wireguard": {"profiles": {"Wireguard0": {"rxbytes": 1, "txbytes": 1}}},
            "wan_interfaces": [{"id": wan_id} for wan_id in wan_ids],
        },
        last_update_success=True,
    )
    return cls(coordinator, _entry(), "Wireguard0")


def test_wireguard_counters_leave_statistics_to_the_wan_sensor_of_an_uplink() -> None:
    from homeassistant.components.sensor import SensorStateClass

    from custom_components.keenetic_router_pro.sensor.wireguard import (
        KeeneticWgRxSensor,
        KeeneticWgTxSensor,
    )

    for cls in (KeeneticWgRxSensor, KeeneticWgTxSensor):
        assert _wg(cls, ["Wireguard0"]).state_class is None
        assert _wg(cls, ["ISP"]).state_class == SensorStateClass.TOTAL_INCREASING


# ---------- Duplicate or static entities start disabled ----------


def test_duplicate_and_static_entities_start_disabled() -> None:
    from custom_components.keenetic_router_pro.binary_sensor import (
        KeeneticWanEnabledSensor,
    )
    from custom_components.keenetic_router_pro.sensor.network import (
        KeeneticPppoeUptimeSensor,
        KeeneticWanInterfaceSensor,
        KeeneticWanProviderSensor,
        KeeneticWanRoleSensor,
    )
    from custom_components.keenetic_router_pro.sensor.traffic import (
        KeeneticWanRxSensor,
        KeeneticWanTxSensor,
    )

    for cls in (
        KeeneticWanEnabledSensor,
        KeeneticPppoeUptimeSensor,
        KeeneticWanInterfaceSensor,
        KeeneticWanProviderSensor,
        KeeneticWanRoleSensor,
        KeeneticWanRxSensor,
        KeeneticWanTxSensor,
    ):
        assert cls._attr_entity_registry_enabled_default is False, cls.__name__


# ---------- A failing ping check stops writing once it has failed ----------


def test_failing_ping_check_counter_stops_at_the_threshold() -> None:
    """WAN state now refreshes every poll; an outage must not write more."""
    from custom_components.keenetic_router_pro.binary_sensor import (
        KeeneticWanConnectedSensor,
    )

    wan = {
        "id": "ISP",
        "ping_check": {"passing": False, "fail_count": 3, "max_fails": 3},
    }
    coordinator = SimpleNamespace(
        data={"wan_interfaces": [wan], "wan_by_id": {"ISP": wan}},
        last_update_success=True,
    )
    sensor = KeeneticWanConnectedSensor(coordinator, _entry(), "ISP")
    at_threshold = sensor.extra_state_attributes

    for fails in (4, 17, 250):  # the router keeps counting through the outage
        wan["ping_check"]["fail_count"] = fails
        assert sensor.extra_state_attributes == at_threshold
