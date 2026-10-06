"""Regressions for the October 6 deep audit."""

from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from custom_components.keenetic_router_pro.api import KeeneticClient
from custom_components.keenetic_router_pro.coordinator_parts.enrichment import enrich_wan_interfaces
from custom_components.keenetic_router_pro.sensor.traffic import KeeneticLanRxSensor
from custom_components.keenetic_router_pro.sensor.wifi import KeeneticWifi24RxSensor
from custom_components.keenetic_router_pro.number import KeeneticClientRateLimitNumber, _MAX_KBPS
from custom_components.keenetic_router_pro import device_trigger
from custom_components.keenetic_router_pro.const import DOMAIN
from homeassistant.helpers import device_registry
from custom_components.keenetic_router_pro.entity import UptimeMixin
from custom_components.keenetic_router_pro.sensor.network import (
    KeeneticWanDowntimeSensor,
    KeeneticWanFailoverCountSensor,
    KeeneticWanLinkDowntimeSensor,
    KeeneticWanRxBytesSensor,
    KeeneticWanRxThroughputSensor,
)
from test_coordinator_stages import StageFixtureClient


@pytest.mark.parametrize("per_wan", [False, True])
async def test_downtime_stops_during_tolerated_critical_fetch_failure(
    keenetic_entry, per_wan
) -> None:
    client = StageFixtureClient()
    client.system_info["uptime"] = 86_400
    client.wan_interfaces = [{
        "id": "ISP", "enabled": True, "link_state": "down",
        "internet_access": False,
    }]
    coordinator = KeeneticCoordinator(object(), client)
    coordinator.last_update_success = True
    coordinator.data = await coordinator._async_update_data()
    sensor = (
        KeeneticWanLinkDowntimeSensor(coordinator, keenetic_entry, "ISP")
        if per_wan else KeeneticWanDowntimeSensor(coordinator, keenetic_entry)
    )
    sensor._now = lambda: 0.0
    sensor._handle_coordinator_update()
    client.async_get_interfaces = AsyncMock(side_effect=TimeoutError("router unreachable"))
    coordinator.data = await coordinator._async_update_data()
    # The real coordinator keeps last_update_success=True in its grace window.
    sensor._now = lambda: 60.0
    sensor._handle_coordinator_update()
    assert sensor._down_since is None
    assert sensor._seconds == 0


@pytest.mark.parametrize("kind", ["internet", "provider", "failover"])
async def test_accumulated_network_counter_survives_unavailable_restart(
    keenetic_entry, keenetic_coordinator_factory, kind
) -> None:
    coordinator = keenetic_coordinator_factory({"wan_interfaces": [{"id": "ISP"}]})
    if kind == "internet":
        sensor = KeeneticWanDowntimeSensor(coordinator, keenetic_entry)
    elif kind == "provider":
        sensor = KeeneticWanLinkDowntimeSensor(coordinator, keenetic_entry, "ISP")
    else:
        sensor = KeeneticWanFailoverCountSensor(coordinator, keenetic_entry)
    value = 7357.5 if kind != "failover" else 17
    if kind == "failover":
        sensor._count = value
    else:
        sensor._seconds = value
        sensor._published = 7200
    saved = sensor.extra_restore_state_data.as_dict()
    sensor._count = 0
    sensor._seconds = 0
    sensor._published = 0
    sensor.async_get_last_state = AsyncMock(return_value=SimpleNamespace(
        state="unavailable", attributes={}
    ))
    sensor.async_get_last_extra_data = AsyncMock(return_value=SimpleNamespace(
        as_dict=lambda: saved
    ))
    await sensor.async_added_to_hass()
    assert sensor.native_value == int(value)
    if kind != "failover":
        assert sensor._seconds == value


@pytest.mark.parametrize("bad", ["nan", "inf", "-1"])
async def test_corrupt_downtime_restore_cannot_poison_statistics(
    keenetic_entry, keenetic_coordinator_factory, bad
) -> None:
    sensor = KeeneticWanDowntimeSensor(keenetic_coordinator_factory({}), keenetic_entry)
    sensor.async_get_last_state = AsyncMock(return_value=SimpleNamespace(
        state=bad, attributes={"unit_of_measurement": "s"}
    ))
    await sensor.async_added_to_hass()
    assert sensor.native_value == 0


