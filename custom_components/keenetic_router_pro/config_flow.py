"""Config flow for Keenetic Router Pro."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

import asyncio
import logging
import voluptuous as vol
import aiohttp

from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_SSL,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from homeassistant.helpers.device_registry import format_mac

from .api import (
    KeeneticApiError,
    KeeneticAuthError,
    KeeneticClient,
    normalize_connection_target,
)
from .const import (
    DOMAIN,
    DEFAULT_PORT,
    DEFAULT_SSL,
    CONF_CONNECTION_MODE,
    CONNECTION_MODE_DIRECT,
    CONNECTION_MODE_KEENDNS_PROTECTED,
    CONF_TRACKED_CLIENTS,
    CONF_USE_CHALLENGE_AUTH,
)
from .utils import iter_tracked_clients, mask_identifier, normalize_mac

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.config_flow")
_DEFAULT_DEVICE_NAME = "Keenetic Router"

# Reusable masked password input — keeps the password hidden in the HA UI
# during setup, reauth and reconfigure flows.
_PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)
_CONNECTION_MODE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(
                value=CONNECTION_MODE_DIRECT,
                label="Direct / local",
            ),
            selector.SelectOptionDict(
                value=CONNECTION_MODE_KEENDNS_PROTECTED,
                label="KeenDNS protected web app",
            ),
        ],
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)

def _normalize_client(client_info: dict[str, Any]) -> dict[str, str] | None:
    """Return the compact tracked-client representation used in config data."""
    mac = normalize_mac(client_info.get("mac"))
    if not mac:
        return None
    ip = str(client_info.get("ip") or "")
    if ip in {"0.0.0.0", "::"}:
        ip = ""
    return {
        "mac": mac,
        "ip": ip,
        "name": str(client_info.get("name") or client_info.get("hostname") or ""),
    }


def _client_label(client: dict[str, Any], *, offline: bool = False) -> str:
    """Build the display label shown in multi-select client lists."""
    label = client.get("name") or client.get("ip") or client["mac"].upper()
    if client.get("ip"):
        label = f"{label} ({client['ip']})"
    if offline:
        label = f"{label} [offline]"
    return label


def _client_options(clients: list[dict[str, Any]]) -> dict[str, str]:
    """Return sorted MAC -> label options for a client multi-select."""
    options = {
        normalize_mac(c["mac"]): _client_label({**c, "mac": normalize_mac(c["mac"])})
        for c in clients
        if c.get("mac") and normalize_mac(c["mac"])
    }
    return dict(sorted(options.items(), key=lambda item: item[1].lower()))


def _tracked_client_lookup(
    available_clients: list[dict[str, str]],
    tracked_clients: list[Any],
) -> dict[str, dict[str, str]]:
    """Return MAC-keyed client records, preserving tracked offline clients."""
    mac_lookup: dict[str, dict[str, str]] = {
        normalized["mac"]: normalized
        for client in available_clients
        if isinstance(client, dict) and (normalized := _normalize_client(client))
    }
    for client in tracked_clients:
        if isinstance(client, dict) and client.get("mac"):
            normalized = _normalize_client(client)
            if normalized:
                mac_lookup.setdefault(normalized["mac"], normalized)
    return mac_lookup


def _tracked_clients_from_selection(
    selected_macs: list[str],
    mac_lookup: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    """Return config-entry client records for selected MAC addresses."""
    tracked_clients: list[dict[str, str]] = []
    seen: set[str] = set()
    for mac in selected_macs:
        normalized = normalize_mac(mac)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        tracked_clients.append(
            mac_lookup.get(normalized, {"mac": normalized, "ip": "", "name": ""})
        )
    return tracked_clients


def _client_options_with_offline_tracked(
    available_clients: list[dict[str, str]],
    tracked_clients: list[Any],
) -> dict[str, str]:
    """Return selection options including previously tracked offline clients."""
    client_options = _client_options(available_clients)
    for mac, label, initial_ip in iter_tracked_clients(
        SimpleNamespace(data={CONF_TRACKED_CLIENTS: tracked_clients})
    ):
        if mac not in client_options:
            client_options[mac] = _client_label(
                {"mac": mac, "name": label, "ip": initial_ip or ""},
                offline=True,
            )
    return dict(sorted(client_options.items(), key=lambda item: item[1].lower()))


def _normalized_clients(clients: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return compact tracked-client records from raw router client data."""
    return [
        client
        for client in (_normalize_client(c) for c in clients)
        if client is not None
    ]


