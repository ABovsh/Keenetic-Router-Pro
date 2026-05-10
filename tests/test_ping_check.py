"""Regression tests for Keenetic ping-check parsing."""

from __future__ import annotations

import asyncio

from custom_components.keenetic_router_pro.api import KeeneticClient


def test_webadmin_ping_check_profile_is_authoritative() -> None:
    """Persistent web UI ping-check profiles must drive WAN outage state."""
    client = KeeneticClient("192.0.2.1", "admin", "secret")

    async def fake_rci_get(path: str):
        assert path == "show/ping-check"
        return {
            "pingcheck": [
                {
                    "profile": "_WEBADMIN_PPPoE0",
                    "host": ["captive.keenetic.net"],
                    "port": 80,
                    "mode": "icmp",
                    "update-interval": 30,
                    "max-fails": 3,
                    "interface": {
                        "PPPoE0": {
                            "status": "fail",
                            "successcount": 7,
                            "failcount": 3,
                            "ipcache": [
                                {
                                    "host": "captive.keenetic.net",
                                    "addresses": ["135.181.129.158"],
                                }
                            ],
                        }
                    },
                }
            ]
        }

    client._rci_get = fake_rci_get

    status = asyncio.run(client.async_get_ping_check_status())

    assert status["PPPoE0"]["passing"] is False
    assert status["PPPoE0"]["profile"] == "_WEBADMIN_PPPoE0"
    assert status["PPPoE0"]["check_addresses"] == ["135.181.129.158"]


def test_test_net_only_ping_check_profiles_are_ignored() -> None:
    """One-off TEST-NET probes should not create permanent false outages."""
    client = KeeneticClient("192.0.2.1", "admin", "secret")

    async def fake_rci_get(path: str):
        return {
            "pingcheck": [
                {
                    "profile": "temporary-test",
                    "host": ["192.0.2.1"],
                    "interface": {
                        "GigabitEthernet0/Vlan5": {
                            "status": "fail",
                            "ipcache": [
                                {"host": "probe", "addresses": ["203.0.113.10"]}
                            ],
                        }
                    },
                }
            ]
        }

    client._rci_get = fake_rci_get

    status = asyncio.run(client.async_get_ping_check_status())

    assert status["GigabitEthernet0/Vlan5"]["passing"] is None
    assert status["GigabitEthernet0/Vlan5"]["ignored_profiles"] == ["temporary-test"]


def test_mixed_ping_check_profiles_fail_closed() -> None:
    """Any real failing profile should mark the interface as failing."""
    client = KeeneticClient("192.0.2.1", "admin", "secret")

    async def fake_rci_get(path: str):
        return {
            "pingcheck": [
                {
                    "profile": "primary",
                    "host": ["captive.keenetic.net"],
                    "interface": {"PPPoE0": {"status": "pass"}},
                },
                {
                    "profile": "backup",
                    "host": ["1.1.1.1"],
                    "interface": {"PPPoE0": {"status": "fail"}},
                },
            ]
        }

    client._rci_get = fake_rci_get

    status = asyncio.run(client.async_get_ping_check_status())

    assert status["PPPoE0"]["status"] == "fail"
    assert status["PPPoE0"]["passing"] is False
    assert status["PPPoE0"]["profile"] == "backup"
