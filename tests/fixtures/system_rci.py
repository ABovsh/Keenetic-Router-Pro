"""Canned system/version RCI payloads for coordinator tests."""

from __future__ import annotations

SYSTEM_INFO = {
    "hostname": "router-pro",
    "uptime": 12345,
}

CURRENT_VERSION = {
    "title": "4.2.1",
    "release": "4.2.1",
    "model": "KN-1811",
}

AVAILABLE_VERSION = {
    "title": "4.3.0",
    "sandbox": "stable",
    "update-available": True,
}
