"""Unit tests for lightweight Keenetic API helpers."""

from __future__ import annotations

import asyncio
import base64

import pytest

from custom_components.keenetic_router_pro.api import (
    KeeneticApiError,
    KeeneticClient,
    _payload_summary,
    _response_summary,
    _validate_cli_arg,
    normalize_connection_target,
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


def test_connection_target_preserves_direct_defaults() -> None:
    """Bare direct hosts keep the configured port and scheme."""
    target = normalize_connection_target("192.0.2.1", 100, False)

    assert target.host == "192.0.2.1"
    assert target.port == 100
    assert target.ssl is False
    assert target.base_url == "http://192.0.2.1:100"


def test_connection_target_normalizes_keendns_url() -> None:
    """Full HTTPS URLs are accepted for KeenDNS protected web apps."""
    target = normalize_connection_target(
        "https://rsi.example.keenetic.pro",
        443,
        True,
    )

    assert target.host == "rsi.example.keenetic.pro"
    assert target.port == 443
    assert target.ssl is True
    assert target.base_url == "https://rsi.example.keenetic.pro:443"


def test_connection_target_uses_url_port_and_scheme() -> None:
    """URL port and scheme override the separately supplied defaults."""
    target = normalize_connection_target(
        "https://rsi.example.keenetic.pro:8443",
        100,
        False,
    )

    assert target.host == "rsi.example.keenetic.pro"
    assert target.port == 8443
    assert target.ssl is True


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "https://rsi.example.keenetic.pro/rci/show/version",
        "https://rsi.example.keenetic.pro?x=1",
        "ftp://rsi.example.keenetic.pro",
        "bad host",
        "https://rsi.example.keenetic.pro:99999",
    ],
)
def test_connection_target_rejects_invalid_hosts(raw: str) -> None:
    """Unsafe or ambiguous connection targets fail before requests are made."""
    with pytest.raises(KeeneticApiError):
        normalize_connection_target(raw, 443, True)


def test_502_response_mentions_keendns_upstream() -> None:
    """Bad Gateway errors point users at protected web-app upstream setup."""

    class FakeResponse:
        status = 502

        async def text(self) -> str:
            return "<html><h1>502 Bad Gateway</h1></html>"

    client = KeeneticClient("rsi.example.keenetic.pro", "admin", "secret", 443, True)

    with pytest.raises(KeeneticApiError, match="internal published application"):
        asyncio.run(client._handle_response(FakeResponse(), "/rci/show/version"))


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


def test_parse_ipsec_vici_diagnostics_counts_recent_memory_errors() -> None:
    """IPsec VICI memory errors are summarized from router log lines."""
    lines = [
        "May 6 23:03:32 ndhcpc GigabitEthernet0/Vlan5: received ACK",
        "May 6 23:04:13 ndm IpSec::Vici::Stats: out of memory [0xcffe02a7].",
        "May 6 23:05:12 ndm IpSec::Vici::Stats: out of memory [0xcffe02a7].",
    ]

    summary = KeeneticClient._parse_ipsec_vici_diagnostics(lines)

    assert summary["status"] == "warning"
    assert summary["vici_out_of_memory_count"] == 2
    assert summary["last_error_code"] == "0xcffe02a7"
    assert summary["scanned_log_lines"] == 3


def test_parse_ipsec_vici_diagnostics_reports_ok_without_errors() -> None:
    """Normal logs produce an OK diagnostic state."""
    summary = KeeneticClient._parse_ipsec_vici_diagnostics(["normal log line"])

    assert summary["status"] == "ok"
    assert summary["vici_out_of_memory_count"] == 0
    assert summary["last_vici_out_of_memory"] is None


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
