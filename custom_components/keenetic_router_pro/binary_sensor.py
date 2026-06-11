"""Binary sensors for Keenetic Router Pro (Mesh AP status)."""
from __future__ import annotations
from typing import Any
from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import FIELD_CONNECTED, LINK_STATE_UP
from .coordinator import KeeneticCoordinator
from .entity import MeshEntity, ControllerEntity, WanEntity, CryptoMapEntity
from .entity_setup import DynamicEntityTracker, register_dynamic_entities
from .utils import iter_new_items


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Keenetic Router Pro binary sensors from a config entry."""
    runtime = entry.runtime_data
    coordinator: KeeneticCoordinator = runtime.coordinator
    entities: list[BinarySensorEntity] = []

    entities.append(KeeneticControllerUpdateSensor(coordinator, entry))

    tracker = DynamicEntityTracker()

    def _build_dynamic_binary_sensors() -> list[BinarySensorEntity]:
        dynamic_entities: list[BinarySensorEntity] = []
        _add_mesh_binary_sensors(
            dynamic_entities,
            coordinator,
            entry,
            tracker.mesh_nodes,
        )
        for wan in iter_new_items(coordinator, "wan_interfaces", tracker.wan_ids):
            wan_id = wan["id"]
            dynamic_entities.append(
                KeeneticWanConnectedSensor(coordinator, entry, wan_id)
            )
            dynamic_entities.append(
                KeeneticWanEnabledSensor(coordinator, entry, wan_id)
            )
        crypto_maps = coordinator.data.get("crypto_maps") or {}
        if not isinstance(crypto_maps, dict):
            crypto_maps = {}
        for cmap_name in crypto_maps.keys():
            if not tracker.mark_crypto_map(cmap_name):
                continue
            dynamic_entities.append(
                KeeneticCryptoMapConnectedSensor(
                    coordinator, entry, cmap_name
                )
            )
        return dynamic_entities

    entities.extend(_build_dynamic_binary_sensors())
    if entities:
        async_add_entities(entities)

    register_dynamic_entities(
        entry,
        coordinator,
        async_add_entities,
        _build_dynamic_binary_sensors,
        add_initial=False,
    )


def _add_mesh_binary_sensors(
    entities: list[BinarySensorEntity],
    coordinator: KeeneticCoordinator,
    entry: ConfigEntry,
    known_mesh_ids: set[str],
) -> None:
    """Append binary sensors for newly discovered mesh nodes."""
    for node in iter_new_items(coordinator, "mesh_nodes", known_mesh_ids, ("cid", "id")):
        node_id = str(node.get("cid") or node.get("id"))
        entities.append(KeeneticMeshNodeSensor(coordinator, entry, node_id))
        entities.append(KeeneticMeshUpdateSensor(coordinator, entry, node_id))


class KeeneticWanConnectedSensor(WanEntity, BinarySensorEntity):
    """Per-WAN "Connected" sensor — true when the uplink is actually usable.

    This is the signal behind the red "NO INTERNET ACCESS (PING CHECK)"
    badge in the Keenetic web UI and the condition that drives failover
    to a backup WAN. "Usable" here means: link up, global role, has a
    routable public IP, and the router isn't reporting a session
    failure. See api._derive_internet_access for the full logic.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        wan_id: str,
    ) -> None:
        WanEntity.__init__(self, coordinator, entry.entry_id, entry.title, wan_id)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_{self._wan_id}_connected"

    @property
    def name(self) -> str:
        return "Connected"

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        wan = self._wan
        if wan is None:
            return False
        # None means "pending / unknown" — surface as unavailable rather
        # than silently flipping to False and triggering bogus outage
        # automations.
        return wan.get("internet_access") is not None

    @property
    def is_on(self) -> bool:
        wan = self._wan
        if wan is None:
            return False
        return bool(wan.get("internet_access"))

    @property
    def icon(self) -> str:
        wan = self._wan
        if wan and wan.get("internet_access"):
            return "mdi:web-check"
        if wan and wan.get("link_state") == LINK_STATE_UP:
            return "mdi:web-remove"
        return "mdi:web-off"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        wan = self._wan
        if not wan:
            return None
        attrs: dict[str, Any] = {
            "interface": wan.get("id"),
            "description": wan.get("description"),
            "type": wan.get("type"),
            "link_state": wan.get("link_state"),
            "defaultgw": wan.get("defaultgw"),
            "priority": wan.get("priority"),
            "role_label": wan.get("role_label"),
            "public_ip": wan.get("ip"),
            "underlying": wan.get("underlying"),
            # Where the current reachability value came from — either
            # the router's own ping-check or our link+IP heuristic.
            "source": wan.get("internet_access_source"),
        }

        # Expose authoritative ping-check details when the router is
        # actually running a profile bound to this WAN. These are the
        # attributes the feature request asked for:
        #   - check target(s)
        #   - failure reason / counters
        #   - last check time
        pc = wan.get("ping_check")
        if pc:
            targets = []
            hosts = pc.get("check_hosts") or []
            if hosts:
                targets.extend(hosts)
            addrs = pc.get("check_addresses") or []
            if addrs:
                targets.extend(addrs)
            if targets:
                attrs["check_targets"] = targets
            if pc.get("check_port") is not None:
                attrs["check_port"] = pc.get("check_port")
            if pc.get("check_mode"):
                attrs["check_mode"] = pc.get("check_mode")
            if pc.get("profile"):
                attrs["check_profile"] = pc.get("profile")
            if pc.get("success_count") is not None:
                attrs["success_count"] = pc.get("success_count")
            if pc.get("fail_count") is not None:
                attrs["fail_count"] = pc.get("fail_count")
            if pc.get("max_fails") is not None:
                attrs["max_fails"] = pc.get("max_fails")
            if pc.get("update_interval") is not None:
                attrs["update_interval"] = pc.get("update_interval")
            if pc.get("status"):
                attrs["ping_check_status"] = pc.get("status")
            # Human-readable failure reason: Keenetic doesn't expose a
            # free-form reason string, so we synthesise one from the
            # counters when the check is failing.
            if pc.get("passing") is False:
                fc = pc.get("fail_count") or 0
                mf = pc.get("max_fails")
                if mf:
                    attrs["failure_reason"] = (
                        f"ping check failing ({fc}/{mf} consecutive failures"
                        f" to {', '.join(targets) if targets else 'check targets'})"
                    )
                else:
                    attrs["failure_reason"] = (
                        f"ping check failing to "
                        f"{', '.join(targets) if targets else 'check targets'}"
                    )
            ignored = pc.get("all_profiles")
            if ignored and len(ignored) > 1:
                # Surface all observed profiles for debugging when more
                # than one is touching this interface.
                attrs["all_ping_check_profiles"] = ignored

        layers = wan.get("summary_layers") or {}
        if layers:
            attrs["summary_layers"] = layers
        last_update = getattr(self.coordinator, "last_update_success_time", None)
        if last_update is not None:
            attrs["last_check"] = last_update.isoformat()
        return attrs


