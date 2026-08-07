"""Keenetic Router Pro integration root."""

from __future__ import annotations

from contextlib import suppress
import importlib
import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import KeeneticApiError, KeeneticAuthError, KeeneticClient
from .const import (
    DOMAIN,
    DEFAULT_PORT,
    DEFAULT_SSL,
    CONF_USE_CHALLENGE_AUTH,
    CLIENT_SENSORS_FULL,
    CLIENT_SENSORS_OFF,
    CONF_CLIENT_SENSORS,
    CONF_TRACKED_CLIENTS,
    DEFAULT_CLIENT_SENSORS,
    EVENT_CLIENT_CONNECTED,
    EVENT_CLIENT_DISCONNECTED,
    EVENT_NEW_DEVICE,
    EVENT_WAN_FAILOVER,
)
from .coordinator import KeeneticCoordinator
from .utils import (
    iter_tracked_clients,
    mask_identifier,
    mesh_unique_id,
    normalize_mac,
)


@dataclass
class KeeneticRuntimeData:
    """Strongly-typed runtime container for a Keenetic config entry.

    Stored on ``ConfigEntry.runtime_data`` so platforms can reach the
    coordinator and API client without going through ``hass.data``.
    """

    client: KeeneticClient
    coordinator: KeeneticCoordinator


# Type alias used by platform code: ``entry: KeeneticConfigEntry``
# gives correct typing for ``entry.runtime_data``.
KeeneticConfigEntry = ConfigEntry  # ConfigEntry[KeeneticRuntimeData] on HA 2024.5+

_LOGGER = logging.getLogger(__name__)

ISSUE_INSECURE_HTTP = "insecure_http"
ISSUE_UNSUPPORTED_FEATURES = "unsupported_features"

# Capability latches the API layer flips off the first time the router answers
# "no such endpoint". Each one silently removes a group of entities, which
# otherwise looks to the user like the integration is broken.
_CAPABILITY_LABELS: dict[str, str] = {
    "_mws_member_supported": "Mesh (MWS) nodes",
    "_crypto_map_supported": "IPsec site-to-site tunnels",
    "_ipsec_diagnostics_supported": "IPsec diagnostics",
    "_ping_check_supported": "WAN ping-check status",
    "_dns_proxy_supported": "DNS proxy statistics",
    "_ndns_supported": "KeenDNS name and certificate",
}


@callback
def _async_update_unsupported_features_issue(
    hass: HomeAssistant, entry: ConfigEntry, client: KeeneticClient
) -> None:
    """Tell the user which feature groups this router turned out not to have.

    These are not errors — plenty of models simply lack the component — but
    without this the missing entities are indistinguishable from a bug.
    """
    issue_id = f"{ISSUE_UNSUPPORTED_FEATURES}_{entry.entry_id}"
    missing = [
        label
        for attr, label in _CAPABILITY_LABELS.items()
        if getattr(client, attr, None) is False
    ]
    if not missing:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_UNSUPPORTED_FEATURES,
        translation_placeholders={
            "title": entry.title,
            "features": ", ".join(missing),
        },
    )


def _mask_identifier(value: Any, *, keep: int = 5) -> str:
    """Return a short non-sensitive suffix for logs."""
    return mask_identifier(value, keep=keep)


def _needs_client_data(entry: ConfigEntry) -> bool:
    """Return False only when nothing at all consumes the client tree.

    ``show/ip/hotspot`` is the largest payload of every tick. A user who
    tracks no clients and has turned the per-client sensors off has told us
    they do not want client data, so there is no point fetching and parsing a
    hundred hosts for entities that will never exist.
    """
    options = dict(getattr(entry, "options", None) or {})
    if options.get(CONF_CLIENT_SENSORS, DEFAULT_CLIENT_SENSORS) != CLIENT_SENSORS_OFF:
        return True
    return bool(entry.data.get(CONF_TRACKED_CLIENTS) or ())


