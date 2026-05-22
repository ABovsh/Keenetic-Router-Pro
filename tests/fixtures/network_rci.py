"""Canned network-domain RCI payloads."""

from __future__ import annotations

MULTI_WAN_INTERFACES = {
    "PPPoE0": {
        "id": "PPPoE0",
        "interface-name": "ISP",
        "description": "Fiber ISP",
        "type": "PPPoE",
        "state": "up",
        "link": "up",
        "global": True,
        "defaultgw": True,
        "priority": 100,
        "role": ["inet"],
        "address": "198.51.100.20/32",
        "summary": {"layer": {"conf": "running", "ipv4": "running"}},
    },
    "Wireguard0": {
        "id": "Wireguard0",
        "interface-name": "WG-Backup",
        "description": "Backup VPN",
        "type": "Wireguard",
        "state": "up",
        "link": "up",
        "global": True,
        "defaultgw": False,
        "priority": "50",
        "role": ["inet"],
        "address": "100.64.10.2/32",
        "summary": {"layer": {"conf": "running", "ipv4": "running"}},
    },
    "GigabitEthernet1": {
        "id": "GigabitEthernet1",
        "interface-name": "Carrier",
        "description": "Carrier only",
        "type": "GigabitEthernet",
        "state": "up",
        "link": "up",
        "global": False,
        "summary": {"layer": {"conf": "running", "ipv4": "running"}},
    },
}

NESTED_PORT_INTERFACES = {
    "GigabitEthernet0": {
        "id": "GigabitEthernet0",
        "type": "GigabitEthernet",
        "port": {
            "1": {"label": "1", "type": "Port", "link": "up", "speed": "1000", "duplex": "full"},
            "2": {"label": "2", "type": "Port", "link": "down"},
            "bad": "ignored",
        },
    },
    "GigabitEthernet1": {
        "id": "GigabitEthernet1",
        "type": "GigabitEthernet",
        "port": {"label": "3", "type": "Port", "link": "up", "speed": "100", "duplex": "full"},
    },
}

PING_PARSE_RESPONSES = {
    "success": "PING 1.1.1.1: 56 data bytes\n64 bytes from 1.1.1.1: icmp_seq=0 ttl=58\n1 packets transmitted, 1 received",
    "timeout": "PING 1.1.1.1: 56 data bytes\n1 packets transmitted, 0 received, 100% packet loss",
    "destination_unreachable": "From 192.0.2.1 icmp_seq=1 Destination host unreachable",
}

INTERFACE_STATS_PARSE = {
    "rxbytes": "2048",
    "txbytes": "1024",
    "rxspeed": "8192",
    "txspeed": "4096",
}

INTERFACE_STATS_BY_NAME = {
    "PPPoE0": {"rxbytes": "1000", "txbytes": "2000", "rxspeed": "8000", "txspeed": "4000"},
    "Wireguard0": {"rx-bytes": "3000", "tx-bytes": "4000", "rx-speed": "16000", "tx-speed": "8000"},
    "GigabitEthernet1": {"rxbytes": "5000", "txbytes": "6000"},
}

