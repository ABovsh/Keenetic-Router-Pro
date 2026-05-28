"""Switches for Keenetic Router Pro (Wi-Fi + WireGuard on/off)."""
from __future__ import annotations
from typing import Any
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .api import KeeneticClient
from .const import DOMAIN, LINK_STATE_UP
from .coordinator import KeeneticCoordinator
from .entity import ControllerEntity, CryptoMapEntity, InterfaceEntity, WanEntity
from .entity_setup import DynamicEntityTracker, register_dynamic_entities
from .utils import iter_new_items


def _add_wan_enabled_switches(
    entities: list[SwitchEntity],
    coordinator: KeeneticCoordinator,
    entry: ConfigEntry,
    client: KeeneticClient,
    known_wan_ids: set[str],
) -> None:
    """Append switches for newly discovered WAN interfaces."""
    for wan in iter_new_items(coordinator, "wan_interfaces", known_wan_ids):
        wan_id = wan["id"]
        entities.append(
            KeeneticWanEnabledSwitch(
                coordinator=coordinator,
                entry=entry,
                client=client,
                wan_id=wan_id,
            )
        )


def _add_vpn_enabled_switches(
    entities: list[SwitchEntity],
    coordinator: KeeneticCoordinator,
    entry: ConfigEntry,
    client: KeeneticClient,
    known_wan_ids: set[str],
    known_vpn_ids: set[str],
) -> None:
    """Append switches for VPN interfaces that are not already WAN devices."""
    profiles = coordinator.data.get("vpn_tunnels", {}).get("profiles", {}) or {}
    for iface_id, profile in profiles.items():
        if not iface_id or iface_id in known_wan_ids or iface_id in known_vpn_ids:
            continue
        known_vpn_ids.add(iface_id)
        entities.append(
            KeeneticVpnSwitch(
                coordinator=coordinator,
                entry=entry,
                client=client,
                iface_id=iface_id,
                profile=profile,
            )
        )


def _add_crypto_map_enabled_switches(
    entities: list[SwitchEntity],
    coordinator: KeeneticCoordinator,
    entry: ConfigEntry,
    client: KeeneticClient,
    known_cmap_names: set[str],
) -> None:
    """Append switches for newly discovered site-to-site IPsec crypto maps."""
    crypto_maps = coordinator.data.get("crypto_maps") or {}
    if not isinstance(crypto_maps, dict):
        return
    for cmap_name in crypto_maps.keys():
        if cmap_name in known_cmap_names:
            continue
        known_cmap_names.add(cmap_name)
        entities.append(
            KeeneticCryptoMapEnabledSwitch(
                coordinator=coordinator,
                entry=entry,
                client=client,
                cmap_name=cmap_name,
            )
        )


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Keenetic Router Pro switches from a config entry."""
    runtime = entry.runtime_data
    coordinator: KeeneticCoordinator = runtime.coordinator
    client: KeeneticClient = runtime.client
    entities: list[SwitchEntity] = []

    # Wi-Fi interface switches
    for net in coordinator.data.get("wifi", []):
        if not isinstance(net, dict):
            continue
        iface_id = net.get("id") or net.get("name")
        if not iface_id:
            continue

        display_name = net.get("name") or net.get("ssid") or iface_id

        entities.append(
            KeeneticWifiSwitch(
                coordinator=coordinator,
                entry=entry,
                client=client,
                interface_id=iface_id,
                display_name=display_name,
            )
        )

    tracker = DynamicEntityTracker()

    def _build_dynamic_switches() -> list[SwitchEntity]:
        dynamic_entities: list[SwitchEntity] = []
        _add_wan_enabled_switches(
            dynamic_entities,
            coordinator,
            entry,
            client,
            tracker.wan_ids,
        )
        _add_vpn_enabled_switches(
            dynamic_entities,
            coordinator,
            entry,
            client,
            tracker.wan_ids,
            tracker.vpn_ids,
        )
        _add_crypto_map_enabled_switches(
            dynamic_entities,
            coordinator,
            entry,
            client,
            tracker.crypto_maps,
        )
        return dynamic_entities

    entities.extend(_build_dynamic_switches())

    if entities:
        async_add_entities(entities)

    register_dynamic_entities(
        entry,
        coordinator,
        async_add_entities,
        _build_dynamic_switches,
        add_initial=False,
    )


class BaseKeeneticSwitch(ControllerEntity, SwitchEntity):
    """Base switch class sharing device_info + refresh logic."""
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        client: KeeneticClient,
    ) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._client = client


class KeeneticWifiSwitch(BaseKeeneticSwitch):
    """Wi-Fi SSID / interface aç/kapat switch'i."""

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        client: KeeneticClient,
        interface_id: str,
        display_name: str,
    ) -> None:
        super().__init__(coordinator, entry, client)
        self._interface_id = interface_id
        self._display_name = display_name
        self._attr_name = f"Wi-Fi {self._display_name}"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wifi_{self._interface_id}"

    @property
    def available(self) -> bool:
        if not bool(getattr(super(), "available", True)):
            return False
        return any(
            (net.get("id") or net.get("name")) == self._interface_id
            for net in self.coordinator.data.get("wifi", []) or []
        )

    @property
    def is_on(self) -> bool:
        for net in self.coordinator.data.get("wifi", []):
            nid = net.get("id") or net.get("name")
            if nid == self._interface_id:
                enabled = net.get("enabled")
                if enabled is not None:
                    return bool(enabled)
                state = str(net.get("state", "")).lower()
                if state:
                    return state == LINK_STATE_UP
        return False

    async def async_turn_on(self, **_: Any) -> None:
        await self._client.async_set_wifi_enabled(self._interface_id, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **_: Any) -> None:
        await self._client.async_set_wifi_enabled(self._interface_id, False)
        await self.coordinator.async_request_refresh()


