"""IPsec phase-state binary sensor matrix."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.keenetic_router_pro.binary_sensor import (
    KeeneticControllerUpdateSensor,
    KeeneticCryptoMapConnectedSensor,
    KeeneticMeshNodeSensor,
    KeeneticMeshUpdateSensor,
    KeeneticWanConnectedSensor,
    KeeneticWanEnabledSensor,
    async_setup_entry,
)
from tests.fixtures.mesh_rci import FALLBACK_MESH_NODE
from tests.fixtures.vpn_rci import IPSEC_PHASE_STATES, crypto_map_for_phase


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router", data={})


def _coordinator(data: dict) -> SimpleNamespace:
    return SimpleNamespace(data=data)


@pytest.mark.parametrize("phase", IPSEC_PHASE_STATES)
def test_crypto_map_connected_sensor_only_phase2_established_is_on(
    phase: str,
) -> None:
    """Only a phase-2 SA means the IPsec tunnel is connected."""
    entry = _entry()
    coordinator = _coordinator({"crypto_maps": crypto_map_for_phase(phase)})
    sensor = KeeneticCryptoMapConnectedSensor(coordinator, entry, "SITE")

    assert sensor.is_on is (phase == "PHASE2_ESTABLISHED")


def test_crypto_map_connected_sensor_exposes_phase_attributes() -> None:
    """IPsec connected sensor attributes mirror the normalized crypto map."""
    entry = _entry()
    data = crypto_map_for_phase("PHASE2_ESTABLISHED")
    data["SITE"].update(
        {
            "via": "PPPoE0",
            "local_endpoint": "192.0.2.10",
            "remote_endpoint": "198.51.100.1",
            "rx_bytes": 100,
            "tx_bytes": 200,
        }
    )
    sensor = KeeneticCryptoMapConnectedSensor(
        _coordinator({"crypto_maps": data}),
        entry,
        "SITE",
    )

    assert sensor.extra_state_attributes == {
        "state": "PHASE2_ESTABLISHED",
        "ike_state": "PHASE2_ESTABLISHED",
        "via": "PPPoE0",
        "remote_peer": "198.51.100.1",
        "local_endpoint": "192.0.2.10",
        "remote_endpoint": "198.51.100.1",
        "rx_bytes": 100,
        "tx_bytes": 200,
    }


def test_binary_setup_adds_initial_ipsec_and_dynamic_new_ipsec_entities() -> None:
    """Platform setup registers existing and newly discovered IPsec sensors."""
    listeners = []

    def async_add_listener(listener):
        listeners.append(listener)
        return lambda: None

    added = []
    coordinator = SimpleNamespace(
        data={
            "mesh_nodes": [],
            "wan_interfaces": [],
            "crypto_maps": crypto_map_for_phase("PHASE2_ESTABLISHED"),
            "system": {},
        },
        async_add_listener=async_add_listener,
    )
    entry = SimpleNamespace(
        entry_id="entry_123",
        title="Router",
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda unsub: None,
    )

    import asyncio

    asyncio.run(async_setup_entry(SimpleNamespace(), entry, added.extend))
    coordinator.data["crypto_maps"]["BACKUP"] = {
        "connected": False,
        "state": "DOWN",
    }
    listeners[0]()

    assert [entity.unique_id for entity in added] == [
        "entry_123_controller_update",
        "entry_123_cmap_SITE_connected",
        "entry_123_cmap_BACKUP_connected",
    ]


def test_wan_connected_sensor_exposes_full_ping_check_attributes() -> None:
    """WAN connected attributes include ping-check and timestamp details."""
    from datetime import datetime, timezone

    wan = {
        "id": "PPPoE0",
        "internet_access": False,
        "link_state": "up",
        "description": "ISP",
        "type": "PPPoE",
        "defaultgw": True,
        "priority": 10,
        "role_label": "primary",
        "ip": "198.51.100.10",
        "underlying": "GigabitEthernet0",
        "internet_access_source": "ping_check",
        "summary_layers": {"conf": "running", "link": "up"},
        "ping_check": {
            "passing": False,
            "check_hosts": ["captive.keenetic.net"],
            "check_addresses": ["1.1.1.1"],
            "check_port": 443,
            "check_mode": "icmp",
            "profile": "default",
            "success_count": 1,
            "fail_count": 3,
            "max_fails": 3,
            "update_interval": 10,
            "status": "fail",
            "all_profiles": ["default", "backup"],
        },
    }
    coordinator = SimpleNamespace(
        data={"wan_interfaces": [wan]},
        last_update_success_time=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )
    sensor = KeeneticWanConnectedSensor(coordinator, _entry(), "PPPoE0")

    assert sensor.extra_state_attributes == {
        "interface": "PPPoE0",
        "description": "ISP",
        "type": "PPPoE",
        "link_state": "up",
        "defaultgw": True,
        "priority": 10,
        "role_label": "primary",
        "public_ip": "198.51.100.10",
        "underlying": "GigabitEthernet0",
        "source": "ping_check",
        "check_targets": ["captive.keenetic.net", "1.1.1.1"],
        "check_port": 443,
        "check_mode": "icmp",
        "check_profile": "default",
        "success_count": 1,
        "fail_count": 3,
        "max_fails": 3,
        "update_interval": 10,
        "ping_check_status": "fail",
        "failure_reason": (
            "ping check failing (3/3 consecutive failures to "
            "captive.keenetic.net, 1.1.1.1)"
        ),
        "all_ping_check_profiles": ["default", "backup"],
        "summary_layers": {"conf": "running", "link": "up"},
        "last_check": "2026-05-20T00:00:00+00:00",
    }


def test_wan_enabled_sensor_reports_summary_layers() -> None:
    """WAN enabled sensor follows the interface enabled flag."""
    sensor = KeeneticWanEnabledSensor(
        _coordinator(
            {
                "wan_interfaces": [
                    {
                        "id": "PPPoE0",
                        "enabled": True,
                        "summary_layers": {
                            "conf": "running",
                            "link": "up",
                            "ipv4": "running",
                            "ctrl": "up",
                        },
                    }
                ]
            }
        ),
        _entry(),
        "PPPoE0",
    )

    assert (sensor.unique_id, sensor.name, sensor.is_on, sensor.extra_state_attributes) == (
        "entry_123_wan_PPPoE0_enabled",
        "Enabled",
        True,
        {
            "conf": "running",
            "link": "up",
            "ipv4": "running",
            "ctrl": "up",
        },
    )


def test_mesh_binary_sensors_read_fallback_node_payload() -> None:
    """Mesh binary sensors support fallback nodes keyed by MAC."""
    node = dict(
        FALLBACK_MESH_NODE,
        model="Buddy 6",
        cpuload=12,
        memory={"total": 100, "free": 50},
        associations=3,
        rci_errors=0,
    )
    coordinator = _coordinator({"mesh_nodes": [node]})
    entry = _entry()
    connected = KeeneticMeshNodeSensor(coordinator, entry, "AA:BB:CC:00:00:01")
    update = KeeneticMeshUpdateSensor(coordinator, entry, "AA:BB:CC:00:00:01")

    assert {
        "connected_name": connected.name,
        "connected_on": connected.is_on,
        "connected_icon": connected.icon,
        "connected_attrs": connected.extra_state_attributes,
        "update_name": update.name,
        "update_on": update.is_on,
        "update_icon": update.icon,
        "update_attrs": update.extra_state_attributes,
    } == {
        "connected_name": "Connected",
        "connected_on": True,
        "connected_icon": "mdi:access-point-network",
        "connected_attrs": {
            "cid": "AA:BB:CC:00:00:01",
            "mac": "AA:BB:CC:00:00:01",
            "ip": "192.0.2.20",
            "model": "Buddy 6",
            "mode": "extender",
            "uptime": 120,
            "cpuload": 12,
            "memory": {"total": 100, "free": 50},
            "firmware": "4.2.0",
            "firmware_available": "4.3.0",
            "associations": 3,
            "rci_errors": 0,
        },
        "update_name": "Update Available",
        "update_on": True,
        "update_icon": "mdi:update",
        "update_attrs": {
            "cid": "AA:BB:CC:00:00:01",
            "model": "Buddy 6",
            "current_version": "4.2.0",
            "available_version": "4.3.0",
        },
    }


def test_controller_update_sensor_only_stable_updates_are_on() -> None:
    """Controller update binary sensor ignores non-stable channel updates."""
    coordinator = _coordinator(
        {
            "system": {
                "title": "4.2.0",
                "fw-available": "4.3.0",
                "fw-update-sandbox": "preview",
            }
        }
    )
    sensor = KeeneticControllerUpdateSensor(coordinator, _entry())

    assert (
        sensor.unique_id,
        sensor.name,
        sensor.is_on,
        sensor.icon,
        sensor.extra_state_attributes,
    ) == (
        "entry_123_controller_update",
        "Update Available",
        False,
        "mdi:check-circle",
        {
            "current_version": "4.2.0",
            "update_channel": "preview",
            "available_version": "4.3.0",
        },
    )