def test_finite_but_unrepresentable_uptime_recovers_without_crashing() -> None:
    uptime = UptimeMixin()
    uptime._now = lambda: datetime(2026, 10, 6, tzinfo=timezone.utc)
    # coerce_seconds accepts this finite value, but the timestamp underflows.
    assert uptime._publish_uptime(100_000_000_000) is None
    assert uptime._publish_uptime(60) == datetime(2026, 10, 5, 23, 59, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", ["nan", "inf", "-1", str(_MAX_KBPS + 1)])
async def test_invalid_bandwidth_limit_restore_is_rejected(
    keenetic_entry, keenetic_coordinator_factory, bad
) -> None:
    sensor = KeeneticClientRateLimitNumber(
        keenetic_coordinator_factory({}), keenetic_entry, object(), "aa:bb:cc:dd:ee:ff", "phone"
    )
    sensor.async_get_last_state = AsyncMock(return_value=SimpleNamespace(state=bad))
    await sensor.async_added_to_hass()
    assert sensor.native_value == 0


@pytest.mark.parametrize("is_router", [False, True])
async def test_device_trigger_options_match_the_event_router_id(monkeypatch, is_router) -> None:
    identifiers = {(DOMAIN, "entry")} if is_router else {(DOMAIN, "entry_wan_ISP")}
    device = SimpleNamespace(identifiers=identifiers, config_entries={"entry"})
    registry = SimpleNamespace(async_get=lambda _id: device)
    monkeypatch.setattr(device_registry, "async_get", lambda _hass: registry, raising=False)
    offered = await device_trigger.async_get_triggers(object(), "device")
    if not is_router:
        assert offered == []
        return
    attached = []

    async def attach(_hass, config, *_args, **_kwargs):
        attached.append(config)
        return lambda: None

    monkeypatch.setattr(device_trigger.event_trigger, "async_attach_trigger", attach)
    for trigger in offered:
        await device_trigger.async_attach_trigger(object(), trigger, None, {})
    assert len(attached) == 3
    assert all(config["event_data"]["device_id"] == "device" for config in attached)


@pytest.mark.parametrize("client,expected", [
    ({"link": "up", "active": False}, 1),
    ({"active": True, "neighbour-expired": True}, 0),
])
def test_connected_client_counts_agree_with_presence(client, expected) -> None:
    summary = KeeneticClient.summarize_client_stats([{**client, "mac": "aa:bb:cc:dd:ee:ff"}])
    assert summary["connected"] == expected
    assert summary["disconnected"] == 1 - expected


@pytest.mark.parametrize("bad", [-1, 2**64])
def test_invalid_wan_byte_sample_cannot_fabricate_zero_throughput(bad) -> None:
    wans, _ = enrich_wan_interfaces(
        [{"id": "ISP"}], {"ISP": {"rxbytes": bad, "txbytes": bad}}, {}, [], 10.0
    )
    assert wans[0]["rx_bytes"] is None
    assert wans[0]["tx_bytes"] is None
    assert wans[0]["rx_throughput"] is None
    assert wans[0]["tx_throughput"] is None


@pytest.mark.parametrize("sensor_type", [KeeneticWanRxBytesSensor, KeeneticWanRxThroughputSensor])
async def test_partial_interface_stat_failure_is_unavailable_not_zero(
    keenetic_entry, sensor_type
) -> None:
    client = StageFixtureClient()
    client.wan_interfaces[0]["link_state"] = "up"
    coordinator = KeeneticCoordinator(object(), client)
    coordinator.last_update_success = True
    coordinator.data = await coordinator._async_update_data()
    sensor = sensor_type(coordinator, keenetic_entry, "PPPoE0")
    assert sensor.available
    # Drive the production aggregate: one target fails, its sibling succeeds.
    api = KeeneticClient("192.0.2.1", "admin", "test")
    api._rci_batch_supported = False
    api.async_get_interface_stat = AsyncMock(side_effect=[
        TimeoutError("stat endpoint unavailable"), {"rxbytes": 3_000, "txbytes": 4_000}
    ])
    client.async_get_all_interface_stats = api.async_get_all_interface_stats
    coordinator._refresh_count = 2  # medium-tier sample
    coordinator.data = await coordinator._async_update_data()
    assert coordinator.data["interface_stats_fresh"] is True
    assert "Wireguard0" in coordinator.data["interface_stats"]
    assert sensor.available is False
    assert sensor.native_value is None


@pytest.mark.parametrize("sensor_type", [KeeneticLanRxSensor, KeeneticWifi24RxSensor])
def test_partial_stat_loss_also_gates_other_interface_counters(
    keenetic_entry, keenetic_coordinator_factory, sensor_type
) -> None:
    coordinator = keenetic_coordinator_factory({
        "interface_stats": {}, "interface_stats_fresh": True,
    })
    sensor = sensor_type(coordinator, keenetic_entry)
    assert sensor.native_value is None
    assert sensor.available is False


@pytest.mark.parametrize("sensor_type", [KeeneticWanRxBytesSensor, KeeneticWanRxThroughputSensor])
def test_readable_zero_remains_available(
    keenetic_entry, keenetic_coordinator_factory, sensor_type
) -> None:
    data = {"wan_interfaces": [{
        "id": "ISP", "link_state": "up", "rx_bytes": 0, "rx_throughput": 0,
    }]}
    sensor = sensor_type(keenetic_coordinator_factory(data), keenetic_entry, "ISP")
    assert sensor.available is True
    assert sensor.native_value == 0
