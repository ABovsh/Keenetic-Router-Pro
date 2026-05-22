"""Coordinator-published mesh association summary tests."""

from __future__ import annotations

import asyncio
from typing import Any

from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from tests.test_coordinator_update_flow import FakeKeeneticClient


def test_coordinator_publishes_mesh_associations_dict() -> None:
    client = FakeKeeneticClient()
    client.mesh_nodes = [
        {"cid": "node-1", "associations": "2"},
        {"id": "node-2", "associations": 3},
        {"cid": "node-3", "associations": "bad"},
        {"cid": "node-4"},
        "bad-row",
    ]

    client.clients = [
        {"mac": "AA:BB:CC:DD:EE:FF", "active": True, "ssid": "Main"},
        {"mac": "11:22:33:44:55:66", "active": True, "ssid": "Guest"},
    ]

    async def clients() -> list[dict[str, Any]]:
        return client.clients

    client.async_get_clients = clients  # type: ignore[assignment]
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    data = asyncio.run(coordinator._async_update_data())

    assert data["mesh_associations"] == {
        "total": 5,
        "by_node": {
            "node-1": 2,
            "node-2": 3,
            "node-3": 0,
            "node-4": 0,
        },
    }
