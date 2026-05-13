"""Coordinator update-flow tests with a lightweight fake Keenetic client."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.keenetic_router_pro.api import KeeneticAuthError, KeeneticClient
from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from custom_components.keenetic_router_pro.device_tracker import KeeneticClientTracker


class FakeKeeneticClient:
    """Small async fake covering the coordinator's staged fetch contract."""

    def __init__(self) -> None:
        self.interfaces = {
            "PPPoE0": {
                "id": "PPPoE0",
                "type": "PPPoE",
                "state": "up",
                "global": True,
                "defaultgw": True,
                "priority": 100,
                "role": ["inet"],
                "address": "203.0.113.10/32",
                "summary": {"layer": {"conf": "running", "ipv4": "running"}},
            },
            "Wireguard0": {
                "id": "Wireguard0",
                "type": "WireGuard",
                "state": "up",
                "global": True,
                "priority": 50,
                "role": ["inet"],
                "address": "10.10.10.2/32",
                "summary": {"layer": {"conf": "running", "ipv4": "running"}},
            },
        }
        self.clients = [{"mac": "AA:BB:CC:DD:EE:FF", "active": True, "ssid": "Main"}]
        self.mesh_nodes = [{"cid": "node-1", "ip": "192.0.2.20"}]
        self.interface_stats = {
            "PPPoE0": {"rxbytes": "1000", "txbytes": "2000"},
            "Wireguard0": {"rxbytes": "3000", "txbytes": "4000"},
        }
        self.ping_check = {
            "PPPoE0": {"passing": False, "status": "fail", "profile": "default"}
        }

    def _normalize_interfaces(self, interfaces: Any) -> list[dict[str, Any]]:
        return KeeneticClient("192.0.2.1", "admin", "secret")._normalize_interfaces(
            interfaces
        )

    async def async_get_system_info(self) -> dict[str, Any]:
        return {"hostname": "router", "uptime": 100}

    async def async_get_current_version_info(self) -> dict[str, Any]:
        return {"title": "4.2.0", "release": "4.2.0"}

    async def async_get_available_version_info(self) -> dict[str, Any]:
        return {"title": "4.3.0", "sandbox": "stable", "update-available": True}

    async def async_get_interfaces(self) -> dict[str, Any]:
        return self.interfaces

    async def async_get_clients(self) -> list[dict[str, Any]]:
        return self.clients

    async def async_get_host_policies(self) -> dict[str, Any]:
        return {"aa:bb:cc:dd:ee:ff": {"policy": "Policy0"}}

    async def async_get_ndns_info(self) -> dict[str, Any]:
        return {"name": "router", "domain": "keenetic.pro"}

    async def async_get_ping_check_status(self) -> dict[str, Any]:
        return self.ping_check

    async def async_get_crypto_maps(self) -> dict[str, Any]:
        return {
            "SITE": {
                "rx_bytes": 100,
                "tx_bytes": 200,
                "connected": True,
            }
        }

    async def async_get_dns_proxy_status(self) -> dict[str, Any]:
        return {"status": "ok"}

    async def async_get_ipsec_diagnostics(self) -> dict[str, Any]:
        return {"status": "ok"}

    async def async_get_mesh_nodes(
        self, clients: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        assert clients == self.clients
        return self.mesh_nodes

    async def async_get_wifi_networks(self, **kwargs: Any) -> list[dict[str, Any]]:
        assert kwargs["interfaces"] is self.interfaces
        assert kwargs["iface_list"]
        return [{"id": "WifiMaster0/AccessPoint0", "ssid": "Main"}]

    async def async_get_wireguard_status(self, **kwargs: Any) -> dict[str, Any]:
        return {"profiles": {"Wireguard0": {"enabled": True}}}

    async def async_get_vpn_tunnels(self, **kwargs: Any) -> dict[str, Any]:
        return {"profiles": {"Wireguard0": {"enabled": True}}}

    async def async_get_wan_status(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "connected", "interface": "PPPoE0"}

    async def async_get_wan_interfaces(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": "PPPoE0",
                "defaultgw": True,
                "priority": 100,
                "internet_access": True,
            },
            {
                "id": "Wireguard0",
                "defaultgw": False,
                "priority": 50,
                "internet_access": True,
            },
        ]

    async def async_get_traffic_stats(self, **kwargs: Any) -> dict[str, Any]:
        return {"download_speed": 1.0, "upload_speed": 2.0}

    async def async_get_port_info(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"label": "0", "link": "up"}]

    async def async_get_all_interface_stats(self, **kwargs: Any) -> dict[str, Any]:
        return self.interface_stats

    @staticmethod
    def summarize_client_stats(clients: list[dict[str, Any]]) -> dict[str, Any]:
        return KeeneticClient.summarize_client_stats(clients)


