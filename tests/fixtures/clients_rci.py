"""Canned client RCI payloads for coordinator tests."""

from __future__ import annotations

CLIENTS = [
    {
        "mac": "AA-BB-CC-DD-EE-FF",
        "name": "phone",
        "active": True,
        "ip": "0.0.0.0",
        "ssid": "Main",
    },
    {
        "mac": "11:22:33:44:55:66",
        "name": "tablet",
        "active": False,
        "ip": "192.0.2.66",
        "ssid": "Guest",
    },
]

IP_NEIGHBOURS = [
    {
        "mac": "AA:BB:CC:DD:EE:FF",
        "address-family": "ipv4",
        "address": "192.0.2.55",
        "first-seen": 100,
        "last-seen": 5,
        "leasetime": 900,
        "expired": False,
        "wireless": True,
    }
]

HOST_POLICIES = {
    "aa:bb:cc:dd:ee:ff": {"policy": "Kids"},
    "11:22:33:44:55:66": {"policy": "Guests"},
}
