"""Every data counter is damped, and the audit that keeps it that way (1.14.0).

1.12.0 put a deadband on two byte-counter bases and measured a win in
simulation. On hardware the rate went UP, and the reason was that byte counters
live in SIX places, not two: ``traffic.py`` and ``network.py`` were damped,
while ``crypto.py``, ``wireguard.py``, ``wifi.py`` and ``client.py`` were not.
That is the failure the repo checklist names outright — "a hand-rolled
per-sensor fix is precisely how the next instance gets missed".

So the fix is not a fifth and sixth hand-rolled deadband. It is one shared step
in bytes, applied through the existing ``DeadbandMixin`` at every counter base,
plus an audit test that fails the moment a seventh counter is added without one.

Measured on 183 restart-free minutes of live 1.13.0 data: the 32 data-size
counters wrote 8 424 rows/day, and a single 250 MB step takes that to 801.
"""

from __future__ import annotations

import inspect
import pkgutil
from importlib import import_module
from types import SimpleNamespace

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from custom_components.keenetic_router_pro import sensor as sensor_pkg
from custom_components.keenetic_router_pro.const import COUNTER_DEADBAND_BYTES
from custom_components.keenetic_router_pro.entity import ThroughputDeadbandMixin
from custom_components.keenetic_router_pro.sensor.crypto import (
    KeeneticCryptoMapRxBytesSensor,
)
from custom_components.keenetic_router_pro.sensor.network import (
    KeeneticActiveConnectionsSensor,
)
from custom_components.keenetic_router_pro.sensor.wifi import KeeneticWifi24RxSensor
from custom_components.keenetic_router_pro.sensor.wireguard import KeeneticWgRxSensor

GIB = 1024**3
MIB = 1024**2


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router", data={})


def _sensor_classes() -> list[type]:
    classes: list[type] = []
    for mod in pkgutil.iter_modules(sensor_pkg.__path__):
        module = import_module(f"{sensor_pkg.__name__}.{mod.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, SensorEntity) and obj.__module__ == module.__name__:
                classes.append(obj)
    return classes


# --- 1. The audit: no data counter ships undamped ---


def test_every_data_size_sensor_declares_a_deadband() -> None:
    """A counter on a live link moves every poll; undamped that is a row a poll."""
    counters = [
        cls
        for cls in _sensor_classes()
        if getattr(cls, "_attr_device_class", None) == SensorDeviceClass.DATA_SIZE
    ]
    # An audit that silently discovers nothing is worse than no audit: it
    # reports success forever. Pin the floor so a broken walk fails loudly.
    assert len(counters) >= 10, f"discovery found only {len(counters)} counters"

    undamped = [c.__name__ for c in counters if not getattr(c, "_DEADBAND", 0)]
    assert undamped == [], (
        "these DATA_SIZE sensors write a recorder row on every poll: "
        f"{sorted(undamped)}"
    )


def test_the_counter_step_is_one_shared_number() -> None:
    """Stated once in bytes; each base converts it into its own unit."""
    assert COUNTER_DEADBAND_BYTES == 250_000_000


# --- 2. The four bases that were missed ---


def test_crypto_map_bytes_holds_below_the_step() -> None:
    data = {"crypto_maps": {"OfficeVPN": {"rx_bytes": 5_000_000_000}}}
    coordinator = SimpleNamespace(data=data)
    sensor = KeeneticCryptoMapRxBytesSensor(coordinator, _entry(), "OfficeVPN")
    assert sensor.native_value == 5_000_000_000

    data["crypto_maps"]["OfficeVPN"]["rx_bytes"] = 5_100_000_000
    assert sensor.native_value == 5_000_000_000

    data["crypto_maps"]["OfficeVPN"]["rx_bytes"] = 5_300_000_000
    assert sensor.native_value == 5_300_000_000


def test_wireguard_counter_holds_below_the_step() -> None:
    """WireGuard reports MiB, so the shared byte step has to be converted."""
    profiles = {"Wireguard0": {"rxbytes": 10 * GIB}}
    coordinator = SimpleNamespace(data={"wireguard": {"profiles": profiles}})
    sensor = KeeneticWgRxSensor(coordinator, _entry(), "Wireguard0")
    first = sensor.native_value
    assert first == round(10 * GIB / MIB, 2)

    profiles["Wireguard0"]["rxbytes"] = 10 * GIB + 100_000_000
    assert sensor.native_value == first

    profiles["Wireguard0"]["rxbytes"] = 10 * GIB + 300_000_000
    assert sensor.native_value != first


def test_wifi_counter_holds_below_the_step() -> None:
    stats = {"WifiMaster0": {"rxbytes": 20 * GIB}}
    coordinator = SimpleNamespace(data={"interface_stats": stats})
    sensor = KeeneticWifi24RxSensor(coordinator, _entry())
    first = sensor.native_value
    assert first == 20.0

    stats["WifiMaster0"]["rxbytes"] = 20 * GIB + 100_000_000
    assert sensor.native_value == first

    stats["WifiMaster0"]["rxbytes"] = 20 * GIB + 300_000_000
    assert sensor.native_value != first


# --- 3. The defect 1.12.0 introduced: attributes outliving the held state ---


def test_active_connections_attributes_follow_the_published_value() -> None:
    """Held state + moving attributes = a row carrying nothing.

    Measured live after 1.12.0: 116 rows in three hours where the state was
    identical and only ``free`` / ``used_percent`` had moved. Before the
    deadband these attributes were free, because the state moved with them.
    """
    coordinator = SimpleNamespace(data={"system": {"conntotal": 63488, "connfree": 63188}})
    sensor = KeeneticActiveConnectionsSensor(coordinator, _entry())
    assert sensor.native_value == 300
    assert sensor.extra_state_attributes["free"] == 63188

    # Raw count dithers 300 -> 310, inside the deadband: state holds, and the
    # attributes must hold with it.
    coordinator.data["system"] = {"conntotal": 63488, "connfree": 63178}
    assert sensor.native_value == 300
    assert sensor.extra_state_attributes["free"] == 63188


def test_active_connections_deadband_matches_how_far_the_gauge_swings() -> None:
    """Live deltas between adjacent polls were +31, +70, -97, +54."""
    assert KeeneticActiveConnectionsSensor._DEADBAND == 50


# --- 4. Throughput: the 100 kbit floor was still crossed every poll ---


def test_throughput_band_widened() -> None:
    assert ThroughputDeadbandMixin._THROUGHPUT_FLOOR == 500_000.0
    assert ThroughputDeadbandMixin._THROUGHPUT_DEADBAND == 0.10
