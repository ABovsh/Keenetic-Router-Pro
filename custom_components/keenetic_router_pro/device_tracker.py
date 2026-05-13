"""Device tracker (presence) for Keenetic Router Pro."""
from __future__ import annotations
from typing import Any
from homeassistant.components.device_tracker.config_entry import ScannerEntity
from homeassistant.components.device_tracker import SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN, CONF_TRACKED_CLIENTS
from .coordinator import KeeneticCoordinator
from .entity import ClientEntity
from .utils import coerce_bool


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Keenetic Router Pro device trackers from a config entry."""
    runtime = entry.runtime_data
    coordinator: KeeneticCoordinator = runtime.coordinator
    entities: list[KeeneticClientTracker] = []

    tracked_clients = entry.data.get(CONF_TRACKED_CLIENTS, [])

    if not tracked_clients:
        return

    seen_macs: set[str] = set()

    for client_info in tracked_clients:
        if not isinstance(client_info, dict):
            continue
            
        mac = str(client_info.get("mac") or "").lower()
        if not mac or mac in seen_macs:
            continue
        seen_macs.add(mac)

        label = client_info.get("name") or mac.upper()

        entities.append(
            KeeneticClientTracker(
                coordinator=coordinator,
                entry=entry,
                mac=mac,
                label=label,
                initial_ip=client_info.get("ip"),
            )
        )

    if entities:
        async_add_entities(entities)


class KeeneticClientTracker(ClientEntity, ScannerEntity):
    """Device tracker entity representing a tracked client."""
    # _attr_should_poll is already False on CoordinatorEntity (parent of
    # ClientEntity), so re-declaring it here adds nothing.
    _attr_entity_category = None  # Show as standalone tracker, not under Diagnostic

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        mac: str,
        label: str,
        initial_ip: str | None = None,
    ) -> None:
        ClientEntity.__init__(
            self, 
            coordinator,
            entry.entry_id,
            entry.title,
            mac,
            label,
            initial_ip,
        )
        self._main_coordinator = coordinator

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        
        self.async_on_remove(
            self._main_coordinator.async_add_listener(
                self._handle_coordinator_update
            )
        )
    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_client_{self._mac}"

    @property
    def mac_address(self) -> str:
        return self._mac

    @property
    def ip_address(self) -> str | None:
        client = self._client_from_main
        if client:
            ip = client.get("ip")
            if ip:
                return str(ip)
        
        return self._initial_ip

    @property
    def hostname(self) -> str | None:
        client = self._client_from_main
        if not client:
            return self._label

        name = client.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        h = client.get("hostname")
        if isinstance(h, str) and h.strip():
            return h.strip()
        return self._label

    @property
    def source_type(self) -> SourceType:
        return SourceType.ROUTER

    def _presence_source(self, client: dict[str, Any] | None) -> str:
        """Return the router field that currently proves client presence."""
        if not client:
            return "missing"
        if str(client.get("link", "")).lower() == "up":
            return "link"
        if coerce_bool(client.get("neighbour-expired")):
            return "neighbour_expired"
        if coerce_bool(client.get("active")):
            return "active"
        return "inactive"

    @property
    def is_connected(self) -> bool:
        return self._presence_source(self._client_from_main) in {"link", "active"}

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        client = self._client_from_main
        presence_source = self._presence_source(client)
        tracking_info: dict[str, Any] = {
            "tracking_method": "router_link",
            "presence_source": presence_source,
            "link_status": (client or {}).get("link", "unknown"),
        }

        attrs: dict[str, Any] = {
            "label": self._label,
            **tracking_info,
        }
        
        if not client:
            attrs["ip"] = self._initial_ip
            return attrs

        iface = client.get("interface")
        if isinstance(iface, dict):
            iface_name = iface.get("name") or iface.get("id")
        else:
            iface_name = iface

        attrs.update({
            "ip": client.get("ip") or self._initial_ip,
            "hostname": client.get("hostname"),
            "interface": iface_name,
            "ssid": client.get("ssid"),
            "rssi": client.get("rssi"),
            "txrate": client.get("txrate"),
            "access": client.get("access"),
            "priority": client.get("priority"),
            "active": client.get("active"),
            "link": client.get("link"),
            "last-seen": client.get("last-seen"),
            "last_seen_source": client.get("last-seen-source"),
            "first-seen": client.get("first-seen"),
            "first_seen_source": client.get("first-seen-source"),
            "uptime": client.get("uptime"),
            "registered": client.get("registered"),
            "neighbour_expired": client.get("neighbour-expired"),
            "neighbour_wireless": client.get("neighbour-wireless"),
            "neighbour_leasetime": client.get("neighbour-leasetime"),
        })
        return {k: v for k, v in attrs.items() if v is not None}

    @property
    def _client_from_main(self) -> dict[str, Any] | None:
        data = self._main_coordinator.data or {}
        index = data.get("clients_by_mac")
        if isinstance(index, dict):
            return index.get(self._mac)
        for item in data.get("clients", []) or []:
            if str(item.get("mac") or "").lower() == self._mac:
                return item
        return None
