"""Options flow tests for tracked clients."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

from types import SimpleNamespace

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
)

import custom_components.keenetic_router_pro.config_flow as config_flow
from custom_components.keenetic_router_pro.config_flow import KeeneticOptionsFlow
from custom_components.keenetic_router_pro.const import CONF_TRACKED_CLIENTS


class ConfigEntries:
    def __init__(self) -> None:
        self.updated = []

    def async_update_entry(self, entry, *, data=None):
        self.updated.append(data)
        entry.data = data


def _entry(runtime_client=None) -> SimpleNamespace:
    runtime_data = (
        SimpleNamespace(client=runtime_client) if runtime_client is not None else None
    )
    return SimpleNamespace(
        data={
            CONF_HOST: TEST_HOST,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_TRACKED_CLIENTS: [
                {
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "ip": "192.0.2.10",
                    "name": "Phone",
                },
                {
                    "mac": "33:33:33:33:33:33",
                    "ip": "192.0.2.30",
                    "name": "Tablet",
                },
            ],
        },
        runtime_data=runtime_data,
    )


def _pin_flow_result_helpers(flow: KeeneticOptionsFlow) -> None:
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


async def test_options_flow_reuses_runtime_client_for_available_clients() -> None:
    calls: list[str] = []

    class RuntimeClient:
        async def async_get_clients(self):
            calls.append("runtime")
            return [
                {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "ip": "192.0.2.10",
                    "name": "Phone",
                },
                {
                    "mac": "22:22:22:22:22:22",
                    "ip": "192.0.2.20",
                    "name": "Laptop",
                },
            ]

    flow = KeeneticOptionsFlow(_entry(RuntimeClient()))
    flow.hass = SimpleNamespace(config_entries=ConfigEntries())
    _pin_flow_result_helpers(flow)

    result = await flow.async_step_init()

    assert calls == ["runtime"]
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["errors"] == {}
    assert result["description_placeholders"] == {"client_count": "3"}


async def test_options_flow_adds_and_removes_tracked_clients() -> None:
    class RuntimeClient:
        async def async_get_clients(self):
            return [
                {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "ip": "192.0.2.10",
                    "name": "Phone",
                },
                {
                    "mac": "22:22:22:22:22:22",
                    "ip": "192.0.2.20",
                    "name": "Laptop",
                },
            ]

    config_entries = ConfigEntries()
    entry = _entry(RuntimeClient())
    flow = KeeneticOptionsFlow(entry)
    flow.hass = SimpleNamespace(config_entries=config_entries)
    _pin_flow_result_helpers(flow)

    await flow.async_step_init()
    result = await flow.async_step_init(
        {
            "tracked_clients": [
                "22:22:22:22:22:22",
                "33:33:33:33:33:33",
            ]
        }
    )

    assert result == {"type": "create_entry", "title": "", "data": {}}
    assert config_entries.updated == [
        {
            CONF_HOST: TEST_HOST,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_TRACKED_CLIENTS: [
                {
                    "mac": "22:22:22:22:22:22",
                    "ip": "192.0.2.20",
                    "name": "Laptop",
                },
                {
                    "mac": "33:33:33:33:33:33",
                    "ip": "192.0.2.30",
                    "name": "Tablet",
                },
            ],
        }
    ]


async def test_options_flow_creates_temporary_client_without_runtime_client(
    monkeypatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class TemporaryClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def async_start(self, session):
            calls.append(("start", session))

        async def async_get_clients(self):
            calls.append(("clients", None))
            return [
                {
                    "mac": "22:22:22:22:22:22",
                    "ip": "192.0.2.20",
                    "name": "Laptop",
                }
            ]

    monkeypatch.setattr(config_flow, "KeeneticClient", TemporaryClient)
    entry = _entry()
    flow = KeeneticOptionsFlow(entry)
    flow.hass = SimpleNamespace(config_entries=ConfigEntries())
    _pin_flow_result_helpers(flow)

    result = await flow.async_step_init()

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert calls == [
        (
            "init",
            {
                "host": TEST_HOST,
                "username": TEST_USERNAME,
                "password": TEST_PASSWORD,
                "port": 100,
                "ssl": False,
                "use_challenge_auth": False,
            },
        ),
        ("start", None),
        ("clients", None),
    ]


async def test_options_flow_falls_back_to_tracked_clients_when_fetch_fails() -> None:
    class RuntimeClient:
        async def async_get_clients(self):
            raise RuntimeError("router unavailable")

    flow = KeeneticOptionsFlow(_entry(RuntimeClient()))
    flow.hass = SimpleNamespace(config_entries=ConfigEntries())
    _pin_flow_result_helpers(flow)

    result = await flow.async_step_init()

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["description_placeholders"] == {"client_count": "2"}
