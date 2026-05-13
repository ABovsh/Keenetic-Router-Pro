"""Sensors for Keenetic Router Pro."""

from __future__ import annotations

from typing import Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import DOMAIN, CONF_TRACKED_CLIENTS
from ..coordinator import KeeneticCoordinator
from .. import KeeneticClient

from .system import (
    KeeneticCpuLoadSensor,
    KeeneticMemoryUsageSensor,
    KeeneticUptimeSensor,
    KeeneticFirmwareVersionSensor,
)
from .network import (
    KeeneticWanStatusSensor,
    KeeneticWanIpSensor,
    KeeneticPppoeUptimeSensor,
    KeeneticActiveConnectionsSensor,
    KeeneticLocalIpSensor,
    KeeneticMainPortSensor,
    KeeneticWanProviderSensor,
    KeeneticWanRoleSensor,
    KeeneticWanInterfaceSensor,
    KeeneticWanPublicIpSensor,
    KeeneticWanUptimeSensor,
    KeeneticWanRxBytesSensor,
    KeeneticWanTxBytesSensor,
    KeeneticWanRxThroughputSensor,
    KeeneticWanTxThroughputSensor,
)
from .clients import (
    KeeneticConnectedClientsSensor,
    KeeneticRouterClientsSensor,
    KeeneticDisconnectedClientsSensor,
    KeeneticExtenderCountSensor,
)
from .wifi import (
    KeeneticWifi24TemperatureSensor,
    KeeneticWifi5TemperatureSensor,
    KeeneticWifi24RxSensor,
    KeeneticWifi24TxSensor,
    KeeneticWifi5RxSensor,
    KeeneticWifi5TxSensor,
)
from .mesh import (
    KeeneticMeshSystemStateSensor,
    KeeneticMeshCpuLoadSensor,
    KeeneticMeshMemorySensor,
    KeeneticMeshUptimeSensor,
    KeeneticMeshClientsSensor,
    KeeneticMeshFirmwareVersionSensor,
    KeeneticMeshLocalIpSensor,
    KeeneticMeshPortSensor
)
from .traffic import (
    KeeneticLanRxSensor,
    KeeneticLanTxSensor,
    KeeneticWanRxSensor,
    KeeneticWanTxSensor,
)
from .client import (
    KeeneticClientIpSensor,
    KeeneticClientRegisteredSensor,
    KeeneticClientUptimeSensor,
    KeeneticClientFirstSeenSensor,
    KeeneticClientLastSeenSensor,
    KeeneticClientRxSensor,
    KeeneticClientTxSensor,
    KeeneticClientRssiSensor,
    KeeneticClientTxRateSensor,
    KeeneticClientConnectionTypeSensor,
    KeeneticClientWifiBandSensor,
    KeeneticClientWifiModeSensor,   
)
from .crypto import (
    KeeneticCryptoMapStateSensor,
    KeeneticCryptoMapIkeStateSensor,
    KeeneticCryptoMapRxBytesSensor,
    KeeneticCryptoMapTxBytesSensor,
    KeeneticCryptoMapRxThroughputSensor,
    KeeneticCryptoMapTxThroughputSensor,
)
from .dns import (
    KeeneticDnsProxyStatusSensor,
    KeeneticDnsProxyFailedRequestsSensor,
)
from .ipsec import (
    KeeneticIpsecViciStatusSensor,
    KeeneticIpsecViciOutOfMemorySensor,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Keenetic Router Pro sensors from a config entry."""
    runtime = entry.runtime_data
    coordinator: KeeneticCoordinator = runtime.coordinator
    client: Optional[KeeneticClient] = runtime.client
    entities: list[SensorEntity] = []

    # Temel sistem sensörleri
    entities.append(KeeneticCpuLoadSensor(coordinator, entry))
    entities.append(KeeneticMemoryUsageSensor(coordinator, entry))
    entities.append(KeeneticUptimeSensor(coordinator, entry))
    entities.append(KeeneticFirmwareVersionSensor(coordinator, entry))

    # Legacy controller-level WAN summary sensors are kept for
    # backwards compatibility. The per-WAN device entities below are
    # the preferred model for new dashboards.
    entities.append(KeeneticWanStatusSensor(coordinator, entry))
    entities.append(KeeneticWanIpSensor(coordinator, entry))
    entities.append(KeeneticPppoeUptimeSensor(coordinator, entry))
    entities.append(KeeneticActiveConnectionsSensor(coordinator, entry))
    entities.append(KeeneticDnsProxyStatusSensor(coordinator, entry))
    entities.append(KeeneticDnsProxyFailedRequestsSensor(coordinator, entry))
    entities.append(KeeneticIpsecViciStatusSensor(coordinator, entry))
    entities.append(KeeneticIpsecViciOutOfMemorySensor(coordinator, entry))
    entities.append(KeeneticConnectedClientsSensor(coordinator, entry))
    entities.append(KeeneticRouterClientsSensor(coordinator, entry))
    entities.append(KeeneticDisconnectedClientsSensor(coordinator, entry))
    entities.append(KeeneticExtenderCountSensor(coordinator, entry))

    # WiFi radio sensors
    entities.append(KeeneticWifi24TemperatureSensor(coordinator, entry))
    entities.append(KeeneticWifi5TemperatureSensor(coordinator, entry))
    entities.append(KeeneticWifi24RxSensor(coordinator, entry))
    entities.append(KeeneticWifi24TxSensor(coordinator, entry))
    entities.append(KeeneticWifi5RxSensor(coordinator, entry))
    entities.append(KeeneticWifi5TxSensor(coordinator, entry))

    # Traffic counters
    entities.append(KeeneticLanRxSensor(coordinator, entry))
    entities.append(KeeneticLanTxSensor(coordinator, entry))
    entities.append(KeeneticWanRxSensor(coordinator, entry))
    entities.append(KeeneticWanTxSensor(coordinator, entry))

    host = entry.data.get("host") or entry.data.get("ip", "unknown")
    entities.append(KeeneticLocalIpSensor(coordinator, entry, host))

    # Main router port sensors
    main_ports = coordinator.data.get("port_info", [])
    for port in main_ports:
        port_label = port.get("label")
        if port_label is not None:
            entities.append(KeeneticMainPortSensor(coordinator, entry, port_label))

    entities.append(KeeneticMeshSystemStateSensor(coordinator, entry))

    # Mesh node sensors
    known_mesh_ids: set[str] = set()
    known_mesh_local_ip_ids: set[str] = set()
    known_mesh_port_keys: set[tuple[str, str]] = set()
    _add_mesh_sensors(
        entities,
        coordinator,
        entry,
        known_mesh_ids,
        known_mesh_local_ip_ids,
        known_mesh_port_keys,
    )

    # Per-tracked-client sensors
    tracked_clients = entry.data.get(CONF_TRACKED_CLIENTS, [])
    seen_macs: set[str] = set()

    for client_info in tracked_clients:
        if not isinstance(client_info, dict):
            continue

        mac = str(client_info.get("mac") or "").lower()
        if not mac or mac in seen_macs:
            continue
        seen_macs.add(mac)

        label = client_info.get("name") or mac.upper()
        initial_ip = client_info.get("ip")

        entities.append(KeeneticClientIpSensor(coordinator, entry, mac, label, initial_ip))
        entities.append(KeeneticClientRegisteredSensor(coordinator, entry, mac, label))
        entities.append(KeeneticClientUptimeSensor(coordinator, entry, mac, label))
        entities.append(KeeneticClientFirstSeenSensor(coordinator, entry, mac, label))
        entities.append(KeeneticClientLastSeenSensor(coordinator, entry, mac, label))
        entities.append(KeeneticClientRxSensor(coordinator, entry, mac, label))
        entities.append(KeeneticClientTxSensor(coordinator, entry, mac, label))
        entities.append(KeeneticClientRssiSensor(coordinator, entry, mac, label))
        entities.append(KeeneticClientTxRateSensor(coordinator, entry, mac, label))
        entities.append(KeeneticClientConnectionTypeSensor(coordinator, entry, mac, label))
        entities.append(KeeneticClientWifiBandSensor(coordinator, entry, mac, label))
        entities.append(KeeneticClientWifiModeSensor(coordinator, entry, mac, label))

    # Per-WAN sensor set: one sub-device per uplink (Default + backups).
    # Covers provider name, priority role, underlying interface, public
    # IP, uptime, byte counters and live throughput.
    known_wan_ids: set[str] = set()

    def _wan_sensor_set(wan_id: str) -> list[SensorEntity]:
        return [
            KeeneticWanProviderSensor(coordinator, entry, wan_id),
            KeeneticWanRoleSensor(coordinator, entry, wan_id),
            KeeneticWanInterfaceSensor(coordinator, entry, wan_id),
            KeeneticWanPublicIpSensor(coordinator, entry, wan_id),
            KeeneticWanUptimeSensor(coordinator, entry, wan_id),
            KeeneticWanRxBytesSensor(coordinator, entry, wan_id),
            KeeneticWanTxBytesSensor(coordinator, entry, wan_id),
            KeeneticWanRxThroughputSensor(coordinator, entry, wan_id),
            KeeneticWanTxThroughputSensor(coordinator, entry, wan_id),
        ]

    for wan in coordinator.data.get("wan_interfaces", []) or []:
        wan_id = wan.get("id")
        if not wan_id or wan_id in known_wan_ids:
            continue
        known_wan_ids.add(wan_id)
        entities.extend(_wan_sensor_set(wan_id))

    # Per-crypto-map sensor set: one sub-device per site-to-site
    # IPsec tunnel. Covers the two state strings (tunnel, IKE), byte
    # counters and live throughput. Connected binary_sensor and the
    # Enabled switch live on their respective platforms.
    known_cmap_names: set[str] = set()

    def _crypto_map_sensor_set(cmap_name: str) -> list[SensorEntity]:
        return [
            KeeneticCryptoMapStateSensor(coordinator, entry, cmap_name),
            KeeneticCryptoMapIkeStateSensor(coordinator, entry, cmap_name),
            KeeneticCryptoMapRxBytesSensor(coordinator, entry, cmap_name),
            KeeneticCryptoMapTxBytesSensor(coordinator, entry, cmap_name),
            KeeneticCryptoMapRxThroughputSensor(coordinator, entry, cmap_name),
            KeeneticCryptoMapTxThroughputSensor(coordinator, entry, cmap_name),
        ]

    for cmap_name in (coordinator.data.get("crypto_maps") or {}).keys():
        if cmap_name in known_cmap_names:
            continue
        known_cmap_names.add(cmap_name)
        entities.extend(_crypto_map_sensor_set(cmap_name))

    async_add_entities(entities)

    # New WAN interfaces may appear at runtime (LTE stick plugged in,
    # new WireGuard tunnel configured as uplink, PPPoE redialed on a
    # different interface). Mirror the binary_sensor platform and add
    # the per-WAN sensor set on the fly so the user doesn't need to
    # restart HA. Crypto maps added from the web UI fan out through
    # the same listener.
    @callback
    def _async_add_new_dynamic_entities() -> None:
        new_entities: list[SensorEntity] = []
        _add_mesh_sensors(
            new_entities,
            coordinator,
            entry,
            known_mesh_ids,
            known_mesh_local_ip_ids,
            known_mesh_port_keys,
        )
        for wan in coordinator.data.get("wan_interfaces", []) or []:
            wan_id = wan.get("id")
            if not wan_id or wan_id in known_wan_ids:
                continue
            known_wan_ids.add(wan_id)
            new_entities.extend(_wan_sensor_set(wan_id))
        for cmap_name in (coordinator.data.get("crypto_maps") or {}).keys():
            if cmap_name in known_cmap_names:
                continue
            known_cmap_names.add(cmap_name)
            new_entities.extend(_crypto_map_sensor_set(cmap_name))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        coordinator.async_add_listener(_async_add_new_dynamic_entities)
    )


def _add_mesh_sensors(
    entities: list[SensorEntity],
    coordinator: KeeneticCoordinator,
    entry: ConfigEntry,
    known_mesh_ids: set[str],
    known_mesh_local_ip_ids: set[str],
    known_mesh_port_keys: set[tuple[str, str]],
) -> None:
    """Append sensors for newly discovered mesh nodes and ports."""
    for node in coordinator.data.get("mesh_nodes", []) or []:
        node_cid = node.get("cid") or node.get("id")
        if not node_cid:
            continue

        node_id = str(node_cid)
        if node_id not in known_mesh_ids:
            known_mesh_ids.add(node_id)
            entities.append(KeeneticMeshCpuLoadSensor(coordinator, entry, node_id))
            entities.append(KeeneticMeshMemorySensor(coordinator, entry, node_id))
            entities.append(KeeneticMeshUptimeSensor(coordinator, entry, node_id))
            entities.append(KeeneticMeshClientsSensor(coordinator, entry, node_id))
            entities.append(KeeneticMeshFirmwareVersionSensor(coordinator, entry, node_id))

        node_ip = node.get("ip")
        if node_ip and node_id not in known_mesh_local_ip_ids:
            known_mesh_local_ip_ids.add(node_id)
            entities.append(KeeneticMeshLocalIpSensor(coordinator, entry, node_id, node_ip))

        for port in node.get("port", []) or []:
            port_label = port.get("label") if isinstance(port, dict) else None
            if port_label is None:
                continue
            port_key = (node_id, str(port_label))
            if port_key in known_mesh_port_keys:
                continue
            known_mesh_port_keys.add(port_key)
            entities.append(KeeneticMeshPortSensor(coordinator, entry, node_id, str(port_label)))