def _router_device_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Return the registry id of the router device, if it is registered yet.

    Device triggers match on ``device_id``, so the events have to carry it.
    The lookup is an in-memory dict hit, and returning None on the very first
    tick (before the device exists) is fine — nothing can be listening yet.
    """
    try:
        dr = importlib.import_module("homeassistant.helpers.device_registry")
        device = dr.async_get(hass).async_get_device(
            identifiers={(DOMAIN, entry.entry_id)}
        )
    except (ImportError, AttributeError, TypeError):
        return None
    return device.id if device else None


def _is_loopback_host(host: str) -> bool:
    """True if host is loopback (localhost / 127.x / ::1) — plaintext is acceptable."""
    candidate = (host or "").strip().lower()
    if candidate in {"localhost", "ip6-localhost", "ip6-loopback"}:
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


@callback
def _async_update_insecure_http_issue(
    hass: HomeAssistant, entry: ConfigEntry, host: str, use_ssl: bool
) -> None:
    """Raise/clear a Repair issue when credentials traverse plaintext HTTP to a non-loopback host."""
    issue_id = f"{ISSUE_INSECURE_HTTP}_{entry.entry_id}"
    if not use_ssl and not _is_loopback_host(host):
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_INSECURE_HTTP,
            translation_placeholders={"host": host, "title": entry.title},
            learn_more_url="https://github.com/ABovsh/Keenetic-Router-Pro/blob/main/SECURITY.md",
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


@callback
def _async_migrate_mesh_unique_ids(
    hass: HomeAssistant,
    entry: ConfigEntry,
    mesh_nodes: list[dict[str, Any]],
) -> None:
    """Migrate old truncated mesh unique IDs to entry-scoped full IDs."""
    try:
        er = importlib.import_module("homeassistant.helpers.entity_registry")
    except ImportError:
        return

    registry = er.async_get(hass)

    def _move(platform: str, old_uid: str, new_uid: str) -> None:
        if old_uid == new_uid:
            return
        old_entity_id = registry.async_get_entity_id(platform, DOMAIN, old_uid)
        if old_entity_id is None:
            return
        if registry.async_get_entity_id(platform, DOMAIN, new_uid) is not None:
            return
        with suppress(ValueError):
            registry.async_update_entity(old_entity_id, new_unique_id=new_uid)

    for node in mesh_nodes or []:
        node_cid = node.get("cid") or node.get("id")
        if not node_cid:
            continue

        node_id = str(node_cid)
        old_safe = node_id.replace("-", "_").replace(":", "_")[:16]
        old_compact = node_id.replace("-", "").replace(":", "")[:16]

        mesh_suffixes = {
            "uptime_v2": "uptime_v2",
            "clients_v2": "clients_v2",
            "local_ip_v2": "local_ip_v2",
            "cpu_load_v2": "cpu_load_v2",
            "memory_v2": "memory_v2",
            "firmware_version_v2": "firmware_version_v2",
        }
        for old_suffix, new_suffix in mesh_suffixes.items():
            _move(
                "sensor",
                f"{old_safe}_{old_suffix}",
                mesh_unique_id(entry.entry_id, node_id, new_suffix),
            )

        for port in node.get("port", []) or []:
            port_label = port.get("label") if isinstance(port, dict) else None
            if port_label is None:
                continue
            _move(
                "sensor",
                f"{old_safe}_port_{port_label}_v2",
                mesh_unique_id(entry.entry_id, node_id, f"port_{port_label}_v2"),
            )

        _move(
            "binary_sensor",
            f"{old_safe}_connect_v2",
            mesh_unique_id(entry.entry_id, node_id, "connect_v2"),
        )
        _move(
            "binary_sensor",
            f"{entry.entry_id}_mesh_{old_compact}_update_v2",
            mesh_unique_id(entry.entry_id, node_id, "update_v2"),
        )
        _move(
            "button",
            f"{old_safe}_reboot_button_v2",
            mesh_unique_id(entry.entry_id, node_id, "reboot_button_v2"),
        )
        _move(
            "update",
            f"{old_safe}_firmware_update_v2",
            mesh_unique_id(entry.entry_id, node_id, "firmware_update_v2"),
        )

# Per-client sensor unique-id suffixes, split by the ``client_sensors`` option.
# "basic" keeps the presence-adjacent facts; "full" adds the counters.
_BASIC_CLIENT_SUFFIXES = ("ip", "session_start", "last_seen", "connection_type")
_FULL_CLIENT_SUFFIXES = ("rx", "tx", "rssi", "txrate", "wifi_band", "wifi_mode")
# Retired ids that must never be left behind: the seconds-counter session sensor
# was replaced by a timestamp under a new unique_id (1.9.0).
_RETIRED_CLIENT_SUFFIXES = ("uptime",)
# Controller-level counts derived from the client tree (1.10.0): removed when
# the tree is not fetched, since they would otherwise report a confident zero.
_CLIENT_COUNT_SUFFIXES = (
    "connected_clients_v2",
    "router_clients_v2",
    "disconnected_clients",
    "extender_count",
)


@callback
def _async_prune_client_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove per-client entities this configuration no longer creates.

    Without this, retiring an id or dialling ``client_sensors`` back leaves the
    old entities registered forever as ``unavailable`` — the user has no way to
    clean them up short of deleting each one by hand.
    """
    try:
        er = importlib.import_module("homeassistant.helpers.entity_registry")
    except ImportError:
        return

    options = dict(getattr(entry, "options", None) or {})
    client_sensors = options.get(CONF_CLIENT_SENSORS, DEFAULT_CLIENT_SENSORS)

    stale = list(_RETIRED_CLIENT_SUFFIXES)
    if client_sensors != CLIENT_SENSORS_FULL:
        stale.extend(_FULL_CLIENT_SUFFIXES)
    if client_sensors == CLIENT_SENSORS_OFF:
        stale.extend(_BASIC_CLIENT_SUFFIXES)

    registry = er.async_get(hass)
    for mac, _label, _ip in iter_tracked_clients(entry):
        for suffix in stale:
            entity_id = registry.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.entry_id}_client_{mac}_{suffix}"
            )
            if entity_id is not None:
                registry.async_remove(entity_id)

    # The loop above is driven by the CURRENT config, so it never visits a MAC
    # the user has removed from the tracked list — precisely the case that
    # strands the most entities. Sweep the registry for client entities whose
    # MAC is no longer tracked at all.
    tracked = {mac for mac, _label, _ip in iter_tracked_clients(entry)}
    prefix = f"{entry.entry_id}_client_"
    entries_for = getattr(er, "async_entries_for_config_entry", None)
    if entries_for is not None:
        for registry_entry in list(entries_for(registry, entry.entry_id)):
            unique_id = getattr(registry_entry, "unique_id", "") or ""
            if not unique_id.startswith(prefix):
                continue
            # unique_id is "<entry>_client_<mac>_<suffix>" and a MAC contains
            # colons, not underscores, so the MAC is the next segment.
            mac = unique_id[len(prefix) :].split("_", 1)[0]
            if mac and mac not in tracked:
                registry.async_remove(registry_entry.entity_id)

    # The controller-level counts go the same way once the client tree is no
    # longer fetched at all, or they linger as orphans the user cannot delete.
    if not _needs_client_data(entry):
        for suffix in _CLIENT_COUNT_SUFFIXES:
            entity_id = registry.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.entry_id}_{suffix}"
            )
            if entity_id is not None:
                registry.async_remove(entity_id)


