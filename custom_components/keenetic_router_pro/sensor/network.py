"""Network sensors for WAN status, IP, PPPoE and connections."""

from __future__ import annotations

from time import monotonic
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
    SensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.const import UnitOfTime, UnitOfInformation, UnitOfDataRate, EntityCategory

from ..const import LINK_STATE_DOWN, LINK_STATE_UP, WAN_STATUS_CONNECTED, WAN_STATUS_LINK_UP
from ..const import COUNTER_DEADBAND_BYTES
from ..coordinator import KeeneticCoordinator
from ..entity import (
    ControllerEntity,
    CounterDeadbandMixin,
    DeadbandMixin,
    LinkActiveMixin,
    SourceFreshnessMixin,
    ThroughputDeadbandMixin,
    UptimeMixin,
    WanEntity,
)
from ..utils import (
    apply_relative_deadband,
    coerce_byte_count,
    coerce_int,
    coerce_seconds,
)

_ICON_ETHERNET = "mdi:ethernet"
_ICON_IP_NETWORK = "mdi:ip-network"


class KeeneticWanStatusSensor(ControllerEntity, SensorEntity):
    """WAN connection status sensor."""
    _attr_has_entity_name = True
    _attr_translation_key = "wan_status"

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_status"

    @property
    def native_value(self) -> str | None:
        wan = self.coordinator.data.get("wan_status", {})
        return wan.get("status", "down")

    @property
    def icon(self) -> str:
        status = self.native_value
        if status == WAN_STATUS_CONNECTED:
            return "mdi:web-check"
        if status == WAN_STATUS_LINK_UP:
            return "mdi:web-remove"
        return "mdi:web-off"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        wan = self.coordinator.data.get("wan_status", {})
        attrs: dict[str, Any] = {}
        if wan.get("interface"):
            attrs["interface"] = wan["interface"]
        if wan.get("type"):
            attrs["type"] = wan["type"]
        if wan.get("ip"):
            attrs["ip"] = wan["ip"]
        if wan.get("gateway"):
            attrs["gateway"] = wan["gateway"]
        if wan.get("link"):
            attrs["link"] = wan["link"]
        return attrs if attrs else None


class KeeneticWanIpSensor(ControllerEntity, SensorEntity):
    """WAN IP address sensor."""
    _attr_has_entity_name = True
    _attr_icon = _ICON_IP_NETWORK

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_ip"

    @property
    def name(self) -> str:
        return "WAN IP"

    @property
    def native_value(self) -> str | None:
        wan = self.coordinator.data.get("wan_status", {})
        return wan.get("ip")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        wan = self.coordinator.data.get("wan_status", {})
        return {
            "interface": wan.get("interface"),
            "gateway": wan.get("gateway"),
            "status": wan.get("status"),
        }


class KeeneticPppoeUptimeSensor(UptimeMixin, ControllerEntity, SensorEntity):
    """Uplink session uptime, published hourly (see ``UptimeMixin``)."""
    _attr_has_entity_name = True
    _attr_translation_key = "pppoe_uptime"
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Each WAN has its own Uptime sensor; new installs opt in to this copy.
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_pppoe_uptime"

    @property
    def native_value(self) -> int | None:
        wan = self.coordinator.data.get("wan_status", {}) or {}
        return self._publish_uptime(coerce_seconds(wan.get("uptime"), default=None))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        wan = self.coordinator.data.get("wan_status", {})
        return {
            "interface": wan.get("interface"),
            "type": wan.get("type"),
            "status": wan.get("status"),
            "ip": wan.get("ip"),
        }


