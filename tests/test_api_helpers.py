"""Unit tests for lightweight Keenetic API helpers."""

from __future__ import annotations

import base64

import pytest

from custom_components.keenetic_router_pro.api import (
    KeeneticApiError,
    KeeneticClient,
    _payload_summary,
    _response_summary,
    _validate_cli_arg,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("GigabitEthernet0", "GigabitEthernet0"),
        (" WifiMaster0/AccessPoint0 ", "WifiMaster0/AccessPoint0"),
        ("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"),
    ],
)
def test_validate_cli_arg_accepts_router_tokens(raw: str, expected: str) -> None:
    """Normal Keenetic identifiers are accepted and trimmed."""
    assert _validate_cli_arg(raw, "token") == expected


@pytest.mark.parametrize("raw", ["", "   ", "ISP\nsystem reboot", "ISP; reboot"])
def test_validate_cli_arg_rejects_injection(raw: str) -> None:
    """Newlines and shell-like separators cannot reach /rci/parse."""
    with pytest.raises(KeeneticApiError):
        _validate_cli_arg(raw, "token")


def test_basic_auth_headers_are_generated_without_mutating_state() -> None:
    """Basic auth helper builds the expected header and no extra state."""
    client = KeeneticClient("192.0.2.1", "admin", "secret")

    headers = client._basic_auth_headers()

    expected = base64.b64encode(b"admin:secret").decode()
    assert headers == {"Authorization": f"Basic {expected}"}
    assert client._auth_header is None


def test_response_summary_redacts_obvious_secrets() -> None:
    """Router error excerpts should not expose credentials in logs/errors."""
    summary = _response_summary(
        '{"password": "secret", "cookie": "session=abc", "message": "failed"}'
    )

    assert "secret" not in summary
    assert "session=abc" not in summary
    assert "<redacted>" in summary


def test_payload_summary_redacts_sensitive_keys() -> None:
    """Debug request logging keeps shape while hiding secret values."""
    summary = _payload_summary(
        {"login": "admin", "password": "secret", "components": [{"name": "base"}]}
    )

    assert summary == {
        "login": "str",
        "password": "<redacted>",
        "components": "list",
    }


def test_normalize_interfaces_injects_ids_for_dict_payloads() -> None:
    """Dict-shaped /show/interface payloads keep their interface id."""
    client = KeeneticClient("192.0.2.1", "admin", "secret")

    result = client._normalize_interfaces(
        {
            "Bridge0": {"type": "Bridge", "mac": "aa:bb:cc:dd:ee:ff"},
            "bad": "ignored",
        }
    )

    assert result == [
        {"type": "Bridge", "mac": "aa:bb:cc:dd:ee:ff", "id": "Bridge0"}
    ]


def test_summarize_client_stats_excludes_extenders() -> None:
    """Client stats count user devices separately from mesh extenders."""
    clients = [
        {"mac": "a", "active": True, "ssid": "Main"},
        {"mac": "b", "active": "no", "interface": "Bridge0"},
        {"mac": "c", "system-mode": "extender", "active": True},
    ]

    summary = KeeneticClient.summarize_client_stats(clients)

    assert summary["connected"] == 1
    assert summary["disconnected"] == 1
    assert summary["total"] == 2
    assert summary["per_ap"] == {"Main": 1}
    assert summary["extender_count"] == 1
