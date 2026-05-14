"""Base entity classes for Keenetic Router Pro."""
from __future__ import annotations

from typing import Any
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN
from .coordinator import KeeneticCoordinator
from .utils import (
    coerce_bool,
    find_client_by_mac,
    get_main_device_info,
    get_mesh_device_info,
    get_client_device_info,
    get_wan_device_info,
    get_vpn_interface_device_info,
    get_crypto_map_device_info,
    mesh_unique_id,
    normalize_mac,
    sanitize_mesh_id,
    usable_ip,
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
        self._safe_cid = sanitize_mesh_id(node_cid)

    def _mesh_unique_id(self, suffix: str) -> str:
        """Return the entry-scoped unique ID for this mesh entity."""
        return mesh_unique_id(self._entry_id, self._node_cid, suffix)

    @property
    def _node(self) -> dict[str, Any] | None:
        nodes = self.coordinator.data.get("mesh_nodes", [])
        for node in nodes:
            if (node.get("cid") or node.get("id")) == self._node_cid:
                return node
        return None

    @property
    def available(self) -> bool:
        """Return whether this mesh node still exists in coordinator data."""
        return bool(getattr(super(), "available", True)) and self._node is not None
    
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
            fqdn=node.get("fqdn") if node else None,
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
    def available(self) -> bool:
        """Return whether this WAN still exists in coordinator data."""
        return bool(getattr(super(), "available", True)) and self._wan is not None

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


class InterfaceEntity(CoordinatorEntity):
    """Base class for generic router interface entities.

    If an interface is also present in ``wan_interfaces`` it is grouped
    under the existing WAN sub-device. Otherwise it gets a lightweight
    interface/VPN sub-device under the router.
    """

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry_id: str,
        title: str,
        iface_id: str,
        label: str | None = None,
        iface_type: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._title = title
        self._iface_id = iface_id
        self._label = label or iface_id
        self._iface_type = iface_type

    @property
    def _wan(self) -> dict[str, Any] | None:
        for w in self.coordinator.data.get("wan_interfaces", []) or []:
            if w.get("id") == self._iface_id:
                return w
        return None

    @property
    def device_info(self) -> DeviceInfo:
        wan = self._wan
        if wan is not None:
            return get_wan_device_info(
                title=self._title,
                entry_id=self._entry_id,
                wan_id=self._iface_id,
                description=wan.get("description"),
                iface_type=wan.get("type"),
                role_label=wan.get("role_label"),
            )

        return get_vpn_interface_device_info(
            title=self._title,
            entry_id=self._entry_id,
            iface_id=self._iface_id,
            label=self._label,
            iface_type=self._iface_type,
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

    # Keenetic's hotspot endpoint refreshes ``last-seen`` (and ``uptime`` for
    # currently-connected clients) on every poll even when nothing else
    # changed. Excluding these from the change-detection fingerprint lets
    # idle/sleeping clients skip the per-tick state-write storm. Both fields
    # are still surfaced on the dedicated uptime / last-seen sensors which
    # subscribe to ``_handle_coordinator_update`` of their own — those
    # sensors define their own native_value from the same coordinator data
    # and HA's own state-bus dedup handles them at the SQLite level.
    _CLIENT_FINGERPRINT_IGNORE = frozenset({"last-seen", "uptime"})

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry_id: str,
        title: str,
        mac: str,
        label: str,
        initial_ip: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._title = title
        self._mac = normalize_mac(mac)
        self._label = label
        self._initial_ip = initial_ip
        self._last_fingerprint: dict[str, Any] | None = None

    def _client_fingerprint(self, client: dict[str, Any] | None) -> dict[str, Any] | None:
        if not client:
            return None
        return {
            k: v for k, v in client.items()
            if k not in self._client_fingerprint_ignore
        }

    @property
    def _client_fingerprint_ignore(self) -> frozenset[str]:
        """Return client fields ignored for this entity's state writes."""
        return self._CLIENT_FINGERPRINT_IGNORE

    @callback
    def _handle_coordinator_update(self) -> None:
        fingerprint = self._client_fingerprint(self._client)
        if fingerprint is not None and fingerprint == self._last_fingerprint:
            return
        self._last_fingerprint = fingerprint
        super()._handle_coordinator_update()

    @property
    def _client(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        index = data.get("clients_by_mac")
        if isinstance(index, dict):
            client = index.get(self._mac)
            if isinstance(client, dict):
                return client
        return find_client_by_mac(data.get("clients"), self._mac)

    @property
    def device_info(self) -> DeviceInfo:
        client = self._client
        return get_client_device_info(
            entry_id=self._entry_id,
            title=self._title,
            mac=self._mac,
            label=self._label,
            client=client,
            initial_ip=self._initial_ip,
        )

    @property
    def ip_address(self) -> str | None:
        client = self._client
        if client:
            ip = usable_ip(client.get("ip"))
            if ip:
                return ip
        return usable_ip(self._initial_ip)

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
    def is_connected(self) -> bool:
        client = self._client
        if not client:
            return False
        if str(client.get("link", "")).lower() == "up":
            return True
        if coerce_bool(client.get("neighbour-expired")):
            return False
        return coerce_bool(client.get("active"))