class KeeneticActiveConnectionsSensor(DeadbandMixin, ControllerEntity, SensorEntity):
    """Active connections count sensor."""
    _attr_has_entity_name = True
    _attr_translation_key = "active_connections"
    _attr_icon = "mdi:connection"
    # Active connections is an instantaneous count, not a lifetime total.
    # MEASUREMENT keeps HA statistics from treating it as a monotonic sum.
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    # A dithering gauge: measured live it walks +31/+70/-97/+54 between
    # adjacent polls of an otherwise quiet router. 25 was too narrow to catch
    # that — the gauge stepped straight over it — so 50, still only 0.08 % of
    # the 63 488-entry conntrack table. On a busy router with thousands of
    # flows the swing grows with the count, so the band does too: 2026-10-02 a
    # router at ~3,000 flows still crossed the fixed 50 band 308 times a day.
    _DEADBAND = 50
    _DEADBAND_FRACTION = 0.05

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_active_connections"

    @property
    def native_value(self) -> int | None:
        sys = self.coordinator.data.get("system", {}) or {}
        # No reading is unknown, not zero: a fake 0 is a valid-looking
        # measurement in long-term statistics.
        conntotal = coerce_int(sys.get("conntotal"), None)
        connfree = coerce_int(sys.get("connfree"), None)
        if conntotal is None or connfree is None:
            self._deadband_published = None
            return None
        used = max(0, conntotal - connfree)
        published = apply_relative_deadband(
            used,
            getattr(self, "_deadband_published", None),
            self._DEADBAND_FRACTION,
            floor=self._DEADBAND,
        )
        self._deadband_published = published
        return int(published)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        # Derive from the DEADBANDED value, never the raw one. Both of these
        # restate the state, so reading the raw counters here meant that on
        # every tick the deadband held the state the attributes moved anyway
        # and HA wrote a row carrying nothing — 116 such rows in three hours,
        # measured after 1.12.0 shipped the deadband without this.
        used = self.native_value
        if used is None:
            return None
        sys = self.coordinator.data.get("system", {}) or {}
        conntotal = coerce_int(sys.get("conntotal"), 0)
        return {
            "total_capacity": conntotal,
            "free": max(0, conntotal - used),
            "used_percent": (
                round(used * 100.0 / conntotal, 1) if conntotal > 0 else 0
            ),
        }


class KeeneticLocalIpSensor(ControllerEntity, SensorEntity):
    """Sensor for local IP address of the router/device."""
    _attr_has_entity_name = True
    _attr_name = "IP"
    _attr_icon = _ICON_IP_NETWORK
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry, ip_address: str) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._ip_address = ip_address

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_local_ip"

    @property
    def native_value(self) -> str | None:
        return self._ip_address