def test_coordinator_first_refresh_builds_enriched_payload() -> None:
    """The staged coordinator update assembles the runtime payload consistently."""
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    data = asyncio.run(coordinator._async_update_data())

    assert data["system"]["title"] == "4.2.0"
    assert data["system"]["release-available"] == "4.3.0"
    assert data["clients_by_mac"] == {
        "aa:bb:cc:dd:ee:ff": client.clients[0],
    }
    assert data["new_clients"] == {"aa:bb:cc:dd:ee:ff"}
    assert data["mesh_nodes"] == client.mesh_nodes
    assert data["wan_interfaces"][0]["id"] == "PPPoE0"
    assert data["wan_interfaces"][0]["internet_access"] is False
    assert data["wan_interfaces"][0]["internet_access_source"] == "ping_check"
    assert data["wan_interfaces"][0]["rx_bytes"] == 1000
    assert data["wan_interfaces"][0]["tx_bytes"] == 2000
    assert data["wan_interfaces"][0]["rx_throughput"] == 0.0
    assert data["wan_interfaces"][0]["role_label"] == "Default connection"
    assert data["wan_interfaces"][1]["role_label"] == "Backup connection 1"
    assert data["crypto_maps"]["SITE"]["rx_throughput"] == 0.0


def test_coordinator_fast_refresh_reuses_slow_cached_data_and_rates() -> None:
    """Fast ticks reuse slow data and calculate monotonic WAN/crypto throughput."""
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())
    first["wan_interfaces"][0]["_sample_ts"] = 1.0
    first["wan_interfaces"][0]["rx_bytes"] = 100
    first["wan_interfaces"][0]["tx_bytes"] = 500
    first["crypto_maps"]["SITE"]["_sample_ts"] = 1.0
    first["crypto_maps"]["SITE"]["rx_bytes"] = 10
    first["crypto_maps"]["SITE"]["tx_bytes"] = 20
    first["crypto_maps"]["SITE"]["rx_throughput"] = 7.0
    first["crypto_maps"]["SITE"]["tx_throughput"] = 8.0
    first["host_policies"] = {"cached": {"policy": "Policy1"}}
    first["ndns"] = {"cached": True}
    first["dns_proxy"] = {"cached": True}
    first["ipsec_diagnostics"] = {"cached": True}
    coordinator.data = first
    coordinator._refresh_count = 1

    second = asyncio.run(coordinator._async_update_data())

    assert second["host_policies"] == {"cached": {"policy": "Policy1"}}
    assert second["ndns"] == {"cached": True}
    assert second["dns_proxy"] == {"cached": True}
    assert second["ipsec_diagnostics"] == {"cached": True}
    assert second["new_clients"] == set()
    assert second["wan_interfaces"][0]["rx_throughput"] > 0
    assert second["wan_interfaces"][0]["tx_throughput"] > 0
    assert second["crypto_maps"]["SITE"]["_sample_ts"] == 1.0
    assert second["crypto_maps"]["SITE"]["rx_throughput"] == 7.0
    assert second["crypto_maps"]["SITE"]["tx_throughput"] == 8.0


def test_coordinator_preserves_tracked_client_presence_on_fetch_failure() -> None:
    """A transient client-table failure must not emit false away events."""
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())
    coordinator.data = first

    async def fail_clients() -> list[dict[str, Any]]:
        raise RuntimeError("hotspot table unavailable")

    client.async_get_clients = fail_clients  # type: ignore[assignment]

    tracker = KeeneticClientTracker(
        coordinator=coordinator,
        entry=SimpleNamespace(entry_id="entry_123", title="Router"),
        mac="aa:bb:cc:dd:ee:ff",
        label="Kitchen tablet",
        initial_ip="192.0.2.10",
    )
    assert tracker.is_connected is True

    second = asyncio.run(coordinator._async_update_data())
    coordinator.data = second

    assert second["clients_stale"] is True
    assert second["clients"] == first["clients"]
    assert second["clients_by_mac"] == first["clients_by_mac"]
    assert tracker.is_connected is True
    assert tracker.extra_state_attributes["presence_source"] == "active"


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (KeeneticAuthError("bad password"), ConfigEntryAuthFailed),
        (RuntimeError("router offline"), UpdateFailed),
    ],
)
def test_coordinator_critical_fetch_failures_raise_ha_errors(
    exception: Exception,
    expected: type[Exception],
) -> None:
    """Critical system/interface failures should mark the config entry unavailable."""
    client = FakeKeeneticClient()

    async def fail_system_info() -> dict[str, Any]:
        raise exception

    client.async_get_system_info = fail_system_info  # type: ignore[assignment]
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    with pytest.raises(expected):
        asyncio.run(coordinator._async_update_data())
