"""Change-detection fingerprint tests for WAN, mesh and crypto-map entities."""

from __future__ import annotations

from unittest.mock import Mock

from custom_components.keenetic_router_pro.entity import (
    CryptoMapEntity,
    MeshEntity,
    WanEntity,
)


class _DummyCoordinator:
    """Stand-in for KeeneticCoordinator with mutable data."""

    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {}

    def async_add_listener(self, *_a, **_kw):
        return lambda: None


def _patch_state_writer(entity) -> Mock:
    writer = Mock()
    entity.async_write_ha_state = writer
    return writer


def test_wan_counter_only_tick_skips_state_write() -> None:
    coordinator = _DummyCoordinator(
        {
            "wan_by_id": {
                "PPPoE0": {
                    "id": "PPPoE0",
                    "link_state": "up",
                    "ip": "203.0.113.10",
                    "rx_bytes": 100,
                    "tx_bytes": 200,
                }
            }
        }
    )
    entity = WanEntity(coordinator, "entry", "Router", "PPPoE0")
    writer = _patch_state_writer(entity)

    entity._handle_coordinator_update()
    coordinator.data["wan_by_id"]["PPPoE0"] = {
        **coordinator.data["wan_by_id"]["PPPoE0"],
        "rx_bytes": 900,
        "tx_bytes": 1200,
        "rx_packets": 10,
        "tx_packets": 20,
        "rx_speed_raw": 1000,
        "tx_speed_raw": 2000,
        "rx_throughput": 80.0,
        "tx_throughput": 40.0,
        "_sample_ts": 123.0,
        "stats_timestamp": "tick",
        "uptime": 500,
    }
    entity._handle_coordinator_update()

    assert writer.call_count == 1


def test_wan_semantic_change_triggers_state_write() -> None:
    coordinator = _DummyCoordinator(
        {
            "wan_by_id": {
                "PPPoE0": {"id": "PPPoE0", "link_state": "up", "ip": "203.0.113.10"}
            }
        }
    )
    entity = WanEntity(coordinator, "entry", "Router", "PPPoE0")
    writer = _patch_state_writer(entity)

    entity._handle_coordinator_update()
    coordinator.data["wan_by_id"]["PPPoE0"] = {
        "id": "PPPoE0",
        "link_state": "down",
        "ip": "203.0.113.20",
    }
    entity._handle_coordinator_update()

    assert writer.call_count == 2


def test_mesh_counter_only_tick_skips_state_write() -> None:
    coordinator = _DummyCoordinator(
        {
            "mesh_nodes_by_cid": {
                "node-1": {
                    "cid": "node-1",
                    "connected": True,
                    "ip": "192.0.2.20",
                    "role_label": "Kitchen",
                    "uptime": 100,
                    "cpuload": 5,
                    "mem-free": 1000,
                    "mem-cached": 200,
                    "last-seen": 10,
                    "rx-bytes": 300,
                    "tx-bytes": 400,
                }
            }
        }
    )
    entity = MeshEntity(coordinator, "entry", "Router", "node-1")
    writer = _patch_state_writer(entity)

    entity._handle_coordinator_update()
    coordinator.data["mesh_nodes_by_cid"]["node-1"] = {
        **coordinator.data["mesh_nodes_by_cid"]["node-1"],
        "uptime": 200,
        "cpuload": 10,
        "mem-free": 900,
        "mem-cached": 250,
        "last-seen": 20,
        "rx-bytes": 600,
        "tx-bytes": 700,
    }
    entity._handle_coordinator_update()

    assert writer.call_count == 1


def test_mesh_semantic_change_triggers_state_write() -> None:
    coordinator = _DummyCoordinator(
        {
            "mesh_nodes_by_cid": {
                "node-1": {"cid": "node-1", "connected": True, "role_label": "Kitchen"}
            }
        }
    )
    entity = MeshEntity(coordinator, "entry", "Router", "node-1")
    writer = _patch_state_writer(entity)

    entity._handle_coordinator_update()
    coordinator.data["mesh_nodes_by_cid"]["node-1"] = {
        "cid": "node-1",
        "connected": False,
        "role_label": "Office",
    }
    entity._handle_coordinator_update()

    assert writer.call_count == 2


def test_crypto_map_counter_only_tick_skips_state_write() -> None:
    coordinator = _DummyCoordinator(
        {
            "crypto_maps": {
                "SITE": {
                    "name": "SITE",
                    "connected": True,
                    "remote_peer": "198.51.100.10",
                    "rx_bytes": 100,
                    "tx_bytes": 200,
                }
            }
        }
    )
    entity = CryptoMapEntity(coordinator, "entry", "Router", "SITE")
    writer = _patch_state_writer(entity)

    entity._handle_coordinator_update()
    coordinator.data["crypto_maps"]["SITE"] = {
        **coordinator.data["crypto_maps"]["SITE"],
        "rx_bytes": 900,
        "tx_bytes": 1200,
        "rx_throughput": 80.0,
        "tx_throughput": 40.0,
        "_sample_ts": 123.0,
    }
    entity._handle_coordinator_update()

    assert writer.call_count == 1


def test_crypto_map_semantic_change_triggers_state_write() -> None:
    coordinator = _DummyCoordinator(
        {"crypto_maps": {"SITE": {"name": "SITE", "connected": True}}}
    )
    entity = CryptoMapEntity(coordinator, "entry", "Router", "SITE")
    writer = _patch_state_writer(entity)

    entity._handle_coordinator_update()
    coordinator.data["crypto_maps"]["SITE"] = {"name": "SITE", "connected": False}
    entity._handle_coordinator_update()

    assert writer.call_count == 2
