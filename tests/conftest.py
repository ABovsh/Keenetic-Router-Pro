"""Test bootstrap for lightweight unit tests.

The parser/helper tests do not need a full Home Assistant installation.
Provide the tiny exception surface imported by the integration API module.
"""

from __future__ import annotations

import sys
import types


class _AttrEnum:
    """Tiny enum-like object that returns stable string values."""

    def __getattr__(self, name: str) -> str:
        return name.lower()


class _Entity:
    """Base stand-in for Home Assistant entity classes."""

    pass


class HomeAssistantError(Exception):
    """Minimal stand-in for homeassistant.exceptions.HomeAssistantError."""


homeassistant = types.ModuleType("homeassistant")
exceptions = types.ModuleType("homeassistant.exceptions")
exceptions.HomeAssistantError = HomeAssistantError


class _ConfigEntryAuthFailed(HomeAssistantError):
    """Stub for homeassistant.exceptions.ConfigEntryAuthFailed."""


class _ConfigEntryNotReady(HomeAssistantError):
    """Stub for homeassistant.exceptions.ConfigEntryNotReady."""


exceptions.ConfigEntryAuthFailed = _ConfigEntryAuthFailed
exceptions.ConfigEntryNotReady = _ConfigEntryNotReady

config_entries = types.ModuleType("homeassistant.config_entries")
config_entries.ConfigEntry = object

core = types.ModuleType("homeassistant.core")
core.HomeAssistant = object


def _callback(func):  # mimic homeassistant.core.callback
    return func


core.callback = _callback

helpers = types.ModuleType("homeassistant.helpers")
helpers.__path__ = []  # mark as package so submodule imports resolve
aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
aiohttp_client.async_get_clientsession = lambda hass: None
helpers.aiohttp_client = aiohttp_client

entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
entity_platform.AddEntitiesCallback = object
helpers.entity_platform = entity_platform

# Stub homeassistant.helpers.config_validation just enough for the
# integration root's CONFIG_SCHEMA helper to import. Real validation is
# never exercised in these unit tests.
config_validation = types.ModuleType("homeassistant.helpers.config_validation")
config_validation.config_entry_only_config_schema = lambda domain: None
helpers.config_validation = config_validation

issue_registry = types.ModuleType("homeassistant.helpers.issue_registry")


class _IssueSeverity:
    WARNING = "warning"
    ERROR = "error"


issue_registry.IssueSeverity = _IssueSeverity
issue_registry.async_create_issue = lambda *a, **kw: None
issue_registry.async_delete_issue = lambda *a, **kw: None
helpers.issue_registry = issue_registry


class _DataUpdateCoordinator:
    def __class_getitem__(cls, item):  # support Generic subscript
        return cls

    def __init__(self, *args, **kwargs):
        self.data = None

    def async_add_listener(self, *_a, **_kw):
        return lambda: None

    async def async_config_entry_first_refresh(self):
        return None

    async def async_refresh(self):
        return None


class _UpdateFailed(Exception):
    pass


class _CoordinatorEntity:
    """Stub mirroring just enough of CoordinatorEntity for unit tests."""

    def __init__(self, coordinator, *_a, **_kw):
        self.coordinator = coordinator

    def __class_getitem__(cls, item):
        return cls

    def _handle_coordinator_update(self) -> None:
        return None

    def async_write_ha_state(self) -> None:
        return None


update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
update_coordinator.DataUpdateCoordinator = _DataUpdateCoordinator
update_coordinator.UpdateFailed = _UpdateFailed
update_coordinator.CoordinatorEntity = _CoordinatorEntity
helpers.update_coordinator = update_coordinator

device_registry = types.ModuleType("homeassistant.helpers.device_registry")
device_registry.DeviceInfo = dict
device_registry.format_mac = lambda mac: str(mac).lower()
helpers.device_registry = device_registry

components = types.ModuleType("homeassistant.components")
components.__path__ = []
sensor = types.ModuleType("homeassistant.components.sensor")
sensor.SensorEntity = _Entity
sensor.SensorDeviceClass = _AttrEnum()
sensor.SensorStateClass = _AttrEnum()
components.sensor = sensor


def _async_redact_data(data, to_redact):
    """Recursive redactor matching HA's behaviour for test inputs."""
    lowered = {str(key).lower() for key in to_redact}
    if isinstance(data, dict):
        return {
            key: "**REDACTED**"
            if str(key).lower() in lowered
            else _async_redact_data(value, to_redact)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [_async_redact_data(value, to_redact) for value in data]
    return data


diagnostics = types.ModuleType("homeassistant.components.diagnostics")
diagnostics.async_redact_data = _async_redact_data
components.diagnostics = diagnostics

const = types.ModuleType("homeassistant.const")
const.CONF_HOST = "host"
const.CONF_PASSWORD = "password"
const.CONF_USERNAME = "username"
const.PERCENTAGE = "%"
const.EntityCategory = _AttrEnum()
const.UnitOfDataRate = _AttrEnum()
const.UnitOfInformation = _AttrEnum()
const.UnitOfTemperature = _AttrEnum()
const.UnitOfTime = _AttrEnum()

issue_registry = types.ModuleType("homeassistant.helpers.issue_registry")


class _IssueSeverity:
    WARNING = "warning"
    ERROR = "error"


issue_registry.IssueSeverity = _IssueSeverity
issue_registry.async_create_issue = lambda *a, **kw: None
issue_registry.async_delete_issue = lambda *a, **kw: None
helpers.issue_registry = issue_registry
sys.modules.setdefault("homeassistant.helpers.issue_registry", issue_registry)

homeassistant.__path__ = []  # treat as package

homeassistant.exceptions = exceptions
homeassistant.config_entries = config_entries
homeassistant.core = core
homeassistant.helpers = helpers
homeassistant.components = components
homeassistant.const = const

sys.modules.setdefault("homeassistant", homeassistant)
sys.modules.setdefault("homeassistant.exceptions", exceptions)
sys.modules.setdefault("homeassistant.config_entries", config_entries)
sys.modules.setdefault("homeassistant.core", core)
sys.modules.setdefault("homeassistant.helpers", helpers)
sys.modules.setdefault("homeassistant.helpers.aiohttp_client", aiohttp_client)
sys.modules.setdefault("homeassistant.helpers.config_validation", config_validation)
sys.modules.setdefault("homeassistant.helpers.entity_platform", entity_platform)
sys.modules.setdefault("homeassistant.helpers.update_coordinator", update_coordinator)
sys.modules.setdefault("homeassistant.helpers.device_registry", device_registry)
sys.modules.setdefault("homeassistant.components", components)
sys.modules.setdefault("homeassistant.components.sensor", sensor)
sys.modules.setdefault("homeassistant.components.diagnostics", diagnostics)
sys.modules.setdefault("homeassistant.const", const)
