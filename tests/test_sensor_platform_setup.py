"""Efficient contract tests for the sensor platform setup path."""

from __future__ import annotations

from tests.conftest import TEST_HOST

import asyncio
from types import SimpleNamespace
from typing import Callable

from custom_components.keenetic_router_pro.binary_sensor import (
    async_setup_entry as async_setup_binary_entry,
)
from custom_components.keenetic_router_pro.button import (
    async_setup_entry as async_setup_button_entry,
)
from custom_components.keenetic_router_pro.const import CONF_TRACKED_CLIENTS
from custom_components.keenetic_router_pro.sensor import async_setup_entry
from custom_components.keenetic_router_pro.switch import (
    async_setup_entry as async_setup_switch_entry,
)
from custom_components.keenetic_router_pro.update import (
    async_setup_entry as async_setup_update_entry,
)


class _Coordinator:
    """Minimal coordinator stub that captures dynamic entity listeners."""

    def __init__(self, data: dict) -> None:
        self.data = data
        self.listeners: list[Callable[[], None]] = []

    def async_add_listener(self, listener: Callable[[], None]):
        self.listeners.append(listener)
        return lambda: None


class _Entry(SimpleNamespace):
    """Config-entry stub with runtime data and unload capture."""

    def __init__(self, coordinator: _Coordinator) -> None:
        super().__init__(
            entry_id="entry_123",
            title="Router",
            data={
                "host": TEST_HOST,
                CONF_TRACKED_CLIENTS: [
                    {"mac": "AA:BB:CC:00:00:01", "name": "Phone", "ip": "192.0.2.10"},
                    {"mac": "aa-bb-cc-00-00-01", "name": "Duplicate phone"},
                    "not-a-client",
                ],
            },
            runtime_data=SimpleNamespace(
                coordinator=coordinator,
                client=SimpleNamespace(),
            ),
        )
        self.unloads: list[object] = []

    def async_on_unload(self, remover: object) -> None:
        self.unloads.append(remover)


def _base_data() -> dict:
    return {
        "system": {"title": "4.2.0"},
        "wan_status": {"status": "connected", "ip": "203.0.113.1"},
        "traffic_stats": {},
        "client_stats": {},
        "wifi": [],
        "wireguard": {},
        "mesh_nodes": [
            {
                "cid": "aa:bb:cc:dd:ee:ff",
                "ip": "192.0.2.20",
                "port": [{"label": "0", "link": "up"}, "bad-port"],
            },
            "bad-node",
        ],
        "port_info": [{"label": "1", "link": "up"}, "bad-port"],
        "wan_interfaces": [{"id": "PPPoE0"}],
        "crypto_maps": {"SITE": {"connected": True}},
        "clients_by_mac": {},
        "clients": [],
    }


def _capture_setup(setup, coordinator: _Coordinator, entry: _Entry) -> list[list[object]]:
    batches: list[list[object]] = []
    asyncio.run(setup(None, entry, lambda entities: batches.append(entities)))
    return batches


def test_sensor_setup_deduplicates_inputs_and_adds_dynamic_entities_once() -> None:
    """One compact setup flow covers fixtures, stubs, and dynamic listeners."""
    coordinator = _Coordinator(_base_data())
    entry = _Entry(coordinator)

    batches = _capture_setup(async_setup_entry, coordinator, entry)

    assert len(batches) == 1
    initial_unique_ids = {
        entity.unique_id for entity in batches[0] if hasattr(entity, "unique_id")
    }
    assert "entry_123_client_aa:bb:cc:00:00:01_ip" in initial_unique_ids
    assert "entry_123_client_aa:bb:cc:00:00:01_txrate" in initial_unique_ids
    assert "entry_123_mesh_aa_bb_cc_dd_ee_ff_port_0_v2" in initial_unique_ids
    assert "entry_123_wan_PPPoE0_provider" in initial_unique_ids
    assert "entry_123_cmap_SITE_state" in initial_unique_ids
    assert len([uid for uid in initial_unique_ids if "_client_aa:bb:cc:00:00:01_" in uid]) == 10
    assert len(coordinator.listeners) == 1
    assert len(entry.unloads) == 1

    coordinator.data["mesh_nodes"].append(
        {
            "cid": "11:22:33:44:55:66",
            "ip": "192.0.2.21",
            "port": [{"label": "1", "link": "down"}],
        }
    )
    coordinator.data["wan_interfaces"].append({"id": "Wireguard0"})
    coordinator.data["crypto_maps"]["BRANCH"] = {"connected": False}

    coordinator.listeners[0]()
    coordinator.listeners[0]()

    assert len(batches) == 2
    dynamic_unique_ids = {
        entity.unique_id for entity in batches[1] if hasattr(entity, "unique_id")
    }
    assert "entry_123_mesh_11_22_33_44_55_66_port_1_v2" in dynamic_unique_ids
    assert "entry_123_wan_Wireguard0_provider" in dynamic_unique_ids
    assert "entry_123_cmap_BRANCH_state" in dynamic_unique_ids
    assert len(dynamic_unique_ids) == len(batches[1])


