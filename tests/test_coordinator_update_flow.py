"""Coordinator update-flow tests with a lightweight fake Keenetic client."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_HOST_ALT, TEST_PASSWORD, TEST_USERNAME

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.keenetic_router_pro.api import KeeneticAuthError, KeeneticClient
from custom_components.keenetic_router_pro.coordinator import (
    KeeneticCoordinator,
    _advance_oom_state,
    _merge_clients_with_neighbours,
)
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
        self.clients = [
            {
                "mac": "AA:BB:CC:DD:EE:FF",
                "active": True,
                "ssid": "Main",
                "ip": "0.0.0.0",
            }
        ]
        self.ip_neighbours = [
            {
                "mac": "AA:BB:CC:DD:EE:FF",
                "address-family": "ipv4",
                "address": "192.0.2.55",
                "first-seen": 277971,
                "last-seen": 1,
                "leasetime": 129,
                "expired": False,
                "wireless": True,
            }
        ]
        self.mesh_nodes = [{"cid": "node-1", "ip": "192.0.2.20"}]
        self.mesh_clients_args: list[list[dict[str, Any]] | None] = []
        self.interface_stats = {
            "PPPoE0": {"rxbytes": "1000", "txbytes": "2000"},
            "Wireguard0": {"rxbytes": "3000", "txbytes": "4000"},
        }
        self._rci_batch_supported: bool | None = False
        self._hotspot_subpath_winner: str | None = None
        self.ping_check = {
            "PPPoE0": {"passing": False, "status": "fail", "profile": "default"}
        }

    def clear_tick_cache(self) -> None:
        pass

    async def prefetch_tick(self, tree: dict) -> bool:
        return False

    def _normalize_interfaces(self, interfaces: Any) -> list[dict[str, Any]]:
        return KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)._normalize_interfaces(
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

    async def async_get_ip_neighbours(self) -> list[dict[str, Any]]:
        return self.ip_neighbours

    async def async_get_host_policies(self) -> dict[str, Any]:
        return {"aa:bb:cc:dd:ee:ff": {"policy": "Policy0"}}

    async def async_get_policies(self) -> dict[str, Any]:
        return {"Policy0": "Default VPN"}

    async def async_get_ndns_info(self) -> dict[str, Any]:
        return {"name": "router", "domain": "keenetic.pro"}

    async def async_get_ping_check_status(self) -> dict[str, Any]:
        return self.ping_check

    async def async_get_ipsec_status(self) -> dict[str, Any]:
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
        self.mesh_clients_args.append(clients)
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
        assert kwargs.get("wan_interfaces") is not None
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
        "aa:bb:cc:dd:ee:ff": {
            **client.clients[0],
            "ip": "192.0.2.55",
            "neighbour": client.ip_neighbours[0],
            "last-seen": 1,
            "last-seen-source": "neighbour",
            "first-seen": 277971,
            "first-seen-source": "neighbour",
            "neighbour-expired": False,
            "neighbour-wireless": True,
            "neighbour-leasetime": 129,
        },
    }
    assert data["new_clients"] == {"aa:bb:cc:dd:ee:ff"}
    assert data["mesh_nodes"] == client.mesh_nodes
    assert data["wan_interfaces"][0]["id"] == "PPPoE0"
    assert data["wan_interfaces"][0]["internet_access"] is False
    assert data["wan_interfaces"][0]["internet_access_source"] == "ping_check"
    assert data["wan_interfaces"][0]["rx_bytes"] == 1000
    assert data["wan_interfaces"][0]["tx_bytes"] == 2000
    assert data["wan_interfaces"][0]["rx_throughput"] == pytest.approx(0.0)
    assert data["wan_interfaces"][0]["role_label"] == "Default connection"
    assert data["wan_interfaces"][1]["role_label"] == "Backup connection 1"
    assert data["crypto_maps"]["SITE"]["rx_throughput"] == pytest.approx(0.0)


def test_coordinator_refreshes_wan_uptime_when_iface_set_unchanged() -> None:
    """WAN uptime must advance every medium tick even when the link set is stable.

    Regression guard for the freeze where ``wan_interfaces`` (which carries
    each interface's ``uptime``) was reused verbatim whenever the
    ``(id, type, link, state)`` fingerprint matched. The router's interface
    uptime ticks every second, but the fingerprint only changes on a link
    flap, so the WAN uptime sensor stuck at its value from the last flap for
    hours/days. The per-interface payload must be rebuilt every medium tick so
    uptime (and ip) stay fresh.
    """
    client = FakeKeeneticClient()

    calls = {"n": 0}

    async def wan_interfaces(**kwargs: Any) -> list[dict[str, Any]]:
        calls["n"] += 1
        uptime = calls["n"] * 100  # 100 on first tick, 200 on the next, ...
        return [
            {"id": "PPPoE0", "defaultgw": True, "priority": 100, "uptime": uptime},
            {"id": "Wireguard0", "defaultgw": False, "priority": 50, "uptime": uptime},
        ]

    client.async_get_wan_interfaces = wan_interfaces  # type: ignore[assignment]
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())
    assert first["wan_by_id"]["PPPoE0"]["uptime"] == 100

    coordinator.data = first
    coordinator._refresh_count = 3  # next tick is a medium refresh (count % 3 == 0)

    second = asyncio.run(coordinator._async_update_data())

    # The interface fingerprint is unchanged across both ticks, so the bug
    # reused the cached payload and froze uptime at 100. It must advance.
    assert second["wan_by_id"]["PPPoE0"]["uptime"] == 200
    assert second["wan_by_id"]["Wireguard0"]["uptime"] == 200


def test_coordinator_mesh_fetch_failure_falls_back_to_empty_mesh() -> None:
    """Optional mesh discovery must not fail the whole coordinator tick."""
    client = FakeKeeneticClient()

    async def fail_mesh_nodes(
        clients: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        raise RuntimeError("mesh endpoint unavailable")

    client.async_get_mesh_nodes = fail_mesh_nodes  # type: ignore[assignment]
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    data = asyncio.run(coordinator._async_update_data())

    assert data["mesh_nodes"] == []
    assert data["clients_by_mac"]["aa:bb:cc:dd:ee:ff"]["ip"] == "192.0.2.55"


def test_coordinator_backup_order_handles_string_priorities() -> None:
    """RCI commonly returns numbers as strings; WAN ordering must still work."""
    client = FakeKeeneticClient()

    async def wan_interfaces(**kwargs: Any) -> list[dict[str, Any]]:
        return [
            {"id": "BackupLow", "defaultgw": False, "priority": "10"},
            {"id": "Default", "defaultgw": True, "priority": "100"},
            {"id": "BackupHigh", "defaultgw": False, "priority": "80"},
        ]

    client.async_get_wan_interfaces = wan_interfaces  # type: ignore[assignment]
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    data = asyncio.run(coordinator._async_update_data())

    assert [wan["id"] for wan in data["wan_interfaces"]] == [
        "Default",
        "BackupHigh",
        "BackupLow",
    ]
    assert [wan["role_label"] for wan in data["wan_interfaces"]] == [
        "Default connection",
        "Backup connection 1",
        "Backup connection 2",
    ]


def test_neighbour_merge_keeps_offline_last_seen_and_ip() -> None:
    """Offline registered hotspot rows should get their stale timestamp from neighbours."""
    clients = [
        {
            "mac": "80:07:94:46:ab:ab",
            "ip": "0.0.0.0",
            "active": False,
            "uptime": 0,
        }
    ]
    neighbours = [
        {
            "mac": "80:07:94:46:ab:ab",
            "address-family": "ipv4",
            "address": TEST_HOST_ALT,
            "first-seen": 277668,
            "last-seen": 672,
            "leasetime": 1122,
            "expired": True,
            "wireless": False,
        }
    ]

    merged = _merge_clients_with_neighbours(clients, neighbours)

    assert merged == [
        {
            **clients[0],
            "ip": TEST_HOST_ALT,
            "neighbour": neighbours[0],
            "last-seen": 672,
            "last-seen-source": "neighbour",
            "first-seen": 277668,
            "first-seen-source": "neighbour",
            "neighbour-expired": True,
            "neighbour-wireless": False,
            "neighbour-leasetime": 1122,
        }
    ]


def test_neighbour_merge_prefers_neighbour_last_seen_for_offline_hotspot_row() -> None:
    """Offline hotspot rows may report zero-ish seen data; neighbour is authoritative."""
    clients = [
        {
            "mac": "80:07:94:46:ab:ab",
            "ip": "0.0.0.0",
            "active": False,
            "last-seen": 0,
        }
    ]
    neighbours = [
        {
            "mac": "80:07:94:46:ab:ab",
            "address-family": "ipv4",
            "address": TEST_HOST_ALT,
            "last-seen": 672,
            "expired": True,
        }
    ]

    merged = _merge_clients_with_neighbours(clients, neighbours)

    assert merged[0]["last-seen"] == 672
    assert merged[0]["last-seen-source"] == "neighbour"


def test_neighbour_merge_prefers_live_hotspot_last_seen() -> None:
    """Online hotspot timestamps are fresher than the neighbour fallback."""
    clients = [
        {
            "mac": "80:07:94:46:ab:ab",
            "ip": TEST_HOST_ALT,
            "active": True,
            "last-seen": 1,
            "first-seen": 276710,
        }
    ]
    neighbours = [
        {
            "mac": "80:07:94:46:ab:ab",
            "address-family": "ipv4",
            "address": TEST_HOST_ALT,
            "first-seen": 277971,
            "last-seen": 20,
            "expired": False,
            "wireless": True,
        }
    ]

    merged = _merge_clients_with_neighbours(clients, neighbours)

    assert merged[0]["last-seen"] == 1
    assert merged[0]["last-seen-source"] == "hotspot"
    assert merged[0]["first-seen"] == 276710
    assert merged[0]["first-seen-source"] == "hotspot"
    assert merged[0]["neighbour-expired"] is False


def test_coordinator_fast_refresh_reuses_slow_cached_data_and_rates() -> None:
    """Fast ticks reuse slow data and preserve cached WAN/crypto throughput."""
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())
    first["wan_interfaces"][0]["_sample_ts"] = 1.0
    first["wan_interfaces"][0]["rx_bytes"] = 100
    first["wan_interfaces"][0]["tx_bytes"] = 500
    first["wan_interfaces"][0]["rx_throughput"] = 9.0
    first["wan_interfaces"][0]["tx_throughput"] = 11.0
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
    # ipsec_diagnostics gets the in-memory OOM tracker state appended on
    # every tick (even cached), so the cached payload is preserved but
    # surfaced alongside ``oom_total`` / ``oom_last_seen`` for sensors.
    assert second["ipsec_diagnostics"]["cached"] is True
    assert second["ipsec_diagnostics"]["oom_total"] == 0
    assert second["ipsec_diagnostics"]["oom_last_seen"] is None
    assert second["new_clients"] == set()
    assert second["wan_interfaces"][0]["rx_throughput"] == pytest.approx(9.0)
    assert second["wan_interfaces"][0]["tx_throughput"] == pytest.approx(11.0)
    assert second["crypto_maps"]["SITE"]["_sample_ts"] == pytest.approx(1.0)
    assert second["crypto_maps"]["SITE"]["rx_throughput"] == pytest.approx(7.0)
    assert second["crypto_maps"]["SITE"]["tx_throughput"] == pytest.approx(8.0)


def test_coordinator_fast_refresh_does_not_mutate_cached_crypto_maps() -> None:
    """Fast ticks should copy cached slow data before enrichment."""
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())
    first["crypto_maps"]["SITE"]["_sample_ts"] = 1.0
    first["crypto_maps"]["SITE"]["rx_throughput"] = 7.0
    first["crypto_maps"]["SITE"]["tx_throughput"] = 8.0
    coordinator.data = first
    coordinator._refresh_count = 1

    second = asyncio.run(coordinator._async_update_data())

    assert second["crypto_maps"] is first["crypto_maps"]
    assert second["crypto_maps"]["SITE"] is first["crypto_maps"]["SITE"]
    assert first["crypto_maps"]["SITE"]["rx_throughput"] == pytest.approx(7.0)
    assert first["crypto_maps"]["SITE"]["tx_throughput"] == pytest.approx(8.0)


def test_coordinator_tolerates_malformed_optional_dict_payloads() -> None:
    """Optional diagnostic endpoints should not break the whole refresh."""
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    async def malformed_crypto_maps() -> dict[str, Any]:
        return {
            "BROKEN": {"connected": False},
            "SKIP": "not-a-map",
        }

    async def malformed_ping_check_status() -> list[Any]:
        return ["not-a-dict"]

    client.async_get_ipsec_status = malformed_crypto_maps  # type: ignore[assignment]
    client.async_get_ping_check_status = malformed_ping_check_status  # type: ignore[assignment]

    data = asyncio.run(coordinator._async_update_data())

    assert data["crypto_maps"]["BROKEN"]["rx_throughput"] == pytest.approx(0.0)
    assert data["crypto_maps"]["BROKEN"]["tx_throughput"] == pytest.approx(0.0)
    assert "SKIP" not in data["crypto_maps"]
    assert data["wan_interfaces"][0]["ping_check"] is None


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


def test_coordinator_new_client_detection_normalizes_mac_formats() -> None:
    """A MAC separator/case change must not fire a duplicate new-device event."""
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())
    first["clients"] = [{"mac": "AA-BB-CC-DD-EE-FF"}]
    coordinator.data = first

    client.clients = [{"mac": "aa:bb:cc:dd:ee:ff", "active": True}]

    second = asyncio.run(coordinator._async_update_data())

    assert second["new_clients"] == set()


def test_coordinator_new_client_detection_rejects_non_mac_tokens() -> None:
    """IPv6/link-local neighbour tokens and blanks must not become client IDs."""
    client = FakeKeeneticClient()
    client.clients = [
        {"mac": "fe80::1", "active": True},
        {"mac": "", "active": True},
        {"mac": "AA-BB-CC-DD-EE-FF", "active": True},
    ]
    client.ip_neighbours = [
        {"mac": "fe80::2", "address": "fe80::2"},
        {"mac": "aa.bb.cc.dd.ee.ff", "address": "192.0.2.55"},
    ]
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    data = asyncio.run(coordinator._async_update_data())

    assert set(data["clients_by_mac"]) == {"aa:bb:cc:dd:ee:ff"}
    assert data["new_clients"] == {"aa:bb:cc:dd:ee:ff"}


def test_coordinator_new_client_detection_normalizes_previous_index_keys() -> None:
    """A stale uppercase/index format must not emit a duplicate new-device event."""
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]
    first = asyncio.run(coordinator._async_update_data())
    first["clients_by_mac"] = {"AA-BB-CC-DD-EE-FF": first["clients"][0]}
    coordinator.data = first

    client.clients = [{"mac": "aa:bb:cc:dd:ee:ff", "active": True}]

    second = asyncio.run(coordinator._async_update_data())

    assert second["new_clients"] == set()


def test_coordinator_ignores_invalid_stored_oom_timestamp() -> None:
    """A corrupt Store timestamp should not crash a coordinator refresh."""
    client = FakeKeeneticClient()

    async def ipsec_diagnostics() -> dict[str, Any]:
        return {
            "events": [
                ("May 1 12:00:00", "IpSec::Vici::Stats: out of memory"),
            ]
        }

    client.async_get_ipsec_diagnostics = ipsec_diagnostics  # type: ignore[assignment]
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]
    coordinator._oom_state_loaded = True
    coordinator._oom_state = {"last_seen_iso": "not-a-timestamp", "total": 4}
    coordinator._oom_store = None  # type: ignore[assignment]

    data = asyncio.run(coordinator._async_update_data())

    assert data["ipsec_diagnostics"]["oom_total"] == 5
    assert data["ipsec_diagnostics"]["oom_last_seen"].startswith("202")


def test_oom_state_counts_new_events_sharing_last_seen_second() -> None:
    """Keenetic log timestamps have second precision; overlapping windows can add events in the same second."""
    state = {
        "last_seen_iso": "2026-05-01T12:00:00",
        "last_seen_count": 1,
        "total": 4,
    }
    events = [
        ("May 1 12:00:00", "IpSec::Vici::Stats: out of memory [0xcffe02a7]"),
        ("May 1 12:00:00", "IpSec::Vici::Stats: out of memory [0xcffe02a7]"),
        ("May 1 11:59:59", "IpSec::Vici::Stats: out of memory [0xcffe02a7]"),
    ]

    next_state = _advance_oom_state(
        state,
        events,
        now=datetime(2026, 5, 1, 12, 1, 0),
    )

    assert next_state == {
        "last_seen_iso": "2026-05-01T12:00:00",
        "last_seen_count": 2,
        "total": 5,
    }


def test_oom_state_ignores_future_watermark_after_clock_rollback() -> None:
    """A future stored timestamp must not suppress all newly visible log events."""
    state = {
        "last_seen_iso": "2026-06-01T00:00:00",
        "last_seen_count": 1,
        "total": 4,
    }
    events = [
        ("May 1 12:00:00", "IpSec::Vici::Stats: out of memory [0xcffe02a7]"),
    ]

    next_state = _advance_oom_state(
        state,
        events,
        now=datetime(2026, 5, 1, 12, 1, 0),
    )

    assert next_state == {
        "last_seen_iso": "2026-05-01T12:00:00",
        "last_seen_count": 1,
        "total": 5,
    }


def test_oom_state_tolerates_infinite_corrupt_last_seen_count() -> None:
    """JSON Store accepts Infinity; treating it as corrupt avoids a refresh crash."""
    state = {
        "last_seen_iso": "2026-05-01T12:00:00",
        "last_seen_count": float("inf"),
        "total": 4,
    }
    events = [
        ("May 1 12:00:00", "IpSec::Vici::Stats: out of memory [0xcffe02a7]"),
        ("May 1 12:00:00", "IpSec::Vici::Stats: out of memory [0xcffe02a7]"),
    ]

    next_state = _advance_oom_state(
        state,
        events,
        now=datetime(2026, 5, 1, 12, 1, 0),
    )

    assert next_state == {
        "last_seen_iso": "2026-05-01T12:00:00",
        "last_seen_count": 2,
        "total": 5,
    }


def test_coordinator_reuses_preserved_clients_for_mesh_on_fetch_failure() -> None:
    """Mesh fallback should not see an empty list during transient client failures."""
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())
    coordinator.data = first
    coordinator._refresh_count = 6
    client.mesh_clients_args.clear()

    async def fail_clients() -> list[dict[str, Any]]:
        raise RuntimeError("hotspot table unavailable")

    async def mesh_from_clients(
        clients: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        client.mesh_clients_args.append(clients)
        return [{"cid": "node-from-stale-clients"}] if clients else []

    client.async_get_clients = fail_clients  # type: ignore[assignment]
    client.async_get_mesh_nodes = mesh_from_clients  # type: ignore[assignment]

    second = asyncio.run(coordinator._async_update_data())

    assert second["clients_stale"] is True
    assert client.mesh_clients_args == [first["clients"]]
    assert second["mesh_nodes"] == [{"cid": "node-from-stale-clients"}]


def test_coordinator_propagates_gathered_cancelled_error() -> None:
    """A gathered child cancellation must cancel the update, not become empty data."""
    client = FakeKeeneticClient()

    async def cancel_clients() -> list[dict[str, Any]]:
        raise asyncio.CancelledError()

    client.async_get_clients = cancel_clients  # type: ignore[assignment]
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(coordinator._async_update_data())


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


def test_coordinator_tolerates_transient_system_timeout_keeping_last_data() -> None:
    """A single system_info timeout after a good tick keeps the last snapshot."""
    from custom_components.keenetic_router_pro.api import KeeneticApiError

    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())
    coordinator.data = first
    assert first["system"]["hostname"] == "router"

    async def timeout_system_info() -> dict[str, Any]:
        raise KeeneticApiError("Timeout for /rci/show/system")

    client.async_get_system_info = timeout_system_info  # type: ignore[assignment]

    # Within the grace window the tick succeeds and preserves the last system.
    second = asyncio.run(coordinator._async_update_data())
    assert second["system"]["hostname"] == "router"
    assert coordinator._critical_fail_streak == 1


def test_coordinator_fails_after_sustained_system_timeouts() -> None:
    """Repeated system_info timeouts exhaust the grace window and then fail."""
    from custom_components.keenetic_router_pro.api import KeeneticApiError
    from custom_components.keenetic_router_pro.coordinator_parts.fetching import (
        CRITICAL_FETCH_GRACE_TICKS,
    )

    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]
    coordinator.data = asyncio.run(coordinator._async_update_data())

    async def timeout_system_info() -> dict[str, Any]:
        raise KeeneticApiError("Timeout for /rci/show/system")

    client.async_get_system_info = timeout_system_info  # type: ignore[assignment]

    for _ in range(CRITICAL_FETCH_GRACE_TICKS):
        coordinator.data = asyncio.run(coordinator._async_update_data())

    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
