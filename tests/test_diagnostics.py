"""Tests for the diagnostics redaction surface.

These tests avoid pulling in a full Home Assistant install. We register
lightweight stubs for the few symbols ``diagnostics.py`` imports from HA,
exercise the real redaction set against a representative payload, and
assert that no credential, MAC, SSID or session cookie survives.
"""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import sys
import types

import pytest


def _install_ha_diagnostics_stub() -> None:
    """Provide a minimal HA stub so diagnostics.py imports cleanly."""
    if "homeassistant.components.diagnostics" in sys.modules:
        return

    def async_redact_data(data, to_redact):
        """Recursive redactor matching HA's behaviour for our test inputs."""
        lowered = {str(k).lower() for k in to_redact}
        if isinstance(data, dict):
            return {
                k: "**REDACTED**"
                if str(k).lower() in lowered
                else async_redact_data(v, to_redact)
                for k, v in data.items()
            }
        if isinstance(data, list):
            return [async_redact_data(v, to_redact) for v in data]
        return data

    components = types.ModuleType("homeassistant.components")
    diagnostics_mod = types.ModuleType("homeassistant.components.diagnostics")
    diagnostics_mod.async_redact_data = async_redact_data
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    const = types.ModuleType("homeassistant.const")
    const.CONF_HOST = "host"
    const.CONF_PASSWORD = "password"
    const.CONF_USERNAME = "username"
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object

    sys.modules.setdefault("homeassistant.components", components)
    sys.modules.setdefault("homeassistant.components.diagnostics", diagnostics_mod)
    sys.modules.setdefault("homeassistant.config_entries", config_entries)
    sys.modules.setdefault("homeassistant.const", const)
    sys.modules.setdefault("homeassistant.core", core)


@pytest.fixture(scope="module", autouse=True)
def _ha_stubs() -> None:
    _install_ha_diagnostics_stub()


def test_redact_set_covers_every_obvious_credential_key() -> None:
    """Every key the integration reads/uses for auth must be in TO_REDACT."""
    from custom_components.keenetic_router_pro.diagnostics import TO_REDACT

    required = {
        "password",
        "username",
        "login",
        "mac",
        "ssid",
        "bssid",
        "psk",
        "cookie",
        "set-cookie",
        "authorization",
        "x-ndm-challenge",
        "token",
        "secret",
    }
    assert required <= TO_REDACT


def test_redaction_strips_credentials_and_network_identifiers() -> None:
    """Apply async_redact_data with the integration's TO_REDACT set."""
    from homeassistant.components.diagnostics import async_redact_data

    from custom_components.keenetic_router_pro.diagnostics import TO_REDACT

    payload = {
        "entry": {
            "data": {
                "host": TEST_HOST,
                "username": TEST_USERNAME,
                "password": "test-password",
                "ssl": False,
                "use_challenge_auth": True,
            },
            "options": {},
        },
        "coordinator_data": {
            "clients": [
                {
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "ip": "192.0.2.55",
                    "ssid": "MyWiFi",
                    "name": "phone",
                }
            ],
            "wireguard": {"peers": [{"psk": "very-secret-psk"}]},
        },
        "raw_headers": {
            "Authorization": "Basic YWRtaW46c2VjcmV0",
            "Cookie": "session=deadbeef",
        },
    }

    redacted = async_redact_data(payload, TO_REDACT)

    flat = repr(redacted)
    for forbidden in (
        "test-password",
        TEST_HOST,
        TEST_USERNAME,
        "aa:bb:cc:dd:ee:ff",
        "MyWiFi",
        "very-secret-psk",
        "Basic YWRtaW46c2VjcmV0",
        "session=deadbeef",
    ):
        assert forbidden not in flat, f"Leaked: {forbidden}"

    assert redacted["entry"]["data"]["password"] == "**REDACTED**"
    assert redacted["entry"]["data"]["ssl"] is False  # non-secret survives


def test_keenetic_client_repr_does_not_leak_credentials() -> None:
    """Defensive: stray repr(client) in logs must not show username/password."""
    from custom_components.keenetic_router_pro.api import KeeneticClient

    client = KeeneticClient(TEST_HOST, TEST_USERNAME, "test-password")
    text = repr(client)

    assert TEST_USERNAME not in text
    assert "test-password" not in text
    assert "<redacted>" in text


def test_log_identifier_masking_hides_full_mac_and_ip() -> None:
    """Info logs should not expose full client MAC/IP identifiers."""
    from custom_components.keenetic_router_pro import _mask_identifier

    assert _mask_identifier("aa:bb:cc:dd:ee:ff") == "...ee:ff"
    assert _mask_identifier("192.168.3.55") == "...3.55"
    assert _mask_identifier("") == "<unknown>"
