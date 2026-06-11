"""Client sensors for tracked devices."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfInformation, UnitOfTime, EntityCategory

from ..coordinator import KeeneticCoordinator
from ..entity import ClientEntity
from ..utils import bytes_to_gib, coerce_bool, coerce_seconds, is_client_online

ZERO_COUNTER_VALUES = (None, "", 0, "0")
_BAND_2_4 = "2.4 GHz"
_BAND_5 = "5 GHz"
_ICON_WIFI = "mdi:wifi"
_ICON_WIFI_4 = "mdi:wifi-strength-4"


def _client_has_live_session(client: dict[str, Any] | None) -> bool:
    """Return whether live association-only client fields are meaningful."""
    return is_client_online(client)


def _coerce_optional_int(value: Any) -> int | None:
    """Return an integer for router numeric fields, or None when absent."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bytes_to_gb(value: Any) -> float | None:
    """Convert a byte counter to GiB for the existing client UI contract.

    Rejects missing, non-finite (NaN/inf), and negative values so malformed
    firmware counters never reach the TOTAL_INCREASING data-size sensors.
    """
    return bytes_to_gib(value)


def _client_counter_available(client: dict[str, Any] | None, key: str) -> bool:
    """Return whether a client byte counter is meaningful to show."""
    if not client:
        return False
    if not is_client_online(client) and client.get(key) in ZERO_COUNTER_VALUES:
        return False
    return True


def _router_ap_band(ap: Any) -> str | None:
    """Return Wi-Fi band inferred from a Keenetic access-point token."""
    ap_name = str(ap or "")
    if "WifiMaster0" in ap_name:
        return _BAND_2_4
    if "WifiMaster1" in ap_name:
        return _BAND_5
    return None


def _wifi_band_from_client(client: dict[str, Any]) -> str | None:
    """Infer the client's Wi-Fi band from the most authoritative fields."""
    if client.get("port") is not None or client.get("auto-negotiation") is not None:
        return None

    mws = client.get("mws")
    if isinstance(mws, dict):
        band = _router_ap_band(mws.get("ap"))
        if band:
            return band

    band = _router_ap_band(client.get("ap"))
    if band:
        return band

    txrate = _coerce_optional_int(client.get("txrate"))
    if txrate is not None:
        return _BAND_5 if txrate > 300 else _BAND_2_4

    return None


class KeeneticClientIpSensor(ClientEntity, SensorEntity):
    """IP address sensor for client."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:ip-network"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_client_{self._mac}_ip"

    @property
    def name(self) -> str:
        return "IP"

    @property
    def native_value(self) -> str | None:
        return self.ip_address


class KeeneticClientUptimeSensor(ClientEntity, SensorEntity):
    """Uptime sensor for client."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:timer-outline"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 0
    # Uptime must stay IN the fingerprint or this sensor freezes for idle
    # clients; only last-seen remains noise here.
    _FINGERPRINT_IGNORE = frozenset({"last-seen"})

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        mac: str,
        label: str,
    ) -> None:
        ClientEntity.__init__(self, coordinator, entry.entry_id, entry.title, mac, label)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_client_{self._mac}_uptime"

    @property
    def name(self) -> str:
        return "Wi-Fi Session"

    @property
    def available(self) -> bool:
        return super().available and _client_has_live_session(self._client)

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfTime.SECONDS

    @property
    def native_value(self) -> int:
        client = self._client
        if not client:
            return 0
        return coerce_seconds(client.get("uptime"), default=0) or 0


class KeeneticClientLastSeenSensor(ClientEntity, SensorEntity):
    """Local date/time when the router last saw the offline client."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:clock"
    _attr_device_class = None
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # last-seen must stay IN the fingerprint for this sensor to advance.
    _FINGERPRINT_IGNORE = frozenset({"uptime"})

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        mac: str,
        label: str,
    ) -> None:
        ClientEntity.__init__(self, coordinator, entry.entry_id, entry.title, mac, label)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_client_{self._mac}_last_seen"

    @property
    def name(self) -> str:
        return "Last Seen"

    @property
    def available(self) -> bool:
        """Last Seen is only useful once the tracked client is offline."""
        client = self._client
        if not client or is_client_online(client):
            return False
        return super().available and coerce_seconds(
            client.get("last-seen"),
            default=None,
        ) is not None

    @property
    def native_value(self) -> str | None:
        client = self._client
        if not client:
            return None
        if is_client_online(client):
            return None
        seconds = coerce_seconds(client.get("last-seen"), default=None)
        if seconds is None:
            return None
        seen_at = datetime.now().astimezone() - timedelta(seconds=seconds)
        return seen_at.strftime("%d.%m.%Y %H:%M:%S")


class KeeneticClientRxSensor(ClientEntity, SensorEntity):
    """Received traffic sensor."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:download-network"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        mac: str,
        label: str,
    ) -> None:
        ClientEntity.__init__(self, coordinator, entry.entry_id, entry.title, mac, label)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_client_{self._mac}_rx"

    @property
    def name(self) -> str:
        return "RX"

    @property
    def available(self) -> bool:
        return super().available and _client_counter_available(self._client, "rxbytes")

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfInformation.GIGABYTES

    @property
    def native_value(self) -> float | None:
        client = self._client
        if not _client_counter_available(client, "rxbytes"):
            return None
        return _bytes_to_gb(client.get("rxbytes"))


