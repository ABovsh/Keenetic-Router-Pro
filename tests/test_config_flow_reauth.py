"""Config flow reauthentication tests."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_USERNAME

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

from custom_components.keenetic_router_pro.config_flow import KeeneticRouterProConfigFlow
from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticAuthError
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
        unique_id="Keenetic Router cc000001",
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


@pytest.mark.parametrize(
    ("entry_unique_id", "system_info", "interfaces", "expected_reason"),
    [
        pytest.param(
            "Keenetic Router CC000001",
            {"vendor": "Updated Vendor", "device": "Updated Device"},
            {"Bridge0": {"mac": "aa:bb:cc:00:00:01"}},
            "reauth_successful",
            id="firmware_strings_changed",
        ),
        pytest.param(
            "Keenetic Router cc000001",
            {},
            {
                "Bridge1": {"type": "Bridge", "mac": "aa:bb:cc:00:00:02"},
                "Bridge0": {"type": "Bridge", "mac": "AA-BB-CC-00-00-01"},
            },
            "reauth_successful",
            id="matching_mac_after_other_bridge",
        ),
        pytest.param(
            "Keenetic Router old-router-hostname",
            {},
            {"Bridge0": {"mac": "aa:bb:cc:00:00:01"}},
            "reauth_successful",
            id="hostname_style_id",
        ),
        pytest.param(
            "Keenetic Router cc000001",
            {"hostname": "router"},
            {
                "Bridge0": {"mac": "00-00-00-00-00-00"},
                "Ethernet0": {"mac": "unknown"},
                "Ethernet1": None,
            },
            "reauth_successful",
            id="no_valid_probe_mac",
        ),
        pytest.param(
            None,
            {},
            {"Bridge0": {"mac": "aa:bb:cc:00:00:01"}},
            "reauth_successful",
            id="no_entry_id",
        ),
        pytest.param(
            "Keenetic Router cc000001",
            {},
            {"Bridge0": {"mac": "aa:bb:cc:00:00:02"}},
            "wrong_router",
            id="different_router",
        ),
    ],
)
async def test_reauth_checks_router_identity_before_updating_password(
    entry_unique_id: str | None,
    system_info: dict,
    interfaces: dict,
    expected_reason: str,
) -> None:
    entry = _entry()
    entry.unique_id = entry_unique_id
    original_data = entry.data.copy()
    config_entries = ConfigEntries(entry)
    flow = KeeneticRouterProConfigFlow()
    flow.hass = SimpleNamespace(config_entries=config_entries)
    flow.context = {"entry_id": "entry-1"}
    _pin_flow_result_helpers(flow, config_entries)
    seen_passwords = []

    async def connect(data):
        seen_passwords.append(data[CONF_PASSWORD])
        return None, system_info, interfaces

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
    assert result["reason"] == expected_reason
    assert seen_passwords == ["new-password"]
    if expected_reason == "wrong_router":
        assert config_entries.updated == []
        assert entry.data == original_data
        return

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


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (KeeneticAuthError("bad auth"), {"base": "invalid_auth"}),
        (KeeneticApiError("offline"), {"base": "cannot_connect"}),
        (RuntimeError("boom"), {"base": "unknown"}),
    ],
)
async def test_reauth_validation_maps_router_errors(
    exc: Exception, expected: dict[str, str]
) -> None:
    flow = KeeneticRouterProConfigFlow()
    flow._async_connect = AsyncMock(side_effect=exc)

    assert await flow._async_validate_and_update(
        _entry(),
        {
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: "new-password",
        },
        "reauth",
    ) == expected


async def test_reauth_unknown_entry_aborts() -> None:
    flow = KeeneticRouterProConfigFlow()
    config_entries = ConfigEntries(_entry())
    flow.hass = SimpleNamespace(config_entries=config_entries)
    flow.context = {"entry_id": "missing"}
    _pin_flow_result_helpers(flow, config_entries)

    result = await flow.async_step_reauth_confirm()

    assert result == {"type": "abort", "reason": "unknown"}