class KeeneticMainPortSensor(ControllerEntity, SensorEntity):
    """Individual main router port sensor."""
    _attr_has_entity_name = True
    _attr_icon = _ICON_ETHERNET
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        port_label: str,
    ) -> None:
        """Initialize individual port sensor."""
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._port_label = port_label

    @property
    def name(self) -> str:
        """Return name for the sensor."""
        return f"Port {self._port_label}"

    @property
    def unique_id(self) -> str:
        """Return unique ID for the sensor."""
        return f"{self._entry_id}_port_{self._port_label}"

    @property
    def native_value(self) -> str | None:
        """Return port state."""
        ports = self.coordinator.data.get("port_info", [])
        for port in ports:
            if not isinstance(port, dict):
                continue
            if str(port.get("label")) == self._port_label:
                return port.get("link", "unknown")
        return None

    @property
    def available(self) -> bool:
        """Become unavailable when the router no longer reports this port."""
        return bool(getattr(super(), "available", True)) and self.native_value is not None

    @property
    def icon(self) -> str:
        """Return icon based on port state."""
        state = self.native_value
        if state == LINK_STATE_UP:
            return _ICON_ETHERNET
        if state == LINK_STATE_DOWN:
            return "mdi:ethernet-off"
        return _ICON_ETHERNET

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional port attributes."""
        ports = self.coordinator.data.get("port_info", [])
        for port in ports:
            if not isinstance(port, dict):
                continue
            if str(port.get("label")) == self._port_label:
                attrs = {
                    "label": port.get("label"),
                    "appearance": port.get("appearance"),
                }
                if port.get("link") == LINK_STATE_UP:
                    attrs["speed"] = port.get("speed")
                    attrs["duplex"] = port.get("duplex")
                return attrs
        return None

# =============================================================================
# Per-WAN interface sensors
#
# One set of these is instantiated per entry in coordinator.data["wan_interfaces"].
# Each WAN becomes its own HA sub-device (see utils.get_wan_device_info).
# =============================================================================


class _WanSensorBase(WanEntity, SensorEntity):
    """Shared base for per-WAN SensorEntity classes."""
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        wan_id: str,
    ) -> None:
        WanEntity.__init__(self, coordinator, entry.entry_id, entry.title, wan_id)


class KeeneticWanProviderSensor(_WanSensorBase):
    """Provider / description shown as the entity name."""
    _attr_icon = "mdi:web"
    # Static text, also the WAN Connected ``description`` attribute; opt in.
    _attr_entity_registry_enabled_default = False

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_{self._wan_id}_provider"

    @property
    def name(self) -> str:
        return "Provider"

    @property
    def native_value(self) -> str | None:
        wan = self._wan
        if not wan:
            return None
        return wan.get("description") or wan.get("interface_name") or self._wan_id


class KeeneticWanRoleSensor(_WanSensorBase):
    """Routing role: Default connection / Backup connection N."""
    _attr_icon = "mdi:sort-numeric-ascending"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Also the WAN Connected ``role_label`` attribute; opt in.
    _attr_entity_registry_enabled_default = False

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_{self._wan_id}_role"

    @property
    def name(self) -> str:
        return "Role"

    @property
    def native_value(self) -> str | None:
        wan = self._wan
        if not wan:
            return None
        return wan.get("role_label")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        wan = self._wan
        if not wan:
            return None
        return {
            "priority": wan.get("priority"),
            "role_index": wan.get("role_index"),
            "defaultgw": wan.get("defaultgw"),
        }


class KeeneticWanInterfaceSensor(_WanSensorBase):
    """Underlying interface id (e.g. GigabitEthernet1/Vlan35)."""
    _attr_icon = "mdi:ethernet-cable"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Static, also the WAN Connected ``underlying`` attribute; opt in.
    _attr_entity_registry_enabled_default = False

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_{self._wan_id}_interface"

    @property
    def name(self) -> str:
        return "Interface"

    @property
    def native_value(self) -> str | None:
        wan = self._wan
        if not wan:
            return None
        # The physical/logical carrier (PPPoE `via`) is the most useful
        # value here; fall back to the WAN's own id for Ethernet WANs
        # and WireGuard tunnels that have no underlying interface.
        return wan.get("underlying") or wan.get("id")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        wan = self._wan
        if not wan:
            return None
        return {
            "wan_id": wan.get("id"),
            "interface_name": wan.get("interface_name"),
            "type": wan.get("type"),
            "remote": wan.get("remote"),
            "mac": wan.get("mac"),
        }


class KeeneticWanPublicIpSensor(_WanSensorBase):
    """Public IP address of the WAN (PPPoE / DHCP / static)."""
    _attr_icon = _ICON_IP_NETWORK

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_{self._wan_id}_public_ip"

    @property
    def name(self) -> str:
        return "Public IP"

    @property
    def native_value(self) -> str | None:
        wan = self._wan
        if not wan:
            return None
        return wan.get("ip")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        wan = self._wan
        if not wan:
            return None
        return {
            "mask": wan.get("mask"),
            "remote": wan.get("remote"),
            "global": wan.get("global"),
        }


class KeeneticWanUptimeSensor(UptimeMixin, _WanSensorBase):
    """Session uptime for the WAN, published hourly (see ``UptimeMixin``)."""
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # native_value derives from wan["uptime"]; the WanEntity base ignores it
    # for write-suppression, so opt out of dedup here.
    _FINGERPRINT_IGNORE = frozenset()

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_{self._wan_id}_uptime"

    @property
    def name(self) -> str:
        return "Uptime"

    @property
    def native_value(self) -> int | None:
        wan = self._wan
        if not wan:
            return self._publish_uptime(None)
        return self._publish_uptime(coerce_seconds(wan.get("uptime"), default=None))


class _WanLinkActiveMixin(LinkActiveMixin):
    """Gate a WAN traffic sensor on the uplink's physical link."""

    def _link_active(self) -> bool:
        wan = self._wan
        if not wan or wan.get("link_state") != LINK_STATE_UP:
            return False
        # ``link_state`` is the configured state; a standby uplink whose modem
        # or cable is absent reports state "up" with link "down".
        raw = wan.get("raw")
        link = raw.get("link") if isinstance(raw, dict) else None
        return link is None or str(link).lower() == LINK_STATE_UP


class _WanBytesBase(
    _WanLinkActiveMixin, SourceFreshnessMixin, CounterDeadbandMixin, _WanSensorBase
):
    """Shared RX/TX byte counter base."""
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # A counter reset (router reboot) is a move far larger than the band, so
    # TOTAL_INCREASING still sees it at once.
    _COUNTER_DEADBAND = COUNTER_DEADBAND_BYTES
    _freshness_key = "interface_stats_fresh"
    _field = "rx_bytes"
    # native_value reads rx_bytes/tx_bytes — fields the WanEntity base
    # ignores for change-detection. Opt out so counters actually update.
    _FINGERPRINT_IGNORE = frozenset()

    @property
    def native_value(self) -> int | None:
        wan = self._wan
        if not wan:
            return None
        # Reject negative/non-finite counters so a malformed router stat does
        # not poison the TOTAL_INCREASING long-term statistics.
        count = coerce_byte_count(wan.get(self._field))
        held = self._publish_counter(count)
        return None if held is None else int(held)


