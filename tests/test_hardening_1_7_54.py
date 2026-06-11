"""Hardening regression tests for the 1.7.54 audit round."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

from custom_components.keenetic_router_pro.api import (
    KeeneticApiError,
    KeeneticClient,
)
from custom_components.keenetic_router_pro.coordinator_parts.derived import (
    mesh_associations,
    real_client_macs,
)
from custom_components.keenetic_router_pro.coordinator_parts.oom import (
    advance_oom_state,
    parse_keenetic_log_ts,
)
from custom_components.keenetic_router_pro.coordinator_parts.payloads import (
    merge_clients_with_neighbours,
)
from custom_components.keenetic_router_pro.utils import coerce_byte_count


# ---------------------------------------------------------------- CA01
def test_mesh_transient_error_raises_instead_of_mac_fallback() -> None:
    """A transient mws/member failure must not flip node ids from CID to MAC."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    async def fake_fallback(clients=None):
        return [{"id": "aa:bb:cc:00:00:01", "cid": None, "mac": "aa:bb:cc:00:00:01"}]

    async def fake_rci_get(path, **kwargs):
        raise KeeneticApiError("Timeout for show/mws/member")

    client._get_mesh_nodes_from_clients = fake_fallback
    client._rci_get = fake_rci_get

    with pytest.raises(KeeneticApiError):
        asyncio.run(client.async_get_mesh_nodes())
    # Support is NOT latched off by a transient failure.
    assert client._mws_member_supported is not False


def test_mesh_not_found_still_returns_fallback_and_latches() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    fallback = [{"id": "aa:bb:cc:00:00:01", "cid": None, "mac": "aa:bb:cc:00:00:01"}]

    async def fake_fallback(clients=None):
        return fallback

    async def fake_rci_get(path, **kwargs):
        raise KeeneticApiError('not found: "member"')

    client._get_mesh_nodes_from_clients = fake_fallback
    client._rci_get = fake_rci_get

    assert asyncio.run(client.async_get_mesh_nodes()) == fallback
    assert client._mws_member_supported is False


