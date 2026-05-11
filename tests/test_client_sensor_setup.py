"""Regression tests for the tracked-client sensor set."""

from __future__ import annotations

import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "keenetic_router_pro"


def test_noisy_tracked_client_sensors_are_not_created_by_default() -> None:
    """Avoid creating low-value tracked-client sensors that mostly show unknown."""
    src = (ROOT / "sensor" / "__init__.py").read_text()

    assert "KeeneticClientFirstSeenSensor" not in src
    assert "KeeneticClientLinkSensor" not in src
    assert "KeeneticClientSpeedSensor" not in src
    assert "KeeneticClientPortSensor" not in src
