"""Config flow user and connection step tests."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_HOST_ALT, TEST_PASSWORD, TEST_USERNAME

from types import SimpleNamespace

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)

from custom_components.keenetic_router_pro.api import KeeneticAuthError
from custom_components.keenetic_router_pro.config_flow import KeeneticRouterProConfigFlow
from custom_components.keenetic_router_pro.const import (
    CONF_CONNECTION_MODE,
    CONNECTION_MODE_DIRECT,
)


def _flow() -> KeeneticRouterProConfigFlow:
    flow = KeeneticRouterProConfigFlow()
    flow.hass = SimpleNamespace()
    flow.context = {}
    flow.async_show_form = lambda **kwargs: {
        "type": "form",
        "step_id": kwargs["step_id"],
        "data_schema": kwargs.get("data_schema"),
        "errors": kwargs.get("errors", {}),
        "description_placeholders": kwargs.get("description_placeholders"),
    }
    flow.async_create_entry = lambda **kwargs: {
        "type": "create_entry",
        "title": kwargs.get("title"),
        "data": kwargs.get("data", {}),
    }

    async def set_unique_id(unique_id):
        flow._unique_id = unique_id

    flow.async_set_unique_id = set_unique_id
    flow._abort_if_unique_id_configured = lambda: None
    return flow


def _connection_input() -> dict[str, object]:
    return {
        CONF_HOST: TEST_HOST,
        CONF_PORT: 80,
        CONF_SSL: False,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
    }


async def test_bad_credentials_reshows_connection_form_with_base_error() -> None:
    flow = _flow()

    async def fail_auth(data):
        raise KeeneticAuthError("bad password")

    flow._async_connect = fail_auth

    result = await flow.async_step_connection(_connection_input())

    assert result["type"] == "form"
    assert result["step_id"] == "connection"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_good_credentials_proceed_to_client_selection_step() -> None:
    flow = _flow()

    class Client:
        async def async_get_clients(self):
            return [
                {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "ip": "192.0.2.10",
                    "name": "Phone",
                }
            ]

    async def connect(data):
        return (
            Client(),
            {"vendor": "Keenetic", "device": "Giga"},
            {"Bridge0": {"type": "Bridge", "mac": "11:22:33:44:55:66"}},
        )

    flow._async_connect = connect

    first = await flow.async_step_user({CONF_CONNECTION_MODE: CONNECTION_MODE_DIRECT})
    result = await flow.async_step_connection(_connection_input())

    assert first["type"] == "form"
    assert first["step_id"] == "connection"
    assert first["errors"] == {}
    assert result["type"] == "form"
    assert result["step_id"] == "select_clients"
    assert result["errors"] == {}


async def test_good_credentials_with_no_clients_create_entry_for_discovered_host() -> None:
    flow = _flow()
    flow._discovered_host = "router.local"

    class Client:
        async def async_get_clients(self):
            return []

    async def connect(data):
        return (
            Client(),
            {"vendor": "Keenetic", "device": "Giga", "hostname": "router"},
            {},
        )

    flow._async_connect = connect

    result = await flow.async_step_connection(
        {
            **_connection_input(),
            CONF_HOST: "192.168.1.1",  # NOSONAR(python:S1313) — matches production discovery-override sentinel
        }
    )

    assert result["type"] == "create_entry"
    assert result["title"] == "Keenetic Giga"
    assert result["data"][CONF_HOST] == "router.local"
    assert result["data"]["tracked_clients"] == []


async def test_client_selection_creates_entry_with_selected_clients() -> None:
    flow = _flow()
    flow._title = "Keenetic Giga"
    flow._user_input = _connection_input()
    flow._available_clients = [
        {"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.0.2.10", "name": "Phone"},
        {"mac": "11:22:33:44:55:66", "ip": "192.0.2.20", "name": "Laptop"},
    ]

    result = await flow.async_step_select_clients(
        {"tracked_clients": ["11:22:33:44:55:66"]}
    )

    assert result["type"] == "create_entry"
    assert result["data"]["tracked_clients"] == [
        {"mac": "11:22:33:44:55:66", "ip": "192.0.2.20", "name": "Laptop"}
    ]
