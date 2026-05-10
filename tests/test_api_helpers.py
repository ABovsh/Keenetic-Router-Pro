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
    assert summary["recent_matches"] == [
        "May 6 23:04:13 ndm IpSec::Vici::Stats: out of memory [0xcffe02a7].",
        "May 6 23:05:12 ndm IpSec::Vici::Stats: out of memory [0xcffe02a7].",
    ]
    assert summary["scanned_log_lines"] == 3


def test_extract_parse_messages_handles_structured_log_payloads() -> None:
    """Structured show-log payloads are flattened into searchable lines."""
    payload = {
        "log": [
            {
                "level": "C",
                "time": "May 7 00:12:18",
                "module": "ndm",
                "message": "IpSec::Vici::Stats: out of memory [0xcffe02a7].",
            },
            {
                "time": "May 7 00:13:32",
                "service": "ndhcpc",
                "msg": "GigabitEthernet0/Vlan5: received ACK",
            },
        ]
    }

    lines = KeeneticClient._extract_parse_messages(payload)

    assert lines == [
        "IpSec::Vici::Stats: out of memory [0xcffe02a7].",
        "May 7 00:13:32 ndhcpc GigabitEthernet0/Vlan5: received ACK",
    ]
    summary = KeeneticClient._parse_ipsec_vici_diagnostics(lines)
    assert summary["status"] == "warning"
    assert summary["vici_out_of_memory_count"] == 1


def test_parse_ipsec_vici_diagnostics_reports_ok_without_errors() -> None:
    """Normal logs produce an OK diagnostic state."""
    summary = KeeneticClient._parse_ipsec_vici_diagnostics(["normal log line"])

    assert summary["status"] == "ok"
    assert summary["vici_out_of_memory_count"] == 0
    assert summary["last_vici_out_of_memory"] is None


def test_iface_list_kwarg_skips_redundant_normalization() -> None:
    """Stage-2 calls accept a pre-normalized iface_list and must not re-normalize."""
    client = KeeneticClient("192.0.2.1", "admin", "secret")

    normalized = [
        {"id": "Wireguard0", "type": "WireGuard", "state": "up"},
        {"id": "GigabitEthernet0", "type": "Ethernet"},
    ]

    calls = 0
    real_normalize = client._normalize_interfaces

    def counting_normalize(raw):
        nonlocal calls
        calls += 1
        return real_normalize(raw)

    client._normalize_interfaces = counting_normalize  # type: ignore[assignment]

    result = asyncio.run(client.async_get_wireguard_status(iface_list=normalized))

    assert calls == 0, "iface_list path must skip _normalize_interfaces"
    assert isinstance(result, dict)
    assert "profiles" in result


def test_get_mesh_nodes_from_clients_uses_prefetched_clients() -> None:
    """The fallback accepts a pre-fetched client list to avoid a duplicate fetch."""
    client = KeeneticClient("192.0.2.1", "admin", "secret")

    async def fail_get_clients():  # pragma: no cover - must not be invoked
        raise AssertionError("async_get_clients should not be called when clients are supplied")

    client.async_get_clients = fail_get_clients  # type: ignore[assignment]

    clients = [
        {"mac": "AA:BB:CC:00:00:01", "system-mode": "extender", "active": True, "name": "Ext-1", "ip": "10.0.0.2"},
        {"mac": "AA:BB:CC:00:00:02", "system-mode": "client", "active": True},
        {"mac": "", "system-mode": "extender", "active": True},  # missing mac → skipped
    ]

    nodes = asyncio.run(client._get_mesh_nodes_from_clients(clients=clients))

    assert len(nodes) == 1
    node = nodes[0]
    assert node["mac"] == "AA:BB:CC:00:00:01"
    assert node["mode"] == "extender"
    assert node["state"] == "up"
    assert node["connected"] is True


def test_get_mesh_nodes_from_clients_falls_back_to_fetch_when_no_arg() -> None:
    """Without a supplied list the helper still calls async_get_clients."""
    client = KeeneticClient("192.0.2.1", "admin", "secret")

    fetched = [{"mac": "AA:BB:CC:00:00:09", "system-mode": "repeater", "active": False, "name": "Rep"}]

    async def fake_get_clients():
        return fetched

    client.async_get_clients = fake_get_clients  # type: ignore[assignment]

    nodes = asyncio.run(client._get_mesh_nodes_from_clients())

    assert [n["mac"] for n in nodes] == ["AA:BB:CC:00:00:09"]
    assert nodes[0]["state"] == "down"


def test_async_get_all_interface_stats_runs_in_parallel() -> None:
    """Per-interface stat fetches run via asyncio.gather, not sequentially."""
    client = KeeneticClient("192.0.2.1", "admin", "secret")

    interfaces = {
        "GigabitEthernet0": {"id": "GigabitEthernet0", "type": "Ethernet", "link": "up", "state": "up"},
        "PPPoE0": {"id": "PPPoE0", "type": "PPPoE", "link": "up", "state": "up"},
        "Bridge0": {"id": "Bridge0", "type": "Bridge", "link": "up", "state": "up"},  # filtered
    }

    async def fake_wan(interfaces=None, iface_list=None):
        return [{"id": "PPPoE0"}]

    in_flight = 0
    max_in_flight = 0

    async def fake_stat(name):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            await asyncio.sleep(0.01)
            return {"rxbytes": 100, "txbytes": 200}
        finally:
            in_flight -= 1

    client.async_get_wan_interfaces = fake_wan  # type: ignore[assignment]
    client.async_get_interface_stat = fake_stat  # type: ignore[assignment]

    stats = asyncio.run(client.async_get_all_interface_stats(interfaces=interfaces))

    assert "GigabitEthernet0" in stats
    assert "PPPoE0" in stats
    assert "Bridge0" not in stats  # bridge filtered when not in wan_ids
    assert max_in_flight >= 2, "interface stat fetches must run concurrently"


def test_async_get_all_interface_stats_swallows_per_interface_errors() -> None:
    """One failing interface must not poison the whole result set."""
    client = KeeneticClient("192.0.2.1", "admin", "secret")

    interfaces = {
        "ISP": {"id": "ISP", "type": "Ethernet", "link": "up"},
        "Backup": {"id": "Backup", "type": "Ethernet", "link": "up"},
    }

    async def fake_wan(interfaces=None, iface_list=None):
        return [{"id": "ISP"}, {"id": "Backup"}]

    async def fake_stat(name):
        if name == "Backup":
            raise RuntimeError("boom")
        return {"rxbytes": 1}

    client.async_get_wan_interfaces = fake_wan  # type: ignore[assignment]
    client.async_get_interface_stat = fake_stat  # type: ignore[assignment]

    stats = asyncio.run(client.async_get_all_interface_stats(interfaces=interfaces))

    assert "ISP" in stats
    assert "Backup" not in stats


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
