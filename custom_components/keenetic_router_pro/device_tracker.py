"""Device tracker (presence) for Keenetic Router Pro."""
from __future__ import annotations
from typing import Any
from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import CONF_TRACKED_CLIENTS, LINK_STATE_UP
from .coordinator import KeeneticCoordinator
from .entity import ClientEntity
from .utils import coerce_bool, find_client_by_mac, iter_tracked_clients, usable_ip


async def async_setup_entry(
    _hass: HomeAssistant,
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

    for mac, label, initial_ip in iter_tracked_clients(entry):
        entities.append(
            KeeneticClientTracker(
                coordinator=coordinator,
                entry=entry,
                mac=mac,
                label=label,
                initial_ip=initial_ip,
            )
        )

    if entities:
        async_add_entities(entities)


class KeeneticClientTracker(ClientEntity, ScannerEntity):
    """Device tracker entity representing a tracked client."""
    # _attr_should_poll is already False on CoordinatorEntity (parent of
    # ClientEntity), so re-declaring it here adds nothing.
    _attr_entity_category = None  # Show as standalone tracker, not under Diagnostic
    _last_presence: tuple[bool, bool, str | None, str | None] | None = None

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

    @callback
    def _handle_coordinator_update(self) -> None:
        # Trackers bypass ClientEntity's full fingerprint dedup so Away/Home
        # transitions always reach HA, but we still suppress writes when only
        # volatile diagnostics (rssi / last-seen / uptime) tick and the
        # presence-relevant facts are unchanged — those values are surfaced by
        # their own dedicated sensors. Availability and connected-state are in
        # the key, so every Away/Home or unavailable transition still writes.
        # CoordinatorEntity registers a single listener for us in
        # async_added_to_hass; do NOT add a second.
        presence = (self.available, self.is_connected, self.ip_address, self.hostname)
        if presence == self._last_presence:
            return
        self._last_presence = presence
        self.async_write_ha_state()

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_client_{self._mac}"

    @property
    def available(self) -> bool:
        """Keep tracked clients available so missing router rows render as Away."""
        return bool(
            getattr(self.coordinator, "last_update_success", True)
            and not (self.coordinator.data or {}).get("clients_stale")
        )

    @property
    def mac_address(self) -> str:
        return self._mac

    @property
    def ip_address(self) -> str | None:
        client = self._client_from_main
        if client:
            ip = usable_ip(client.get("ip"))
            if ip:
                return ip

        return usable_ip(self._initial_ip)

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
        if str(client.get("link", "")).strip().lower() == LINK_STATE_UP:
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
            "ip": usable_ip(client.get("ip")) or usable_ip(self._initial_ip),
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
            client = index.get(self._mac)
            if isinstance(client, dict):
                return client
        return find_client_by_mac(data.get("clients"), self._mac)
