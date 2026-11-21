"""Sensors that only repeated another entity's value are not created.

Firmware Version (router and extender) repeats the update entity's installed
version; WAN IP repeats the active uplink's Public IP; the router IP is the
configured host; the extender IP is an attribute of its Connected sensor; the
uplink Enabled binary sensor mirrors the Enabled switch. Disconnected
Clients, Extender Count and Active Connections were dropped with them.
"""

from __future__ import annotations

from custom_components.keenetic_router_pro.binary_sensor import (
    async_setup_entry as async_setup_binary_entry,
)
from custom_components.keenetic_router_pro.sensor import async_setup_entry
from tests.test_sensor_platform_setup import (
    _base_data,
    _capture_setup,
    _Coordinator,
    _Entry,
)

_REMOVED_SENSOR_SUFFIXES = (
    "_firmware_version",
    "_firmware_version_v2",
    "_wan_ip",
    "_local_ip",
    "_local_ip_v2",
    "_disconnected_clients",
    "_extender_count",
    "_active_connections",
)


def _unique_ids(setup) -> set[str]:
    coordinator = _Coordinator(_base_data())
    batches = _capture_setup(setup, coordinator, _Entry(coordinator))
    return {entity.unique_id for batch in batches for entity in batch}


def test_duplicate_sensors_are_not_created() -> None:
    created = _unique_ids(async_setup_entry)

    assert not {uid for uid in created if uid.endswith(_REMOVED_SENSOR_SUFFIXES)}
    # Their sources stay published through the kept entities.
    assert "entry_123_wan_PPPoE0_public_ip" in created
    assert "entry_123_connected_clients_v2" in created


def test_uplink_enabled_binary_sensor_is_not_created() -> None:
    created = _unique_ids(async_setup_binary_entry)

    assert "entry_123_wan_PPPoE0_enabled" not in created
    assert "entry_123_wan_PPPoE0_connected" in created