class KeeneticWanEnabledSensor(WanEntity, BinarySensorEntity):
    """Per-WAN "Enabled" sensor — matches the UI toggle state.

    True when summary.layer.conf is "running" (interface is configured
    up), False when it's "disabled" (the user has toggled the uplink
    off in the web UI).
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:toggle-switch-variant"

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        wan_id: str,
    ) -> None:
        WanEntity.__init__(self, coordinator, entry.entry_id, entry.title, wan_id)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_wan_{self._wan_id}_enabled"

    @property
    def name(self) -> str:
        return "Enabled"

    @property
    def is_on(self) -> bool:
        wan = self._wan
        if wan is None:
            return False
        return bool(wan.get("enabled"))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        wan = self._wan
        if not wan:
            return None
        layers = wan.get("summary_layers") or {}
        return {
            "conf": layers.get("conf"),
            "link": layers.get("link"),
            "ipv4": layers.get("ipv4"),
            "ctrl": layers.get("ctrl"),
        }


class KeeneticMeshNodeSensor(MeshEntity, BinarySensorEntity):
    """Binary sensor for mesh/extender node connectivity status."""
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        node_cid: str,
    ) -> None:
        MeshEntity.__init__(self, coordinator, entry.entry_id, entry.title, node_cid)

    @property
    def unique_id(self) -> str:
        return self._mesh_unique_id("connect_v2")

    @property
    def name(self) -> str:
        return "Connected"

    @property
    def is_on(self) -> bool:
        node = self._node
        if node:
            return node.get(FIELD_CONNECTED, False)
        return False

    @property
    def icon(self) -> str:
        node = self._node
        if node:
            mode = node.get("mode", "")
            if mode == "extender":
                return "mdi:access-point-network"
            elif mode == "repeater":
                return "mdi:wifi-sync"
        return "mdi:access-point"

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
            "uptime": node.get("uptime"),
            "cpuload": node.get("cpuload"),
            "memory": node.get("memory"),
            "firmware": node.get("firmware"),
            "firmware_available": node.get("firmware_available"),
            "associations": node.get("associations"),
            "rci_errors": node.get("rci_errors"),
        }
    

class KeeneticControllerUpdateSensor(ControllerEntity, BinarySensorEntity):
    """Binary sensor for main controller firmware update availability."""
    
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.UPDATE
    _attr_icon = "mdi:package-up"

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
    ) -> None:
        ControllerEntity.__init__(self, coordinator, entry.entry_id, entry.title)

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_controller_update"

    @property
    def name(self) -> str:
        return "Update Available"

    @property
    def is_on(self) -> bool:
        """Return True if firmware update is available for controller."""
        system = self.coordinator.data.get("system", {}) or {}
        
        current = system.get("title") or system.get("release")
        available = system.get("fw-available") or system.get("release-available")

        if not available or not current:
            return False

        if available == current:
            return False

        channel = system.get("fw-update-sandbox") or system.get("sandbox", "stable")
        if channel != "stable":
            return False

        # Honor the router's explicit verdict: stale release metadata can
        # linger while fw-update-available says there is nothing to install.
        if system.get("fw-update-available") is False:
            return False

        return True

    @property
    def icon(self) -> str:
        if self.is_on:
            return "mdi:update"
        return "mdi:check-circle"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        system = self.coordinator.data.get("system", {}) or {}
        
        current = system.get("title") or system.get("release")
        available = system.get("fw-available") or system.get("release-available")
        
        attrs = {
            "current_version": current,
            "update_channel": system.get("fw-update-sandbox") or system.get("sandbox"),
        }
        
        if available:
            attrs["available_version"] = available
        
        return attrs
    
class KeeneticMeshUpdateSensor(MeshEntity, BinarySensorEntity):
    """Binary sensor for mesh/extender firmware update availability."""
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.UPDATE

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        node_cid: str,
    ) -> None:
        MeshEntity.__init__(self, coordinator, entry.entry_id, entry.title, node_cid)

    @property
    def unique_id(self) -> str:
        return self._mesh_unique_id("update_v2")

    @property
    def name(self) -> str:
        return "Update Available"

    @property
    def is_on(self) -> bool:
        node = self._node
        if node:
            current = node.get("firmware")
            available = node.get("firmware_available")
            if current and available and current != available:
                return True
        return False

    @property
    def icon(self) -> str:
        if self.is_on:
            return "mdi:update"
        return "mdi:check-circle"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        node = self._node
        if not node:
            return None
        return {
            "cid": self._node_cid,
            "model": node.get("model"),
            "current_version": node.get("firmware"),
            "available_version": node.get("firmware_available"),
        }

class KeeneticCryptoMapConnectedSensor(CryptoMapEntity, BinarySensorEntity):
    """Per-tunnel "Connected" sensor for site-to-site IPsec.

    True when the tunnel has a fully negotiated phase-2 SA. The
    underlying field is ``status.state`` from ``show/crypto/map``;
    the only value we treat as "up" is ``PHASE2_ESTABLISHED``. Every
    other state (``UNDEFINED``, ``CONNECTING``, ``PHASE1_ONLY``,
    etc.) or a missing state is treated as "not connected".
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        cmap_name: str,
    ) -> None:
        CryptoMapEntity.__init__(
            self, coordinator, entry.entry_id, entry.title, cmap_name
        )

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_cmap_{self._cmap_name}_connected"

    @property
    def name(self) -> str:
        return "Connected"

    @property
    def is_on(self) -> bool:
        cmap = self._cmap
        if cmap is None:
            return False
        return bool(cmap.get(FIELD_CONNECTED))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        cmap = self._cmap
        if cmap is None:
            return None
        return {
            "state": cmap.get("state"),
            "ike_state": cmap.get("ike_state"),
            "via": cmap.get("via"),
            "remote_peer": cmap.get("remote_peer"),
            "local_endpoint": cmap.get("local_endpoint"),
            "remote_endpoint": cmap.get("remote_endpoint"),
            "rx_bytes": cmap.get("rx_bytes"),
            "tx_bytes": cmap.get("tx_bytes"),
        }