class KeeneticWanRxBytesSensor(_WanBytesBase):
    _attr_icon = "mdi:download"
    _field = "rx_bytes"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_{self._wan_id}_rx_bytes"

    @property
    def name(self) -> str:
        return "RX Bytes"


class KeeneticWanTxBytesSensor(_WanBytesBase):
    _attr_icon = "mdi:upload"
    _field = "tx_bytes"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_{self._wan_id}_tx_bytes"

    @property
    def name(self) -> str:
        return "TX Bytes"


class _WanThroughputBase(
    _WanLinkActiveMixin, SourceFreshnessMixin, ThroughputDeadbandMixin, _WanSensorBase
):
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_state_class = SensorStateClass.MEASUREMENT
    # See _WanBytesBase: throughput fields are also in the base ignore set.
    _FINGERPRINT_IGNORE = frozenset()
    _attr_native_unit_of_measurement = UnitOfDataRate.BITS_PER_SECOND
    _attr_suggested_display_precision = 0
    _field = "rx_throughput"
    _freshness_key = "interface_stats_fresh"

    @property
    def native_value(self) -> float | None:
        wan = self._wan
        if not wan:
            return None
        return self._publish_throughput(wan.get(self._field))  # bytes/s → bit/s

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        wan = self._wan
        if not wan:
            return None
        # Only the stable source-interface name. The byte counters have their
        # own sensors, rx/tx speed duplicate this entity's own state, and the
        # router stats timestamp moves every poll — as attributes they made HA
        # write a recorder row on every tick even for an idle link.
        return {"stats_interface": wan.get("stats_interface")}


class KeeneticWanRxThroughputSensor(_WanThroughputBase):
    _attr_icon = "mdi:download-network"
    _field = "rx_throughput"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_{self._wan_id}_rx_throughput"

    @property
    def name(self) -> str:
        return "RX Throughput"


class KeeneticWanTxThroughputSensor(_WanThroughputBase):
    _attr_icon = "mdi:upload-network"
    _field = "tx_throughput"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_{self._wan_id}_tx_throughput"

    @property
    def name(self) -> str:
        return "TX Throughput"


class KeeneticWanFailoverCountSensor(ControllerEntity, SensorEntity, RestoreEntity):
    """How many times the router changed its default gateway.

    Long-term statistics answer "how flaky was my ISP this month?" — a
    question no amount of raw state history answers well. This costs a
    recorder row per failover and nothing at all in between.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "wan_failover_count"
    _attr_icon = "mdi:swap-horizontal"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._count = 0

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_failover_count"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in (None, "unknown", "unavailable"):
            try:
                self._count = int(float(last.state))
            except (TypeError, ValueError):
                self._count = 0

    @property
    def native_value(self) -> int:
        return self._count

    def _handle_coordinator_update(self) -> None:
        # The coordinator raises this key only on the tick where the active
        # WAN actually changed. The listener also fires on the first FAILED
        # tick after a success, though, and that call still sees the previous
        # payload — so without the success check a failover followed by one
        # hiccup (a very ordinary sequence) counted twice.
        if self.coordinator.last_update_success and (
            self.coordinator.data or {}
        ).get("wan_failover"):
            self._count += 1
        super()._handle_coordinator_update()


# HA's duration unit strings, in seconds.
_SECONDS_PER_UNIT = {"ms": 0.001, "s": 1, "min": 60, "h": 3600, "d": 86400, "w": 604800}


class _DowntimeClockMixin:
    """Accrue seconds between two observations when the earlier one was down.

    ``down`` is True/False for a fresh observation and None when the state is
    unknown: the clock then stops without billing. That covers a router HA
    cannot read (it says nothing about the providers behind it) and the first
    minutes after the router boots, while it brings its own uplinks up.
    """

    _seconds: float = 0.0
    _down_since: float | None = None
    _published: int = 0
    # PPPoE and LTE uplinks can take a few minutes to come up after a boot.
    _BOOT_GRACE = 300
    # While an outage runs, publish the total every five minutes (the
    # recorder's statistics period) instead of every poll; the exact total
    # is published as soon as it ends.
    _PUBLISH_STEP = 300

    @staticmethod
    def _now() -> float:
        # Monotonic: a wall-clock step (NTP sync, DST) must not invent or
        # erase an outage.
        return monotonic()

    def _accrue(self, down: bool | None) -> None:
        if down is None:
            self._down_since = None
            return
        now = self._now()
        if self._down_since is not None:
            self._seconds += max(0.0, now - self._down_since)
        self._down_since = now if down else None

    def _settle_published(self) -> bool:
        """Move the published total forward when due; True when it moved."""
        seconds = int(self._seconds)
        if seconds == self._published:
            return False
        if self._down_since is None or seconds - self._published >= self._PUBLISH_STEP:
            self._published = seconds
            return True
        return False

    def _router_settled(self) -> bool:
        """Return True when the router answered and is past its boot."""
        if not self.coordinator.last_update_success:
            return False
        system = (self.coordinator.data or {}).get("system") or {}
        uptime = coerce_seconds(system.get("uptime"), default=None)
        return uptime is None or uptime >= self._BOOT_GRACE

    async def _async_restore_seconds(self) -> None:
        last = await self.async_get_last_state()
        if last is not None and last.state not in (None, "unknown", "unavailable"):
            # The state is stored in the display unit (hours by default, or
            # whatever the user picked), not in native seconds.
            attributes = getattr(last, "attributes", None) or {}
            unit = attributes.get("unit_of_measurement") or UnitOfTime.SECONDS
            try:
                self._seconds = float(last.state) * _SECONDS_PER_UNIT.get(str(unit), 1)
            except (TypeError, ValueError):
                self._seconds = 0.0
        self._published = int(self._seconds)


class KeeneticWanDowntimeSensor(
    _DowntimeClockMixin, ControllerEntity, SensorEntity, RestoreEntity
):
    """Cumulative seconds the router was up but had no internet on any WAN.

    A WAN counts as working when the router reports internet access on it
    (ping check where configured), not merely while it holds the default
    route: a failing ping check keeps the route. Writes a state row only while
    an outage is running; long-term statistics give the total for any week,
    month or year.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "wan_downtime"
    _attr_icon = "mdi:web-off"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 2
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)
        self._seconds = 0.0
        self._down_since = None

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_downtime"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_restore_seconds()

    @property
    def native_value(self) -> int:
        return self._published

    def _internet_down(self) -> bool:
        data = self.coordinator.data or {}
        wans = [w for w in data.get("wan_interfaces") or [] if isinstance(w, dict)]
        if wans:
            return not any(w.get("internet_access") for w in wans)
        return not data.get("active_wan")

    def _handle_coordinator_update(self) -> None:
        # A failed tick leaves the PREVIOUS payload in place; we genuinely do
        # not know the WAN state, so stop the clock instead of reading it.
        self._accrue(self._internet_down() if self._router_settled() else None)
        self._settle_published()
        super()._handle_coordinator_update()


