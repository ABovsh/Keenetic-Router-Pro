"""Canned mesh RCI payloads for coordinator tests."""

from __future__ import annotations

from conftest import TEST_HOST

MESH_NODES = [
    {
        "cid": "controller",
        "id": "node-controller",
        "name": "Controller",
        "ip": TEST_HOST,
        "associations": "2",
    },
    {
        "id": "extender-1",
        "name": "Extender",
        "ip": "192.0.2.2",
        "associations": 1,
    },
]

FALLBACK_MESH_NODE = {
    "id": "AA:BB:CC:00:00:01",
    "cid": None,
    "mac": "AA:BB:CC:00:00:01",
    "name": "Kitchen Extender",
    "ip": "192.0.2.20",
    "mode": "extender",
    "connected": True,
    "uptime": 120,
    "firmware": "4.2.0",
    "firmware_available": "4.3.0",
}
