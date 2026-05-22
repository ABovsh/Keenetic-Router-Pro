"""High-value tests for config and options flow helper behaviour."""

from __future__ import annotations

from tests.conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.keenetic_router_pro.config_flow import (
    KeeneticOptionsFlow,
    _client_options_with_offline_tracked,
    _normalize_connection_data,
    _tracked_client_lookup,
    _tracked_clients_from_selection,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)
from custom_components.keenetic_router_pro.const import (
    CONF_CONNECTION_MODE,
    CONF_TRACKED_CLIENTS,
    CONNECTION_MODE_DIRECT,
    CONNECTION_MODE_KEENDNS_PROTECTED,
)


def test_connection_data_normalizes_keendns_defaults() -> None:
    """Protected KeenDNS mode should force the known-safe HTTPS defaults."""
    data = _normalize_connection_data(
        {
            CONF_CONNECTION_MODE: CONNECTION_MODE_KEENDNS_PROTECTED,
            CONF_HOST: "https://rsi.example.keenetic.pro",
            CONF_PORT: 100,
            CONF_SSL: False,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
        }
    )

    assert data[CONF_HOST] == "rsi.example.keenetic.pro"
    assert data[CONF_PORT] == 443
    assert data[CONF_SSL] is True


def test_connection_data_rejects_non_https_keendns() -> None:
    """Protected web app mode must not silently downgrade to plain HTTP."""
    with pytest.raises(Exception, match="requires external HTTPS"):
        _normalize_connection_data(
            {
                CONF_CONNECTION_MODE: CONNECTION_MODE_KEENDNS_PROTECTED,
                CONF_HOST: "http://rsi.example.keenetic.pro",
                CONF_PORT: 80,
                CONF_SSL: False,
                CONF_USERNAME: TEST_USERNAME,
                CONF_PASSWORD: TEST_PASSWORD,
            }
        )


def test_connection_data_preserves_direct_url_port_and_scheme() -> None:
    """Direct mode accepts a full URL and stores normalized host/port/SSL."""
    data = _normalize_connection_data(
        {
            CONF_CONNECTION_MODE: CONNECTION_MODE_DIRECT,
            CONF_HOST: "https://192.0.2.1:8443",
            CONF_PORT: 100,
            CONF_SSL: False,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
        }
    )

    assert data[CONF_HOST] == TEST_HOST
    assert data[CONF_PORT] == 8443
    assert data[CONF_SSL] is True


def test_offline_tracked_clients_remain_selectable() -> None:
    """Options flow must not drop a tracked device just because it is offline."""
    available = [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.0.2.10", "name": "Phone"}]
    tracked = [
        {"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.0.2.10", "name": "Phone"},
        {"mac": "11:22:33:44:55:66", "ip": "192.0.2.99", "name": "Tablet"},
    ]

    options = _client_options_with_offline_tracked(available, tracked)
    lookup = _tracked_client_lookup(available, tracked)
    selected = _tracked_clients_from_selection(
        ["11:22:33:44:55:66", "77:88:99:aa:bb:cc"],
        lookup,
    )

    assert options["11:22:33:44:55:66"].endswith("[offline]")
    assert selected == [
        {"mac": "11:22:33:44:55:66", "ip": "192.0.2.99", "name": "Tablet"},
        {"mac": "77:88:99:aa:bb:cc", "ip": "", "name": ""},
    ]


def test_tracked_client_selection_normalizes_mac_and_ignores_placeholder_ip() -> None:
    """The same client must not duplicate when routers report different MAC formats."""
    available = [{"mac": "80-07-94-46-AB-AB", "ip": "0.0.0.0", "name": "Phone"}]
    tracked = [{"mac": "80079446abab", "ip": "192.0.2.10", "name": "Phone"}]

    lookup = _tracked_client_lookup(available, tracked)
    selected = _tracked_clients_from_selection(
        ["80:07:94:46:ab:ab", "80079446abab"],
        lookup,
    )

    assert selected == [
        {"mac": "80:07:94:46:ab:ab", "ip": "", "name": "Phone"},
    ]


def test_options_flow_prefers_runtime_client() -> None:
    """Opening options should reuse the running client instead of re-authenticating."""
    calls: list[str] = []

    class RuntimeClient:
        async def async_get_clients(self):
            calls.append("runtime")
            return [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.0.2.10", "name": "Phone"}]

    entry = SimpleNamespace(
        data={
            CONF_HOST: TEST_HOST,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_TRACKED_CLIENTS: [],
        },
        options={"ping_interval": 15},
        runtime_data=SimpleNamespace(client=RuntimeClient()),
    )
    flow = KeeneticOptionsFlow(entry)
    flow.hass = SimpleNamespace()
    flow.async_show_form = lambda **kwargs: kwargs

    result = asyncio.run(flow.async_step_init())

    assert calls == ["runtime"]
    assert result["step_id"] == "init"
    assert "ping_interval" not in str(result["data_schema"])