class KeeneticWanEnabledSwitch(WanEntity, SwitchEntity):
    """Enable/disable a WAN interface from its WAN sub-device."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:toggle-switch"

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        client: KeeneticClient,
        wan_id: str,
    ) -> None:
        WanEntity.__init__(self, coordinator, entry.entry_id, entry.title, wan_id)
        self._client = client

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_{self._wan_id}_enabled_switch"

    @property
    def name(self) -> str:
        return "Enabled"

    @property
    def is_on(self) -> bool:
        wan = self._wan
        if wan is None:
            return False
        return bool(wan.get("enabled"))

    async def async_turn_on(self, **_: Any) -> None:
        await self._client.async_set_interface_enabled(self._wan_id, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **_: Any) -> None:
        await self._client.async_set_interface_enabled(self._wan_id, False)
        await self.coordinator.async_request_refresh()


class KeeneticVpnSwitch(InterfaceEntity, SwitchEntity):
    """Generic VPN tunnel enable/disable switch."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        client: KeeneticClient,
        iface_id: str,
        profile: dict[str, Any],
    ) -> None:
        self._iface_id = iface_id
        self._profile_type = str(profile.get("type") or "").lower()
        self._label = profile.get("label") or iface_id
        InterfaceEntity.__init__(
            self,
            coordinator,
            entry.entry_id,
            entry.title,
            iface_id,
            label=self._label,
            iface_type=self._profile_type,
        )
        self._client = client

        self._attr_name = "Enabled"
        self._attr_icon = "mdi:vpn"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_vpn_{self._iface_id}"

    def _current_profile(self) -> dict[str, Any]:
        vpn = self.coordinator.data.get("vpn_tunnels", {}) or {}
        profiles = vpn.get("profiles", {}) or {}
        return profiles.get(self._iface_id, {}) or {}

    @property
    def available(self) -> bool:
        return bool(getattr(super(), "available", True)) and bool(
            self._current_profile()
        )

    @property
    def is_on(self) -> bool:
        prof = self._current_profile()
        if "enabled" in prof:
            return bool(prof["enabled"])
        state = str(prof.get("state") or "").lower()
        if state:
            return state == LINK_STATE_UP
        return False

    async def async_turn_on(self, **_: Any) -> None:
        await self._client.async_set_interface_enabled(self._iface_id, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **_: Any) -> None:
        await self._client.async_set_interface_enabled(self._iface_id, False)
        await self.coordinator.async_request_refresh()

class KeeneticCryptoMapEnabledSwitch(CryptoMapEntity, SwitchEntity):
    """Enable / disable a site-to-site IPsec `crypto map` tunnel.

    Unlike VPN-client interfaces (which go through
    `async_set_interface_enabled`), site-to-site tunnels live under
    their own RCI sub-mode and are toggled with:

        crypto map <name>
          [no] enable

    The api layer also runs ``system configuration save`` after every
    toggle so the change survives a reboot — without that, the user
    would flip the switch, the tunnel would go down, and the next
    router restart would silently bring it back.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:vpn"

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        client: KeeneticClient,
        cmap_name: str,
    ) -> None:
        CryptoMapEntity.__init__(
            self, coordinator, entry.entry_id, entry.title, cmap_name
        )
        self._client = client

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_cmap_{self._cmap_name}_enabled"

    @property
    def name(self) -> str:
        return "Enabled"

    @property
    def is_on(self) -> bool:
        cmap = self._cmap
        if cmap is None:
            return False
        return bool(cmap.get("enabled"))

    async def async_turn_on(self, **_: Any) -> None:
        await self._client.async_set_crypto_map_enabled(
            self._cmap_name, True
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **_: Any) -> None:
        await self._client.async_set_crypto_map_enabled(
            self._cmap_name, False
        )
        await self.coordinator.async_request_refresh()
