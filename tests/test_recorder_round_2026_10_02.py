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


from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from unittest.mock import AsyncMock

from custom_components.keenetic_router_pro.api import KeeneticClient
from custom_components.keenetic_router_pro.sensor.network import (
    KeeneticActiveConnectionsSensor,
)
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


# --- 3. Active connections: band scales with the count ---


def _conns(used: int) -> dict:
    return {"system": {"conntotal": 63488, "connfree": 63488 - used}}


def test_active_connections_band_scales_on_a_busy_router() -> None:
    coordinator = SimpleNamespace(data=_conns(3000))
    sensor = KeeneticActiveConnectionsSensor(coordinator, _entry())
    assert sensor.native_value == 3000
    coordinator.data = _conns(3120)  # +4 %: noise on a busy router
    assert sensor.native_value == 3000
    coordinator.data = _conns(3200)  # +6.7 %
    assert sensor.native_value == 3200


def test_active_connections_keeps_fifty_floor_on_a_quiet_router() -> None:
    coordinator = SimpleNamespace(data=_conns(300))
    sensor = KeeneticActiveConnectionsSensor(coordinator, _entry())
    assert sensor.native_value == 300
    coordinator.data = _conns(340)
    assert sensor.native_value == 300
    coordinator.data = _conns(360)
    assert sensor.native_value == 360


# --- 4. DNS status reflects upstream health, not race losers ---


def _proxy(stat: str) -> dict:
    return {"proxy-status": [{"proxy-name": "Policy1", "proxy-config": "", "proxy-stat": stat}]}


async def _status(stat: str) -> str:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(return_value=_proxy(stat))
    return (await client.async_get_dns_proxy_status())["status"]


async def test_racing_upstreams_on_a_healthy_resolver_are_ok() -> None:
    # Live NH sample, 2026-10-02 21:22: 145 sent, 125 answered (13.8 % "failed").
    stat = "127.0.0.1  40532  127  110  0  36ms  34ms  8\n127.0.0.1  40533  18  15  0  91ms  87ms  4"
    assert await _status(stat) == "ok"


async def test_one_dead_upstream_is_degraded() -> None:
    stat = "127.0.0.1  40532  120  118  0  20ms  22ms  8\n127.0.0.1  40533  30  0  0  0ms  0ms  1"
    assert await _status(stat) == "degraded"


async def test_family_filter_answering_only_nxdomain_is_not_down() -> None:
    # Live PP sample: a family upstream that only blocked names in the window.
    stat = "127.0.0.1  40300  0  0  0  0ms  0ms  4\n127.0.0.1  40301  8  0  8  301ms  300ms  1"
    assert await _status(stat) == "ok"


async def test_no_answers_at_all_is_still_down() -> None:
    stat = "127.0.0.1  40500  30  0  0  0ms  0ms  4"
    assert await _status(stat) == "down"
