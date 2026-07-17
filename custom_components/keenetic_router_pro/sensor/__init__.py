"""Sensors for Keenetic Router Pro."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..coordinator import KeeneticCoordinator
from ..entity_setup import DynamicEntityTracker, register_dynamic_entities
from ..utils import iter_new_items, iter_tracked_clients

# Read-only coordinator-driven platform: no writes to serialize, no limit needed.
PARALLEL_UPDATES = 0

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
    KeeneticMeshPortSensor,
)
from .traffic import (
    KeeneticLanRxSensor,
    KeeneticLanTxSensor,
    KeeneticWanRxSensor,
    KeeneticWanTxSensor,
)
from .client import (
    KeeneticClientIpSensor,
    KeeneticClientUptimeSensor,
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
from .ipsec import KeeneticIpsecViciOomTotalSensor
from .wireguard import KeeneticWgRxSensor, KeeneticWgTxSensor, KeeneticWgUptimeSensor


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Keenetic Router Pro sensors from a config entry."""
    runtime = entry.runtime_data
    coordinator: KeeneticCoordinator = runtime.coordinator
    entities: list[SensorEntity] = []

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
    entities.append(KeeneticIpsecViciOomTotalSensor(coordinator, entry))
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

    entities.append(KeeneticMeshSystemStateSensor(coordinator, entry))

    tracker = DynamicEntityTracker()

    def _build_dynamic_sensors() -> list[SensorEntity]:
        dynamic_entities: list[SensorEntity] = []
        _add_mesh_sensors(
            dynamic_entities,
            coordinator,
            entry,
            tracker.mesh_nodes,
            tracker.mesh_local_ips,
            tracker.mesh_ports,
        )
        for wan in iter_new_items(coordinator, "wan_interfaces", tracker.wan_ids):
            wan_id = wan["id"]
            dynamic_entities.extend(_wan_sensor_set(wan_id))
        for port in coordinator.data.get("port_info", []) or []:
            if not isinstance(port, dict) or port.get("label") is None:
                continue
            port_label = str(port["label"])
            if tracker.mark_main_port(port_label):
                dynamic_entities.append(
                    KeeneticMainPortSensor(coordinator, entry, port_label)
                )
        wireguard = coordinator.data.get("wireguard") or {}
        profiles = wireguard.get("profiles", {}) if isinstance(wireguard, dict) else {}
        if isinstance(profiles, dict):
            for profile_id in profiles:
                if not tracker.mark_wireguard(str(profile_id)):
                    continue
                dynamic_entities.extend(
                    [
                        KeeneticWgUptimeSensor(coordinator, entry, str(profile_id)),
                        KeeneticWgRxSensor(coordinator, entry, str(profile_id)),
                        KeeneticWgTxSensor(coordinator, entry, str(profile_id)),
                    ]
                )
        crypto_maps = coordinator.data.get("crypto_maps") or {}
        if not isinstance(crypto_maps, dict):
            crypto_maps = {}
        for cmap_name in crypto_maps.keys():
            if not tracker.mark_crypto_map(cmap_name):
                continue
            dynamic_entities.extend(_crypto_map_sensor_set(cmap_name))
        return dynamic_entities

    # Per-tracked-client sensors
    for mac, label, initial_ip in iter_tracked_clients(entry):
        entities.append(KeeneticClientIpSensor(coordinator, entry, mac, label, initial_ip))
        entities.append(KeeneticClientUptimeSensor(coordinator, entry, mac, label))
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

    # Per-crypto-map sensor set: one sub-device per site-to-site
    # IPsec tunnel. Covers the two state strings (tunnel, IKE), byte
    # counters and live throughput. Connected binary_sensor and the
    # Enabled switch live on their respective platforms.
    def _crypto_map_sensor_set(cmap_name: str) -> list[SensorEntity]:
        return [
            KeeneticCryptoMapStateSensor(coordinator, entry, cmap_name),
            KeeneticCryptoMapIkeStateSensor(coordinator, entry, cmap_name),
            KeeneticCryptoMapRxBytesSensor(coordinator, entry, cmap_name),
            KeeneticCryptoMapTxBytesSensor(coordinator, entry, cmap_name),
            KeeneticCryptoMapRxThroughputSensor(coordinator, entry, cmap_name),
            KeeneticCryptoMapTxThroughputSensor(coordinator, entry, cmap_name),
        ]

    entities.extend(_build_dynamic_sensors())

    async_add_entities(entities)

    register_dynamic_entities(
        entry,
        coordinator,
        async_add_entities,
        _build_dynamic_sensors,
        add_initial=False,
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
        if not isinstance(node, dict):
            continue
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
