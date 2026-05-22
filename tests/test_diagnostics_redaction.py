"""Diagnostics redaction regression tests."""

from __future__ import annotations

from tests.conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import json
import re
from types import SimpleNamespace
from typing import Any

from custom_components.keenetic_router_pro.diagnostics import (
    _MAC_KEYED_INDEXES,
    async_get_config_entry_diagnostics,
)


MAC = "AA:BB:CC:DD:EE:FF"
MAC_LOWER = MAC.lower()
MAC_RE = re.compile(r"\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b", re.I)


def _walk_dict_keys(value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    keys: list[tuple[tuple[str, ...], str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            keys.append((path, key_text))
            keys.extend(_walk_dict_keys(item, (*path, key_text)))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_walk_dict_keys(item, path))
    return keys


async def test_diagnostics_redacts_mac_keyed_indexes_and_value_leaks() -> None:
    coordinator_data = {
        "clients_by_mac": {MAC_LOWER: {"mac": MAC, "hostname": "phone"}},
        "host_policies": {MAC_LOWER: {"policy": "Policy1", "mac": MAC}},
        "client_stats": {MAC_LOWER: {"mac": MAC, "rx": 1}},
        "mesh_nodes_by_cid": {MAC_LOWER: {"cid": MAC_LOWER, "mac": MAC}},
        "mesh_associations": {
            "total": 4,
            "by_node": {MAC_LOWER: 4},
        },
        "clients": [{"mac": MAC, "neighbour": {"mac_address": MAC}}],
    }
    entry = SimpleNamespace(
        title="router",
        version=1,
        domain="keenetic_router_pro",
        source="user",
        data={"host": TEST_HOST, "username": TEST_USERNAME, "password": TEST_PASSWORD},
        options={},
        runtime_data=SimpleNamespace(
            coordinator=SimpleNamespace(data=coordinator_data),
            client=SimpleNamespace(),
        ),
    )

    result = await async_get_config_entry_diagnostics(None, entry)
    serialized = json.dumps(result, sort_keys=True)

    assert MAC not in serialized
    assert MAC_LOWER not in serialized
    for key in _MAC_KEYED_INDEXES:
        assert result["coordinator_data"][key] == {"<redacted-mac-keys>": 1}
    assert result["coordinator_data"]["mesh_associations"]["by_node"] == {
        "<redacted-mac-keys>": 1
    }
    assert result["coordinator_data"]["mesh_associations"]["total"] == 4


def test_known_mac_keyed_indexes_cover_realistic_payload_shape() -> None:
    payload = {
        "clients_by_mac": {MAC_LOWER: {}},
        "host_policies": {MAC_LOWER: {}},
        "client_stats": {MAC_LOWER: {}},
        "mesh_nodes_by_cid": {MAC_LOWER: {}},
        "mesh_associations": {"total": 1, "by_node": {MAC_LOWER: 1}},
        "safe_counts": {"total": 1},
    }
    allowed_key_parents = set(_MAC_KEYED_INDEXES) | {"by_node"}

    leaked_keys = [
        key
        for path, key in _walk_dict_keys(payload)
        if MAC_RE.fullmatch(key) and not (set(path) & allowed_key_parents)
    ]

    assert leaked_keys == []
