"""Keenetic Router Pro integration root."""

from __future__ import annotations

import asyncio
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
    CONF_TRACKED_CLIENTS,
    CONF_USE_CHALLENGE_AUTH,
    CONF_PING_INTERVAL,
    DEFAULT_PING_INTERVAL,
    MIN_PING_INTERVAL,
    EVENT_NEW_DEVICE,
)
from .coordinator import KeeneticCoordinator, KeeneticPingCoordinator


@dataclass
class KeeneticRuntimeData:
    """Strongly-typed runtime container for a Keenetic config entry.

    Stored on ``ConfigEntry.runtime_data`` so platforms can reach the
    coordinator and API client without going through ``hass.data``.
    """

    client: KeeneticClient
    coordinator: KeeneticCoordinator
    ping_coordinator: "KeeneticPingCoordinator"


# Type alias used by platform code: ``entry: KeeneticConfigEntry``
# gives correct typing for ``entry.runtime_data``.
KeeneticConfigEntry = ConfigEntry  # ConfigEntry[KeeneticRuntimeData] on HA 2024.5+

_LOGGER = logging.getLogger(__name__)

ISSUE_INSECURE_HTTP = "insecure_http"


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

# Hassfest requires every integration that defines async_setup to declare
# a CONFIG_SCHEMA. We only configure via the UI (config_flow), so the
# canonical helper for "no YAML support" is exactly what we want here.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[str] = [
    "sensor",
    "switch",
    "device_tracker",
    "button",
    "binary_sensor",
    "select",
    "update",
]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
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
    username: str = data["username"]
    password: str = data["password"]
    port: int = int(data.get("port", DEFAULT_PORT))
    use_ssl: bool = bool(data.get("ssl", DEFAULT_SSL))

    session = async_get_clientsession(hass)

    client = KeeneticClient(
        host=host,
        username=username,
        password=password,
        port=port,
        ssl=use_ssl,
        use_challenge_auth=bool(data.get(CONF_USE_CHALLENGE_AUTH, False)),
    )
    try:
        await client.async_start(session)
    except KeeneticAuthError as err:
        raise ConfigEntryAuthFailed("Keenetic credentials were rejected") from err
    except KeeneticApiError as err:
        raise ConfigEntryNotReady(f"Could not connect to Keenetic router: {err}") from err

    coordinator = KeeneticCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    tracked_clients = data.get(CONF_TRACKED_CLIENTS, [])

    # Ping interval: options flow takes precedence over data, falls back to default.
    ping_interval = entry.options.get(
        CONF_PING_INTERVAL,
        data.get(CONF_PING_INTERVAL, DEFAULT_PING_INTERVAL),
    )
    try:
        ping_interval = int(ping_interval)
    except (TypeError, ValueError):
        ping_interval = DEFAULT_PING_INTERVAL
    if ping_interval < MIN_PING_INTERVAL:
        ping_interval = DEFAULT_PING_INTERVAL

    ping_coordinator = KeeneticPingCoordinator(
        hass, client, tracked_clients, interval=ping_interval
    )

    if tracked_clients:
        # async_config_entry_first_refresh yerine async_refresh kullanıyoruz.
        # Ping sırasında CancelledError veya başka bir hata olursa setup
        # iptal edilmesin; coordinator boş veriyle başlasın, sonraki
        # döngüde tekrar denensin.
        try:
            await ping_coordinator.async_refresh()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Initial ping refresh failed (non-fatal), will retry on next cycle: %s", err
            )

    _async_update_insecure_http_issue(hass, entry, host, use_ssl)

    # Modern HA pattern: stash strongly-typed runtime data on the entry
    # itself. Platforms read ``entry.runtime_data.coordinator`` instead
    # of indexing ``hass.data[DOMAIN][entry.entry_id]``.
    entry.runtime_data = KeeneticRuntimeData(
        client=client,
        coordinator=coordinator,
        ping_coordinator=ping_coordinator,
    )

    @callback
    def _async_handle_new_device() -> None:
        """Yeni cihaz bağlandığında event tetikle."""
        new_clients = coordinator.data.get("new_clients", set())
        clients = coordinator.data.get("clients", [])
        
        for mac in new_clients:
            client_info = None
            for c in clients:
                if str(c.get("mac") or "").lower() == mac:
                    client_info = c
                    break
            
            if client_info:
                name = client_info.get("name") or client_info.get("hostname") or mac.upper()
                ip = client_info.get("ip")
                
                _LOGGER.info("New device connected: %s (%s) - %s", name, mac, ip)
                
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

    # async_add_listener returns an unsubscribe callable. Without
    # registering it via entry.async_on_unload, every reload of the
    # integration leaks a listener bound to the previous coordinator
    # and the closure-captured hass/_LOGGER, slowly growing memory.
    entry.async_on_unload(coordinator.async_add_listener(_async_handle_new_device))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_listener))
    
    return True


async def async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Config entry güncellendiğinde çağrılır (options flow sonrası)."""
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
