"""Test bootstrap for lightweight unit tests.

The parser/helper tests do not need a full Home Assistant installation.
Provide the tiny exception surface imported by the integration API module.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import types
from types import SimpleNamespace

import pytest

TEST_HOST = "192.0.2.1"  # NOSONAR(python:S1313) - RFC 5737 documentation address, test fixture only
TEST_HOST_ALT = "192.168.1.2"  # NOSONAR(python:S1313) - RFC 1918 LAN address, test fixture only
TEST_BASE_URL = f"http://{TEST_HOST}"  # NOSONAR(python:S5332) - test fixture URL, no real network traffic
TEST_BASE_URL_ALT = f"http://{TEST_HOST_ALT}"  # NOSONAR(python:S5332) - local LAN device
TEST_USERNAME = "admin"  # NOSONAR(python:S2068) - test fixture only
TEST_PASSWORD = "admin"  # NOSONAR(python:S2068) - test fixture only

_ISSUE_REGISTRY_MODULE = "homeassistant.helpers.issue_registry"


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


class _ConfigFlow:
    def __init_subclass__(cls, **kwargs):
        return super().__init_subclass__()

    def __init__(self, *args, **kwargs):
        self.context = {}

    def async_show_form(self, **kwargs):
        return {
            "type": "form",
            "step_id": kwargs["step_id"],
            "data_schema": kwargs.get("data_schema"),
            "errors": kwargs.get("errors", {}),
            "description_placeholders": kwargs.get("description_placeholders"),
        }

    def async_create_entry(self, **kwargs):
        return {
            "type": "create_entry",
            "title": kwargs.get("title"),
            "data": kwargs.get("data", {}),
        }

    def async_abort(self, **kwargs):
        return {"type": "abort", "reason": kwargs.get("reason")}

    async def async_set_unique_id(self, unique_id):
        self._unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        return None

    def _async_current_entries(self):
        config_entries = getattr(getattr(self, "hass", None), "config_entries", None)
        if config_entries is None or not hasattr(config_entries, "async_entries"):
            return []
        return list(config_entries.async_entries())

    def async_update_reload_and_abort(self, entry, *, data=None, reason=None, **kwargs):
        config_entries = getattr(getattr(self, "hass", None), "config_entries", None)
        if config_entries is not None and hasattr(config_entries, "async_update_entry"):
            config_entries.async_update_entry(entry, data=data)
        return {"type": "abort", "reason": reason, "data": data}


class _OptionsFlow:
    def async_show_form(self, **kwargs):
        return {
            "type": "form",
            "step_id": kwargs["step_id"],
            "data_schema": kwargs.get("data_schema"),
            "errors": kwargs.get("errors", {}),
            "description_placeholders": kwargs.get("description_placeholders"),
        }

    def async_create_entry(self, **kwargs):
        return {
            "type": "create_entry",
            "title": kwargs.get("title"),
            "data": kwargs.get("data", {}),
        }


config_entries.ConfigFlow = _ConfigFlow
config_entries.OptionsFlow = _OptionsFlow

data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
data_entry_flow.FlowResult = dict

core = types.ModuleType("homeassistant.core")
core.HomeAssistant = object


def _callback(func):  # mimic homeassistant.core.callback
    return func


core.callback = _callback

util = types.ModuleType("homeassistant.util")
util_logging = types.ModuleType("homeassistant.util.logging")
util_logging.log_exception = lambda *args, **kwargs: None
util.logging = util_logging

helpers = types.ModuleType("homeassistant.helpers")
helpers.__path__ = []  # mark as package so submodule imports resolve
aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
aiohttp_client.async_get_clientsession = lambda hass: None
aiohttp_client._async_make_resolver = lambda *args, **kwargs: None
helpers.aiohttp_client = aiohttp_client

entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
entity_platform.AddEntitiesCallback = object
helpers.entity_platform = entity_platform

# Stub homeassistant.helpers.config_validation just enough for the
# integration root's CONFIG_SCHEMA helper to import. Real validation is
# never exercised in these unit tests.
config_validation = types.ModuleType("homeassistant.helpers.config_validation")
config_validation.config_entry_only_config_schema = lambda domain: None
config_validation.multi_select = lambda options: options
helpers.config_validation = config_validation

selector = types.ModuleType("homeassistant.helpers.selector")


class _SelectorValue:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __call__(self, value):
        return value


class _TextSelectorType:
    PASSWORD = "password"


class _SelectSelectorMode:
    DROPDOWN = "dropdown"


selector.TextSelector = _SelectorValue
selector.TextSelectorConfig = _SelectorValue
selector.TextSelectorType = _TextSelectorType
selector.SelectSelector = _SelectorValue
selector.SelectSelectorConfig = _SelectorValue
selector.SelectSelectorMode = _SelectSelectorMode
selector.SelectOptionDict = lambda **kwargs: dict(kwargs)
helpers.selector = selector

service_info = types.ModuleType("homeassistant.helpers.service_info")
service_info.__path__ = []
ssdp = types.ModuleType("homeassistant.helpers.service_info.ssdp")
ssdp.SsdpServiceInfo = object
service_info.ssdp = ssdp
helpers.service_info = service_info

issue_registry = types.ModuleType(_ISSUE_REGISTRY_MODULE)


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
        self.async_write_ha_state()

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

switch = types.ModuleType("homeassistant.components.switch")
switch.SwitchEntity = _Entity
components.switch = switch

binary_sensor = types.ModuleType("homeassistant.components.binary_sensor")
binary_sensor.BinarySensorEntity = _Entity
binary_sensor.BinarySensorDeviceClass = _AttrEnum()
components.binary_sensor = binary_sensor

button = types.ModuleType("homeassistant.components.button")
button.ButtonEntity = _Entity
components.button = button

update = types.ModuleType("homeassistant.components.update")
update.UpdateEntity = _Entity
update.UpdateDeviceClass = _AttrEnum()


class _UpdateEntityFeature:
    INSTALL = 1
    PROGRESS = 2
    RELEASE_NOTES = 4


update.UpdateEntityFeature = _UpdateEntityFeature
components.update = update

select = types.ModuleType("homeassistant.components.select")
select.SelectEntity = _Entity
components.select = select

device_tracker = types.ModuleType("homeassistant.components.device_tracker")
device_tracker.__path__ = []


class _SourceType:
    ROUTER = "router"


device_tracker.SourceType = _SourceType
device_tracker_config_entry = types.ModuleType(
    "homeassistant.components.device_tracker.config_entry"
)
device_tracker_config_entry.ScannerEntity = _Entity
components.device_tracker = device_tracker


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
const.CONF_PORT = "port"
const.CONF_USERNAME = "username"
const.CONF_SSL = "ssl"
const.PERCENTAGE = "%"
const.EntityCategory = _AttrEnum()
const.UnitOfDataRate = _AttrEnum()
const.UnitOfInformation = _AttrEnum()
const.UnitOfTemperature = _AttrEnum()
const.UnitOfTime = _AttrEnum()

issue_registry = types.ModuleType(_ISSUE_REGISTRY_MODULE)


class _IssueSeverity:
    WARNING = "warning"
    ERROR = "error"


issue_registry.IssueSeverity = _IssueSeverity
issue_registry.async_create_issue = lambda *a, **kw: None
issue_registry.async_delete_issue = lambda *a, **kw: None
helpers.issue_registry = issue_registry
sys.modules[_ISSUE_REGISTRY_MODULE] = issue_registry

homeassistant.__path__ = []  # treat as package

homeassistant.exceptions = exceptions
homeassistant.config_entries = config_entries
homeassistant.core = core
homeassistant.util = util
homeassistant.helpers = helpers
homeassistant.components = components
homeassistant.const = const

sys.modules["homeassistant"] = homeassistant
sys.modules["homeassistant.exceptions"] = exceptions
sys.modules["homeassistant.config_entries"] = config_entries
sys.modules["homeassistant.data_entry_flow"] = data_entry_flow
sys.modules["homeassistant.core"] = core
sys.modules["homeassistant.util"] = util
sys.modules["homeassistant.util.logging"] = util_logging
sys.modules["homeassistant.helpers"] = helpers
sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client
sys.modules["homeassistant.helpers.config_validation"] = config_validation
sys.modules["homeassistant.helpers.selector"] = selector
sys.modules["homeassistant.helpers.service_info"] = service_info
sys.modules["homeassistant.helpers.service_info.ssdp"] = ssdp
sys.modules["homeassistant.helpers.entity_platform"] = entity_platform
sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
sys.modules["homeassistant.helpers.device_registry"] = device_registry
sys.modules["homeassistant.components"] = components
sys.modules["homeassistant.components.sensor"] = sensor
sys.modules["homeassistant.components.switch"] = switch
sys.modules["homeassistant.components.binary_sensor"] = binary_sensor
sys.modules["homeassistant.components.button"] = button
sys.modules["homeassistant.components.update"] = update
sys.modules["homeassistant.components.select"] = select
sys.modules["homeassistant.components.device_tracker"] = device_tracker
sys.modules[
    "homeassistant.components.device_tracker.config_entry"
] = device_tracker_config_entry
sys.modules["homeassistant.components.diagnostics"] = diagnostics
sys.modules["homeassistant.const"] = const


def pytest_pyfunc_call(pyfuncitem):
    """Run async tests without depending on pytest-asyncio for local unit tests."""
    testfunction = pyfuncitem.obj
    if inspect.iscoroutinefunction(testfunction):
        funcargs = {
            name: pyfuncitem.funcargs[name]
            for name in pyfuncitem._fixtureinfo.argnames
        }
        asyncio.run(testfunction(**funcargs))
        return True
    return None


@pytest.fixture
def keenetic_entry() -> SimpleNamespace:
    """Minimal ConfigEntry-shaped object for entity unit tests."""
    return SimpleNamespace(entry_id="entry_123", title="Router", data={})


@pytest.fixture
def keenetic_coordinator_factory():
    """Return lightweight coordinator objects with mutable data."""

    def _factory(data: dict | None = None) -> SimpleNamespace:
        refresh_calls: list[str] = []

        async def async_request_refresh() -> None:
            refresh_calls.append("refresh")

        return SimpleNamespace(
            data=data or {},
            last_update_success=True,
            async_request_refresh=async_request_refresh,
            refresh_calls=refresh_calls,
        )

    return _factory