def test_binary_sensor_setup_skips_bad_payloads_and_adds_dynamic_entities_once() -> None:
    """Binary sensors should tolerate shape drift in mesh/WAN/crypto payloads."""
    data = _base_data()
    data["wan_interfaces"].append("bad-wan")
    coordinator = _Coordinator(data)
    entry = _Entry(coordinator)

    batches = _capture_setup(async_setup_binary_entry, coordinator, entry)

    assert len(batches) == 1
    initial_unique_ids = {
        entity.unique_id for entity in batches[0] if hasattr(entity, "unique_id")
    }
    assert "entry_123_controller_update" in initial_unique_ids
    assert "entry_123_mesh_aa_bb_cc_dd_ee_ff_connect_v2" in initial_unique_ids
    assert "entry_123_wan_PPPoE0_connected" in initial_unique_ids
    assert "entry_123_cmap_SITE_connected" in initial_unique_ids

    coordinator.data["mesh_nodes"].append({"cid": "11:22:33:44:55:66"})
    coordinator.data["wan_interfaces"].append({"id": "Wireguard0"})
    coordinator.data["crypto_maps"]["BRANCH"] = {"connected": False}

    coordinator.listeners[0]()
    coordinator.listeners[0]()

    assert len(batches) == 2
    dynamic_unique_ids = {
        entity.unique_id for entity in batches[1] if hasattr(entity, "unique_id")
    }
    assert dynamic_unique_ids == {
        "entry_123_mesh_11_22_33_44_55_66_connect_v2",
        "entry_123_mesh_11_22_33_44_55_66_update_v2",
        "entry_123_wan_Wireguard0_connected",
        "entry_123_wan_Wireguard0_enabled",
        "entry_123_cmap_BRANCH_connected",
    }


def test_switch_setup_skips_bad_payloads_and_adds_dynamic_entities_once() -> None:
    """Switch setup covers Wi-Fi, WAN, VPN and crypto-map entity families."""
    data = _base_data()
    data.update(
        {
            "wifi": [
                {"id": "WifiMaster1/AccessPoint0", "name": "Main 5", "enabled": True},
                "bad-wifi",
            ],
            "wan_interfaces": [{"id": "PPPoE0"}, "bad-wan"],
            "vpn_tunnels": {
                "profiles": {
                    "PPPoE0": {"type": "pppoe", "enabled": True},
                    "Wireguard0": {"type": "wireguard", "enabled": False},
                }
            },
        }
    )
    coordinator = _Coordinator(data)
    entry = _Entry(coordinator)

    batches = _capture_setup(async_setup_switch_entry, coordinator, entry)

    assert len(batches) == 1
    initial_unique_ids = {
        entity.unique_id for entity in batches[0] if hasattr(entity, "unique_id")
    }
    assert "entry_123_wifi_WifiMaster1/AccessPoint0" in initial_unique_ids
    assert "entry_123_wan_PPPoE0_enabled_switch" in initial_unique_ids
    assert "entry_123_vpn_Wireguard0" in initial_unique_ids
    assert "entry_123_cmap_SITE_enabled" in initial_unique_ids

    coordinator.data["wan_interfaces"].append({"id": "PPPoE1"})
    coordinator.data["vpn_tunnels"]["profiles"]["Wireguard1"] = {
        "type": "wireguard",
        "enabled": True,
    }
    coordinator.data["crypto_maps"]["BRANCH"] = {"enabled": True}

    coordinator.listeners[0]()
    coordinator.listeners[0]()

    assert len(batches) == 2
    dynamic_unique_ids = {
        entity.unique_id for entity in batches[1] if hasattr(entity, "unique_id")
    }
    assert dynamic_unique_ids == {
        "entry_123_wan_PPPoE1_enabled_switch",
        "entry_123_vpn_Wireguard1",
        "entry_123_cmap_BRANCH_enabled",
    }


def test_button_setup_skips_bad_mesh_payloads_and_adds_dynamic_entities_once() -> None:
    """Mesh reboot buttons should follow the same dynamic extender contract."""
    coordinator = _Coordinator(_base_data())
    entry = _Entry(coordinator)

    batches = _capture_setup(async_setup_button_entry, coordinator, entry)

    assert len(batches) == 1
    initial_unique_ids = {
        entity.unique_id for entity in batches[0] if hasattr(entity, "unique_id")
    }
    assert initial_unique_ids == {
        "entry_123_reboot_button",
        "entry_123_mesh_aa_bb_cc_dd_ee_ff_reboot_button_v2",
    }

    coordinator.data["mesh_nodes"].append({"cid": "11:22:33:44:55:66"})

    coordinator.listeners[0]()
    coordinator.listeners[0]()

    assert len(batches) == 2
    dynamic_unique_ids = {
        entity.unique_id for entity in batches[1] if hasattr(entity, "unique_id")
    }
    assert dynamic_unique_ids == {
        "entry_123_mesh_11_22_33_44_55_66_reboot_button_v2",
    }


def test_update_setup_skips_bad_mesh_payloads_and_adds_dynamic_entities_once() -> None:
    """Firmware update entities should appear for new extenders without duplicates."""
    coordinator = _Coordinator(_base_data())
    entry = _Entry(coordinator)

    batches = _capture_setup(async_setup_update_entry, coordinator, entry)

    assert len(batches) == 1
    initial_unique_ids = {
        entity.unique_id for entity in batches[0] if hasattr(entity, "unique_id")
    }
    assert initial_unique_ids == {
        "entry_123_firmware_update",
        "entry_123_mesh_aa_bb_cc_dd_ee_ff_firmware_update_v2",
    }

    coordinator.data["mesh_nodes"].append({"cid": "11:22:33:44:55:66"})

    coordinator.listeners[0]()
    coordinator.listeners[0]()

    assert len(batches) == 2
    dynamic_unique_ids = {
        entity.unique_id for entity in batches[1] if hasattr(entity, "unique_id")
    }
    assert dynamic_unique_ids == {
        "entry_123_mesh_11_22_33_44_55_66_firmware_update_v2",
    }
