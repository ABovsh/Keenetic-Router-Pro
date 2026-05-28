"""Config flow runtime branch coverage."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)

import custom_components.keenetic_router_pro.config_flow as config_flow
from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticAuthError
from custom_components.keenetic_router_pro.config_flow import (
    KeeneticOptionsFlow,
    KeeneticRouterProConfigFlow,
    _connection_defaults,
    _connection_schema,
    _reauth_schema,
    _normalize_connection_data,
)
from custom_components.keenetic_router_pro.const import (
    CONF_CONNECTION_MODE,
    CONF_TRACKED_CLIENTS,
    CONF_USE_CHALLENGE_AUTH,
    CONNECTION_MODE_DIRECT,
    CONNECTION_MODE_KEENDNS_PROTECTED,
)


class ConfigEntries:
    def __init__(self, entry: SimpleNamespace | None = None) -> None:
        self.entry = entry
        self.updated: list[dict[str, object] | None] = []

    def async_get_entry(self, entry_id: str) -> SimpleNamespace | None:
        if self.entry is not None and entry_id == self.entry.entry_id:
            return self.entry
        return None

    def async_update_entry(
        self, entry: SimpleNamespace, *, data: dict[str, object] | None = None
    ) -> None:
        self.updated.append(data)
        entry.data = data or {}


def _entry() -> SimpleNamespace:
    return SimpleNamespace(
        entry_id="entry-1",
        data={
            CONF_CONNECTION_MODE: CONNECTION_MODE_DIRECT,
            CONF_HOST: TEST_HOST,
            CONF_PORT: 80,
            CONF_SSL: False,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: "old",
            CONF_TRACKED_CLIENTS: [],
        },
    )


def _pin_flow(flow: KeeneticRouterProConfigFlow, entries: ConfigEntries) -> None:
    flow.hass = SimpleNamespace(config_entries=entries)
    flow.context = {"entry_id": "entry-1"}
    flow.async_show_form = lambda **kwargs: {
        "type": "form",
        "step_id": kwargs["step_id"],
        "errors": kwargs.get("errors", {}),
        "data_schema": kwargs.get("data_schema"),
    }
    flow.async_abort = lambda **kwargs: {
        "type": "abort",
        "reason": kwargs.get("reason"),
    }

    def update_reload_and_abort(entry, *, data=None, reason=None, **kwargs):
        entries.async_update_entry(entry, data=data)
        return {"type": "abort", "reason": reason, "data": data}

    flow.async_update_reload_and_abort = update_reload_and_abort


def test_connection_helpers_keendns_defaults_and_invalid_port() -> None:
    defaults = _connection_defaults({CONF_CONNECTION_MODE: CONNECTION_MODE_KEENDNS_PROTECTED})
    schema = _connection_schema(defaults)

    assert defaults[CONF_PORT] == 443
    assert defaults[CONF_SSL] is True
    assert CONF_CONNECTION_MODE in str(schema)
    with pytest.raises(KeeneticApiError):
        _normalize_connection_data(
            {
                CONF_CONNECTION_MODE: CONNECTION_MODE_DIRECT,
                CONF_HOST: TEST_HOST,
                CONF_PORT: "bad",
                CONF_SSL: False,
                CONF_USERNAME: TEST_USERNAME,
                CONF_PASSWORD: TEST_PASSWORD,
            }
        )


def test_connection_and_reauth_schemas_keep_password_masked() -> None:
    """Setup/reconfigure/reauth schemas must render password as a masked field."""
    direct_schema = _connection_schema(
        {
            CONF_CONNECTION_MODE: CONNECTION_MODE_DIRECT,
            CONF_HOST: TEST_HOST,
        },
        include_mode=False,
    )
    keendns_schema = _connection_schema(
        {
            CONF_CONNECTION_MODE: CONNECTION_MODE_KEENDNS_PROTECTED,
            CONF_HOST: "rsi.example.keenetic.pro",
        },
        include_mode=False,
    )
    reauth_schema = _reauth_schema(
        {
            CONF_CONNECTION_MODE: CONNECTION_MODE_DIRECT,
            CONF_USERNAME: TEST_USERNAME,
        }
    )

    for schema in (direct_schema, keendns_schema, reauth_schema):
        password_selector = next(
            value
            for key, value in schema.schema.items()
            if getattr(key, "schema", None) == CONF_PASSWORD
        )
        assert password_selector.args[0].kwargs["type"] == "password"

    assert CONF_PORT in str(direct_schema)
    assert CONF_USE_CHALLENGE_AUTH in str(direct_schema)
    assert CONF_PORT not in str(keendns_schema)
    assert CONF_USE_CHALLENGE_AUTH not in str(keendns_schema)


def test_unique_id_from_router_skips_non_dict_interfaces() -> None:
    unique_id, title = KeeneticRouterProConfigFlow._unique_id_from_router(
        {"vendor": "Keenetic", "model": "Hero"},
        {"Bridge0": "bad", "Eth0": {}},
        TEST_HOST,
    )

    assert unique_id == "Keenetic Hero 192.0.2.1"
    assert title == "Keenetic Hero"


async def test_async_connect_constructs_client_and_returns_identity(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class Client:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def async_start(self, session):
            calls.append(("start", session))

        async def async_get_system_info(self):
            return {"hostname": "router"}

        async def async_get_interfaces(self):
            return {"Bridge0": {"mac": "11:22:33:44:55:66"}}

    monkeypatch.setattr(config_flow, "KeeneticClient", Client)
    monkeypatch.setattr(config_flow, "async_get_clientsession", lambda hass: "session")
    flow = KeeneticRouterProConfigFlow()
    flow.hass = SimpleNamespace()

    client, system_info, interfaces = await flow._async_connect(
        {
            CONF_HOST: TEST_HOST,
            CONF_PORT: 80,
            CONF_SSL: False,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_USE_CHALLENGE_AUTH: True,
        }
    )

    assert client is not None
    assert system_info == {"hostname": "router"}
    assert interfaces == {"Bridge0": {"mac": "11:22:33:44:55:66"}}
    assert len(calls) >= 2
    assert calls[0][0] == "init"
    assert calls[1] == ("start", "session")


async def test_connection_step_uses_discovered_host_and_creates_entry_when_clients_fail() -> None:
    """SSDP default-host sentinel should be replaced before entry creation."""
    class Client:
        async def async_get_clients(self):
            raise RuntimeError("optional client list unavailable")

    flow = KeeneticRouterProConfigFlow()
    flow.hass = SimpleNamespace()
    flow.context = {}
    flow._discovered_host = TEST_HOST
    flow._selected_connection_mode = CONNECTION_MODE_DIRECT
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = lambda: None
    flow.async_create_entry = lambda **kwargs: {
        "type": "create_entry",
        "title": kwargs.get("title"),
        "data": kwargs.get("data", {}),
    }
    flow._async_connect = AsyncMock(
        return_value=(
            Client(),
            {"vendor": "Keenetic", "device": "Hero", "hostname": "hero"},
            {"Bridge0": {"type": "Bridge", "mac": "11:22:33:44:55:66"}},
        )
    )

    result = await flow.async_step_connection(
        {
            CONF_HOST: "192.168.1.1",
            CONF_PORT: 80,
            CONF_SSL: False,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
        }
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_HOST] == TEST_HOST
    assert result["data"][CONF_TRACKED_CLIENTS] == []
    flow._async_connect.assert_awaited_once()


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (KeeneticAuthError("bad"), {"base": "invalid_auth"}),
        (KeeneticApiError("offline"), {"base": "cannot_connect"}),
        (RuntimeError("boom"), {"base": "unknown"}),
    ],
)
async def test_async_validate_and_update_error_shape_returns_errors(
    exc: Exception, expected: dict[str, str]
) -> None:
    flow = KeeneticRouterProConfigFlow()
    flow._async_connect = AsyncMock(side_effect=exc)

    assert await flow._async_validate_and_update(_entry(), {}, "test") == expected


async def test_async_validate_and_update_cancelled_error_reraises() -> None:
    flow = KeeneticRouterProConfigFlow()
    flow._async_connect = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await flow._async_validate_and_update(_entry(), {}, "test")


async def test_reconfigure_mode_and_connection_success_updates_entry() -> None:
    entry = _entry()
    entries = ConfigEntries(entry)
    flow = KeeneticRouterProConfigFlow()
    _pin_flow(flow, entries)
    flow._async_connect = AsyncMock(return_value=(None, {}, {}))

    first = await flow.async_step_reconfigure(
        {CONF_CONNECTION_MODE: CONNECTION_MODE_KEENDNS_PROTECTED}
    )
    result = await flow.async_step_reconfigure_connection(
        {
            CONF_HOST: "https://rsi.example.keenetic.pro",
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: "new",
        }
    )

    assert first["step_id"] == "reconfigure_connection"
    assert result["reason"] == "reconfigure_successful"
    assert result["data"][CONF_CONNECTION_MODE] == CONNECTION_MODE_KEENDNS_PROTECTED
    assert result["data"][CONF_SSL] is True


async def test_reconfigure_connection_validation_error_reshows_form() -> None:
    entry = _entry()
    entries = ConfigEntries(entry)
    flow = KeeneticRouterProConfigFlow()
    _pin_flow(flow, entries)
    flow._async_connect = AsyncMock(side_effect=KeeneticAuthError("bad"))

    result = await flow.async_step_reconfigure_connection(
        {
            CONF_HOST: TEST_HOST,
            CONF_PORT: 80,
            CONF_SSL: False,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: "bad",
        }
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_unknown_entry_aborts() -> None:
    flow = KeeneticRouterProConfigFlow()
    _pin_flow(flow, ConfigEntries())

    assert await flow.async_step_reconfigure() == {"type": "abort", "reason": "unknown"}
    assert await flow.async_step_reconfigure_connection() == {
        "type": "abort",
        "reason": "unknown",
    }


async def test_options_flow_cancelled_error_reraises() -> None:
    class RuntimeClient:
        async def async_get_clients(self):
            raise asyncio.CancelledError()

    flow = KeeneticOptionsFlow(
        SimpleNamespace(data={CONF_TRACKED_CLIENTS: []}, runtime_data=SimpleNamespace(client=RuntimeClient()))
    )
    flow.hass = SimpleNamespace(config_entries=ConfigEntries())

    with pytest.raises(asyncio.CancelledError):
        await flow.async_step_init()