async def _async_optional_clients(
    client: KeeneticClient,
    *,
    log_context: str,
) -> list[dict[str, Any]]:
    """Fetch clients while soft-failing only on expected transport/payload errors."""
    try:
        available_clients = await client.async_get_clients()
    except asyncio.CancelledError:
        raise
    except KeeneticAuthError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, KeeneticApiError, ValueError, TypeError, KeyError) as err:
        _LOGGER.debug("Could not fetch clients for %s: %s", log_context, err)
        return []

    if not available_clients and not getattr(client, "_authenticated", True):
        raise KeeneticAuthError("Authentication rejected while fetching clients")

    return available_clients


def _connection_mode(defaults: dict[str, Any]) -> str:
    """Return a valid connection mode for config data."""
    mode = defaults.get(CONF_CONNECTION_MODE, CONNECTION_MODE_DIRECT)
    if mode == CONNECTION_MODE_KEENDNS_PROTECTED:
        return CONNECTION_MODE_KEENDNS_PROTECTED
    return CONNECTION_MODE_DIRECT


def _connection_defaults(defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return form defaults adjusted for the selected connection mode."""
    defaults = dict(defaults or {})
    mode = _connection_mode(defaults)
    defaults[CONF_CONNECTION_MODE] = mode
    if mode == CONNECTION_MODE_KEENDNS_PROTECTED:
        defaults.setdefault(CONF_PORT, 443)
        defaults.setdefault(CONF_SSL, True)
        defaults.setdefault(CONF_USE_CHALLENGE_AUTH, False)
    else:
        defaults.setdefault(CONF_PORT, DEFAULT_PORT)
        defaults.setdefault(CONF_SSL, DEFAULT_SSL)
    return defaults


def _normalize_connection_data(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize a submitted connection form before storing or connecting."""
    normalized = dict(data)
    mode = _connection_mode(normalized)

    # The form initially shows direct defaults. If the user switches to
    # KeenDNS protected mode and leaves those untouched, use the tested
    # protected-access defaults.
    port = normalized.get(CONF_PORT, DEFAULT_PORT)
    ssl = normalized.get(CONF_SSL, DEFAULT_SSL)
    try:
        port = int(port)
    except (TypeError, ValueError) as err:
        raise KeeneticApiError("Port must be between 1 and 65535") from err
    if mode == CONNECTION_MODE_KEENDNS_PROTECTED:
        if port == DEFAULT_PORT:
            port = 443
        if bool(ssl) == DEFAULT_SSL:
            ssl = True

    target = normalize_connection_target(
        normalized[CONF_HOST],
        port,
        bool(ssl),
    )
    if mode == CONNECTION_MODE_KEENDNS_PROTECTED and not target.ssl:
        raise KeeneticApiError(
            "KeenDNS protected web app mode requires external HTTPS"
        )
    normalized[CONF_HOST] = target.host
    normalized[CONF_PORT] = target.port
    normalized[CONF_SSL] = target.ssl
    normalized[CONF_CONNECTION_MODE] = mode
    if mode == CONNECTION_MODE_KEENDNS_PROTECTED:
        normalized[CONF_USE_CHALLENGE_AUTH] = bool(
            normalized.get(CONF_USE_CHALLENGE_AUTH, False)
        )
    return normalized


def _connection_schema(
    defaults: dict[str, Any] | None = None,
    *,
    validate_port: bool = False,
    include_mode: bool = True,
) -> vol.Schema:
    """Build the shared connection schema for setup, reauth and reconfigure."""
    defaults = _connection_defaults(defaults)
    fields: dict[Any, Any] = {
        vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "192.168.1.1")): str  # NOSONAR python:S1313 — Keenetic factory-default discovery sentinel.
    }
    if include_mode:
        fields[
            vol.Optional(
                CONF_CONNECTION_MODE,
                default=defaults.get(CONF_CONNECTION_MODE, CONNECTION_MODE_DIRECT),
            )
        ] = _CONNECTION_MODE_SELECTOR
    if defaults[CONF_CONNECTION_MODE] == CONNECTION_MODE_DIRECT:
        port_validator: Any = int
        if validate_port:
            port_validator = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))
        fields[vol.Optional(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT))] = port_validator
    fields[vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "admin"))] = str
    fields[vol.Required(CONF_PASSWORD)] = _PASSWORD_SELECTOR
    if defaults[CONF_CONNECTION_MODE] == CONNECTION_MODE_DIRECT:
        fields[vol.Optional(CONF_SSL, default=defaults.get(CONF_SSL, DEFAULT_SSL))] = bool
        fields[
            vol.Optional(
                CONF_USE_CHALLENGE_AUTH,
                default=defaults.get(CONF_USE_CHALLENGE_AUTH, False),
            )
        ] = bool
    return vol.Schema(fields)


