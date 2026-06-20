"""Mesh node sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime, EntityCategory

from ..const import FIELD_CONNECTED, LINK_STATE_DOWN, LINK_STATE_UP
from ..coordinator import KeeneticCoordinator
from ..entity import ControllerEntity, MeshEntity
from ..utils import coerce_float, coerce_int, coerce_seconds, parse_memory_fraction

_ICON_ETHERNET = "mdi:ethernet"


class KeeneticMeshSystemStateSensor(ControllerEntity, SensorEntity):
    """Mesh system overall state sensor."""
    _attr_has_entity_name = True
    _attr_name = "Mesh System State"
    _attr_icon = "mdi:access-point-network"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_mesh_system_state"

    @property
    def native_value(self) -> str:
        """Return the overall mesh system state."""
        mesh_nodes = self.coordinator.data.get("mesh_nodes", [])

        if not mesh_nodes:
            return "no_nodes"

        valid_nodes = [node for node in mesh_nodes if isinstance(node, dict)]
        if not valid_nodes:
            return "no_nodes"

        connected = sum(1 for node in valid_nodes if node.get(FIELD_CONNECTED, False))
        total = len(valid_nodes)

        if connected == 0:
            return "down"
        elif connected < total:
            return "problem"
        else:
            return "ok"

    @property
    def icon(self) -> str:
        """Return icon based on current state."""
        state = self.native_value
        if state == "ok":
            return "mdi:check-network"
        elif state == "problem":
            return "mdi:close-network"
        elif state == LINK_STATE_DOWN:
            return "mdi:network-off"
        else:
            return "mdi:help-network"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return detailed mesh system information."""
        mesh_nodes = self.coordinator.data.get("mesh_nodes", [])

        valid_nodes = [node for node in mesh_nodes if isinstance(node, dict)]

        if not valid_nodes:
            return {
                "total_nodes": 0,
                "connected_nodes": 0,
                "disconnected_nodes": 0,
                "nodes": [],
            }

        connected = 0
        disconnected = 0
        nodes_detail = []

        for node in valid_nodes:
            is_connected = node.get(FIELD_CONNECTED, False)
            if is_connected:
                connected += 1
            else:
                disconnected += 1

            nodes_detail.append({
                "name": node.get("name") or node.get("mac", "Unknown"),
                "mac": node.get("mac"),
                "ip": node.get("ip"),
                "model": node.get("model"),
                "mode": node.get("mode"),
                "connected": is_connected,
                "firmware": node.get("firmware"),
                "associations": node.get("associations", 0),
            })

        total = len(valid_nodes)
        health_percent = round((connected / total) * 100, 1) if total > 0 else 0

        return {
            "total_nodes": total,
            "connected_nodes": connected,
            "disconnected_nodes": disconnected,
            "health_percent": health_percent,
            "state": self.native_value,
            "nodes": nodes_detail,
        }


class KeeneticMeshUptimeSensor(MeshEntity, SensorEntity):
    """Mesh node uptime sensor."""
    _attr_has_entity_name = True
    _attr_translation_key = "uptime"
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 0
    # native_value reads node["uptime"] — ignored by MeshEntity base by default.
    _FINGERPRINT_IGNORE = frozenset()

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry, node_cid: str) -> None:
        MeshEntity.__init__(self, coordinator, entry.entry_id, entry.title, node_cid)

    @property
    def unique_id(self) -> str:
        return self._mesh_unique_id("uptime_v2")

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfTime.SECONDS

    @property
    def native_value(self) -> int:
        node = self._node
        if not node:
            return 0
        return coerce_seconds(node.get("uptime"), default=0) or 0


class KeeneticMeshClientsSensor(MeshEntity, SensorEntity):
    """Mesh node active clients sensor."""
    _attr_has_entity_name = True
    _attr_translation_key = "mesh_clients"
    _attr_icon = "mdi:account-group"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry, node_cid: str) -> None:
        MeshEntity.__init__(self, coordinator, entry.entry_id, entry.title, node_cid)

    @property
    def unique_id(self) -> str:
        return self._mesh_unique_id("clients_v2")

    @property
    def native_value(self) -> int:
        node = self._node
        if node:
            associations = node.get("associations")
            if isinstance(associations, (list, dict)):
                # Some firmwares list the stations instead of a count.
                return len(associations)
            # A client count cannot be negative or boolean; reject bool drift
            # and clamp negatives to 0 so the count sensor never publishes -1.
            if associations is not None and not isinstance(associations, bool):
                return max(0, coerce_int(associations, 0))
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        node = self._node
        if not node:
            return None

        return {
            "cid": self._node_cid,
            "mac": node.get("mac"),
            "ip": node.get("ip"),
            "model": node.get("model"),
            "mode": node.get("mode"),
        }


