"""Efficiency and capability work shipped in 1.10.0.

Three groups here:

* recorder — the last measured instance of the volatile-attribute class
  (``fail_count``), plus a shared deadband so the next gauge is correct by
  default instead of needing its own hand-rolled fix.
* runtime — skip RCI subtrees nothing consumes, and stretch the poll interval
  while the router is genuinely idle.
* capability — device triggers, outage statistics, richer diagnostics.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.keenetic_router_pro.coordinator_parts.refresh import (
    build_batch_tree,
    idle_poll_interval,
    refresh_plan,
)
from custom_components.keenetic_router_pro.utils import apply_relative_deadband

MAC = "aa:bb:cc:dd:ee:ff"


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router", data={}, options={})


def _coordinator(data: dict) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        last_update_success=True,
        client=SimpleNamespace(_host="192.0.2.1", _ssl=False),
    )


# --------------------------------------------------------------------------
# 1. fail_count — the last instance of the volatile-attribute defect class
# --------------------------------------------------------------------------


def _ping_check_wan(fail_count: int, passing: bool = True) -> dict[str, Any]:
    return {
        "id": "ISP",
        "connected": True,
        "ping_check": {
            "passing": passing,
            "fail_count": fail_count,
            "max_fails": 3,
            "check_hosts": ["8.8.8.8"],
        },
    }


def test_fail_count_is_not_published_while_the_check_passes() -> None:
    """Measured live: fail_count dithered 0->1->0->1 on a healthy link.

    96 of 104 rows/day on the backup WAN binary sensor were this attribute
    alone. A single lost ping bumps the counter and the next success clears
    it, so on a passing check the value carries no information at all.
    """
    from custom_components.keenetic_router_pro.binary_sensor import (
        KeeneticWanConnectedSensor,
    )

    coordinator = _coordinator({"wan_interfaces": [_ping_check_wan(1)]})
    sensor = KeeneticWanConnectedSensor(coordinator, _entry(), "ISP")

    attrs = sensor.extra_state_attributes or {}
    assert "fail_count" not in attrs
    # The static context stays — only the churning counter goes.
    assert attrs.get("max_fails") == 3


def test_fail_count_is_published_once_the_check_is_actually_failing() -> None:
    """When the check fails the counter is the whole diagnostic story."""
    from custom_components.keenetic_router_pro.binary_sensor import (
        KeeneticWanConnectedSensor,
    )

    coordinator = _coordinator(
        {"wan_interfaces": [_ping_check_wan(2, passing=False)]}
    )
    sensor = KeeneticWanConnectedSensor(coordinator, _entry(), "ISP")

    assert (sensor.extra_state_attributes or {}).get("fail_count") == 2


# --------------------------------------------------------------------------
# 2/3. relative deadband + the shared mixin
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("previous", "new", "expected"),
    [
        (None, 5_000_000.0, 5_000_000.0),  # first reading always publishes
        (5_000_000.0, 5_010_000.0, 5_000_000.0),  # 0.2 % — held
        (5_000_000.0, 5_200_000.0, 5_200_000.0),  # 4 % — published
        (5_000_000.0, 0.0, 0.0),  # link went idle: always publish
        (0.0, 5_000.0, 5_000.0),  # and coming back off zero
        (0.0, 0.0, 0.0),
    ],
)
def test_apply_relative_deadband(previous, new, expected) -> None:
    assert apply_relative_deadband(new, previous, 0.02, floor=1_000.0) == expected


def test_relative_deadband_floor_flattens_idle_noise() -> None:
    """A quiet link still shows keepalive jitter; the floor absorbs it."""
    assert apply_relative_deadband(400.0, 0.0, 0.02, floor=1_000.0) == 0.0


def test_deadband_mixin_resets_on_none() -> None:
    """An unavailable reading must not leave a stale value latched."""
    from custom_components.keenetic_router_pro.entity import DeadbandMixin

    class _Probe(DeadbandMixin):
        _DEADBAND = 3.0

    probe = _Probe()
    assert probe._apply_deadband(59.0) == 59.0
    assert probe._apply_deadband(61.0) == 59.0
    assert probe._apply_deadband(None) is None
    # After the gap the next reading is a fresh baseline, not held at 59.
    assert probe._apply_deadband(61.0) == 61.0


def test_cpu_load_stops_dithering_between_zero_and_one() -> None:
    """Measured live: CPU load alternated 0.0 <-> 1.0 all day, ~630 rows/day."""
    from custom_components.keenetic_router_pro.sensor.system import (
        KeeneticCpuLoadSensor,
    )

    coordinator = _coordinator({"system": {"cpu_load": 0.0}})
    sensor = KeeneticCpuLoadSensor(coordinator, _entry())

    assert sensor.native_value == 0.0
    for reading in (1.0, 0.0, 2.0, 1.0):
        coordinator.data["system"]["cpu_load"] = reading
        assert sensor.native_value == 0.0

    coordinator.data["system"]["cpu_load"] = 40.0
    assert sensor.native_value == 40.0


def test_wan_throughput_holds_sub_percent_jitter() -> None:
    from custom_components.keenetic_router_pro.sensor.network import (
        KeeneticWanRxThroughputSensor,
    )

    wan = {"id": "ISP", "rx_throughput": 1_000_000.0}
    coordinator = _coordinator({"wan_interfaces": [wan]})
    sensor = KeeneticWanRxThroughputSensor(coordinator, _entry(), "ISP")

    first = sensor.native_value
    assert first == pytest.approx(8_000_000.0)

    wan["rx_throughput"] = 1_000_500.0  # 0.05 % — no dashboard can see this
    assert sensor.native_value == first

    wan["rx_throughput"] = 2_000_000.0  # a real doubling gets through
    assert sensor.native_value == pytest.approx(16_000_000.0)


# --------------------------------------------------------------------------
# 4. skip RCI subtrees nothing consumes
# --------------------------------------------------------------------------


def test_batch_tree_skips_the_client_tree_when_nothing_consumes_it() -> None:
    """show/ip/hotspot is the largest payload of the tick by a wide margin."""
    plan = refresh_plan(first_refresh=False, refresh_count=1)

    full = build_batch_tree(plan)
    assert "hotspot" in full["show"]["ip"]

    lean = build_batch_tree(plan, needs_clients=False)
    assert "hotspot" not in lean.get("show", {}).get("ip", {})
    # Everything the router entities need is still requested.
    assert "system" in lean["show"]
    assert "interface" in lean["show"]


def test_batch_tree_requests_clients_by_default() -> None:
    """Omitting the argument must never silently drop client data."""
    plan = refresh_plan(first_refresh=True, refresh_count=0)
    assert "hotspot" in build_batch_tree(plan)["show"]["ip"]


# --------------------------------------------------------------------------
# 5. adaptive backoff on a genuinely idle router
# --------------------------------------------------------------------------


def test_idle_poll_interval_stays_fast_until_the_router_is_quiet() -> None:
    assert idle_poll_interval(0) == 30
    assert idle_poll_interval(5) == 30
    assert idle_poll_interval(19) == 30


def test_idle_poll_interval_stretches_then_caps() -> None:
    assert idle_poll_interval(20) == 60
    assert idle_poll_interval(40) == 90
    assert idle_poll_interval(60) == 120
    assert idle_poll_interval(10_000) == 120  # capped, never unbounded


def test_idle_streak_resets_the_moment_anything_moves() -> None:
    """Snapping back must be instant — a stretched poll cannot delay a failover."""
    assert idle_poll_interval(0) == 30


# --------------------------------------------------------------------------
# 6. device triggers
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_device_triggers_expose_the_integration_events() -> None:
    """The 1.9.0 events were only reachable by hand-written YAML until now."""
    from custom_components.keenetic_router_pro import device_trigger

    triggers = await device_trigger.async_get_triggers(None, "device_id_1")
    types = {t["type"] for t in triggers}
    assert types == {"client_connected", "client_disconnected", "wan_failover"}
    for trigger in triggers:
        assert trigger["domain"] == "keenetic_router_pro"
        assert trigger["device_id"] == "device_id_1"


# --------------------------------------------------------------------------
# 7. outage statistics
# --------------------------------------------------------------------------


def test_failover_count_increments_once_per_failover() -> None:
    from custom_components.keenetic_router_pro.sensor.network import (
        KeeneticWanFailoverCountSensor,
    )

    coordinator = _coordinator({"active_wan": "ISP", "wan_failover": None})
    sensor = KeeneticWanFailoverCountSensor(coordinator, _entry())
    sensor.async_write_ha_state = lambda: None

    assert sensor.native_value == 0

    coordinator.data["wan_failover"] = {"from": "ISP", "to": "LTE"}
    sensor._handle_coordinator_update()
    assert sensor.native_value == 1

    # A tick with no failover must not keep counting.
    coordinator.data["wan_failover"] = None
    sensor._handle_coordinator_update()
    assert sensor.native_value == 1


def test_downtime_accrues_only_while_every_wan_is_down() -> None:
    from custom_components.keenetic_router_pro.sensor.network import (
        KeeneticWanDowntimeSensor,
    )

    coordinator = _coordinator({"active_wan": "ISP"})
    sensor = KeeneticWanDowntimeSensor(coordinator, _entry())
    sensor.async_write_ha_state = lambda: None

    clock = iter([100.0, 160.0, 220.0, 280.0])
    sensor._now = lambda: next(clock)

    sensor._handle_coordinator_update()  # t=100, up
    assert sensor.native_value == 0

    coordinator.data["active_wan"] = None
    sensor._handle_coordinator_update()  # t=160, first down tick
    assert sensor.native_value == 0  # nothing accrued yet, outage just started

    sensor._handle_coordinator_update()  # t=220, still down for 60 s
    assert sensor.native_value == 60

    coordinator.data["active_wan"] = "LTE"
    sensor._handle_coordinator_update()  # t=280, recovered
    assert sensor.native_value == 120


# --------------------------------------------------------------------------
# 8. diagnostics health block
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diagnostics_include_a_health_block() -> None:
    """Makes a bug report self-contained without a single extra API call."""
    from custom_components.keenetic_router_pro.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    coordinator = SimpleNamespace(
        data={"system": {}},
        last_update_success=True,
        update_interval=None,
        _refresh_count=1234,
        _idle_streak=7,
        client=SimpleNamespace(
            _rci_batch_supported=True,
            _ping_check_supported=False,
            _ndns_supported=None,
        ),
    )
    entry = SimpleNamespace(
        title="Router",
        version=1,
        domain="keenetic_router_pro",
        source="user",
        data={},
        options={},
        runtime_data=SimpleNamespace(coordinator=coordinator, client=None),
    )

    result = await async_get_config_entry_diagnostics(None, entry)
    health = result["health"]
    assert health["refresh_count"] == 1234
    assert health["last_update_success"] is True
    # The capability latches explain "why is feature X missing?" — the single
    # most common question a Keenetic bug report has to answer.
    assert health["capabilities"]["ping_check"] is False
    assert health["capabilities"]["rci_batch"] is True


# --------------------------------------------------------------------------
# 9. generic interface enable switch
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("iface_id", "iface_type", "eligible"),
    [
        ("GigabitEthernet1", "GigabitEthernet", True),
        ("WifiMaster0/AccessPoint1", "AccessPoint", True),
        ("Bridge0", "Bridge", False),  # disabling this locks you out
        ("Home", "Bridge", False),
        ("GigabitEthernet0", "GigabitEthernet", False),  # management port
    ],
)
def test_only_safe_interfaces_get_an_enable_switch(
    iface_id, iface_type, eligible
) -> None:
    """A switch that can strand the router behind its own bridge is a trap."""
    from custom_components.keenetic_router_pro.switch import (
        is_interface_switchable,
    )

    assert is_interface_switchable(iface_id, iface_type) is eligible


def test_client_count_sensors_are_pruned_when_the_tree_is_not_fetched() -> None:
    """Otherwise they linger as orphans reporting a confident zero clients."""
    from custom_components.keenetic_router_pro import (
        _CLIENT_COUNT_SUFFIXES,
        _needs_client_data,
    )
    from custom_components.keenetic_router_pro.const import (
        CLIENT_SENSORS_OFF,
        CONF_CLIENT_SENSORS,
        CONF_TRACKED_CLIENTS,
    )

    off_and_untracked = SimpleNamespace(
        entry_id="e", data={}, options={CONF_CLIENT_SENSORS: CLIENT_SENSORS_OFF}
    )
    assert _needs_client_data(off_and_untracked) is False

    # A tracked client still needs the tree even with sensors off.
    off_but_tracked = SimpleNamespace(
        entry_id="e",
        data={CONF_TRACKED_CLIENTS: [MAC]},
        options={CONF_CLIENT_SENSORS: CLIENT_SENSORS_OFF},
    )
    assert _needs_client_data(off_but_tracked) is True

    # And the default configuration must never lose client data.
    assert _needs_client_data(SimpleNamespace(entry_id="e", data={}, options={})) is True

    assert "connected_clients_v2" in _CLIENT_COUNT_SUFFIXES


def test_interface_enable_switch_reflects_link_state() -> None:
    from custom_components.keenetic_router_pro.switch import (
        KeeneticInterfaceEnabledSwitch,
    )

    iface = {"type": "GigabitEthernet", "state": "up"}
    coordinator = _coordinator({"interfaces": {"GigabitEthernet1": iface}})
    switch = KeeneticInterfaceEnabledSwitch(
        coordinator=coordinator,
        entry=_entry(),
        client=SimpleNamespace(),
        iface_id="GigabitEthernet1",
    )

    assert switch.is_on is True
    iface["state"] = "down"
    assert switch.is_on is False
    # An explicit enabled flag wins over the derived link state.
    iface["enabled"] = True
    assert switch.is_on is True
    # It must stay off by default: nobody expects HA to be able to kill a port.
    assert switch._attr_entity_registry_enabled_default is False


# --------------------------------------------------------------------------
# Adversarial-review fixes
# --------------------------------------------------------------------------


def test_failover_count_ignores_a_tick_that_failed() -> None:
    """The listener also fires on the success->failure transition.

    On that call ``coordinator.data`` still holds the previous payload, so a
    failover followed by one failed tick — a very ordinary sequence, since the
    link that just changed is often the one that hiccups — counted twice.
    """
    from custom_components.keenetic_router_pro.sensor.network import (
        KeeneticWanFailoverCountSensor,
    )

    coordinator = _coordinator({"active_wan": "LTE", "wan_failover": {"to": "LTE"}})
    sensor = KeeneticWanFailoverCountSensor(coordinator, _entry())
    sensor.async_write_ha_state = lambda: None

    sensor._handle_coordinator_update()
    assert sensor.native_value == 1

    # Next tick fails: same stale payload, must not count again.
    coordinator.last_update_success = False
    sensor._handle_coordinator_update()
    assert sensor.native_value == 1
