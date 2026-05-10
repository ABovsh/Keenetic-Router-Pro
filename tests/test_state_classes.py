"""Regression guard: uptime sensors must use TOTAL_INCREASING.

Storing a monotonic uptime counter as ``MEASUREMENT`` produces a
sawtooth in HA long-term-statistics graphs because every poll inserts
a fresh sample. ``TOTAL_INCREASING`` is the right state class for a
counter that resets to zero on reboot/reconnect.
"""

from __future__ import annotations

import sys

import pytest

# These sensor modules import HA's ``homeassistant.components.sensor``
# directly which is not available in the lightweight unit-test stubs.
# Skip cleanly if HA is not importable rather than failing the suite.
pytest.importorskip("homeassistant.components.sensor")


@pytest.mark.parametrize(
    "module_path,class_name",
    [
        (
            "custom_components.keenetic_router_pro.sensor.system",
            "KeeneticUptimeSensor",
        ),
        (
            "custom_components.keenetic_router_pro.sensor.network",
            "KeeneticPppoeUptimeSensor",
        ),
        (
            "custom_components.keenetic_router_pro.sensor.wireguard",
            "KeeneticWgUptimeSensor",
        ),
    ],
)
def test_uptime_sensor_uses_total_increasing(module_path: str, class_name: str) -> None:
    from homeassistant.components.sensor import SensorStateClass

    module = __import__(module_path, fromlist=[class_name])
    cls = getattr(module, class_name)
    assert cls._attr_state_class is SensorStateClass.TOTAL_INCREASING, (
        f"{class_name} should use TOTAL_INCREASING for monotonic uptime, "
        f"got {cls._attr_state_class!r}"
    )
