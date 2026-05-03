"""Base entity classes for Keenetic Router Pro."""
from __future__ import annotations

from typing import Any
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN
from .coordinator import KeeneticCoordinator, KeeneticPingCoordinator
from .utils import (
    get_main_device_info,
    get_mesh_device_info,
    get_client_device_info,
    get_wan_device_info,
    get_crypto_map_device_info,
)


class ControllerEntity(CoordinatorEntity):
    """Base class for main-router entities."""

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry_id: str,
        title: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._title = title

    @property
    def _version_data(self) -> dict[str, Any]:
        return self.coordinator.data.get("system", {}) or {}

    @property
    def _firmware_version(self) -> str | None:
        version = self.coordinator.data.get("system", {}) or {}

        if version.get("title"):
            return str(version["title"])
        if version.get("release"):
            return str(version["release"])

        ndw4 = version.get("ndw4", {})
        if isinstance(ndw4, dict) and ndw4.get("version"):
            return str(ndw4["version"])

        return None

    @property
    def _model_name(self) -> str | None:
        version = self.coordinator.data.get("system", {}) or {}
        
        if version.get("model"):
            return str(version["model"])
        if version.get("description"):
            return str(version["description"])
        if version.get("device"):
            return str(version["device"])
        if version.get("hw_id"):
            return str(version["hw_id"])
        
        return None
    
    @property
    def device_info(self) -> DeviceInfo:
        ndns_info = self.coordinator.data.get("ndns", {})
        ndns_domain = None
        
        if ndns_info:
            name = ndns_info.get("name")
            domain = ndns_info.get("domain")
            if name and domain:
                ndns_domain = f"{name}.{domain}"
        
        return get_main_device_info(
            self._title, 
            self._entry_id,
            self._firmware_version,
            self._model_name,
            host=getattr(self.coordinator.client, "_host", None),
            ssl=bool(getattr(self.coordinator.client, "_ssl", False)),
            ndns_domain=ndns_domain,
        )


class MeshEntity(CoordinatorEntity):
    """Base class for Mesh node entities."""

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry_id: str,
        title: str,
        node_cid: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._title = title
        self._node_cid = node_cid

    @property
    def _node(self) -> dict[str, Any] | None:
        nodes = self.coordinator.data.get("mesh_nodes", [])
        for node in nodes:
            if (node.get("cid") or node.get("id")) == self._node_cid:
                return node
        return None
    
    @property
    def device_info(self) -> DeviceInfo:
        node = self._node
        node_ip = node.get("ip") if node else None
        
        return get_mesh_device_info(
            self._title,
            self._entry_id,
            self._node,
            self._node_cid,
            host=node_ip,
            ssl=bool(getattr(self.coordinator.client, "_ssl", False)),
            fqdn=node.get("fqdn")
        )
    
class WanEntity(CoordinatorEntity):
    """Base class for per-WAN-interface entities.

    Each WAN is exposed in HA as its own sub-device under the main
    router, so all of its sensors (status, IP, uptime, throughput, ...)
    are grouped together in the UI.
    """

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry_id: str,
        title: str,
        wan_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._title = title
        self._wan_id = wan_id

    @property
    def _wan(self) -> dict[str, Any] | None:
        for w in self.coordinator.data.get("wan_interfaces", []) or []:
            if w.get("id") == self._wan_id:
                return w
        return None

    @property
    def device_info(self) -> DeviceInfo:
        wan = self._wan or {}
        return get_wan_device_info(
            title=self._title,
            entry_id=self._entry_id,
            wan_id=self._wan_id,
            description=wan.get("description"),
            iface_type=wan.get("type"),
            role_label=wan.get("role_label"),
        )


class CryptoMapEntity(CoordinatorEntity):
    """Base class for per-`crypto map` site-to-site IPsec entities.

    Each configured crypto map is exposed in HA as its own sub-device
    under the main router, mirroring the per-WAN model.
    """

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry_id: str,
        title: str,
        cmap_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._title = title
        self._cmap_name = cmap_name

    @property
    def _cmap(self) -> dict[str, Any] | None:
        """Return the current dict for this crypto map, or None if it
        has been removed from the router config since we were created."""
        cmaps = self.coordinator.data.get("crypto_maps") or {}
        if not isinstance(cmaps, dict):
            return None
        entry = cmaps.get(self._cmap_name)
        return entry if isinstance(entry, dict) else None

    @property
    def available(self) -> bool:
        # Mirror CoordinatorEntity.available but additionally require
        # that our tunnel is still present in the router config. If
        # the user deletes the crypto map, our entities become
        # unavailable rather than stale.
        return super().available and self._cmap is not None

    @property
    def device_info(self) -> DeviceInfo:
        cmap = self._cmap or {}
        return get_crypto_map_device_info(
            title=self._title,
            entry_id=self._entry_id,
            cmap_name=self._cmap_name,
            remote_peer=cmap.get("remote_peer"),
        )


class ClientEntity(CoordinatorEntity):
    """Base class for tracked-client entities exposed as their own HA device."""

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry_id: str,
        title: str,
        mac: str,
        label: str,
        initial_ip: str | None = None,
        ping_coordinator=None,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._title = title
        self._mac = mac.lower()
        self._label = label
        self._initial_ip = initial_ip
        self._ping_coordinator = ping_coordinator

    @property
    def _client(self) -> dict[str, Any] | None:
        clients = self.coordinator.data.get("clients", []) or []
        for client in clients:
            if str(client.get("mac") or "").lower() == self._mac:
                return client
        return None

    @property
    def device_info(self) -> DeviceInfo:
        client = self._client
        return get_client_device_info(
            entry_id=self._entry_id,
            mac=self._mac,
            label=self._label,
            client=client,
            initial_ip=self._initial_ip,
        )

    @property
    def ip_address(self) -> str | None:
        client = self._client
        if client:
            ip = client.get("ip")
            if ip:
                return str(ip)
        return self._initial_ip

    @property
    def hostname(self) -> str | None:
        client = self._client
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
    def _is_apple_device(self) -> bool:
        name = self._label or ""
        name_lower = name.lower()
        return any(kw in name_lower for kw in ("apple", "iphone", "ipad", "macbook", "imac"))

    @property
    def is_connected(self) -> bool:
        if self._ping_coordinator and hasattr(self._ping_coordinator, 'data') and not self._is_apple_device:
            ping_results = self._ping_coordinator.data or {}
            return ping_results.get(self._mac, False)
        else:
            client = self._client
            if client:
                return str(client.get("link", "")).lower() == "up"
            return False
