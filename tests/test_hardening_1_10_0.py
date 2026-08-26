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

import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.keenetic_router_pro.coordinator_parts.refresh import (
    build_batch_tree,
    refresh_plan,
)
from custom_components.keenetic_router_pro.utils import apply_relative_deadband

@contextmanager
def _patched_registries(**modules):
    """Swap registry modules in, then put the originals BACK.

    Popping them instead would delete conftest's own stubs and break every
    later test in the session — which is exactly what happened first time.
    """
    saved = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        yield
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


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
# 5. adaptive backoff on a genuinely idle router — REMOVED in 1.13.0.
# The ladder classified a continuously busy router as idle (see
# tests/test_poll_tiers_1_13_0.py) and its stretch multiplied through the
# tick-counted refresh tiers. The poll is a flat FAST_SCAN_INTERVAL now.
# --------------------------------------------------------------------------


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


def test_untracking_a_client_removes_its_entities() -> None:
    """The suffix sweep is driven by the CURRENT config, so a MAC removed from
    the tracked list was never visited at all — its entities stayed registered
    as unavailable forever, which is the exact thing this prune exists to stop.
    """
    from custom_components.keenetic_router_pro import _async_prune_client_entities

    gone = "11:22:33:44:55:66"
    kept = "aa:bb:cc:dd:ee:ff"

    class _Entry(SimpleNamespace):
        pass

    entry = _Entry(
        entry_id="e1",
        data={"tracked_clients": [{"mac": kept, "name": "Kept"}]},
        options={},
    )

    removed: list[str] = []

    class _Registry:
        def __init__(self) -> None:
            self.entities = {
                "sensor.gone_ip": f"e1_client_{gone}_ip",
                "sensor.kept_ip": f"e1_client_{kept}_ip",
                "sensor.other": "e1_cpu_load",
            }

        def async_get_entity_id(self, _domain, _platform, unique_id):
            for eid, uid in self.entities.items():
                if uid == unique_id:
                    return eid
            return None

        def async_remove(self, entity_id):
            removed.append(entity_id)

    registry = _Registry()

    entries = [
        SimpleNamespace(entity_id=eid, unique_id=uid, domain="sensor")
        for eid, uid in registry.entities.items()
    ]
    er_mod = types.ModuleType("homeassistant.helpers.entity_registry")
    er_mod.async_get = lambda _hass: registry
    er_mod.async_entries_for_config_entry = lambda _reg, _eid: entries
    with _patched_registries(**{"homeassistant.helpers.entity_registry": er_mod}):
        _async_prune_client_entities(None, entry)

    assert "sensor.gone_ip" in removed
    # A client that is still tracked, and unrelated entities, must survive.
    assert "sensor.kept_ip" not in removed
    assert "sensor.other" not in removed


def test_dns_failed_requests_survives_a_non_finite_firmware_value() -> None:
    """int(inf) raises OverflowError, which the local except tuple missed.

    Every other counter in the codebase routes through coerce_int, which
    already covers this; these two sensors hand-rolled it and diverged.
    """
    from custom_components.keenetic_router_pro.sensor.dns import (
        KeeneticDnsProxyFailedRequestsSensor,
    )

    coordinator = _coordinator({"dns_proxy": {"failed_requests": float("inf")}})
    sensor = KeeneticDnsProxyFailedRequestsSensor(coordinator, _entry())
    assert sensor.native_value is None


def test_downtime_does_not_close_an_outage_on_an_unreachable_router() -> None:
    """A failed tick leaves the PREVIOUS payload in place.

    That payload still names an active WAN, so the sensor read the router
    going away as the WAN coming back and stopped accruing — the worst
    outages, where the router itself is unreachable, recorded zero.
    """
    from custom_components.keenetic_router_pro.sensor.network import (
        KeeneticWanDowntimeSensor,
    )

    coordinator = _coordinator({"active_wan": None})
    sensor = KeeneticWanDowntimeSensor(coordinator, _entry())
    sensor.async_write_ha_state = lambda: None

    # The failed tick reads no clock at all — it holds state untouched.
    clock = iter([100.0, 220.0])
    sensor._now = lambda: next(clock)

    sensor._handle_coordinator_update()  # t=100, outage begins

    # Router drops off entirely: stale payload claims a working WAN.
    coordinator.data["active_wan"] = "ISP"
    coordinator.last_update_success = False
    sensor._handle_coordinator_update()  # must not end the outage

    coordinator.last_update_success = True
    coordinator.data["active_wan"] = None
    sensor._handle_coordinator_update()  # t=220, still down
    assert sensor.native_value == 120