class KeeneticClientTxSensor(ClientEntity, SensorEntity):
    """Sent traffic sensor."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:upload-network"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        mac: str,
        label: str,
    ) -> None:
        ClientEntity.__init__(self, coordinator, entry.entry_id, entry.title, mac, label)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_client_{self._mac}_tx"

    @property
    def name(self) -> str:
        return "TX"

    @property
    def available(self) -> bool:
        return super().available and _client_counter_available(self._client, "txbytes")

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfInformation.GIGABYTES

    @property
    def native_value(self) -> float | None:
        client = self._client
        if not _client_counter_available(client, "txbytes"):
            return None
        return _bytes_to_gb(client.get("txbytes"))


class KeeneticClientRssiSensor(ClientEntity, SensorEntity):
    """WiFi RSSI (signal strength) sensor."""
    _attr_has_entity_name = True
    _attr_icon = _ICON_WIFI
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        mac: str,
        label: str,
    ) -> None:
        ClientEntity.__init__(self, coordinator, entry.entry_id, entry.title, mac, label)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_client_{self._mac}_rssi"

    @property
    def name(self) -> str:
        return "RSSI"

    @property
    def available(self) -> bool:
        return super().available and _client_has_live_session(self._client)

    @property
    def native_unit_of_measurement(self) -> str:
        return "dBm"

    @property
    def native_value(self) -> int | None:
        client = self._client
        if client:
            return _coerce_optional_int(client.get("rssi"))
        return None


class KeeneticClientTxRateSensor(ClientEntity, SensorEntity):
    """Current Wi-Fi link speed reported by the router."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:speedometer"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        mac: str,
        label: str,
    ) -> None:
        ClientEntity.__init__(self, coordinator, entry.entry_id, entry.title, mac, label)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_client_{self._mac}_txrate"

    @property
    def name(self) -> str:
        return "Link Speed"

    @property
    def available(self) -> bool:
        return super().available and _client_has_live_session(self._client)

    @property
    def native_unit_of_measurement(self) -> str:
        return "Mbps"

    @property
    def native_value(self) -> int | None:
        client = self._client
        if client:
            return _coerce_optional_int(client.get("txrate"))
        return None


