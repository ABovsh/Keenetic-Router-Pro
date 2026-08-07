"""Hardening tests for the 1.8.0 audit round.

A. Attribute stability — HA's recorder only skips a write when *both* state
   and ``extra_state_attributes`` are unchanged between polls. Several
   entities currently leak per-poll-volatile router fields (counters,
   resolved ping-check addresses, per-node uptime/load) straight into their
   attribute dict, defeating that dedup on every fast tick.
B. Gauge cadence — ``show/system`` load/memory/conntrack gauges must only be
   republished on medium ticks, not every fast tick.
C. Memory-usage sensor must round to a whole percent.
D. Firmware update-check tier — ``components/check-update`` (and the
   ``async_get_available_version_info`` call it feeds) must move to its own
   very-low cadence, distinct from the existing very-slow tier.
E. IPsec diagnostics capability latch — a 404 must be remembered so the
   client stops hitting the endpoint every tick.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

from custom_components.keenetic_router_pro.api.client import KeeneticClient
from custom_components.keenetic_router_pro.api.errors import KeeneticApiError
from custom_components.keenetic_router_pro.binary_sensor import (
    KeeneticCryptoMapConnectedSensor,
    KeeneticMeshNodeSensor,
    KeeneticWanConnectedSensor,
)
from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from custom_components.keenetic_router_pro.coordinator_parts.refresh import (
    build_batch_tree,
    refresh_plan,
)
from custom_components.keenetic_router_pro.sensor.dns import (
    KeeneticDnsProxyStatusSensor,
)
from custom_components.keenetic_router_pro.sensor.mesh import (
    KeeneticMeshMemorySensor,
    KeeneticMeshSystemStateSensor,
)
from custom_components.keenetic_router_pro.sensor.network import (
    KeeneticWanRxThroughputSensor,
)
from custom_components.keenetic_router_pro.sensor.system import (
    KeeneticMemoryUsageSensor,
)


class _DummyCoordinator:
    """Stand-in coordinator with mutable ``data``, no HA plumbing."""

    def __init__(self, data: dict) -> None:
        self.data = data
        self.last_update_success = True

    def async_add_listener(self, *_a, **_kw):
        return lambda: None


class _Entry:
    entry_id = "entry"
    title = "Router"


# --------------------------------------------------------------------------
# A. Attribute stability
# --------------------------------------------------------------------------


def test_wan_connected_attrs_ignore_volatile_ping_check_fields() -> None:
    coordinator = _DummyCoordinator(
        {
            "wan_by_id": {
                "PPPoE0": {
                    "id": "PPPoE0",
                    "link_state": "up",
                    "internet_access": True,
                    "ping_check": {
                        "check_hosts": ["8.8.8.8", "1.1.1.1"],
                        "check_addresses": ["8.8.8.8", "1.1.1.1"],
                        "success_count": 10,
                        "fail_count": 0,
                        "max_fails": 3,
                    },
                }
            }
        }
    )
    entity = KeeneticWanConnectedSensor(coordinator, _Entry(), "PPPoE0")
    before = entity.extra_state_attributes

    coordinator.data["wan_by_id"]["PPPoE0"]["ping_check"] = {
        "check_hosts": ["8.8.8.8", "1.1.1.1"],
        "check_addresses": ["9.9.9.9"],
        "success_count": 11,
        "fail_count": 0,
        "max_fails": 3,
    }
    after = entity.extra_state_attributes

    assert after == before
    assert "success_count" not in after
    assert "check_targets" not in after
    # 1.10.0: fail_count is suppressed while the check passes — it dithers
    # 0/1 on a healthy link. See test_hardening_1_10_0.
    assert "fail_count" not in after
    assert after["max_fails"] == 3
    assert after["check_hosts"] == ["8.8.8.8", "1.1.1.1"]


def test_wan_rx_throughput_attrs_ignore_volatile_counters() -> None:
    coordinator = _DummyCoordinator(
        {
            "wan_by_id": {
                "PPPoE0": {
                    "id": "PPPoE0",
                    "stats_interface": "PPPoE0",
                    "stats_timestamp": 1.0,
                    "rx_bytes": 100,
                    "tx_bytes": 200,
                    "rx_speed_raw": 10,
                    "tx_speed_raw": 20,
                    "rx_throughput": 80.0,
                }
            }
        }
    )
    entity = KeeneticWanRxThroughputSensor(coordinator, _Entry(), "PPPoE0")
    before = entity.extra_state_attributes

    coordinator.data["wan_by_id"]["PPPoE0"].update(
        {
            "stats_timestamp": 2.0,
            "rx_bytes": 900,
            "tx_bytes": 1200,
            "rx_speed_raw": 1000,
            "tx_speed_raw": 2000,
        }
    )
    after = entity.extra_state_attributes

    assert after == before
    assert after == {"stats_interface": "PPPoE0"}


def test_mesh_node_attrs_ignore_volatile_runtime_fields() -> None:
    coordinator = _DummyCoordinator(
        {
            "mesh_nodes_by_cid": {
                "node-1": {
                    "cid": "node-1",
                    "mac": "aa:bb:cc:00:00:01",
                    "ip": "192.0.2.20",
                    "model": "Extender",
                    "mode": "extender",
                    "uptime": 100,
                    "cpuload": 5,
                    "memory": 40,
                    "associations": 3,
                }
            }
        }
    )
    entity = KeeneticMeshNodeSensor(coordinator, _Entry(), "node-1")
    before = entity.extra_state_attributes

    coordinator.data["mesh_nodes_by_cid"]["node-1"].update(
        {"uptime": 500, "cpuload": 40, "memory": 90, "associations": 7}
    )
    after = entity.extra_state_attributes

    assert after == before
    for key in ("uptime", "cpuload", "memory", "associations"):
        assert key not in after


def test_crypto_map_connected_attrs_ignore_volatile_counters() -> None:
    coordinator = _DummyCoordinator(
        {
            "crypto_maps": {
                "SITE": {
                    "name": "SITE",
                    "connected": True,
                    "state": "PHASE2_ESTABLISHED",
                    "rx_bytes": 100,
                    "tx_bytes": 200,
                }
            }
        }
    )
    entity = KeeneticCryptoMapConnectedSensor(coordinator, _Entry(), "SITE")
    before = entity.extra_state_attributes

    coordinator.data["crypto_maps"]["SITE"].update({"rx_bytes": 900, "tx_bytes": 1200})
    after = entity.extra_state_attributes

    assert after == before
    assert "rx_bytes" not in after
    assert "tx_bytes" not in after


def test_dns_proxy_status_attrs_ignore_volatile_counters() -> None:
    coordinator = _DummyCoordinator(
        {
            "dns_proxy": {
                "status": "ok",
                "proxy_count": 2,
                "requests_sent": 1000,
                "proxies": [{"name": "doh1", "state": "ok"}],
                "active_dns_server_count": 2,
            }
        }
    )
    entity = KeeneticDnsProxyStatusSensor(coordinator, _Entry())
    before = entity.extra_state_attributes

    coordinator.data["dns_proxy"].update(
        {
            "requests_sent": 1500,
            "proxies": [{"name": "doh1", "state": "degraded"}],
            "active_dns_server_count": 1,
        }
    )
    after = entity.extra_state_attributes

    assert after == before
    for key in ("requests_sent", "proxies", "active_dns_server_count"):
        assert key not in after


def test_mesh_system_state_attrs_ignore_volatile_node_contents() -> None:
    coordinator = _DummyCoordinator(
        {
            "mesh_nodes": [
                {
                    "mac": "aa:bb:cc:00:00:01",
                    "connected": True,
                    "associations": 3,
                    "ip": "192.0.2.20",
                }
            ]
        }
    )
    entity = KeeneticMeshSystemStateSensor(coordinator, _Entry())
    before = entity.extra_state_attributes

    coordinator.data["mesh_nodes"][0]["associations"] = 9
    coordinator.data["mesh_nodes"][0]["ip"] = "192.0.2.99"
    after = entity.extra_state_attributes

    assert after == before
    assert "nodes" not in after
    assert after["total_nodes"] == 1
    assert after["connected_nodes"] == 1
    assert after["disconnected_nodes"] == 0


# --------------------------------------------------------------------------
# C. Memory-usage sensor whole-number rounding
# --------------------------------------------------------------------------


def test_mesh_memory_sensor_returns_whole_percent() -> None:
    """Mesh nodes jitter the same 0.1 % decimal as the controller sensor."""
    coordinator = _DummyCoordinator(
        {"mesh_nodes_by_cid": {"node-1": {"cid": "node-1", "memory": "481/1000"}}}
    )
    entity = KeeneticMeshMemorySensor(coordinator, _Entry(), "node-1")
    assert entity.native_value == 48


def test_memory_usage_percent_fallback_field_also_rounds() -> None:
    """The third firmware shape must not reintroduce the 0.1 % jitter."""
    coordinator = _DummyCoordinator({"system": {"mem_used_percent": 48.1}})
    entity = KeeneticMemoryUsageSensor(coordinator, _Entry())
    assert entity.native_value == 48


def test_memory_usage_sensor_returns_whole_percent() -> None:
    coordinator = _DummyCoordinator(
        {"system": {"memtotal": 1000, "memfree": 519}}  # 48.1% raw
    )
    entity = KeeneticMemoryUsageSensor(coordinator, _Entry())
    assert entity.native_value == 48
    assert isinstance(entity.native_value, int)


# --------------------------------------------------------------------------
# D. Firmware update-check tier
# --------------------------------------------------------------------------


def test_refresh_plan_update_check_tier_flags() -> None:
    assert refresh_plan(first_refresh=True, refresh_count=0).update_check_refresh is True
    assert (
        refresh_plan(first_refresh=False, refresh_count=2880).update_check_refresh
        is True
    )
    assert (
        refresh_plan(first_refresh=False, refresh_count=30).update_check_refresh
        is False
    )
    assert (
        refresh_plan(first_refresh=False, refresh_count=300).update_check_refresh
        is False
    )


def test_build_batch_tree_gates_check_update_on_update_check_tier() -> None:
    very_slow_only = refresh_plan(first_refresh=False, refresh_count=30)
    tree = build_batch_tree(very_slow_only)
    assert "check-update" not in tree.get("components", {})
    assert "version" in tree.get("show", {})
    assert "ndns" in tree.get("show", {})
    assert "dns-proxy" in tree.get("show", {})

    update_check_tick = refresh_plan(first_refresh=False, refresh_count=2880)
    tree2 = build_batch_tree(update_check_tick)
    assert "check-update" in tree2.get("components", {})


class _UpdateTierFakeClient:
    """Fake client whose ``show/system`` and version-check calls are countable."""

    def __init__(self) -> None:
        self.version_available_calls = 0
        self._rci_batch_supported: bool | None = False

    def clear_tick_cache(self) -> None:
        pass

    async def prefetch_tick(self, tree: dict) -> bool:
        return False

    def _normalize_interfaces(self, interfaces: Any) -> list[dict[str, Any]]:
        return KeeneticClient(
            TEST_HOST, TEST_USERNAME, TEST_PASSWORD
        )._normalize_interfaces(interfaces)

    async def async_get_system_info(self) -> dict[str, Any]:
        return {"hostname": "router", "uptime": 100}

    async def async_get_current_version_info(self) -> dict[str, Any]:
        return {"title": "4.2.0", "release": "4.2.0"}

    async def async_get_available_version_info(self) -> dict[str, Any]:
        self.version_available_calls += 1
        return {
            "title": f"4.{self.version_available_calls}.0",
            "sandbox": "stable",
            "update-available": True,
        }

    async def async_get_interfaces(self) -> dict[str, Any]:
        return {
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
            }
        }

    async def async_get_clients(self) -> list[dict[str, Any]]:
        return []

    async def async_get_ip_neighbours(self) -> list[dict[str, Any]]:
        return []

    async def async_get_host_policies(self) -> dict[str, Any]:
        return {}

    async def async_get_policies(self) -> dict[str, Any]:
        return {}

    async def async_get_ndns_info(self) -> dict[str, Any]:
        return {}

    async def async_get_ping_check_status(self) -> dict[str, Any]:
        return {}

    async def async_get_ipsec_status(self) -> dict[str, Any]:
        return {}

    async def async_get_dns_proxy_status(self) -> dict[str, Any]:
        return {}

    async def async_get_ipsec_diagnostics(self) -> dict[str, Any]:
        return {}

    async def async_get_mesh_nodes(
        self, clients: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def async_get_wifi_networks(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def async_get_wireguard_status(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    async def async_get_vpn_tunnels(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    async def async_get_wan_status(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    async def async_get_wan_interfaces(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def async_get_traffic_stats(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    async def async_get_port_info(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def async_get_all_interface_stats(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    @staticmethod
    def summarize_client_stats(clients: list[dict[str, Any]]) -> dict[str, Any]:
        return {}


def test_update_check_tier_reuses_previous_values_off_tier() -> None:
    client = _UpdateTierFakeClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())
    assert first["system"]["release-available"] == "4.1.0"

    # Advance to refresh_count=30: a very-slow tick, but NOT an
    # update-check tick (2880). The version-check call must not fire
    # again and the published values must stay pinned to the previous
    # release info instead of drifting on a tier that never re-fetched it.
    coordinator.data = first
    coordinator._refresh_count = 30

    second = asyncio.run(coordinator._async_update_data())

    assert client.version_available_calls == 1, (
        "async_get_available_version_info must not be awaited off the "
        "update-check tier"
    )
    assert second["system"]["release-available"] == "4.1.0"
    assert second["system"]["fw-update-sandbox"] == "stable"


def test_write_confirm_refresh_does_not_trigger_update_check() -> None:
    """A switch/select confirming refresh must not hit the daily update check.

    ``update.py`` polls ``async_request_refresh`` every ~10 s while a mesh node
    installs firmware; folding the update check into that path would re-run the
    stateful ``components/check-update`` endpoint dozens of times per install.
    """
    client = _UpdateTierFakeClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())
    coordinator.data = first
    coordinator._refresh_count = 5

    coordinator.request_full_refresh()
    asyncio.run(coordinator._async_update_data())

    assert client.version_available_calls == 1


def test_explicit_update_check_request_is_honoured_once() -> None:
    """A post-install update check runs on the next tick, then disarms."""
    client = _UpdateTierFakeClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    first = asyncio.run(coordinator._async_update_data())
    coordinator.data = first
    coordinator._refresh_count = 5

    coordinator.request_update_check()
    second = asyncio.run(coordinator._async_update_data())
    assert client.version_available_calls == 2
    assert second["system"]["release-available"] == "4.2.0"

    coordinator.data = second
    asyncio.run(coordinator._async_update_data())
    assert client.version_available_calls == 2


# --------------------------------------------------------------------------
# B. show/system gauge cadence
# --------------------------------------------------------------------------


class _GaugeFakeClient(_UpdateTierFakeClient):
    """Fake client whose ``show/system`` gauges change on every call."""

    def __init__(self) -> None:
        super().__init__()
        self.system_calls = 0

    async def async_get_system_info(self) -> dict[str, Any]:
        self.system_calls += 1
        n = self.system_calls
        return {
            "hostname": "router",
            "uptime": 100 * n,
            "cpu_load": n,
            "cpuload": n,
            "cpu": n,
            "cpu-utilization": n,
            "memtotal": 1000,
            "memfree": 1000 - n,
            "mem": n,
            "memory": n,
            "conntotal": 100,
            "connfree": 100 - n,
        }


_GAUGE_KEYS = (
    "cpu_load",
    "cpuload",
    "cpu",
    "cpu-utilization",
    "memory",
    "mem",
    "memtotal",
    "memfree",
    "conntotal",
    "connfree",
)


def test_system_gauges_only_refresh_on_medium_ticks() -> None:
    client = _GaugeFakeClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    # tick 0: first refresh (always medium) -> fresh values from call #1.
    first = asyncio.run(coordinator._async_update_data())
    first_gauges = {k: first["system"].get(k) for k in _GAUGE_KEYS}

    # refresh_count is now 1 after the first tick's increment. Tick with
    # refresh_count=1 is a fast (non-medium) tick: 1 % 3 != 0.
    coordinator.data = first
    second = asyncio.run(coordinator._async_update_data())
    second_gauges = {k: second["system"].get(k) for k in _GAUGE_KEYS}

    assert second_gauges == first_gauges, (
        "fast tick must republish the previous tick's gauge values, not "
        "the freshly fetched ones"
    )

    # Force the next tick onto the medium cadence (refresh_count=3).
    coordinator.data = second
    coordinator._refresh_count = 3
    third = asyncio.run(coordinator._async_update_data())
    third_gauges = {k: third["system"].get(k) for k in _GAUGE_KEYS}

    assert third_gauges != first_gauges, (
        "medium tick must publish freshly fetched gauge values"
    )


# --------------------------------------------------------------------------
# E. IPsec diagnostics capability latch
# --------------------------------------------------------------------------


def test_ipsec_diagnostics_latches_off_after_404() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_post = AsyncMock(
        side_effect=KeeneticApiError("not found: 'show/log'", status=404)
    )
    client._rci_parse = AsyncMock(
        side_effect=KeeneticApiError("not found: 'show/log'", status=404)
    )

    async def run() -> tuple[dict, dict]:
        first = await client.async_get_ipsec_diagnostics()
        second = await client.async_get_ipsec_diagnostics()
        return first, second

    first, second = asyncio.run(run())

    assert first == {}
    assert second == {}
    client._rci_post.assert_awaited_once()
