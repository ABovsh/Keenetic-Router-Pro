"""Coordinator staged orchestration tests."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest

from custom_components.keenetic_router_pro.api.domains.clients import ClientsMixin
from custom_components.keenetic_router_pro.api.helpers import _normalize_interfaces
from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from tests.fixtures.clients_rci import CLIENTS, HOST_POLICIES, IP_NEIGHBOURS
from tests.fixtures.dns_rci import DNS_PROXY, NDNS_INFO
from tests.fixtures.mesh_rci import MESH_NODES
from tests.fixtures.system_rci import (
    AVAILABLE_VERSION,
    CURRENT_VERSION,
    SYSTEM_INFO,
)
from tests.fixtures.vpn_rci import CRYPTO_MAPS, IPSEC_DIAGNOSTICS, VPN_TUNNELS
from tests.fixtures.vpn_rci import WIREGUARD_STATUS
from tests.fixtures.wan_rci import (
    INTERFACE_STATS,
    INTERFACES,
    PING_CHECK,
    PORT_INFO,
    TRAFFIC_STATS,
    WAN_INTERFACES,
    WAN_STATUS,
)
from tests.fixtures.wifi_rci import WIFI_NETWORKS


class StageFixtureClient:
    """Async fake for the coordinator's staged fetch contract."""

    def __init__(self) -> None:
        self.system_info = deepcopy(SYSTEM_INFO)
        self.current_version = deepcopy(CURRENT_VERSION)
        self.available_version = deepcopy(AVAILABLE_VERSION)
        self.interfaces = deepcopy(INTERFACES)
        self.clients = deepcopy(CLIENTS)
        self.ip_neighbours = deepcopy(IP_NEIGHBOURS)
        self.host_policies = deepcopy(HOST_POLICIES)
        self.ndns_info = deepcopy(NDNS_INFO)
        self.ping_check = deepcopy(PING_CHECK)
        self.crypto_maps = deepcopy(CRYPTO_MAPS)
        self.dns_proxy = deepcopy(DNS_PROXY)
        self.ipsec_diagnostics = deepcopy(IPSEC_DIAGNOSTICS)
        self.mesh_nodes = deepcopy(MESH_NODES)
        self.wifi_networks = deepcopy(WIFI_NETWORKS)
        self.wireguard_status = deepcopy(WIREGUARD_STATUS)
        self.vpn_tunnels = deepcopy(VPN_TUNNELS)
        self.wan_status = deepcopy(WAN_STATUS)
        self.wan_interfaces = deepcopy(WAN_INTERFACES)
        self.traffic_stats = deepcopy(TRAFFIC_STATS)
        self.port_info = deepcopy(PORT_INFO)
        self.interface_stats = deepcopy(INTERFACE_STATS)

    def _normalize_interfaces(self, interfaces: Any) -> list[dict[str, Any]]:
        return _normalize_interfaces(interfaces)

    async def async_get_system_info(self) -> dict[str, Any]:
        return deepcopy(self.system_info)

    async def async_get_current_version_info(self) -> dict[str, Any]:
        return deepcopy(self.current_version)

    async def async_get_available_version_info(self) -> dict[str, Any]:
        return deepcopy(self.available_version)

    async def async_get_interfaces(self) -> dict[str, Any]:
        return deepcopy(self.interfaces)

    async def async_get_clients(self) -> list[dict[str, Any]]:
        return deepcopy(self.clients)

    async def async_get_ip_neighbours(self) -> list[dict[str, Any]]:
        return deepcopy(self.ip_neighbours)

    async def async_get_host_policies(self) -> dict[str, Any]:
        return deepcopy(self.host_policies)

    async def async_get_ndns_info(self) -> dict[str, Any]:
        return deepcopy(self.ndns_info)

    async def async_get_ping_check_status(self) -> dict[str, Any]:
        return deepcopy(self.ping_check)

    async def async_get_crypto_maps(self) -> dict[str, Any]:
        return deepcopy(self.crypto_maps)

    async def async_get_dns_proxy_status(self) -> dict[str, Any]:
        return deepcopy(self.dns_proxy)

    async def async_get_ipsec_diagnostics(self) -> dict[str, Any]:
        return deepcopy(self.ipsec_diagnostics)

    async def async_get_mesh_nodes(
        self, clients: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        return deepcopy(self.mesh_nodes)

    async def async_get_wifi_networks(self, **kwargs: Any) -> list[dict[str, Any]]:
        return deepcopy(self.wifi_networks)

    async def async_get_wireguard_status(self, **kwargs: Any) -> dict[str, Any]:
        return deepcopy(self.wireguard_status)

    async def async_get_vpn_tunnels(self, **kwargs: Any) -> dict[str, Any]:
        return deepcopy(self.vpn_tunnels)

    async def async_get_wan_status(self, **kwargs: Any) -> dict[str, Any]:
        return deepcopy(self.wan_status)

    async def async_get_wan_interfaces(self, **kwargs: Any) -> list[dict[str, Any]]:
        return deepcopy(self.wan_interfaces)

    async def async_get_traffic_stats(self, **kwargs: Any) -> dict[str, Any]:
        return deepcopy(self.traffic_stats)

    async def async_get_port_info(self, **kwargs: Any) -> list[dict[str, Any]]:
        return deepcopy(self.port_info)

    async def async_get_all_interface_stats(self, **kwargs: Any) -> dict[str, Any]:
        return deepcopy(self.interface_stats)

    @staticmethod
    def summarize_client_stats(clients: list[dict[str, Any]]) -> dict[str, Any]:
        return ClientsMixin.summarize_client_stats(clients)


async def _async_update(
    coordinator: KeeneticCoordinator,
) -> dict[str, Any]:
    async with asyncio.timeout(1):
        return await coordinator._async_update_data()


async def _updated_data(coordinator: KeeneticCoordinator) -> dict[str, Any]:
    return await _async_update(coordinator)


def _coordinator(client: StageFixtureClient) -> KeeneticCoordinator:
    coordinator = object.__new__(KeeneticCoordinator)
    coordinator.client = client
    coordinator.data = None
    coordinator._refresh_count = 0
    return coordinator


async def test_coordinator_pipeline_fixtures_publishes_expected_data_keys() -> None:
    client = StageFixtureClient()

    data = await _updated_data(_coordinator(client))

    assert set(data) == {
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
        "crypto_maps",
        "dns_proxy",
        "ipsec_diagnostics",
        "new_clients",
    }


async def test_coordinator_pipeline_clients_index_normalizes_mac_keys() -> None:
    client = StageFixtureClient()

    data = await _updated_data(_coordinator(client))

    assert set(data["clients_by_mac"]) == {
        "aa:bb:cc:dd:ee:ff",
        "11:22:33:44:55:66",
    }


async def test_coordinator_pipeline_wan_index_uses_enriched_interface_ids() -> None:
    client = StageFixtureClient()

    data = await _updated_data(_coordinator(client))

    assert set(data["wan_by_id"]) == {"PPPoE0", "Wireguard0"}


async def test_coordinator_pipeline_mesh_index_prefers_cid_then_id() -> None:
    client = StageFixtureClient()

    data = await _updated_data(_coordinator(client))

    assert set(data["mesh_nodes_by_cid"]) == {"controller", "extender-1"}


async def test_coordinator_pipeline_mesh_associations_sums_node_counts() -> None:
    client = StageFixtureClient()

    data = await _updated_data(_coordinator(client))

    assert data["mesh_associations"] == {
        "total": 3,
        "by_node": {"controller": 2, "extender-1": 1},
    }


async def test_coordinator_pipeline_host_policies_preserves_policy_map() -> None:
    client = StageFixtureClient()

    data = await _updated_data(_coordinator(client))

    assert data["host_policies"] == HOST_POLICIES


@pytest.mark.parametrize(
    "key",
    [
        "clients",
        "mesh_nodes",
    ],
)
async def test_coordinator_fast_tick_stage2_failure_preserves_prior_slow_data(
    key: str,
) -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    previous = await _updated_data(coordinator)
    coordinator.data = previous
    coordinator._refresh_count = 1

    async def fail_wifi_networks(**kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("wifi endpoint unavailable")

    client.async_get_wifi_networks = fail_wifi_networks  # type: ignore[method-assign]

    data = await _updated_data(coordinator)

    assert data[key] == previous[key]


async def test_coordinator_client_fetch_failure_preserves_prior_client_index() -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    previous = await _updated_data(coordinator)
    coordinator.data = previous

    async def fail_clients() -> list[dict[str, Any]]:
        raise RuntimeError("client table unavailable")

    client.async_get_clients = fail_clients  # type: ignore[method-assign]

    data = await _updated_data(coordinator)

    assert data["clients_by_mac"] == previous["clients_by_mac"]


async def test_coordinator_client_fetch_failure_marks_clients_stale() -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    previous = await _updated_data(coordinator)
    coordinator.data = previous

    async def fail_clients() -> list[dict[str, Any]]:
        raise RuntimeError("client table unavailable")

    client.async_get_clients = fail_clients  # type: ignore[method-assign]

    data = await _updated_data(coordinator)

    assert data["clients_stale"] is True


@pytest.mark.parametrize(
    ("priority", "expected_ids"),
    [
        (100, ["Default", "Candidate", "Backup"]),
        ("100", ["Default", "Candidate", "Backup"]),
        (None, ["Default", "Backup", "Candidate"]),
    ],
)
async def test_coordinator_wan_backup_priority_mixed_values_orders_backups(
    priority: Any,
    expected_ids: list[str],
) -> None:
    client = StageFixtureClient()
    candidate = {"id": "Candidate", "defaultgw": False}
    if priority is not None:
        candidate["priority"] = priority
    client.wan_interfaces = [
        {"id": "Backup", "defaultgw": False, "priority": 50},
        {"id": "Default", "defaultgw": True, "priority": 10},
        candidate,
    ]
    client.interface_stats = {}

    data = await _updated_data(_coordinator(client))

    assert [wan["id"] for wan in data["wan_interfaces"]] == expected_ids


async def test_coordinator_crypto_maps_slow_tick_reuses_previous_timestamp() -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    previous = await _updated_data(coordinator)
    previous["crypto_maps"]["SITE"]["_sample_ts"] = 10.0
    previous["crypto_maps"]["SITE"]["rx_throughput"] = 7.0
    previous["crypto_maps"]["SITE"]["tx_throughput"] = 8.0
    coordinator.data = previous
    coordinator._refresh_count = 6

    data = await _updated_data(coordinator)

    assert data["crypto_maps"]["SITE"]["_sample_ts"] == pytest.approx(10.0)


async def test_coordinator_crypto_maps_slow_tick_reuses_previous_rates() -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    previous = await _updated_data(coordinator)
    previous["crypto_maps"]["SITE"]["_sample_ts"] = 10.0
    previous["crypto_maps"]["SITE"]["rx_throughput"] = 7.0
    previous["crypto_maps"]["SITE"]["tx_throughput"] = 8.0
    coordinator.data = previous
    coordinator._refresh_count = 6

    data = await _updated_data(coordinator)

    assert (
        data["crypto_maps"]["SITE"]["rx_throughput"],
        data["crypto_maps"]["SITE"]["tx_throughput"],
    ) == (7.0, 8.0)


@pytest.mark.parametrize("field", ["rx_throughput", "tx_throughput"])
async def test_coordinator_crypto_maps_very_slow_counter_reset_clamps_rate_zero(
    field: str,
) -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    previous = await _updated_data(coordinator)
    previous["crypto_maps"]["SITE"]["_sample_ts"] = 1.0
    previous["crypto_maps"]["SITE"]["rx_bytes"] = 5000
    previous["crypto_maps"]["SITE"]["tx_bytes"] = 6000
    client.crypto_maps["SITE"]["rx_bytes"] = 100
    client.crypto_maps["SITE"]["tx_bytes"] = 200
    coordinator.data = previous
    coordinator._refresh_count = 30

    data = await _updated_data(coordinator)

    assert data["crypto_maps"]["SITE"][field] == pytest.approx(0.0)


async def test_coordinator_wan_ping_check_false_overrides_internet_access() -> None:
    client = StageFixtureClient()

    data = await _updated_data(_coordinator(client))

    assert data["wan_by_id"]["PPPoE0"]["internet_access"] is False


async def test_coordinator_wan_ping_check_none_keeps_heuristic_access() -> None:
    client = StageFixtureClient()

    data = await _updated_data(_coordinator(client))

    assert data["wan_by_id"]["Wireguard0"]["internet_access"] is True


async def test_coordinator_wan_stats_aliases_publish_packet_counts() -> None:
    client = StageFixtureClient()

    data = await _updated_data(_coordinator(client))

    assert data["wan_by_id"]["Wireguard0"]["rx_packets"] == 30


async def test_coordinator_wan_counter_reset_clamps_rate_zero() -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    previous = await _updated_data(coordinator)
    previous["wan_interfaces"][0]["_sample_ts"] = 1.0
    previous["wan_interfaces"][0]["rx_bytes"] = 5000
    client.interface_stats["PPPoE0"]["rxbytes"] = "100"
    coordinator.data = previous
    coordinator._refresh_count = 1

    data = await _updated_data(coordinator)

    assert data["wan_by_id"]["PPPoE0"]["rx_throughput"] == pytest.approx(0.0)