class KeeneticWanLinkDowntimeSensor(
    _DowntimeClockMixin, _WanSensorBase, RestoreEntity
):
    """Cumulative seconds this provider was down: link lost, or no internet.

    "No internet" is the router's ping check where one is configured, else
    the link-up-with-an-address heuristic. A WAN switched off on purpose is
    not an outage, and nothing is counted while the router itself cannot be
    read. VPN tunnels are WANs too but not providers, so theirs start
    disabled.
    """

    _attr_icon = "mdi:web-off"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 2
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _TUNNEL_TYPES = frozenset({"wireguard", "openvpn"})

    def __init__(
        self, coordinator: KeeneticCoordinator, entry: ConfigEntry, wan_id: str
    ) -> None:
        _WanSensorBase.__init__(self, coordinator, entry, wan_id)
        self._seconds = 0.0
        self._down_since = None
        wan_type = str((self._wan or {}).get("type") or "").lower()
        if wan_type in self._TUNNEL_TYPES:
            self._attr_entity_registry_enabled_default = False

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_{self._wan_id}_downtime"

    @property
    def name(self) -> str:
        return "Downtime"

    @property
    def available(self) -> bool:
        # The counter is a history, not a reading of this tick: keep it
        # visible while the WAN is briefly missing from the payload.
        return bool(getattr(self.coordinator, "last_update_success", True))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_restore_seconds()

    @property
    def native_value(self) -> int:
        return self._published

    @staticmethod
    def _provider_down(wan: dict[str, Any]) -> bool:
        if not wan.get("enabled"):
            return False
        raw = wan.get("raw") if isinstance(wan.get("raw"), dict) else {}
        link_up = wan.get("link_state") == LINK_STATE_UP and str(
            raw.get("link") or LINK_STATE_UP
        ).lower() == LINK_STATE_UP
        return not link_up or wan.get("internet_access") is False

    def _handle_coordinator_update(self) -> None:
        wan = self._wan if self._router_settled() else None
        self._accrue(None if wan is None else self._provider_down(wan))
        if self._settle_published():
            self.async_write_ha_state()
            return
        super()._handle_coordinator_update()
