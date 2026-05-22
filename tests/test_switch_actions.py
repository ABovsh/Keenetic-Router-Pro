"""Switch action tests for router-side enable/disable commands."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.keenetic_router_pro.switch import (
    KeeneticCryptoMapEnabledSwitch,
    KeeneticWanEnabledSwitch,
    KeeneticVpnSwitch,
    KeeneticWifiSwitch,
    async_setup_entry,
)


def _client() -> SimpleNamespace:
    return SimpleNamespace(
        async_set_wifi_enabled=AsyncMock(),
        async_set_interface_enabled=AsyncMock(),
        async_set_crypto_map_enabled=AsyncMock(),
    )


def _entry_with_runtime(coordinator, client) -> SimpleNamespace:
    unloads = []

    return SimpleNamespace(
        entry_id="entry_123",
        title="Router",
        data={},
        runtime_data=SimpleNamespace(coordinator=coordinator, client=client),
        async_on_unload=unloads.append,
        unloads=unloads,
    )


async def test_wifi_switch_turn_on_passes_interface_name_and_enabled_true(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory({"wifi": [{"id": "WifiMaster0"}]})
    client = _client()
    switch = KeeneticWifiSwitch(
        coordinator,
        keenetic_entry,
        client,
        "WifiMaster0",
        "Main",
    )

    await switch.async_turn_on()

    client.async_set_wifi_enabled.assert_awaited_once_with("WifiMaster0", True)


async def test_wifi_switch_turn_off_passes_interface_name_and_enabled_false(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory({"wifi": [{"id": "WifiMaster0"}]})
    client = _client()
    switch = KeeneticWifiSwitch(
        coordinator,
        keenetic_entry,
        client,
        "WifiMaster0",
        "Main",
    )

    await switch.async_turn_off()

    client.async_set_wifi_enabled.assert_awaited_once_with("WifiMaster0", False)


async def test_wan_switch_turn_on_passes_interface_name_and_enabled_true(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory(
        {"wan_interfaces": [{"id": "PPPoE0"}], "wan_by_id": {"PPPoE0": {}}}
    )
    client = _client()
    switch = KeeneticWanEnabledSwitch(coordinator, keenetic_entry, client, "PPPoE0")

    await switch.async_turn_on()

    client.async_set_interface_enabled.assert_awaited_once_with("PPPoE0", True)


async def test_wan_switch_turn_off_passes_interface_name_and_enabled_false(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory(
        {"wan_interfaces": [{"id": "PPPoE0"}], "wan_by_id": {"PPPoE0": {}}}
    )
    client = _client()
    switch = KeeneticWanEnabledSwitch(coordinator, keenetic_entry, client, "PPPoE0")

    await switch.async_turn_off()

    client.async_set_interface_enabled.assert_awaited_once_with("PPPoE0", False)


async def test_crypto_map_switch_turn_on_passes_map_name_and_enabled_true(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory({"crypto_maps": {"OfficeVPN": {}}})
    client = _client()
    switch = KeeneticCryptoMapEnabledSwitch(
        coordinator,
        keenetic_entry,
        client,
        "OfficeVPN",
    )

    await switch.async_turn_on()

    client.async_set_crypto_map_enabled.assert_awaited_once_with("OfficeVPN", True)


async def test_crypto_map_switch_turn_off_passes_map_name_and_enabled_false(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory({"crypto_maps": {"OfficeVPN": {}}})
    client = _client()
    switch = KeeneticCryptoMapEnabledSwitch(
        coordinator,
        keenetic_entry,
        client,
        "OfficeVPN",
    )

    await switch.async_turn_off()

    client.async_set_crypto_map_enabled.assert_awaited_once_with("OfficeVPN", False)


async def test_switch_setup_adds_initial_wifi_wan_vpn_and_crypto_entities(
    keenetic_coordinator_factory,
) -> None:
    listeners = []
    coordinator = keenetic_coordinator_factory(
        {
            "wifi": [{"id": "WifiMaster0", "ssid": "Main"}, "bad", {}],
            "wan_interfaces": [{"id": "PPPoE0"}],
            "vpn_tunnels": {
                "profiles": {
                    "PPPoE0": {"label": "WAN duplicate"},
                    "Wireguard0": {"label": "WG", "enabled": True},
                }
            },
            "crypto_maps": {"OfficeVPN": {}},
        }
    )
    coordinator.async_add_listener = lambda listener: (
        listeners.append(listener),
        (lambda: None),
    )[1]
    client = _client()
    entry = _entry_with_runtime(coordinator, client)
    added = []

    await async_setup_entry(SimpleNamespace(), entry, added.extend)

    assert [entity.unique_id for entity in added] == [
        "entry_123_wifi_WifiMaster0",
        "entry_123_wan_PPPoE0_enabled_switch",
        "entry_123_vpn_Wireguard0",
        "entry_123_cmap_OfficeVPN_enabled",
    ]


async def test_switch_setup_listener_adds_later_discovered_interfaces(
    keenetic_coordinator_factory,
) -> None:
    listeners = []
    coordinator = keenetic_coordinator_factory(
        {
            "wifi": [],
            "wan_interfaces": [],
            "vpn_tunnels": {"profiles": {}},
            "crypto_maps": {},
        }
    )
    coordinator.async_add_listener = lambda listener: (
        listeners.append(listener),
        (lambda: None),
    )[1]
    entry = _entry_with_runtime(coordinator, _client())
    added = []
    await async_setup_entry(SimpleNamespace(), entry, added.extend)

    coordinator.data["wan_interfaces"] = [{"id": "Ethernet1"}]
    coordinator.data["vpn_tunnels"] = {"profiles": {"Wireguard0": {"state": "up"}}}
    coordinator.data["crypto_maps"] = {"BranchVPN": {}}
    listeners[0]()

    assert [entity.unique_id for entity in added] == [
        "entry_123_wan_Ethernet1_enabled_switch",
        "entry_123_vpn_Wireguard0",
        "entry_123_cmap_BranchVPN_enabled",
    ]


def test_wifi_switch_state_falls_back_to_link_state(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory(
        {"wifi": [{"name": "WifiMaster0", "state": "up"}]}
    )
    switch = KeeneticWifiSwitch(
        coordinator,
        keenetic_entry,
        _client(),
        "WifiMaster0",
        "Main",
    )

    assert switch.is_on is True


def test_wifi_switch_missing_interface_is_unavailable(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    switch = KeeneticWifiSwitch(
        keenetic_coordinator_factory({"wifi": []}),
        keenetic_entry,
        _client(),
        "WifiMaster0",
        "Main",
    )

    assert switch.available is False


def test_wan_switch_missing_interface_is_off(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    switch = KeeneticWanEnabledSwitch(
        keenetic_coordinator_factory({"wan_interfaces": []}),
        keenetic_entry,
        _client(),
        "PPPoE0",
    )

    assert switch.is_on is False


def test_vpn_switch_enabled_profile_is_on(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory(
        {"vpn_tunnels": {"profiles": {"Wireguard0": {"enabled": True}}}}
    )
    switch = KeeneticVpnSwitch(
        coordinator,
        keenetic_entry,
        _client(),
        "Wireguard0",
        {"label": "WG"},
    )

    assert switch.is_on is True


def test_vpn_switch_state_profile_is_on(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory(
        {"vpn_tunnels": {"profiles": {"Wireguard0": {"state": "up"}}}}
    )
    switch = KeeneticVpnSwitch(
        coordinator,
        keenetic_entry,
        _client(),
        "Wireguard0",
        {"type": "wireguard"},
    )

    assert switch.is_on is True


async def test_vpn_switch_turn_on_passes_interface_name_and_enabled_true(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory(
        {"vpn_tunnels": {"profiles": {"Wireguard0": {"enabled": False}}}}
    )
    client = _client()
    switch = KeeneticVpnSwitch(
        coordinator,
        keenetic_entry,
        client,
        "Wireguard0",
        {},
    )

    await switch.async_turn_on()

    client.async_set_interface_enabled.assert_awaited_once_with("Wireguard0", True)


async def test_vpn_switch_turn_off_passes_interface_name_and_enabled_false(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    coordinator = keenetic_coordinator_factory(
        {"vpn_tunnels": {"profiles": {"Wireguard0": {"enabled": True}}}}
    )
    client = _client()
    switch = KeeneticVpnSwitch(
        coordinator,
        keenetic_entry,
        client,
        "Wireguard0",
        {},
    )

    await switch.async_turn_off()

    client.async_set_interface_enabled.assert_awaited_once_with("Wireguard0", False)


def test_crypto_map_switch_missing_map_is_off(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    switch = KeeneticCryptoMapEnabledSwitch(
        keenetic_coordinator_factory({"crypto_maps": {}}),
        keenetic_entry,
        _client(),
        "OfficeVPN",
    )

    assert switch.is_on is False