class KeeneticMeshLocalIpSensor(MeshEntity, SensorEntity):
    """Sensor for local IP address of a mesh node."""
    _attr_has_entity_name = True
    _attr_name = "IP"
    _attr_icon = "mdi:ip-network"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        node_cid: str,
        ip_address: str,
    ) -> None:
        MeshEntity.__init__(self, coordinator, entry.entry_id, entry.title, node_cid)
        self._ip_address = ip_address

    @property
    def unique_id(self) -> str:
        return self._mesh_unique_id("local_ip_v2")

    @property
    def native_value(self) -> str | None:
        node = self._node
        if node and node.get("ip"):
            return node.get("ip")
        return self._ip_address


class KeeneticMeshCpuLoadSensor(MeshEntity, SensorEntity):
    """Mesh node CPU load sensor."""
    _attr_has_entity_name = True
    _attr_translation_key = "cpu_load"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cpu-64-bit"
    # native_value reads node["cpuload"] — ignored by MeshEntity base.
    _FINGERPRINT_IGNORE = frozenset()

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry, node_cid: str) -> None:
        MeshEntity.__init__(self, coordinator, entry.entry_id, entry.title, node_cid)

    @property
    def unique_id(self) -> str:
        return self._mesh_unique_id("cpu_load_v2")

    @property
    def native_value(self) -> float | None:
        node = self._node
        if node:
            cpuload = node.get("cpuload")
            if cpuload is not None:
                value = coerce_float(cpuload)
                # A percentage outside 0-100 is firmware garbage, not load.
                if value is not None and 0 <= value <= 100:
                    return value
                return None
        return None


class KeeneticMeshMemorySensor(MeshEntity, SensorEntity):
    """Mesh node memory usage percentage sensor."""
    _attr_has_entity_name = True
    _attr_translation_key = "memory_usage"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:memory"
    # Defensive opt-out: native_value derives from node["memory"]; the MeshEntity
    # base ignore set predates this sensor and may add memory fields later.
    _FINGERPRINT_IGNORE = frozenset()

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry, node_cid: str) -> None:
        MeshEntity.__init__(self, coordinator, entry.entry_id, entry.title, node_cid)

    @property
    def unique_id(self) -> str:
        return self._mesh_unique_id("memory_v2")

    @property
    def native_value(self) -> float | None:
        node = self._node
        if node:
            return parse_memory_fraction(node.get("memory"))
        return None


class KeeneticMeshFirmwareVersionSensor(MeshEntity, SensorEntity):
    """Current firmware version sensor for a mesh node."""
    _attr_has_entity_name = True
    _attr_translation_key = "mesh_firmware_version"
    _attr_icon = "mdi:package-variant-closed"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry, node_cid: str) -> None:
        MeshEntity.__init__(self, coordinator, entry.entry_id, entry.title, node_cid)

    @property
    def unique_id(self) -> str:
        return self._mesh_unique_id("firmware_version_v2")

    @property
    def native_value(self) -> str | None:
        node = self._node
        if node:
            return node.get("firmware")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        node = self._node
        if not node:
            return None
        attrs: dict[str, Any] = {}
        if node.get("firmware_available"):
            attrs["firmware_available"] = node["firmware_available"]
        if node.get("hw_id"):
            attrs["hardware_id"] = node["hw_id"]
        if node.get("model"):
            attrs["model"] = node["model"]
        return attrs if attrs else None
    
class KeeneticMeshPortSensor(MeshEntity, SensorEntity):
    """Individual mesh node port sensor."""
    _attr_has_entity_name = True
    _attr_icon = _ICON_ETHERNET
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        node_cid: str,
        port_label: str,
    ) -> None:
        """Initialize individual port sensor."""
        MeshEntity.__init__(self, coordinator, entry.entry_id, entry.title, node_cid)
        self._port_label = port_label

    @property
    def name(self) -> str:
        """Return name for the sensor."""
        return f"Port {self._port_label}"

    @property
    def unique_id(self) -> str:
        """Return unique ID for the sensor."""
        return self._mesh_unique_id(f"port_{self._port_label}_v2")

    @property
    def native_value(self) -> str | None:
        """Return port state."""
        node = self._node
        if not node:
            return "unknown"

        ports = node.get("port", [])
        for port in ports:
            if not isinstance(port, dict):
                continue
            if str(port.get("label")) == self._port_label:
                return port.get("link", "unknown")

        # Port no longer reported: go unavailable (see ``available``) instead
        # of publishing a phantom "not_found" state into history.
        return None

    @property
    def available(self) -> bool:
        """Become unavailable when the node no longer reports this port."""
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
        node = self._node
        if not node:
            return None

        ports = node.get("port", [])
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
