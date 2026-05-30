"""Base entity classes for Keenetic Router Pro."""
from __future__ import annotations

from typing import Any
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN
from .coordinator import KeeneticCoordinator
from .utils import (
    find_client_by_mac,
    find_mesh_node,
    get_main_device_info,
    get_mesh_device_info,
    get_client_device_info,
    get_wan_device_info,
    get_vpn_interface_device_info,
    get_crypto_map_device_info,
    is_client_online,
    mesh_unique_id,
    normalize_mac,
    sanitize_mesh_id,
    usable_ip,
)


def _entity_fingerprint(
    data: dict[str, Any] | None,
    ignore: frozenset[str],
) -> dict[str, Any] | None:
    """Return a dict fingerprint with volatile fields removed."""
    if not data:
        return None
    return {k: v for k, v in data.items() if k not in ignore}


class _FingerprintedCoordinatorEntity(CoordinatorEntity):
    """CoordinatorEntity that suppresses state writes when only volatile fields tick.

    Subclasses set ``_FINGERPRINT_IGNORE`` (a frozenset of field names that
    change every coordinator tick without semantic meaning) and override
    ``_fingerprint_source`` to return the dict whose fingerprint should be
    compared (e.g. the current client/WAN/mesh-node payload).
    """

    _FINGERPRINT_IGNORE: frozenset[str] = frozenset()
    _last_fingerprint: dict[str, Any] | None = None

    @property
    def _fingerprint_source(self) -> dict[str, Any] | None:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        """Become unavailable (not stale) once our source object disappears.

        Every per-instance entity (mesh node, WAN, crypto map, client) goes
        unavailable when its dict is removed from coordinator data — e.g. the
        user deletes the tunnel or the client ages out.
        """
        return (
            bool(getattr(super(), "available", True))
            and self._fingerprint_source is not None
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        fingerprint = _entity_fingerprint(
            self._fingerprint_source, self._FINGERPRINT_IGNORE
        )
        if fingerprint is not None and fingerprint == self._last_fingerprint:
            return
        self._last_fingerprint = fingerprint
        super()._handle_coordinator_update()


def _lookup_by_id(data: dict[str, Any], index_key: str, list_key: str, target: str) -> dict[str, Any] | None:
    """Return the entry matching ``target`` from a coordinator-published index.

    Falls back to a linear scan over ``list_key`` if the O(1) index is
    missing (e.g. first tick before coordinator data is populated, or an
    older test fixture that doesn't pre-populate the index).
    """
    index = data.get(index_key)
    if isinstance(index, dict):
        entry = index.get(target)
        if isinstance(entry, dict):
            return entry
    for item in data.get(list_key, []) or []:
        if isinstance(item, dict) and item.get("id") == target:
            return item
    return None


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
        version = self._version_data

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
        version = self._version_data

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
            host=getattr(self.coordinator.client, "host", None),
            ssl=bool(getattr(self.coordinator.client, "ssl", False)),
            ndns_domain=ndns_domain,
        )


class MeshEntity(_FingerprintedCoordinatorEntity):
    """Base class for Mesh node entities."""

    _FINGERPRINT_IGNORE = frozenset(
        {
            "uptime",
            "cpuload",
            "mem-free",
            "mem-cached",
            "last-seen",
            "rx-bytes",
            "tx-bytes",
        }
    )

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
    def _fingerprint_source(self) -> dict[str, Any] | None:
        return self._node

    @property
    def _node(self) -> dict[str, Any] | None:
        return find_mesh_node(self.coordinator.data or {}, self._node_cid)

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
            ssl=bool(getattr(self.coordinator.client, "ssl", False)),
            fqdn=node.get("fqdn") if node else None,
        )
    
class WanEntity(_FingerprintedCoordinatorEntity):
    """Base class for per-WAN-interface entities.

    Each WAN is exposed in HA as its own sub-device under the main
    router, so all of its sensors (status, IP, uptime, throughput, ...)
    are grouped together in the UI.
    """

    _FINGERPRINT_IGNORE = frozenset(
        {
            "rx_bytes",
            "tx_bytes",
            "rx_packets",
            "tx_packets",
            "rx_speed_raw",
            "tx_speed_raw",
            "rx_throughput",
            "tx_throughput",
            "_sample_ts",
            "stats_timestamp",
            "uptime",
        }
    )

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
    def _fingerprint_source(self) -> dict[str, Any] | None:
        return self._wan

    @property
    def _wan(self) -> dict[str, Any] | None:
        return _lookup_by_id(
            self.coordinator.data or {}, "wan_by_id", "wan_interfaces", self._wan_id
        )

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
        return _lookup_by_id(
            self.coordinator.data or {}, "wan_by_id", "wan_interfaces", self._iface_id
        )

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


class CryptoMapEntity(_FingerprintedCoordinatorEntity):
    """Base class for per-`crypto map` site-to-site IPsec entities.

    Each configured crypto map is exposed in HA as its own sub-device
    under the main router, mirroring the per-WAN model.
    """

    _FINGERPRINT_IGNORE = frozenset(
        {
            "rx_bytes",
            "tx_bytes",
            "rx_throughput",
            "tx_throughput",
            "_sample_ts",
        }
    )

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
    def _fingerprint_source(self) -> dict[str, Any] | None:
        return self._cmap

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
    def device_info(self) -> DeviceInfo:
        cmap = self._cmap or {}
        return get_crypto_map_device_info(
            title=self._title,
            entry_id=self._entry_id,
            cmap_name=self._cmap_name,
            remote_peer=cmap.get("remote_peer"),
        )


class ClientEntity(_FingerprintedCoordinatorEntity):
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
    _FINGERPRINT_IGNORE = _CLIENT_FINGERPRINT_IGNORE

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

    def _client_fingerprint(self, client: dict[str, Any] | None) -> dict[str, Any] | None:
        return _entity_fingerprint(client, self._client_ignored_fingerprint_fields)

    @property
    def _client_ignored_fingerprint_fields(self) -> frozenset[str]:
        """Return client fields ignored for this entity's state writes."""
        return self._CLIENT_FINGERPRINT_IGNORE

    @property
    def _fingerprint_source(self) -> dict[str, Any] | None:
        return self._client

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
        # Precedence here is intentionally name-first; the device-card
        # label uses hostname-first via client_display_name. Keeping the
        # two precedences avoids silently renaming entity attributes for
        # existing users on upgrade.
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
        return is_client_online(self._client)
