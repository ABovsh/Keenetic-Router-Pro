"""Config flow reauthentication tests."""

from __future__ import annotations

from tests.conftest import TEST_HOST, TEST_USERNAME

from types import SimpleNamespace

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)

from custom_components.keenetic_router_pro.config_flow import KeeneticRouterProConfigFlow
from custom_components.keenetic_router_pro.const import (
    CONF_CONNECTION_MODE,
    CONNECTION_MODE_DIRECT,
)


class ConfigEntries:
    def __init__(self, entry) -> None:
        self.entry = entry
        self.updated = []

    def async_get_entry(self, entry_id):
        if entry_id == self.entry.entry_id:
            return self.entry
        return None

    def async_update_entry(self, entry, *, data=None):
        self.updated.append(data)
        entry.data = data


def _entry() -> SimpleNamespace:
    return SimpleNamespace(
        entry_id="entry-1",
        data={
            CONF_CONNECTION_MODE: CONNECTION_MODE_DIRECT,
            CONF_HOST: TEST_HOST,
            CONF_PORT: 80,
            CONF_SSL: False,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: "old-password",
        },
    )


def _pin_flow_result_helpers(
    flow: KeeneticRouterProConfigFlow,
    config_entries: ConfigEntries,
) -> None:
    flow.async_show_form = lambda **kwargs: {
        "type": "form",
        "step_id": kwargs["step_id"],
        "data_schema": kwargs.get("data_schema"),
        "errors": kwargs.get("errors", {}),
        "description_placeholders": kwargs.get("description_placeholders"),
    }
    flow.async_abort = lambda **kwargs: {
        "type": "abort",
        "reason": kwargs.get("reason"),
    }

    def update_reload_and_abort(entry, *, data=None, reason=None, **kwargs):
        config_entries.async_update_entry(entry, data=data)
        return {"type": "abort", "reason": reason, "data": data}

    flow.async_update_reload_and_abort = update_reload_and_abort


async def test_reauth_accepts_rotated_password_and_updates_entry() -> None:
    entry = _entry()
    config_entries = ConfigEntries(entry)
    flow = KeeneticRouterProConfigFlow()
    flow.hass = SimpleNamespace(config_entries=config_entries)
    flow.context = {"entry_id": "entry-1"}
    _pin_flow_result_helpers(flow, config_entries)
    seen_passwords = []

    async def connect(data):
        seen_passwords.append(data[CONF_PASSWORD])
        return None, {"hostname": "router"}, {}

    flow._async_connect = connect

    shown = await flow.async_step_reauth({"ignored": "by implementation"})
    result = await flow.async_step_reauth_confirm(
        {
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: "new-password",
        }
    )

    assert shown["type"] == "form"
    assert shown["step_id"] == "reauth_confirm"
    assert shown["errors"] == {}
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert seen_passwords == ["new-password"]
    assert config_entries.updated == [
        {
            CONF_CONNECTION_MODE: CONNECTION_MODE_DIRECT,
            CONF_HOST: TEST_HOST,
            CONF_PORT: 80,
            CONF_SSL: False,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: "new-password",
        }
    ]


async def test_reauth_unknown_entry_aborts() -> None:
    flow = KeeneticRouterProConfigFlow()
    config_entries = ConfigEntries(_entry())
    flow.hass = SimpleNamespace(config_entries=config_entries)
    flow.context = {"entry_id": "missing"}
    _pin_flow_result_helpers(flow, config_entries)

    result = await flow.async_step_reauth_confirm()

    assert result == {"type": "abort", "reason": "unknown"}