# Hassfest requires every integration that defines async_setup to declare
# a CONFIG_SCHEMA. We only configure via the UI (config_flow), so the
# canonical helper for "no YAML support" is exactly what we want here.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[str] = [
    "sensor",
    "number",
    "switch",
    "device_tracker",
    "button",
    "binary_sensor",
    "select",
    "update",
]


async def async_setup(_hass: HomeAssistant, _config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data: dict[str, Any] = dict(entry.data)

    host: str | None = data.get("host") or data.get("ip")
    if not host:
        # A config entry with no host is unrecoverable without user
        # intervention — fail fast with a clear error rather than
        # passing None into the API client and getting an opaque
        # crash later. ConfigEntryNotReady triggers HA's normal
        # retry-with-backoff and surfaces the issue to the user.
        raise ConfigEntryNotReady(
            "Keenetic config entry is missing 'host'; please reconfigure the integration"
        )
    username: str | None = data.get("username")
    password: str | None = data.get("password")
    if not username or not password:
        # A stored entry missing credentials is unrecoverable without user
        # action — fail cleanly rather than passing None into the API client.
        raise ConfigEntryNotReady(
            "Keenetic config entry is missing credentials; please reconfigure the integration"
        )
    use_ssl: bool = bool(data.get("ssl", DEFAULT_SSL))

    session = async_get_clientsession(hass)

    # Construct the client and coerce the port inside the try: a corrupted or
    # legacy entry with a non-numeric port or malformed host raises here, and
    # must surface as ConfigEntryNotReady (retry/reconfigure) rather than a
    # raw ValueError/KeeneticApiError that crashes setup.
    try:
        port: int = int(data.get("port", DEFAULT_PORT))
        client = KeeneticClient(
            host=host,
            username=username,
            password=password,
            port=port,
            ssl=use_ssl,
            use_challenge_auth=bool(data.get(CONF_USE_CHALLENGE_AUTH, False)),
        )
        await client.async_start(session)
    except KeeneticAuthError as err:
        raise ConfigEntryAuthFailed("Keenetic credentials were rejected") from err
    except (KeeneticApiError, ValueError, TypeError) as err:
        raise ConfigEntryNotReady(f"Could not connect to Keenetic router: {err}") from err

    coordinator = KeeneticCoordinator(hass, client)
    coordinator.needs_client_data = _needs_client_data(entry)
    await coordinator.async_config_entry_first_refresh()
    _async_migrate_mesh_unique_ids(
        hass,
        entry,
        coordinator.data.get("mesh_nodes", []) if coordinator.data else [],
    )

    _async_prune_client_entities(hass, entry)

    _async_update_insecure_http_issue(hass, entry, host, use_ssl)

    # Modern HA pattern: stash strongly-typed runtime data on the entry
    # itself. Platforms read ``entry.runtime_data.coordinator`` instead
    # of indexing ``hass.data[DOMAIN][entry.entry_id]``.
    entry.runtime_data = KeeneticRuntimeData(
        client=client,
        coordinator=coordinator,
    )

    @callback
    def _async_handle_client_events() -> None:
        """Fire new-device, join/leave and WAN-failover events for one tick."""
        # HA notifies listeners on the first failed refresh after a success
        # with ``data`` unchanged — re-firing would duplicate EVENT_NEW_DEVICE
        # for devices already reported one tick earlier.
        if not coordinator.last_update_success:
            return
        new_clients = coordinator.data.get("new_clients", set())
        clients_by_mac = coordinator.data.get("clients_by_mac", {})
        clients = coordinator.data.get("clients", [])

        for mac in new_clients:
            client_info = None
            if isinstance(clients_by_mac, dict):
                indexed = clients_by_mac.get(mac)
                if isinstance(indexed, dict):
                    client_info = indexed
            if client_info is None:
                for c in clients:
                    if not isinstance(c, dict):
                        continue
                    if normalize_mac(c.get("mac")) == mac:
                        client_info = c
                        break

            if client_info:
                name = client_info.get("name") or client_info.get("hostname") or mac.upper()
                ip = client_info.get("ip")

                _LOGGER.info(
                    "New device connected: %s (%s) - %s",
                    _mask_identifier(name),
                    _mask_identifier(mac),
                    _mask_identifier(ip),
                )

                hass.bus.async_fire(
                    EVENT_NEW_DEVICE,
                    {
                        "mac": mac,
                        "name": name,
                        "ip": ip,
                        "hostname": client_info.get("hostname"),
                        "interface": client_info.get("interface"),
                        "ssid": client_info.get("ssid"),
                    },
                )

        if not isinstance(clients_by_mac, dict):
            clients_by_mac = {}

        for event, key in (
            (EVENT_CLIENT_CONNECTED, "connected_clients"),
            (EVENT_CLIENT_DISCONNECTED, "disconnected_clients"),
        ):
            for mac in coordinator.data.get(key, set()) or ():
                client_info = clients_by_mac.get(mac)
                if not isinstance(client_info, dict):
                    client_info = {}
                hass.bus.async_fire(
                    event,
                    {
                        # Device triggers match on device_id, so every event
                        # has to carry the router it came from.
                        "device_id": _router_device_id(hass, entry),
                        "mac": mac,
                        "name": client_info.get("name")
                        or client_info.get("hostname")
                        or mac.upper(),
                        "ip": client_info.get("ip"),
                        "interface": client_info.get("interface"),
                        "ssid": client_info.get("ssid"),
                    },
                )

        _async_update_unsupported_features_issue(hass, entry, client)

        failover = coordinator.data.get("wan_failover")
        if failover:
            _LOGGER.info(
                "WAN failover: %s -> %s", failover.get("from"), failover.get("to")
            )
            hass.bus.async_fire(
                EVENT_WAN_FAILOVER,
                {"device_id": _router_device_id(hass, entry), **failover},
            )

    # async_add_listener returns an unsubscribe callable. Without
    # registering it via entry.async_on_unload, every reload of the
    # integration leaks a listener bound to the previous coordinator
    # and the closure-captured hass/_LOGGER, slowly growing memory.
    entry.async_on_unload(coordinator.async_add_listener(_async_handle_client_events))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_listener))

    return True


async def async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration after a config entry update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down platforms and clear the entry-scoped Repair issue.

    runtime_data is automatically dropped by HA when the entry is
    unloaded, so there is nothing for us to clean up by hand.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_INSECURE_HTTP}_{entry.entry_id}")

    return True
