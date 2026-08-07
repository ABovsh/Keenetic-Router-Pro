"""Per-client bandwidth limit (router traffic-shape)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfDataRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api import KeeneticClient
from .coordinator import KeeneticCoordinator
from .entity import ClientEntity
from .utils import iter_tracked_clients

# Writes go straight to the router's config; never send two at once.
PARALLEL_UPDATES = 1

# 1 Gbit/s in kbit/s — above any rate a Keenetic will shape.
_MAX_KBPS = 1_000_000


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one bandwidth-limit control per tracked client."""
    runtime = entry.runtime_data
    coordinator: KeeneticCoordinator = runtime.coordinator
    client: KeeneticClient = runtime.client

    async_add_entities(
        KeeneticClientRateLimitNumber(coordinator, entry, client, mac, label)
        for mac, label, _initial_ip in iter_tracked_clients(entry)
    )


class KeeneticClientRateLimitNumber(ClientEntity, NumberEntity, RestoreEntity):
    """Cap one client's throughput via ``ip traffic-shape host``.

    The router does not report the configured shape back through any endpoint
    this integration polls, so the entity restores its own last value across
    restarts rather than showing a stale or empty reading.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:speedometer-slow"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = _MAX_KBPS
    _attr_native_step = 64
    _attr_native_unit_of_measurement = UnitOfDataRate.KILOBITS_PER_SECOND
    # A control, not telemetry: nothing here should ever reach the recorder on
    # a poll, and most people will never set a limit at all.
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        client: KeeneticClient,
        mac: str,
        label: str,
    ) -> None:
        ClientEntity.__init__(self, coordinator, entry.entry_id, entry.title, mac, label)
        self._api_client = client
        self._limit_kbps: float = 0

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_client_{self._mac}_rate_limit"

    @property
    def name(self) -> str:
        return "Bandwidth Limit"

    @property
    def native_value(self) -> float:
        return self._limit_kbps

    async def async_added_to_hass(self) -> None:
        """Restore the limit we last wrote to the router."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None:
            return
        try:
            self._limit_kbps = float(last_state.state)
        except (TypeError, ValueError):
            # "unknown"/"unavailable" from a restart mid-outage: 0 (no limit)
            # is the safe reading, since we cannot confirm what the router has.
            self._limit_kbps = 0

    async def async_set_native_value(self, value: float) -> None:
        """Apply the limit, or remove the shape entry when set to 0."""
        kbps = int(value)
        await self._api_client.async_set_client_rate_limit(self._mac, kbps)
        self._limit_kbps = kbps
        self.async_write_ha_state()
