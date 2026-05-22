"""Tests for WireGuard sensor value extraction."""

from __future__ import annotations

import pytest

from types import SimpleNamespace

from custom_components.keenetic_router_pro.sensor.wireguard import (
    KeeneticWgRxSensor,
    KeeneticWgTxSensor,
    KeeneticWgUptimeSensor,
)


def test_wireguard_sensors_use_profile_labels_and_byte_counters() -> None:
    """WireGuard sensors should tolerate string counters from RCI payloads."""
    entry = SimpleNamespace(entry_id="entry_123", title="Router")
    coordinator = SimpleNamespace(
        data={
            "wireguard": {
                "profiles": {
                    "Wireguard0": {
                        "label": "Zurich",
                        "uptime": "123.9",
                        "rxbytes": str(2 * 1024 * 1024),
                        "tx": str(3 * 1024 * 1024),
                    }
                }
            }
        }
    )

    uptime = KeeneticWgUptimeSensor(coordinator, entry, "Wireguard0")
    rx = KeeneticWgRxSensor(coordinator, entry, "Wireguard0")
    tx = KeeneticWgTxSensor(coordinator, entry, "Wireguard0")

    assert uptime.unique_id == "entry_123_wg_Wireguard0_uptime"
    assert uptime.name == "WireGuard Zurich Uptime"
    assert uptime.native_value == 123
    assert rx.name == "WireGuard Zurich RX"
    assert rx.native_value == pytest.approx(2.0)
    assert tx.name == "WireGuard Zurich TX"
    assert tx.native_value == pytest.approx(3.0)


def test_wireguard_sensors_fall_back_for_missing_or_invalid_values() -> None:
    """Missing profiles should be stable zeros/None instead of exceptions."""
    entry = SimpleNamespace(entry_id="entry_123", title="Router")
    coordinator = SimpleNamespace(
        data={
            "wireguard": {
                "profiles": {
                    "Wireguard0": {
                        "rx": "not-a-number",
                        "sent": "",
                    }
                }
            }
        }
    )

    uptime = KeeneticWgUptimeSensor(coordinator, entry, "Missing")
    rx = KeeneticWgRxSensor(coordinator, entry, "Wireguard0")
    tx = KeeneticWgTxSensor(coordinator, entry, "Wireguard0")

    assert uptime.name == "WireGuard Missing Uptime"
    assert uptime.native_value == 0
    assert rx.native_value is None
    assert tx.native_value is None
