"""Compatibility contracts for the maintainability refactor safety net."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.keenetic_router_pro.api import KeeneticClient
from custom_components.keenetic_router_pro.binary_sensor import (
    KeeneticCryptoMapConnectedSensor,
    KeeneticWanConnectedSensor,
)
from custom_components.keenetic_router_pro.button import KeeneticMeshRebootButton
from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from custom_components.keenetic_router_pro.sensor.client import KeeneticClientIpSensor
from custom_components.keenetic_router_pro.sensor.crypto import (
    KeeneticCryptoMapStateSensor,
)
from custom_components.keenetic_router_pro.sensor.mesh import KeeneticMeshUptimeSensor
from custom_components.keenetic_router_pro.sensor.network import (
    KeeneticWanProviderSensor,
)

from tests.test_coordinator_update_flow import FakeKeeneticClient


EXPECTED_COORDINATOR_KEYS = {
    "system",
    "traffic_stats",
    "interfaces",
    "wifi",
    "wireguard",
    "vpn_tunnels",
    "clients",
    "clients_stale",
    "clients_by_mac",
    "wan_status",
    "wan_interfaces",
    "wan_by_id",
    "mesh_nodes",
    "mesh_associations",
    "mesh_nodes_by_cid",
    "interface_stats",
    "client_stats",
    "ndns",
    "host_policies",
    "port_info",
    "ping_check_status",
    "_iface_fingerprint",
    "crypto_maps",
    "dns_proxy",
    "ipsec_diagnostics",
    "new_clients",
}

FACADE_METHODS = (
    "async_get_system_info",
    "async_get_current_version_info",
    "async_get_available_version_info",
    "async_get_interfaces",
    "async_get_wan_status",
    "async_get_wan_interfaces",
    "async_get_clients",
    "async_get_ip_neighbours",
    "async_get_wifi_networks",
    "async_get_wireguard_status",
    "async_get_vpn_tunnels",
    "async_get_ipsec_status",
    "async_get_crypto_maps",
    "async_get_dns_proxy_status",
    "async_get_mesh_nodes",
    "async_set_interface_enabled",
    "async_set_wireguard_enabled",
    "async_set_crypto_map_enabled",
    "prefetch_tick",
    "clear_tick_cache",
)


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router")


def _coordinator(data: dict) -> SimpleNamespace:
    def async_add_listener(*_args, **_kwargs):
        return lambda: None

    return SimpleNamespace(data=data, async_add_listener=async_add_listener)


def test_coordinator_output_keys_are_stable() -> None:
    """Coordinator data remains compatible with existing platform consumers."""
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    data = asyncio.run(coordinator._async_update_data())

    assert set(data) == EXPECTED_COORDINATOR_KEYS


def test_keenetic_client_facade_methods_remain_callable() -> None:
    """Public facade names used by coordinator/platform code keep existing APIs."""
    for method_name in FACADE_METHODS:
        assert callable(getattr(KeeneticClient, method_name))


def test_representative_unique_ids_are_stable() -> None:
    """Entity unique IDs remain stable across the planned module split."""
    entry = _entry()
    coordinator = _coordinator(
        {
            "wan_interfaces": [{"id": "PPPoE0"}],
            "mesh_nodes": [{"cid": "aa:bb:cc:dd:ee:ff"}],
            "crypto_maps": {"OfficeVPN": {"state": "PHASE2_ESTABLISHED"}},
            "clients_by_mac": {
                "aa:bb:cc:dd:ee:ff": {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "ip": "192.0.2.55",
                }
            },
        }
    )
    client = SimpleNamespace()

    assert (
        KeeneticWanProviderSensor(coordinator, entry, "PPPoE0").unique_id
        == "entry_123_wan_PPPoE0_provider"
    )
    assert (
        KeeneticWanConnectedSensor(coordinator, entry, "PPPoE0").unique_id
        == "entry_123_wan_PPPoE0_connected"
    )
    assert (
        KeeneticMeshUptimeSensor(
            coordinator,
            entry,
            "aa:bb:cc:dd:ee:ff",
        ).unique_id
        == "entry_123_mesh_aa_bb_cc_dd_ee_ff_uptime_v2"
    )
    assert (
        KeeneticMeshRebootButton(
            coordinator,
            entry,
            client,
            "aa:bb:cc:dd:ee:ff",
        ).unique_id
        == "entry_123_mesh_aa_bb_cc_dd_ee_ff_reboot_button_v2"
    )
    assert (
        KeeneticCryptoMapStateSensor(coordinator, entry, "OfficeVPN").unique_id
        == "entry_123_cmap_OfficeVPN_state"
    )
    assert (
        KeeneticCryptoMapConnectedSensor(coordinator, entry, "OfficeVPN").unique_id
        == "entry_123_cmap_OfficeVPN_connected"
    )
    assert (
        KeeneticClientIpSensor(
            coordinator,
            entry,
            "AA:BB:CC:DD:EE:FF",
            "Laptop",
            "192.0.2.55",
        ).unique_id
        == "entry_123_client_aa:bb:cc:dd:ee:ff_ip"
    )
