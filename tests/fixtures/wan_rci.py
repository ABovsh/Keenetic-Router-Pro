"""Canned WAN/interface RCI payloads for coordinator tests."""

from __future__ import annotations

INTERFACES = {
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
        "defaultgw": False,
        "priority": 50,
        "role": ["inet"],
        "address": "10.10.10.2/32",
        "summary": {"layer": {"conf": "running", "ipv4": "running"}},
    },
}

WAN_STATUS = {"status": "connected", "interface": "PPPoE0"}

WAN_INTERFACES = [
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

INTERFACE_STATS = {
    "PPPoE0": {
        "rxbytes": "1000",
        "txbytes": "2000",
        "rxpackets": "10",
        "txpackets": "20",
        "rxspeed": "1000000",
        "txspeed": "500000",
        "interface_name": "PPPoE0",
        "timestamp": 123.0,
    },
    "Wireguard0": {
        "rx-bytes": "3000",
        "tx-bytes": "4000",
        "rx-packets": "30",
        "tx-packets": "40",
        "rx-speed": "2000000",
        "tx-speed": "1000000",
        "interface_name": "Wireguard0",
        "timestamp": 123.0,
    },
}

PING_CHECK = {
    "PPPoE0": {"passing": False, "status": "fail", "profile": "main"},
    "Wireguard0": {"passing": None, "status": "unknown", "profile": "backup"},
}

TRAFFIC_STATS = {"download_speed": 1.0, "upload_speed": 2.0}

PORT_INFO = [{"label": "0", "link": "up"}]