# ---------------------------------------------------------------- CA02
def test_ndns_info_does_not_mutate_rci_payload() -> None:
    """Tick-cache subtrees are shared; ndns must not int-coerce them in place."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    payload = {"ttp": {"tunnel": [{"uptime": "120", "idle": "5"}]}}

    async def fake_rci_get(path, **kwargs):
        return payload

    client._rci_get = fake_rci_get
    result = asyncio.run(client.async_get_ndns_info())

    assert payload["ttp"]["tunnel"][0]["uptime"] == "120"
    assert result["ttp"]["tunnel"][0]["uptime"] == 120


# ---------------------------------------------------------------- CA03
def test_node_auth_basic_fallback_not_cached() -> None:
    """A no-challenge response must not latch Basic auth for the session."""

    class FakeResponse:
        status = 200
        headers: dict[str, str] = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def read(self):
            return b""

    class FakeSession:
        async def get(self, *_a, **_kw):
            return FakeResponse()

    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._session = FakeSession()

    headers = asyncio.run(client._authenticate_to_node("192.168.1.3", 80))
    assert "Authorization" in headers
    assert ("192.168.1.3", 80) not in client._node_auth_headers


# ---------------------------------------------------------------- CA04
def test_dns_proxy_status_accepts_dict_payload() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    async def fake_rci_get(path, **kwargs):
        return {
            "proxy-status": {
                "proxy-name": "Dns::Proxy",
                "proxy-config": "",
                "proxy-stat": "",
            }
        }

    client._rci_get = fake_rci_get
    result = asyncio.run(client.async_get_dns_proxy_status())

    assert result.get("proxies")
    assert client._dns_proxy_supported is True


# ---------------------------------------------------------------- CA05
def test_set_client_policy_none_mac_raises_api_error() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    with pytest.raises(KeeneticApiError):
        asyncio.run(client.async_set_client_policy(None, "deny"))


# ---------------------------------------------------------------- CA06
def test_check_firmware_update_has_update_is_bool() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    async def fake_rci_get(path, **kwargs):
        return {
            "title": "4.1.0",
            "fw-available": "4.2.0",
            "fw-update-sandbox": "stable",
        }

    client._rci_get = fake_rci_get
    result = asyncio.run(client.async_check_firmware_update())
    assert result["has_update"] is True


# ---------------------------------------------------------------- CB01
def test_neighbour_only_clients_are_flagged_and_excluded_from_new_macs() -> None:
    clients = [{"mac": "AA:BB:CC:00:00:01", "active": True}]
    neighbours = [
        {"mac": "AA:BB:CC:00:00:01", "address-family": "ipv4", "address": "10.0.0.5"},
        {"mac": "AA:BB:CC:00:00:02", "address-family": "ipv4", "address": "10.0.0.6"},
    ]
    merged = merge_clients_with_neighbours(clients, neighbours)
    by_mac = {c["mac"].lower(): c for c in merged}

    assert not by_mac["aa:bb:cc:00:00:01"].get("neighbour-only")
    assert by_mac["aa:bb:cc:00:00:02"].get("neighbour-only") is True

    macs = real_client_macs(
        {m: c for m, c in ((k.lower(), v) for k, v in by_mac.items())}
    )
    assert macs == {"aa:bb:cc:00:00:01"}


# ---------------------------------------------------------------- CB03
def test_merge_clients_tolerates_dict_shaped_neighbours() -> None:
    clients = [{"mac": "AA:BB:CC:00:00:01", "active": True}]
    merged = merge_clients_with_neighbours(clients, {"unexpected": "dict"})
    assert merged == clients


# ---------------------------------------------------------------- CB04
def test_oom_high_water_mark_not_lowered_by_sliding_window() -> None:
    ts = "May 27 17:33:48"
    parsed = parse_keenetic_log_ts(ts)
    state = {
        "last_seen_iso": parsed.isoformat(),
        "last_seen_count": 3,
        "total": 3,
    }
    # Window slid: only one event at the same second remains visible.
    new_state = advance_oom_state(state, [(ts, "oom")], now=parsed)
    assert new_state["total"] == 3
    assert new_state["last_seen_count"] == 3  # high-water mark preserved


# ---------------------------------------------------------------- CB05
def test_feb_29_log_timestamp_survives_non_leap_year() -> None:
    parsed = parse_keenetic_log_ts(
        "Feb 29 10:00:00", now=datetime(2026, 3, 1, 0, 0, 0)
    )
    assert parsed is not None
    assert parsed.month == 2


# ---------------------------------------------------------------- CB07
def test_mesh_associations_accepts_list_shaped_payload() -> None:
    nodes = [
        {"cid": "node1", "associations": [{"mac": "a"}, {"mac": "b"}]},
        {"cid": "node2", "associations": 3},
    ]
    result = mesh_associations(nodes)
    assert result["by_node"]["node1"] == 2
    assert result["by_node"]["node2"] == 3
    assert result["total"] == 5


# ---------------------------------------------------------------- CC01
def test_uptime_and_last_seen_sensors_fingerprint_live_path() -> None:
    """The dedup path reads _FINGERPRINT_IGNORE; overrides must target it."""
    from custom_components.keenetic_router_pro.entity import ClientEntity
    from custom_components.keenetic_router_pro.sensor.client import (
        KeeneticClientLastSeenSensor,
        KeeneticClientUptimeSensor,
    )

    assert ClientEntity._FINGERPRINT_IGNORE == frozenset({"last-seen", "uptime"})
    assert KeeneticClientUptimeSensor._FINGERPRINT_IGNORE == frozenset({"last-seen"})
    assert KeeneticClientLastSeenSensor._FINGERPRINT_IGNORE == frozenset({"uptime"})


# ---------------------------------------------------------------- CC05
@pytest.mark.parametrize("value", [True, False])
def test_coerce_byte_count_rejects_booleans(value: bool) -> None:
    assert coerce_byte_count(value) is None


def test_wan_throughput_rejects_boolean() -> None:
    from custom_components.keenetic_router_pro.sensor.network import (
        KeeneticWanRxBytesSensor,
    )

    sensor = KeeneticWanRxBytesSensor.__new__(KeeneticWanRxBytesSensor)
    sensor._get_wan = lambda: {"rx_throughput": True}  # type: ignore[attr-defined]
    # native_value reads self._wan; emulate via property bypass
    wan = {"rx_throughput": True}
    value = KeeneticWanRxBytesSensor.native_value.fget  # type: ignore[attr-defined]

    class _Stub(KeeneticWanRxBytesSensor):
        def __init__(self) -> None:  # noqa: D401 - test stub
            pass

        @property
        def _wan(self):
            return wan

    assert _Stub().native_value is None


# ---------------------------------------------------------------- CD01
def test_diagnostics_redacts_tracked_client_names() -> None:
    import json

    from custom_components.keenetic_router_pro.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    class FakeEntry:
        title = "router"
        version = 1
        domain = "keenetic_router_pro"
        source = "user"
        data = {
            "host": "192.168.1.1",
            "tracked_clients": [
                {"mac": "AA:BB:CC:00:00:01", "ip": "10.0.0.5", "name": "Anton-iPhone"}
            ],
        }
        options: dict[str, Any] = {}
        runtime_data = None

    result = asyncio.run(async_get_config_entry_diagnostics(None, FakeEntry()))
    assert "Anton-iPhone" not in json.dumps(result)


# ---------------------------------------------------------------- CD03
def test_mesh_reboot_button_name_is_static() -> None:
    from custom_components.keenetic_router_pro.button import KeeneticMeshRebootButton

    button = KeeneticMeshRebootButton.__new__(KeeneticMeshRebootButton)
    assert KeeneticMeshRebootButton._attr_has_entity_name is True

    class _Stub(KeeneticMeshRebootButton):
        def __init__(self) -> None:
            pass

        @property
        def _node(self):
            return {"name": "Bedroom Extender"}

    assert _Stub().name == "Reboot"
