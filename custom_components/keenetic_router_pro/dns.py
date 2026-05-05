"""DNS/DoH diagnostic sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory

from ..coordinator import KeeneticCoordinator
from ..entity import ControllerEntity


class KeeneticDnsProxyStatusSensor(ControllerEntity, SensorEntity):
    """Overall DNS proxy health, including DoH upstream status."""

    _attr_has_entity_name = True
    _attr_name = "DNS Proxy Status"
    _attr_icon = "mdi:dns"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_dns_proxy_status"

    @property
    def native_value(self) -> str | None:
        dns_proxy = self.coordinator.data.get("dns_proxy", {}) or {}
        return dns_proxy.get("status")

    @property
    def icon(self) -> str:
        status = self.native_value
        if status == "ok":
            return "mdi:dns"
        if status == "degraded":
            return "mdi:dns-outline"
        if status == "down":
            return "mdi:dns-outline"
        return "mdi:help-network"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        dns_proxy = self.coordinator.data.get("dns_proxy", {}) or {}
        if not dns_proxy:
            return None
        return {
            "client_path_uses_doh": dns_proxy.get("client_path_uses_doh"),
            "proxy_count": dns_proxy.get("proxy_count"),
            "doh_server_count": dns_proxy.get("doh_server_count"),
            "dns_server_count": dns_proxy.get("dns_server_count"),
            "active_dns_server_count": dns_proxy.get("active_dns_server_count"),
            "requests_sent": dns_proxy.get("requests_sent"),
            "failed_requests": dns_proxy.get("failed_requests"),
            "proxies": dns_proxy.get("proxies"),
        }


class KeeneticDnsProxyFailedRequestsSensor(ControllerEntity, SensorEntity):
    """Number of failed DNS proxy upstream requests in router stats."""

    _attr_has_entity_name = True
    _attr_name = "DNS Proxy Failed Requests"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_dns_proxy_failed_requests"

    @property
    def native_value(self) -> int | None:
        dns_proxy = self.coordinator.data.get("dns_proxy", {}) or {}
        value = dns_proxy.get("failed_requests")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

