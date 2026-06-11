"""Hardening regression tests for the 1.7.57 audit round (Codex pass)."""

from __future__ import annotations

import asyncio

import pytest

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

from custom_components.keenetic_router_pro.api import KeeneticClient
from custom_components.keenetic_router_pro.utils import (
    coerce_seconds,
    get_client_device_info,
    get_mesh_device_info,
)


# X103 — non-dict system/version payloads are rejected
def test_system_getters_reject_list_payloads() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    async def fake_rci_get(path, **kwargs):
        return ["unexpected", "list"]

    client._rci_get = fake_rci_get
    assert asyncio.run(client.async_get_system_info()) == {}
    assert asyncio.run(client.async_get_current_version_info()) == {}
    assert asyncio.run(client.async_get_available_version_info()) == {}


# X106 — string-boolean defaultgw must not mark a backup WAN as default
def test_wan_defaultgw_string_false_is_false() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    async def fake_rci_get(path, **kwargs):
        return None

    client._rci_get = fake_rci_get
    wans = asyncio.run(
        client.async_get_wan_interfaces(
            iface_list=[
                {
                    "id": "PPPoE0",
                    "role": ["inet"],
                    "global": True,
                    "priority": 100,
                    "defaultgw": "no",
                    "state": "up",
                }
            ]
        )
    )
    assert wans and wans[0]["defaultgw"] is False


# X107 — multi-peer WireGuard traffic is summed, not first-peer-only
def test_wireguard_multi_peer_counters_summed() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    profiles = asyncio.run(
        client.async_get_wireguard_status(
            iface_list=[
                {
                    "id": "Wireguard0",
                    "type": "Wireguard",
                    "state": "up",
                    "wireguard": {
                        "peer": [
                            {"remote-endpoint-address": "a", "rxbytes": 100, "txbytes": 10},
                            {"remote-endpoint-address": "b", "rxbytes": 200, "txbytes": 20},
                        ]
                    },
                }
            ]
        )
    )
    prof = next(iter(profiles["profiles"].values()))
    assert prof["rxbytes"] == 300
    assert prof["txbytes"] == 30


# X303/X305 — malformed ssdp / fqdn payloads must not crash DeviceInfo
def test_client_device_info_tolerates_non_dict_ssdp() -> None:
    info = get_client_device_info(
        entry_id="e",
        title="router",
        mac="aa:bb:cc:00:00:01",
        label="phone",
        client={"ssdp": "weird-string", "ip": "10.0.0.5"},
        initial_ip=None,
    )
    assert info["identifiers"]


def test_mesh_device_info_tolerates_non_string_fqdn() -> None:
    info = get_mesh_device_info(
        title="router",
        entry_id="e",
        node={"name": "ext", "ip": "10.0.0.3"},
        node_cid="cid1",
        host="10.0.0.1",
        ssl=False,
        fqdn={"weird": "dict"},  # type: ignore[arg-type]
    )
    assert info["configuration_url"].endswith("10.0.0.3")


# X309 (partial) — boolean durations rejected
def test_coerce_seconds_rejects_bool() -> None:
    assert coerce_seconds(True, default=None) is None
    assert coerce_seconds(False, default=None) is None
