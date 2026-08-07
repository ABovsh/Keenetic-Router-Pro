"""Recorder-dither hardening shipped in 1.9.1.

Both defects here were found by reading the live recorder DB after 1.9.0: an
entity writing hundreds of rows a day while alternating between two adjacent
readings. Neither involves attributes — the state itself dithers.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.keenetic_router_pro.sensor.client import (
    KeeneticClientLastSeenSensor,
)
from custom_components.keenetic_router_pro.sensor.wifi import (
    KeeneticWifi5TemperatureSensor,
    KeeneticWifi24TemperatureSensor,
)
from custom_components.keenetic_router_pro.utils import apply_deadband

MAC = "aa:bb:cc:dd:ee:ff"


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router", data={})


def _coordinator(data: dict) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        last_update_success=True,
        client=SimpleNamespace(_host="192.0.2.1", _ssl=False),
    )


@pytest.mark.parametrize(
    ("previous", "new", "expected"),
    [
        (None, 59.0, 59.0),  # first reading always publishes
        (59.0, 61.0, 59.0),  # the measured 2 °C dither is held down
        (59.0, 59.0, 59.0),
        (59.0, 62.0, 62.0),  # a real move gets through
        (59.0, 55.0, 55.0),
        (59.0, 56.5, 59.0),  # 2.5 °C is still inside the band
    ],
)
def test_apply_deadband_holds_small_moves(previous, new, expected) -> None:
    assert apply_deadband(new, previous, 3.0) == expected


@pytest.mark.parametrize(
    ("sensor_cls", "iface"),
    [
        (KeeneticWifi24TemperatureSensor, "WifiMaster0/AccessPoint0"),
        (KeeneticWifi5TemperatureSensor, "WifiMaster1/AccessPoint0"),
    ],
)
def test_radio_temperature_stops_flip_flopping(sensor_cls, iface) -> None:
    """Measured live: 59.0 -> 61.0 -> 59.0 on every poll, ~1,000 rows/day."""
    coordinator = _coordinator({"interfaces": {iface: {"temperature": 59.0}}})
    sensor = sensor_cls(coordinator, _entry())

    assert sensor.native_value == 59.0
    for reading in (61.0, 59.0, 61.0, 60.0):
        coordinator.data["interfaces"][iface]["temperature"] = reading
        assert sensor.native_value == 59.0

    # A genuine change still comes through promptly.
    coordinator.data["interfaces"][iface]["temperature"] = 64.0
    assert sensor.native_value == 64.0


def test_radio_temperature_still_rejects_glitch_readings() -> None:
    """The deadband must not become a way for an implausible value to stick."""
    iface = "WifiMaster0/AccessPoint0"
    coordinator = _coordinator({"interfaces": {iface: {"temperature": 59.0}}})
    sensor = KeeneticWifi24TemperatureSensor(coordinator, _entry())
    assert sensor.native_value == 59.0

    coordinator.data["interfaces"][iface]["temperature"] = 999.0
    assert sensor.native_value is None


def test_last_seen_ignores_one_second_recompute_jitter() -> None:
    """Measured live: '…19:25:10' <-> '…19:25:11' alternating every poll.

    The value is computed as now-minus-elapsed, so it wobbles by a second
    whenever the router's counter and our clock disagree on rounding.
    """
    client = {"mac": MAC, "active": False, "last-seen": 600}
    coordinator = _coordinator({"clients_by_mac": {MAC: client}})
    sensor = KeeneticClientLastSeenSensor(coordinator, _entry(), MAC, "Laptop")

    first = sensor.native_value
    assert first is not None

    for elapsed in (601, 600, 602, 599):
        client["last-seen"] = elapsed
        assert sensor.native_value == first


def test_last_seen_moves_when_the_client_is_seen_again() -> None:
    """A real new sighting resets the elapsed counter and must be published."""
    client = {"mac": MAC, "active": False, "last-seen": 3600}
    coordinator = _coordinator({"clients_by_mac": {MAC: client}})
    sensor = KeeneticClientLastSeenSensor(coordinator, _entry(), MAC, "Laptop")

    old = sensor.native_value
    client["last-seen"] = 30
    assert sensor.native_value != old