def _mode_schema(default_mode: str = CONNECTION_MODE_DIRECT) -> vol.Schema:
    """Return the mode-only schema used before showing mode-specific fields."""
    return vol.Schema(
        {
            vol.Required(
                CONF_CONNECTION_MODE,
                default=default_mode,
            ): _CONNECTION_MODE_SELECTOR
        }
    )


def _reauth_schema(entry_data: dict[str, Any]) -> vol.Schema:
    """Build the credential update schema for the entry's connection mode."""
    fields: dict[Any, Any] = {
        vol.Required(
            CONF_USERNAME,
            default=entry_data.get(CONF_USERNAME, "admin"),
        ): str,
        vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
    }
    if _connection_mode(entry_data) == CONNECTION_MODE_DIRECT:
        fields[
            vol.Optional(
                CONF_USE_CHALLENGE_AUTH,
                default=entry_data.get(CONF_USE_CHALLENGE_AUTH, False),
            )
        ] = bool
    return vol.Schema(fields)


class KeeneticRouterProConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Keenetic Router Pro config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_host: str | None = None
        self._discovered_name: str | None = None
        self._available_clients: list[dict[str, Any]] = []
        self._user_input: dict[str, Any] = {}
        self._title: str = ""
        self._selected_connection_mode: str = CONNECTION_MODE_DIRECT

    async def _async_connect(
        self, data: dict[str, Any]
    ) -> tuple[KeeneticClient, dict[str, Any], dict[str, Any]]:
        """Connect to the router and return client plus core identity data."""
        session = async_get_clientsession(self.hass)
        client = KeeneticClient(
            host=data[CONF_HOST],
            username=data[CONF_USERNAME],
            password=data[CONF_PASSWORD],
            port=data[CONF_PORT],
            ssl=data[CONF_SSL],
            use_challenge_auth=data.get(CONF_USE_CHALLENGE_AUTH, False),
        )
        await client.async_start(session)
        system_info = await client.async_get_system_info()
        interfaces = await client.async_get_interfaces()
        return client, system_info, interfaces

    async def _async_validate_and_update(
        self,
        entry: config_entries.ConfigEntry,
        new_data: dict[str, Any],
        log_context: str,
    ) -> dict[str, str] | None:
        """Validate new data against the router. Returns error dict or None on success.

        On success, callers are expected to follow up with
        ``async_update_reload_and_abort`` so HA persists the new data,
        reloads the integration, and aborts the flow in one step. The
        previous pattern (``async_update_entry`` + manual abort + a
        background reload triggered by an update listener) is deprecated.
        """
        try:
            await self._async_connect(new_data)
        except KeeneticAuthError:
            return {"base": "invalid_auth"}
        except KeeneticApiError:
            return {"base": "cannot_connect"}
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during %s", log_context)
            return {"base": "unknown"}

        return None

    @staticmethod
    def _unique_id_from_router(
        system_info: dict[str, Any],
        interfaces: dict[str, Any],
        host: str,
    ) -> tuple[str, str]:
        """Return the HA unique id and title for a router."""
        mac = None
        if isinstance(interfaces, dict):
            candidates = list(interfaces.items())
            bridge_candidates = [
                item for item in candidates
                if isinstance(item[1], dict)
                and (item[1].get("type") == "Bridge" or "Bridge0" in item[0])
            ]
            for _iface_id, iface_data in bridge_candidates + candidates:
                if not isinstance(iface_data, dict):
                    continue
                mac = iface_data.get("mac")
                if mac and mac != "00:00:00:00:00:00":
                    break

        vendor = system_info.get("vendor", "Keenetic")
        device = system_info.get("device", system_info.get("model", "Router"))
        if mac:
            formatted_mac = format_mac(mac).replace(":", "")
            suffix = formatted_mac[-8:] if len(formatted_mac) >= 8 else formatted_mac
            return f"{vendor} {device} {suffix}", f"{vendor} {device}"

        hostname = system_info.get("hostname", host)
        return f"{vendor} {device} {hostname}", f"{vendor} {device}"

    async def async_step_ssdp(self, discovery_info: SsdpServiceInfo) -> FlowResult:
        """Handle a discovered Keenetic router via SSDP."""
        _LOGGER.debug("SSDP discovery received")
        
        hostname = urlparse(discovery_info.ssdp_location).hostname
        if not hostname:
            _LOGGER.debug("No hostname in SSDP discovery, aborting")
            return self.async_abort(reason="no_host")

        current_entries = self._async_current_entries()
        _LOGGER.debug(
            "Checking %d existing entries for host %s",
            len(current_entries),
            mask_identifier(hostname),
        )
        
        for entry in current_entries:
            entry_host = entry.data.get(CONF_HOST)
            _LOGGER.debug(
                "Entry %s has host %s",
                mask_identifier(entry.title),
                mask_identifier(entry_host),
            )
            if entry_host == hostname:
                _LOGGER.debug(
                    "Router at %s is already configured as %s, skipping SSDP",
                    mask_identifier(hostname),
                    mask_identifier(entry.title),
                )
                return self.async_abort(reason="already_configured")
        
        self._discovered_host = hostname
        self._discovered_name = discovery_info.upnp.get("friendlyName", _DEFAULT_DEVICE_NAME)

        self.context["title_placeholders"] = {
            "name": self._discovered_name,
            "host": hostname
        }

        _LOGGER.debug(
            "Discovered Keenetic router via SSDP: %s at %s",
            mask_identifier(self._discovered_name),
            mask_identifier(hostname),
        )
        
        return await self.async_step_user()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Select the connection mode before showing mode-specific fields."""
        if user_input is not None:
            self._selected_connection_mode = _connection_mode(user_input)
            return await self.async_step_connection()

        return self.async_show_form(
            step_id="user",
            data_schema=_mode_schema(self._selected_connection_mode),
            description_placeholders={"name": self._discovered_name}
            if self._discovered_name
            else None,
        )

    async def async_step_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle mode-specific connection settings."""
        errors: dict[str, str] = {}

        _LOGGER.debug("Step connection called with input=%s", user_input is not None)

        if user_input is not None:
            try:
                data = {
                    CONF_CONNECTION_MODE: self._selected_connection_mode,
                    **dict(user_input),
                }
                if self._discovered_host and data.get(CONF_HOST) == "192.168.1.1":  # NOSONAR python:S1313 — Keenetic factory-default discovery sentinel.
                    data[CONF_HOST] = self._discovered_host
                    _LOGGER.debug(
                        "Using discovered host %s",
                        mask_identifier(data[CONF_HOST]),
                    )
                data = _normalize_connection_data(data)
                
                _LOGGER.debug(
                    "Attempting to connect to router at %s:%s",
                    mask_identifier(data[CONF_HOST]),
                    data[CONF_PORT],
                )
                
                client, system_info, interfaces = await self._async_connect(data)
                unique_id, title = self._unique_id_from_router(
                    system_info, interfaces, data[CONF_HOST]
                )
                
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                self._user_input = data
                self._title = title
                
                available_clients = await _async_optional_clients(
                    client,
                    log_context="setup",
                )
                _LOGGER.debug(
                    "Found %d clients",
                    len(available_clients) if available_clients else 0,
                )

                self._available_clients = _normalized_clients(available_clients)
                if self._available_clients:
                    return await self.async_step_select_clients()

                _LOGGER.debug("No clients found, creating entry directly")
                return self.async_create_entry(
                    title=self._title,
                    data={**data, CONF_TRACKED_CLIENTS: []},
                )

            except KeeneticAuthError:
                errors["base"] = "invalid_auth"
            except KeeneticApiError:
                errors["base"] = "cannot_connect"
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = "unknown"

        default_host = (
            "rsi.example.keenetic.pro"
            if self._selected_connection_mode == CONNECTION_MODE_KEENDNS_PROTECTED
            else self._discovered_host or "192.168.1.1"  # NOSONAR python:S1313 — Keenetic factory-default discovery sentinel.
        )
        defaults = _connection_defaults(
            {
                CONF_CONNECTION_MODE: self._selected_connection_mode,
                CONF_HOST: default_host,
            }
        )
        
        return self.async_show_form(
            step_id="connection",
            data_schema=_connection_schema(defaults, include_mode=False),
            errors=errors,
            description_placeholders={"name": self._discovered_name}
            if self._discovered_name
            else None,
        )

    async def async_step_select_clients(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select clients to track."""
        _LOGGER.debug("Step select_clients called (input present: %s)", user_input is not None)

        if user_input is not None:
            selected_macs = user_input.get("tracked_clients", [])
            _LOGGER.debug("Selected %d tracked clients", len(selected_macs))
            
            # Filter selected clients
            tracked_clients = [
                client for client in self._available_clients
                if client["mac"] in selected_macs
            ]
            
            _LOGGER.debug(
                "Creating entry with title %s",
                mask_identifier(self._title),
            )
            return self.async_create_entry(
                title=self._title,
                data={**self._user_input, CONF_TRACKED_CLIENTS: tracked_clients},
            )
        
        client_options = _client_options(self._available_clients)
        
        _LOGGER.debug("Showing client selection form with %d options", len(client_options))
        
        return self.async_show_form(
            step_id="select_clients",
            data_schema=vol.Schema(
                {
                    vol.Optional("tracked_clients", default=[]): cv.multi_select(client_options),
                }
            ),
            description_placeholders={
                "client_count": str(len(client_options)),
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Start reauthentication when HA reports rejected credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user update credentials for an existing entry."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}
        entry_data = dict(entry.data)

        if user_input is not None:
            new_data = {**entry_data, **user_input}
            errors = await self._async_validate_and_update(entry, new_data, "reauth") or {}
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data=new_data,
                    reason="reauth_successful",
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_reauth_schema(entry_data),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select the connection mode for an existing entry."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="unknown")

        entry_data = dict(entry.data)
        if user_input is not None:
            self._selected_connection_mode = _connection_mode(user_input)
            return await self.async_step_reconfigure_connection()

        self._selected_connection_mode = _connection_mode(entry_data)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_mode_schema(self._selected_connection_mode),
            errors={},
        )

    async def async_step_reconfigure_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Allow changing router connection settings for an existing entry."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}
        entry_data = dict(entry.data)

        if user_input is not None:
            try:
                new_data = _normalize_connection_data(
                    {
                        **entry_data,
                        CONF_CONNECTION_MODE: self._selected_connection_mode,
                        **user_input,
                    }
                )
            except KeeneticApiError:
                # Invalid host/scheme/port/KeenDNS combination — surface as a
                # form error instead of raising out of the flow.
                errors["base"] = "cannot_connect"
            else:
                errors = await self._async_validate_and_update(
                    entry, new_data, "reconfigure"
                ) or {}
                if not errors:
                    return self.async_update_reload_and_abort(
                        entry,
                        data=new_data,
                        reason="reconfigure_successful",
                    )

        defaults = _connection_defaults(
            {
                **entry_data,
                CONF_CONNECTION_MODE: self._selected_connection_mode,
            }
        )

        return self.async_show_form(
            step_id="reconfigure_connection",
            data_schema=_connection_schema(
                defaults,
                validate_port=True,
                include_mode=False,
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Options flow handler."""
        return KeeneticOptionsFlow(config_entry)


class KeeneticOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Keenetic Router Pro."""
    
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._available_clients = []

    def _runtime_client(self) -> Any | None:
        """Return the active integration client when options run during normal operation."""
        runtime = getattr(self._config_entry, "runtime_data", None)
        client = getattr(runtime, "client", None)
        return client if hasattr(client, "async_get_clients") else None
    
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options."""
        _LOGGER.debug("Options flow init called with input=%s", user_input is not None)
        
        if user_input is not None:
            selected_macs = user_input.get("tracked_clients", [])
            current_tracked = self._config_entry.data.get(CONF_TRACKED_CLIENTS, [])
            tracked_clients = _tracked_clients_from_selection(
                selected_macs,
                _tracked_client_lookup(self._available_clients, current_tracked),
            )
            
            # Update configuration only when the selection actually changed —
            # an unchanged save should not trigger a reload of the integration.
            new_data = dict(self._config_entry.data)
            if new_data.get(CONF_TRACKED_CLIENTS) != tracked_clients:
                new_data[CONF_TRACKED_CLIENTS] = tracked_clients
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data=new_data,
                )
                _LOGGER.debug(
                    "Updated configuration with %d tracked clients",
                    len(tracked_clients),
                )
            return self.async_create_entry(
                title="",
                data={},
            )
        
        # Get current tracked clients
        current_tracked = self._config_entry.data.get(CONF_TRACKED_CLIENTS, [])
        current_macs = {
            mac
            for c in current_tracked
            if isinstance(c, dict) and (mac := normalize_mac(c.get("mac")))
        }
        _LOGGER.debug("Loaded %d existing tracked MACs", len(current_macs))
        
        # Try to get current clients from router
        client = self._runtime_client()
        available_clients: list[dict[str, Any]] = []
        if client is None:
            data = self._config_entry.data
            session = async_get_clientsession(self.hass)
            try:
                client = KeeneticClient(
                    host=data[CONF_HOST],
                    username=data[CONF_USERNAME],
                    password=data[CONF_PASSWORD],
                    port=data.get(CONF_PORT, DEFAULT_PORT),
                    ssl=data.get(CONF_SSL, DEFAULT_SSL),
                    use_challenge_auth=data.get(CONF_USE_CHALLENGE_AUTH, False),
                )
                await client.async_start(session)
            except (KeeneticAuthError, KeeneticApiError) as err:
                # Integration not loaded and the router is offline or rejecting
                # credentials — keep the options form working from the preserved
                # tracked-client list instead of raising out of the flow.
                _LOGGER.debug("Options flow could not reach router: %s", err)
                client = None

        if client is not None:
            available_clients = await _async_optional_clients(
                client,
                log_context="options",
            )
        _LOGGER.debug(
            "Found %d clients from router",
            len(available_clients) if available_clients else 0,
        )

        self._available_clients = _normalized_clients(available_clients)
        client_options = _client_options_with_offline_tracked(
            self._available_clients,
            current_tracked,
        )
        _LOGGER.debug("Prepared %d client options", len(client_options))
        
        # Show form
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional("tracked_clients", default=list(current_macs)): cv.multi_select(client_options),
                }
            ),
            description_placeholders={
                "client_count": str(len(client_options)),
            },
        )
