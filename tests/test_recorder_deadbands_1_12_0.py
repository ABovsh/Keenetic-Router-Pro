"""Recorder-write deadbands on the traffic counters and connection gauge (1.12.0).

Measured on the live recorder DB 2026-08-26: keenetic wrote 10 052 rows/day,
only 3.2 % of which were attribute-driven — the atttribute cleanup rounds are
done. What is left is genuine value churn on four groups that carry far more
resolution than anyone reads:

    *_rx / *_tx (GiB)      3 065 rows/day at a 0.01 GiB (10.7 MB) step
    *_bytes (raw B)        2 686 rows/day, undamped
    *_throughput           2 223 rows/day at a 10 kbit/s floor
    *_active_connections     993 rows/day, undamped

A counter in the tens of GB does not need 10 MB resolution and a 100 Mbit link
does not need 10 kbit resolution. Widening the step is invisible on a graph and
keeps long-term statistics intact — unlike a recorder ``exclude``, which was
verified on this host to suppress statistics entirely.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.keenetic_router_pro.entity import ThroughputDeadbandMixin
from custom_components.keenetic_router_pro.sensor.network import (
    KeeneticActiveConnectionsSensor,
    KeeneticWanRxBytesSensor,
    KeeneticWanRxThroughputSensor,
)
from custom_components.keenetic_router_pro.sensor.traffic import KeeneticLanRxSensor

GIB = 1024**3


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router", data={})


def _iface_coordinator(rxbytes: int) -> SimpleNamespace:
    return SimpleNamespace(
        data={"interface_stats": {"GigabitEthernet0": {"rxbytes": rxbytes}}}
    )


def _wan_coordinator(**fields: object) -> SimpleNamespace:
    wan = {"id": "PPPoE0", **fields}
    return SimpleNamespace(data={"wan_interfaces": [wan], "wan_by_id": {"PPPoE0": wan}})


# --- 1. GiB interface counters: 0.05 GiB step, not 0.01 ---


def test_interface_counter_holds_below_the_step() -> None:
    coordinator = _iface_coordinator(int(43.70 * GIB))
    sensor = KeeneticLanRxSensor(coordinator, _entry())
    assert sensor.native_value == 43.7

    # +20 MB: a real move, but far under anything a graph can render.
    coordinator.data["interface_stats"]["GigabitEthernet0"]["rxbytes"] += 20_000_000
    assert sensor.native_value == 43.7


def test_interface_counter_publishes_a_real_move() -> None:
    coordinator = _iface_coordinator(int(43.70 * GIB))
    sensor = KeeneticLanRxSensor(coordinator, _entry())
    assert sensor.native_value == 43.7

    coordinator.data["interface_stats"]["GigabitEthernet0"]["rxbytes"] += 100_000_000
    assert sensor.native_value == 43.79


def test_interface_counter_gap_clears_the_latch() -> None:
    """A missing stat row must not leave a stale baseline latched."""
    coordinator = _iface_coordinator(int(43.70 * GIB))
    sensor = KeeneticLanRxSensor(coordinator, _entry())
    assert sensor.native_value == 43.7

    coordinator.data["interface_stats"] = {}
    assert sensor.native_value is None

    coordinator.data["interface_stats"] = {"GigabitEthernet0": {"rxbytes": 1_000_000}}
    assert sensor.native_value == 0.0


# --- 2. Raw WAN byte counters: 50 MB step ---


def test_wan_bytes_holds_below_the_step() -> None:
    coordinator = _wan_coordinator(rx_bytes=39_000_000_000)
    sensor = KeeneticWanRxBytesSensor(coordinator, _entry(), "PPPoE0")
    assert sensor.native_value == 39_000_000_000

    coordinator.data["wan_by_id"]["PPPoE0"]["rx_bytes"] = 39_010_000_000
    assert sensor.native_value == 39_000_000_000


def test_wan_bytes_publishes_a_real_move() -> None:
    coordinator = _wan_coordinator(rx_bytes=39_000_000_000)
    sensor = KeeneticWanRxBytesSensor(coordinator, _entry(), "PPPoE0")
    assert sensor.native_value == 39_000_000_000

    coordinator.data["wan_by_id"]["PPPoE0"]["rx_bytes"] = 39_060_000_000
    assert sensor.native_value == 39_060_000_000


def test_wan_bytes_publishes_a_counter_reset_immediately() -> None:
    """A router reboot zeroes the counter; TOTAL_INCREASING needs to see it."""
    coordinator = _wan_coordinator(rx_bytes=39_000_000_000)
    sensor = KeeneticWanRxBytesSensor(coordinator, _entry(), "PPPoE0")
    assert sensor.native_value == 39_000_000_000

    coordinator.data["wan_by_id"]["PPPoE0"]["rx_bytes"] = 1_024
    assert sensor.native_value == 1_024


# --- 3. Active connections: a dithering gauge, 25-connection deadband ---


def test_active_connections_holds_small_dither() -> None:
    coordinator = SimpleNamespace(data={"system": {"conntotal": 63488, "connfree": 63188}})
    sensor = KeeneticActiveConnectionsSensor(coordinator, _entry())
    assert sensor.native_value == 300

    # 300 -> 310: measured live, this gauge walks ±30 all day long.
    coordinator.data["system"] = {"conntotal": 63488, "connfree": 63178}
    assert sensor.native_value == 300


def test_active_connections_publishes_a_real_move() -> None:
    coordinator = SimpleNamespace(data={"system": {"conntotal": 63488, "connfree": 63188}})
    sensor = KeeneticActiveConnectionsSensor(coordinator, _entry())
    assert sensor.native_value == 300

    coordinator.data["system"] = {"conntotal": 63488, "connfree": 63088}
    assert sensor.native_value == 400


# --- 4. Throughput: 5 % band over a 100 kbit/s floor ---


def test_throughput_floor_is_one_hundred_kbit() -> None:
    assert ThroughputDeadbandMixin._THROUGHPUT_FLOOR == 100_000.0
    assert ThroughputDeadbandMixin._THROUGHPUT_DEADBAND == 0.05


def test_wan_throughput_holds_below_the_floor() -> None:
    coordinator = _wan_coordinator(rx_throughput=10_000.0)  # B/s -> 80 kbit/s
    sensor = KeeneticWanRxThroughputSensor(coordinator, _entry(), "PPPoE0")
    assert sensor.native_value == 80_000.0

    coordinator.data["wan_by_id"]["PPPoE0"]["rx_throughput"] = 17_500.0  # 140 kbit/s
    assert sensor.native_value == 80_000.0


def test_wan_throughput_publishes_a_move_past_the_floor() -> None:
    coordinator = _wan_coordinator(rx_throughput=10_000.0)
    sensor = KeeneticWanRxThroughputSensor(coordinator, _entry(), "PPPoE0")
    assert sensor.native_value == 80_000.0

    coordinator.data["wan_by_id"]["PPPoE0"]["rx_throughput"] = 30_000.0  # 240 kbit/s
    assert sensor.native_value == 240_000.0


def test_wan_throughput_still_publishes_a_link_going_quiet() -> None:
    coordinator = _wan_coordinator(rx_throughput=1_000_000.0)
    sensor = KeeneticWanRxThroughputSensor(coordinator, _entry(), "PPPoE0")
    assert sensor.native_value == 8_000_000.0

    coordinator.data["wan_by_id"]["PPPoE0"]["rx_throughput"] = 0.0
    assert sensor.native_value == 0.0