# --------------------------------------------------------------------------
# 1.11.0 — the interface switch must not litter the device registry
# --------------------------------------------------------------------------


def test_stale_devices_with_no_entities_are_removed() -> None:
    """Renaming an identifier scheme orphans the OLD device row.

    Mesh nodes moved to entry-scoped identifiers and clients did too, but
    nothing ever removed the devices left behind — and a phone rotating its
    MAC strands one every time. A device with no entities cannot come back.
    """
    from custom_components.keenetic_router_pro import _async_remove_stale_devices

    removed: list[str] = []

    live = SimpleNamespace(id="dev_live", identifiers={("keenetic_router_pro", "a")})
    stale = SimpleNamespace(id="dev_stale", identifiers={("keenetic_router_pro", "b")})

    class _DevReg:
        def async_remove_device(self, device_id):
            removed.append(device_id)

    class _EntReg:
        pass

    dr_mod = types.ModuleType("homeassistant.helpers.device_registry")
    dr_mod.async_get = lambda _hass: _DevReg()
    dr_mod.async_entries_for_config_entry = lambda _reg, _eid: [live, stale]

    er_mod = types.ModuleType("homeassistant.helpers.entity_registry")
    er_mod.async_get = lambda _hass: _EntReg()
    er_mod.async_entries_for_device = lambda _reg, device_id, include_disabled_entities: (
        [SimpleNamespace(entity_id="sensor.x")] if device_id == "dev_live" else []
    )

    with _patched_registries(
        **{
            "homeassistant.helpers.device_registry": dr_mod,
            "homeassistant.helpers.entity_registry": er_mod,
        }
    ):
        _async_remove_stale_devices(None, SimpleNamespace(entry_id="e1"))

    assert removed == ["dev_stale"]


def test_stale_device_sweep_counts_disabled_entities_as_alive() -> None:
    """A device whose only entities are disabled is still wanted.

    Deleting it would silently destroy the user's decision to turn those
    entities off.
    """
    from custom_components.keenetic_router_pro import _async_remove_stale_devices

    removed: list[str] = []
    dev = SimpleNamespace(id="dev_1", identifiers={("keenetic_router_pro", "a")})

    dr_mod = types.ModuleType("homeassistant.helpers.device_registry")
    dr_mod.async_get = lambda _hass: SimpleNamespace(
        async_remove_device=lambda device_id: removed.append(device_id)
    )
    dr_mod.async_entries_for_config_entry = lambda _reg, _eid: [dev]

    seen: dict = {}

    def _entries_for_device(_reg, device_id, include_disabled_entities):
        seen["include_disabled"] = include_disabled_entities
        return [SimpleNamespace(entity_id="switch.x", disabled_by="user")]

    er_mod = types.ModuleType("homeassistant.helpers.entity_registry")
    er_mod.async_get = lambda _hass: object()
    er_mod.async_entries_for_device = _entries_for_device

    with _patched_registries(
        **{
            "homeassistant.helpers.device_registry": dr_mod,
            "homeassistant.helpers.entity_registry": er_mod,
        }
    ):
        _async_remove_stale_devices(None, SimpleNamespace(entry_id="e1"))

    assert seen["include_disabled"] is True
    assert removed == []


def test_interface_enable_switches_are_removed_from_the_registry() -> None:
    """1.10.0 shipped one per interface, each on its own device.

    They duplicated the Wi-Fi/WAN/VPN switches that already existed and left
    dozens of empty-looking devices behind. Disabled entities are never added
    to their platform again, so setup has to delete them explicitly.
    """
    from custom_components.keenetic_router_pro import _async_prune_interface_switches

    removed: list[str] = []
    entries = [
        SimpleNamespace(
            entity_id="switch.ap5",
            unique_id="e1_interface_WifiMaster0/AccessPoint5_enabled",
        ),
        SimpleNamespace(entity_id="switch.port1", unique_id="e1_interface_1_enabled"),
        SimpleNamespace(entity_id="sensor.cpu", unique_id="e1_cpu_load"),
        SimpleNamespace(entity_id="switch.wifi", unique_id="e1_wifi_WifiMaster0"),
    ]

    er_mod = types.ModuleType("homeassistant.helpers.entity_registry")
    er_mod.async_get = lambda _hass: SimpleNamespace(
        async_remove=lambda entity_id: removed.append(entity_id)
    )
    er_mod.async_entries_for_config_entry = lambda _reg, _eid: entries

    with _patched_registries(**{"homeassistant.helpers.entity_registry": er_mod}):
        _async_prune_interface_switches(None, SimpleNamespace(entry_id="e1"))

    assert removed == ["switch.ap5", "switch.port1"]
