"""Hardening regression tests for the 1.7.56 audit round."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient
from custom_components.keenetic_router_pro.api.constants import _RCI_PARSE_ERROR_RE
from custom_components.keenetic_router_pro.api.parsers.ipsec import parse_ipsec_vici_diagnostics
from custom_components.keenetic_router_pro.api.parsers.wan import _extract_ip_from_value
from custom_components.keenetic_router_pro.coordinator import _first_stat_int
from custom_components.keenetic_router_pro.coordinator_parts.derived import (
    build_clients_by_mac,
    counter_rate_bytes_per_second,
)
from custom_components.keenetic_router_pro.coordinator_parts.oom import advance_oom_state
from custom_components.keenetic_router_pro.utils import (
    coerce_float,
    coerce_seconds,
    usable_ip,
)


# S101 — CLI rejection phrases detected
@pytest.mark.parametrize(
    "msg", ["No such command", "Bad parameter foo", "Already exists", "Syntax error"]
)
def test_rci_parse_error_regex_covers_cli_rejections(msg: str) -> None:
    assert _RCI_PARSE_ERROR_RE.search(msg)


# S102 — challenge auth without a session cookie must not mark authenticated
def test_challenge_auth_without_cookie_raises() -> None:
    class FakeResponse:
        def __init__(self, status: int, headers: dict[str, str]) -> None:
            self.status = status
            self.headers = headers

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def text(self):
            return ""

    class FakeSession:
        async def get(self, *_a, **_kw):
            return FakeResponse(
                401, {"X-NDM-Challenge": "ch", "X-NDM-Realm": "Keenetic"}
            )

        async def post(self, *_a, **_kw):
            return FakeResponse(200, {})

    client = KeeneticClient(
        TEST_HOST, TEST_USERNAME, TEST_PASSWORD, use_challenge_auth=True
    )
    client._session = FakeSession()

    with pytest.raises(KeeneticApiError, match="no session cookie"):
        asyncio.run(client._async_authenticate_challenge())
    assert client._authenticated is False


# S103 — multiple Set-Cookie headers are all collected
def test_cookie_header_collects_all_set_cookie_values() -> None:
    from custom_components.keenetic_router_pro.api.helpers import (
        _cookie_header_from_response,
    )

    class MultiHeaders(dict):
        def getall(self, key, default=None):
            return ["csrf=abc; Path=/", "session=xyz; HttpOnly"]

    class Resp:
        headers = MultiHeaders()

    assert _cookie_header_from_response(Resp()) == "csrf=abc; session=xyz"


# S201 — single ping-check profile collapsed to a dict is kept
def test_ping_check_single_profile_dict_not_dropped() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    async def fake_rci_get(path, **kwargs):
        return {
            "pingcheck": {
                "profile": "main",
                "host": "1.1.1.1",
                "interface": {"ISP": {"status": "pass"}},
            }
        }

    client._rci_get = fake_rci_get
    result = asyncio.run(client.async_get_ping_check_status())
    assert result  # observations extracted, not silently dropped


# S301 — recent_matches returns the newest entries
def test_ipsec_oom_recent_matches_are_newest() -> None:
    entries = [
        {"time": f"t{i}", "message": f"IpSec::Vici::Stats: out of memory [E{i}]"}
        for i in range(9, -1, -1)
    ]
    result = parse_ipsec_vici_diagnostics([], entries=entries)
    assert "E9" in result["recent_matches"][0]


# S302 — dict-shaped address payload yields the IP
def test_extract_ip_handles_dict_shape() -> None:
    assert _extract_ip_from_value({"address": "1.2.3.4", "mask": "255.255.255.0"}) == "1.2.3.4"


# S305 — non-canonical all-zero IPv6 placeholders rejected
@pytest.mark.parametrize("ip", ["::0", "0:0:0:0:0:0:0:0"])
def test_usable_ip_rejects_zero_ipv6_forms(ip: str) -> None:
    assert usable_ip(ip) is None


# S402 — boolean/absent counters never fabricate a throughput spike
def test_first_stat_int_rejects_bool_and_counter_rate_skips_none() -> None:
    assert _first_stat_int({"rxbytes": False}, "rxbytes") is None
    assert counter_rate_bytes_per_second(10**9, None, 30.0) == 0.0
    assert counter_rate_bytes_per_second(None, 100, 30.0) == 0.0
    assert counter_rate_bytes_per_second(10**9, False, 30.0) == 0.0


# S404 — future-dated OOM events are not counted
def test_oom_future_event_skipped() -> None:
    state = {"last_seen_iso": None, "last_seen_count": 0, "total": 0}
    now = datetime(2026, 6, 1, 12, 0, 0)
    out = advance_oom_state(state, [("Dec 15 10:00:00", "oom")], now=now)
    assert out["total"] == 0


# S405 — duplicate MAC keeps the online record
def test_build_clients_by_mac_prefers_online_duplicate() -> None:
    online = {"mac": "AA:BB:CC:00:00:01", "active": True}
    offline = {"mac": "AA:BB:CC:00:00:01", "active": False}
    index = build_clients_by_mac([online, offline])
    assert index["aa:bb:cc:00:00:01"] is online


# S603 — coerce_float rejects booleans
def test_coerce_float_rejects_bool() -> None:
    assert coerce_float(True) is None
    assert coerce_float(False) is None


# S604 — absurd durations cannot overflow timedelta downstream
def test_coerce_seconds_rejects_timedelta_overflow_values() -> None:
    assert coerce_seconds(10**14, default=None) is None
    assert coerce_seconds(3600) == 3600
