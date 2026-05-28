"""Config flow SSDP discovery tests."""

from __future__ import annotations

from conftest import TEST_BASE_URL, TEST_HOST

from types import SimpleNamespace

from homeassistant.const import CONF_HOST

from custom_components.keenetic_router_pro.config_flow import KeeneticRouterProConfigFlow


def _flow(entries=None) -> KeeneticRouterProConfigFlow:
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
    flow.async_abort = lambda **kwargs: {
        "type": "abort",
        "reason": kwargs.get("reason"),
    }
    flow._async_current_entries = lambda: list(entries or [])
    return flow


def _discovery(location: str, **upnp: str) -> SimpleNamespace:
    return SimpleNamespace(ssdp_location=location, upnp=upnp)


async def test_ssdp_keenetic_manufacturer_match_extracts_hostname() -> None:
    flow = _flow()

    result = await flow.async_step_ssdp(
        _discovery(
            f"{TEST_BASE_URL}:1900/rootDesc.xml",
            manufacturer="Keenetic Ltd.",
            friendlyName="Keenetic Giga",
        )
    )

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {}
    assert flow._discovered_host == TEST_HOST
    assert flow._discovered_name == "Keenetic Giga"
    assert flow.context["title_placeholders"] == {
        "name": "Keenetic Giga",
        "host": TEST_HOST,
    }


async def test_ssdp_aborts_when_location_has_no_hostname() -> None:
    result = await _flow().async_step_ssdp(
        _discovery("urn:schemas-upnp-org:device:InternetGatewayDevice:1")
    )

    assert result == {"type": "abort", "reason": "no_host"}


async def test_ssdp_aborts_when_host_already_configured() -> None:
    entry = SimpleNamespace(title="Keenetic Giga", data={CONF_HOST: TEST_HOST})

    result = await _flow([entry]).async_step_ssdp(
        _discovery(
            f"{TEST_BASE_URL}/rootDesc.xml",
            manufacturer="Keenetic",
            friendlyName="Keenetic Giga",
        )
    )

    assert result == {"type": "abort", "reason": "already_configured"}
