"""Recorder round measured on the live DB, 2026-10-01 20:30 → 2026-10-02 20:30.

Keenetic wrote 3,762 ``states`` rows and 35,997 short-term statistics rows in
that window. The changes below remove rows that carried nothing:

* the fixed "WAN" RX/TX counters duplicate the per-WAN byte counters, so
  both wrote the same long-term statistics. The fixed pair read
  GigabitEthernet1, the per-WAN pair the actual uplink;
* router memory flipped between two adjacent percents 187 times a day;
* active connections (thousands on a busy router) stepped over the fixed
  50-connection band 308 times a day;
* DNS proxy status flipped ok/degraded 51 times a day on a healthy
  resolver. About 13 % of queries go unanswered because the proxy races two
  upstreams and drops the slower answer.
"""

from __future__ import annotations

from types import SimpleNamespace



from custom_components.keenetic_router_pro.sensor.system import KeeneticMemoryUsageSensor
from custom_components.keenetic_router_pro.sensor.traffic import (
    KeeneticLanRxSensor,
    KeeneticLanTxSensor,
    KeeneticWanRxSensor,
    KeeneticWanTxSensor,
)


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry", title="Router", data={}, options={})


# --- 1. Fixed WAN counters stop duplicating long-term statistics ---


def test_fixed_wan_counters_publish_no_statistics() -> None:
    for cls in (KeeneticWanRxSensor, KeeneticWanTxSensor):
        assert cls._attr_state_class is None


def test_lan_counters_keep_no_statistics() -> None:
    # Dropped in the 2026-10-03 usability round: one LAN-wide total whose
    # history nobody reads.
    for cls in (KeeneticLanRxSensor, KeeneticLanTxSensor):
        assert cls._attr_state_class is None


# --- 2. Router memory holds within two percentage points ---


def _memory(used: int) -> dict:
    return {"system": {"memory": f"{used}/1000"}}


def test_router_memory_ignores_one_point_flips() -> None:
    coordinator = SimpleNamespace(data=_memory(350))
    sensor = KeeneticMemoryUsageSensor(coordinator, _entry())
    assert sensor.native_value == 35
    for used in (360, 350, 360):
        coordinator.data = _memory(used)
        assert sensor.native_value == 35
    coordinator.data = _memory(380)
    assert sensor.native_value == 38


def test_router_memory_drops_latch_when_reading_disappears() -> None:
    coordinator = SimpleNamespace(data=_memory(350))
    sensor = KeeneticMemoryUsageSensor(coordinator, _entry())
    assert sensor.native_value == 35
    coordinator.data = {"system": {}}
    assert sensor.native_value is None
    coordinator.data = _memory(360)
    assert sensor.native_value == 36