class KeeneticClientConnectionTypeSensor(ClientEntity, SensorEntity):
    """Connection type sensor (WiFi 2.4GHz, WiFi 5GHz, Ethernet)."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:connection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        mac: str,
        label: str,
    ) -> None:
        ClientEntity.__init__(self, coordinator, entry.entry_id, entry.title, mac, label)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_client_{self._mac}_connection_type"

    @property
    def name(self) -> str:
        return "Connection Type"

    @property
    def native_value(self) -> str:
        """Return connection type."""
        client = self._client
        if not client:
            return "unknown"

        # Check if it's a wired connection (has port or speed without mws/ssid)
        if client.get("port") is not None or client.get("auto-negotiation") is not None:
            speed = client.get("speed")
            if speed:
                return f"Ethernet ({speed} Mbps)"
            return "Ethernet"

        mws = client.get("mws")
        if isinstance(mws, dict):
            ap = mws.get("ap", "")
            band = _router_ap_band(ap)
            if band == _BAND_2_4:
                return "WiFi 2.4 GHz (Mesh)"
            if band == _BAND_5:
                return "WiFi 5 GHz (Mesh)"
            return f"WiFi (Mesh) - {ap}"

        ssid = client.get("ssid")
        ap = client.get("ap")

        if ssid or ap:
            band = _router_ap_band(ap)
            if band is None:
                txrate = _coerce_optional_int(client.get("txrate")) or 0
                mode = str(client.get("mode") or "").lower()
                band = (
                    _BAND_5
                    if txrate > 300 or "ac" in mode or "ax" in mode
                    else _BAND_2_4
                )

            if ssid:
                return f"WiFi {band} - {ssid}"
            return f"WiFi {band}"

        # Try to determine from interface
        iface = client.get("interface")
        if iface:
            iface_name = iface if isinstance(iface, str) else iface.get("name", "")
            if "WifiMaster0" in str(iface_name):
                return "WiFi 2.4 GHz"
            if "WifiMaster1" in str(iface_name):
                return "WiFi 5 GHz"
            if "GigabitEthernet" in str(iface_name):
                return "Ethernet"

        txrate = _coerce_optional_int(client.get("txrate"))
        if txrate is not None:
            return "WiFi 5 GHz (likely)" if txrate > 300 else "WiFi 2.4 GHz (likely)"

        return "unknown"

    @property
    def icon(self) -> str:
        """Return icon based on connection type."""
        conn_type = self.native_value
        if "Ethernet" in conn_type:
            return "mdi:ethernet"
        if "2.4" in conn_type:
            return _ICON_WIFI
        if "5" in conn_type:
            return _ICON_WIFI_4
        return "mdi:wifi-question"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional attributes."""
        client = self._client
        if not client:
            return None

        attrs: dict[str, Any] = {}

        mws = client.get("mws")
        if isinstance(mws, dict):
            attrs["ap"] = mws.get("ap")
            attrs["mode"] = mws.get("mode")
            attrs["ht"] = mws.get("ht")
            attrs["security"] = mws.get("security")
            attrs["authenticated"] = mws.get("authenticated")
            if mws.get("roam"):
                attrs["roaming"] = mws.get("roam")

        if client.get("ssid"):
            attrs["ssid"] = client.get("ssid")
        if client.get("ap"):
            attrs["ap"] = client.get("ap")
        if client.get("mode"):
            attrs["mode"] = client.get("mode")

        if client.get("speed"):
            attrs["speed_mbps"] = client.get("speed")
        if client.get("duplex") is not None:
            attrs["duplex"] = "Full" if client.get("duplex") else "Half"
        if client.get("port"):
            attrs["port"] = client.get("port")

        return attrs if attrs else None


class KeeneticClientWifiBandSensor(ClientEntity, SensorEntity):
    """WiFi band sensor (2.4GHz, 5GHz, or None for wired)."""
    _attr_has_entity_name = True
    _attr_icon = _ICON_WIFI
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        mac: str,
        label: str,
    ) -> None:
        ClientEntity.__init__(self, coordinator, entry.entry_id, entry.title, mac, label)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_client_{self._mac}_wifi_band"

    @property
    def name(self) -> str:
        return "WiFi Band"

    @property
    def native_value(self) -> str | None:
        """Return WiFi band."""
        client = self._client
        if not client:
            return None

        return _wifi_band_from_client(client)

    @property
    def icon(self) -> str:
        """Return icon based on band."""
        band = self.native_value
        if band == _BAND_5:
            return _ICON_WIFI_4
        if band == _BAND_2_4:
            return _ICON_WIFI
        return "mdi:wifi-off"


class KeeneticClientWifiModeSensor(ClientEntity, SensorEntity):
    """WiFi mode sensor (11n, 11ac, 11ax)."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:wifi-settings"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        mac: str,
        label: str,
    ) -> None:
        ClientEntity.__init__(self, coordinator, entry.entry_id, entry.title, mac, label)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_client_{self._mac}_wifi_mode"

    @property
    def name(self) -> str:
        return "WiFi Mode"

    @property
    def available(self) -> bool:
        return super().available and _client_has_live_session(self._client)

    @property
    def native_value(self) -> str | None:
        """Return WiFi mode."""
        client = self._client
        if not client:
            return None

        mws = client.get("mws")
        if isinstance(mws, dict):
            mode = mws.get("mode")
            if mode:
                return mode.upper()

        mode = client.get("mode")
        if mode:
            return str(mode).upper()

        return None

    @property
    def icon(self) -> str:
        """Return icon based on WiFi mode."""
        mode = self.native_value
        if mode == "11AX":
            return _ICON_WIFI_4
        if mode == "11AC":
            return "mdi:wifi-strength-3"
        if mode == "11N":
            return "mdi:wifi-strength-2"
        if mode in ("11G", "11B"):
            return "mdi:wifi-strength-1"
        return _ICON_WIFI
