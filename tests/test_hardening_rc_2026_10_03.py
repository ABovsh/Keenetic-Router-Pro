"""Regressions for the October 2026 rc round.

Each test here was written against a defect observed on live routers or in
the live recorder database, and failed before its fix.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient
from custom_components.keenetic_router_pro.coordinator_parts.refresh import (
    RefreshPlan,
    build_batch_tree,
    unsupported_batch_paths,
)
from custom_components.keenetic_router_pro.sensor.crypto import (
    KeeneticCryptoMapRxBytesSensor,
    KeeneticCryptoMapRxThroughputSensor,
    KeeneticCryptoMapStateSensor,
)
from custom_components.keenetic_router_pro.sensor.dns import (
    KeeneticDnsProxyFailedRequestsSensor,
    KeeneticDnsProxyStatusSensor,
)
from custom_components.keenetic_router_pro.sensor.ipsec import (
    KeeneticIpsecViciOomTotalSensor,
)
from custom_components.keenetic_router_pro.sensor.network import (
    KeeneticActiveConnectionsSensor,
    KeeneticWanDowntimeSensor,
    KeeneticWanRxBytesSensor,
    KeeneticWanRxThroughputSensor,
    KeeneticWanTxBytesSensor,
)
from custom_components.keenetic_router_pro.sensor.wireguard import (
    KeeneticWgRxSensor,
    KeeneticWgTxSensor,
    KeeneticWgUptimeSensor,
)
from test_coordinator_stages import StageFixtureClient, _coordinator, _updated_data

_ALL_TIERS = RefreshPlan(True, True, True, True, True, True)


def _client() -> KeeneticClient:
    return KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)


# ---------- show/ip/neighbour on KeeneticOS 5.1 ----------


async def test_ip_neighbours_reads_the_id_keyed_table_without_a_cli_fallback() -> None:
    """KeeneticOS 5.1 answers with ``{"1": {...}, "2": {...}}``, no wrapper.

    Not recognising that shape sent a ``show ip neighbour`` CLI parse to every
    router on every tick (observed live on three routers, 2026-10-03).
    """
    client = _client()
    client._rci_get = AsyncMock(
        return_value={
            "1": {
                "id": 1,
                "mac": "d0:27:02:92:64:68",
                "address": "192.0.2.139",
                "last-seen": 1,
            },
            "2": {
                "id": 2,
                "mac": "34:ab:95:26:7b:3d",
                "address": "192.0.2.32",
                "last-seen": 4,
            },
        }
    )
    client._rci_parse = AsyncMock(return_value={})

    neighbours = await client.async_get_ip_neighbours()

    assert [n["address"] for n in neighbours] == ["192.0.2.139", "192.0.2.32"]
    client._rci_parse.assert_not_awaited()


# ---------- RCI batch tree vs. capability latches ----------


def test_batch_tree_leaves_out_endpoints_the_router_already_refused() -> None:
    """A missing path inside the batch is logged by the router as "not found".

    The latches exist to stop exactly that log line; the batch must honour them.
    """
    tree = build_batch_tree(
        _ALL_TIERS,
        unsupported=frozenset(
            {
                "show/ping-check",
                "show/ipsec",
                "show/ndns",
                "show/dns-proxy",
                "show/ip/hotspot",
            }
        ),
    )

    assert set(tree["show"]) == {"system", "interface", "ip", "version"}
    assert tree["show"]["ip"] == {"neighbour": {}}
    assert tree["components"] == {"check-update": {}}


def test_unsupported_batch_paths_follow_the_client_latches() -> None:
    client = _client()
    assert unsupported_batch_paths(client) == frozenset()

    client._ping_check_supported = False
    client._crypto_map_supported = False
    client._ndns_supported = False
    client._dns_proxy_supported = False
    client._hotspot_subpath_skip = {"show/ip/hotspot/host"}

    assert unsupported_batch_paths(client) == frozenset(
        {
            "show/ping-check",
            "show/ipsec",
            "show/ndns",
            "show/dns-proxy",
            "show/ip/hotspot",
        }
    )


async def test_coordinator_prefetch_skips_latched_off_endpoints() -> None:
    client = StageFixtureClient()
    client._rci_batch_supported = None
    client._ping_check_supported = False
    client._dns_proxy_supported = False

    await _updated_data(_coordinator(client))

    tree = client.prefetch_calls[0]
    assert "ping-check" not in tree["show"]
    assert "dns-proxy" not in tree["show"]
    assert "ndns" in tree["show"]


def test_batch_tree_carries_mesh_members_on_slow_ticks_when_supported() -> None:
    slow = RefreshPlan(False, True, True, False, True, False)
    fast = RefreshPlan(False, False, False, False, False, False)

    assert build_batch_tree(slow, include_mesh=True)["show"]["mws"] == {"member": {}}
    assert "mws" not in build_batch_tree(slow)["show"]
    assert "mws" not in build_batch_tree(fast, include_mesh=True)["show"]


async def test_coordinator_prefetch_includes_mesh_members_only_once_supported() -> None:
    client = StageFixtureClient()
    client._rci_batch_supported = None
    await _updated_data(_coordinator(client))
    assert "mws" not in client.prefetch_calls[0]["show"]

    client = StageFixtureClient()
    client._rci_batch_supported = None
    client._mws_member_supported = True
    await _updated_data(_coordinator(client))
    assert client.prefetch_calls[0]["show"]["mws"] == {"member": {}}


# ---------- Site-to-site IPsec: transient failures and missing component ----------

_STATUSALL = {
    "ipsec_statusall": (
        "Security Associations (1 up, 0 connecting):\n"
        "        Office[1]: ESTABLISHED 10 seconds ago, "
        "192.0.2.1[192.0.2.1]...198.51.100.1[198.51.100.1]\n"
    )
}


def _rci_get_by_path(responses: dict[str, Any]) -> AsyncMock:
    async def _get(subpath: str, **_kwargs: Any) -> Any:
        result = responses[subpath]
        if isinstance(result, BaseException):
            raise result
        return result

    return AsyncMock(side_effect=_get)


@pytest.mark.parametrize(
    "responses",
    [
        {
            "show/ipsec": KeeneticApiError("Timeout for /rci/show/ipsec"),
            "crypto/map": {"Office": {"enable": True}},
        },
        {
            "show/ipsec": _STATUSALL,
            "crypto/map": KeeneticApiError("Timeout for /rci/crypto/map"),
        },
    ],
    ids=["status-timeout", "config-timeout"],
)
async def test_ipsec_status_raises_on_a_transient_failure(responses) -> None:
    """A half-read must not be published as "every tunnel is down".

    Live history: one failed ``show/ipsec`` read published the established
    S2S tunnel as UNDEFINED with 0 bytes for one slow tick (2026-10-01), and
    another made all its entities unavailable (2026-09-26). Raising lets the
    coordinator keep the previous snapshot instead.
    """
    client = _client()
    client._rci_get = _rci_get_by_path(responses)

    with pytest.raises(KeeneticApiError):
        await client.async_get_ipsec_status()
    assert client._crypto_map_supported is None


async def test_ipsec_status_latches_off_on_a_router_without_ipsec() -> None:
    client = _client()
    client._rci_get = _rci_get_by_path(
        {
            "show/ipsec": KeeneticApiError("HTTP error 404", status=404),
            "crypto/map": KeeneticApiError("HTTP error 404", status=404),
        }
    )

    assert await client.async_get_ipsec_status() == {}
    assert client._crypto_map_supported is False
    calls = client._rci_get.await_count

    assert await client.async_get_ipsec_status() == {}
    assert client._rci_get.await_count == calls


async def test_ipsec_status_marks_the_component_supported_on_success() -> None:
    client = _client()
    client._rci_get = _rci_get_by_path(
        {"show/ipsec": _STATUSALL, "crypto/map": {"Office": {"enable": True}}}
    )

    result = await client.async_get_ipsec_status()

    assert result["Office"]["enabled"] is True
    assert client._crypto_map_supported is True


async def test_ipsec_status_keeps_config_only_tunnels_without_a_status_endpoint() -> None:
    """A firmware without ``show/ipsec`` still lists configured maps as off."""
    client = _client()
    client._rci_get = _rci_get_by_path(
        {
            "show/ipsec": KeeneticApiError("HTTP error 404", status=404),
            "crypto/map": {"Office": {"enable": False}},
        }
    )

    result = await client.async_get_ipsec_status()

    assert result["Office"]["enabled"] is False
    assert client._crypto_map_supported is True


def _slow_tick(coordinator) -> None:
    coordinator._refresh_count = 3


async def test_coordinator_keeps_the_crypto_map_snapshot_when_ipsec_fails() -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    first = await _updated_data(coordinator)
    first["crypto_maps"]["SITE"]["rx_throughput"] = 7.0
    coordinator.data = first

    client.async_get_ipsec_status = AsyncMock(
        side_effect=KeeneticApiError("Timeout for /rci/show/ipsec")
    )
    _slow_tick(coordinator)
    second = await _updated_data(coordinator)

    assert second["crypto_maps"]["SITE"]["connected"] is True
    assert second["crypto_maps"]["SITE"]["rx_bytes"] == 1000
    # The failed read has no new sample, so the rate must not drop to 0.
    assert second["crypto_maps"]["SITE"]["rx_throughput"] == pytest.approx(7.0)
    assert second["crypto_maps_fresh"] is True


async def test_coordinator_marks_crypto_maps_stale_after_repeated_failures() -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    coordinator.data = await _updated_data(coordinator)
    client.async_get_ipsec_status = AsyncMock(
        side_effect=KeeneticApiError("Timeout for /rci/show/ipsec")
    )

    fresh = []
    for _ in range(4):
        _slow_tick(coordinator)
        coordinator.data = await _updated_data(coordinator)
        fresh.append(coordinator.data["crypto_maps_fresh"])

    assert fresh == [True, True, True, False]


def test_crypto_map_entities_go_unavailable_once_the_source_is_stale(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    data = {
        "crypto_maps": {"SITE": {"connected": True, "state": "PHASE2_ESTABLISHED"}},
        "crypto_maps_fresh": False,
    }
    sensor = KeeneticCryptoMapStateSensor(
        keenetic_coordinator_factory(data), keenetic_entry, "SITE"
    )
    assert sensor.available is False

    data["crypto_maps_fresh"] = True
    assert sensor.available is True


# ---------- DNS proxy ----------


async def test_dns_proxy_status_raises_on_a_transient_failure() -> None:
    client = _client()
    client._rci_get = AsyncMock(
        side_effect=KeeneticApiError("Timeout for /rci/show/dns-proxy")
    )

    with pytest.raises(KeeneticApiError):
        await client.async_get_dns_proxy_status()
    assert client._dns_proxy_supported is None


def _dns_payload(sent: int, answered: int) -> dict[str, Any]:
    stat = (
        "DNS Servers\n\n"
        "  Ip   Port  R.Sent  A.Rcvd  NX.Rcvd  Med.Resp  Avg.Resp  Rank\n"
        f"  127.0.0.1  40500  {sent}  {answered}  0  20ms  20ms  4\n"
    )
    return {"proxy-status": [{"proxy-name": "System", "proxy-stat": stat}]}


async def test_dns_proxy_down_needs_a_real_sample() -> None:
    """The stats window is ten seconds; three unanswered queries are not an outage."""
    client = _client()
    client._rci_get = AsyncMock(return_value=_dns_payload(sent=3, answered=0))
    assert (await client.async_get_dns_proxy_status())["status"] == "ok"

    client._rci_get = AsyncMock(return_value=_dns_payload(sent=25, answered=0))
    assert (await client.async_get_dns_proxy_status())["status"] == "down"


async def test_coordinator_keeps_the_dns_snapshot_when_the_fetch_fails() -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    first = await _updated_data(coordinator)
    coordinator.data = first

    client.async_get_dns_proxy_status = AsyncMock(
        side_effect=KeeneticApiError("Timeout for /rci/show/dns-proxy")
    )
    coordinator._refresh_count = 15  # very-slow tick
    second = await _updated_data(coordinator)

    assert second["dns_proxy"] == first["dns_proxy"]
    assert second["dns_proxy_fresh"] is True


@pytest.mark.parametrize(
    "sensor_cls", [KeeneticDnsProxyStatusSensor, KeeneticDnsProxyFailedRequestsSensor]
)
def test_dns_sensors_go_unavailable_once_the_source_is_stale(
    sensor_cls, keenetic_entry, keenetic_coordinator_factory
) -> None:
    data = {"dns_proxy": {"status": "ok", "failed_requests": 3}, "dns_proxy_fresh": False}
    sensor = sensor_cls(keenetic_coordinator_factory(data), keenetic_entry)
    assert sensor.available is False

    data["dns_proxy_fresh"] = True
    assert sensor.available is True


# ---------- Active connections without conntrack fields ----------


@pytest.mark.parametrize(
    "system",
    [{}, {"conntotal": 65536}, {"conntotal": "x", "connfree": 1}],
    ids=["absent", "half", "garbage"],
)
def test_active_connections_is_unknown_without_a_valid_conntrack_reading(
    system, keenetic_entry, keenetic_coordinator_factory
) -> None:
    sensor = KeeneticActiveConnectionsSensor(
        keenetic_coordinator_factory({"system": system}), keenetic_entry
    )
    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None


def test_active_connections_keeps_a_valid_zero(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    sensor = KeeneticActiveConnectionsSensor(
        keenetic_coordinator_factory(
            {"system": {"conntotal": 65536, "connfree": 65536}}
        ),
        keenetic_entry,
    )
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes["free"] == 65536


# ---------- Traffic counters of links that are not up ----------


def _wan_data(**wan: Any) -> dict[str, Any]:
    base = {
        "id": "Vlan6",
        "link_state": "up",
        "rx_bytes": 0,
        "tx_bytes": 512,
        "rx_throughput": 0.0,
        "raw": {"state": "up", "link": "up"},
    }
    base.update(wan)
    return {
        "wan_interfaces": [base],
        "wan_by_id": {"Vlan6": base},
        "interface_stats_fresh": True,
    }


@pytest.mark.parametrize(
    "sensor_cls",
    [KeeneticWanRxBytesSensor, KeeneticWanTxBytesSensor, KeeneticWanRxThroughputSensor],
)
@pytest.mark.parametrize(
    "wan",
    [
        {"link_state": "down", "raw": {"state": "down", "link": "down"}},
        # Configured up, cable/modem absent: observed on a standby LTE uplink.
        {"link_state": "up", "raw": {"state": "up", "link": "down"}},
    ],
    ids=["disabled", "no-link"],
)
def test_wan_traffic_sensors_are_unavailable_while_the_link_is_down(
    sensor_cls, wan, keenetic_entry, keenetic_coordinator_factory
) -> None:
    sensor = sensor_cls(
        keenetic_coordinator_factory(_wan_data(**wan)), keenetic_entry, "Vlan6"
    )
    assert sensor.available is False


@pytest.mark.parametrize(
    "sensor_cls",
    [KeeneticWanRxBytesSensor, KeeneticWanTxBytesSensor, KeeneticWanRxThroughputSensor],
)
def test_idle_standby_uplink_keeps_its_true_zero(
    sensor_cls, keenetic_entry, keenetic_coordinator_factory
) -> None:
    sensor = sensor_cls(
        keenetic_coordinator_factory(_wan_data()), keenetic_entry, "Vlan6"
    )
    assert sensor.available is True
    assert sensor.native_value in (0, 0.0, 512)


@pytest.mark.parametrize(
    "sensor_cls", [KeeneticCryptoMapRxBytesSensor, KeeneticCryptoMapRxThroughputSensor]
)
def test_crypto_map_traffic_is_unavailable_while_the_tunnel_is_down(
    sensor_cls, keenetic_entry, keenetic_coordinator_factory
) -> None:
    data = {"crypto_maps": {"SITE": {"connected": False, "rx_bytes": 0, "rx_throughput": 0.0}}}
    sensor = sensor_cls(keenetic_coordinator_factory(data), keenetic_entry, "SITE")
    assert sensor.available is False

    data["crypto_maps"]["SITE"]["connected"] = True
    assert sensor.available is True


def test_crypto_map_state_stays_available_while_the_tunnel_is_down(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    data = {"crypto_maps": {"SITE": {"connected": False, "state": "UNDEFINED"}}}
    sensor = KeeneticCryptoMapStateSensor(
        keenetic_coordinator_factory(data), keenetic_entry, "SITE"
    )
    assert sensor.available is True
    assert sensor.native_value == "UNDEFINED"


@pytest.mark.parametrize("sensor_cls", [KeeneticWgRxSensor, KeeneticWgTxSensor])
def test_wireguard_traffic_is_unavailable_while_the_profile_is_down(
    sensor_cls, keenetic_entry, keenetic_coordinator_factory
) -> None:
    profile = {"enabled": False, "state": "down", "rxbytes": 0, "txbytes": 0}
    data = {"wireguard": {"profiles": {"Wireguard0": profile}}}
    sensor = sensor_cls(keenetic_coordinator_factory(data), keenetic_entry, "Wireguard0")
    assert sensor.available is False

    profile.update(enabled=True, state="up")
    assert sensor.available is True


# ---------- Zero-on-a-healthy-router counters ship disabled ----------


@pytest.mark.parametrize(
    "sensor_cls", [KeeneticIpsecViciOomTotalSensor, KeeneticWanDowntimeSensor]
)
def test_zero_on_healthy_counters_are_disabled_by_default(sensor_cls) -> None:
    assert sensor_cls._attr_entity_registry_enabled_default is False


# ---------- show/log on KeeneticOS 5.x ----------

# Shape of ``POST /rci/ {"show": {"log": ...}}`` on KeeneticOS 5.1.6 (OP Titan,
# 2026-10-03): records keyed by ascending id, oldest first; the text is nested
# one level down and the time lives in ``timestamp`` on the outer record.
_LIVE_LOG = {
    "show": {
        "log": {
            "4216": {
                "message": {"level": "Info", "label": "I", "message": "DHCPREQUEST received. "},
                "timestamp": "Oct  3 09:50:01",
                "ident": "ndhcps",
                "id": 4216,
            },
            "4217": {
                "message": {
                    "level": "Critical",
                    "label": "C",
                    "message": "IpSec::Vici::Stats: out of memory [0xcffe02b0]. ",
                },
                "timestamp": "Oct  3 09:56:28",
                "ident": "ndm",
                "id": 4217,
            },
            "4218": {
                "message": {
                    "level": "Critical",
                    "label": "C",
                    "message": "IpSec::Vici::Stats: out of memory [0xcffe0300]. ",
                },
                "timestamp": "Oct  3 10:06:28",
                "ident": "ndm",
                "id": 4218,
            },
        }
    }
}


def test_log_entries_carry_the_time_of_keeneticos_5_records() -> None:
    from custom_components.keenetic_router_pro.api.helpers import _extract_log_entries

    entries = _extract_log_entries(_LIVE_LOG)

    assert [e["time"] for e in entries] == [
        "Oct  3 10:06:28",
        "Oct  3 09:56:28",
        "Oct  3 09:50:01",
    ]
    assert entries[0]["module"] == "ndm"
    assert entries[0]["level"] == "Critical"


async def test_ipsec_diagnostics_events_are_timestamped_newest_first() -> None:
    """Without a time every event was dropped and the OOM total stayed 0."""
    client = _client()
    client._rci_post = AsyncMock(return_value=_LIVE_LOG)

    diag = await client.async_get_ipsec_diagnostics()

    assert diag["vici_out_of_memory_count"] == 2
    assert diag["last_error_code"] == "0xcffe0300"
    assert [t for t, _ in diag["events"]] == ["Oct  3 10:06:28", "Oct  3 09:56:28"]


def test_oom_total_counts_live_shaped_events() -> None:
    from datetime import datetime

    from custom_components.keenetic_router_pro.api.helpers import _extract_log_entries
    from custom_components.keenetic_router_pro.api.parsers.ipsec import (
        parse_ipsec_vici_diagnostics,
    )
    from custom_components.keenetic_router_pro.coordinator_parts.oom import (
        advance_oom_state,
    )

    events = parse_ipsec_vici_diagnostics([], entries=_extract_log_entries(_LIVE_LOG))["events"]
    state = advance_oom_state(
        {"last_seen_iso": None, "last_seen_count": 0, "total": 0},
        events,
        now=datetime(2026, 10, 3, 11, 0, 0),
    )

    assert state["total"] == 2
    assert state["last_seen_iso"] == "2026-10-03T10:06:28"


# ---------- Client Wi-Fi session start ----------


def _session_sensor(monkeypatch, keenetic_entry, keenetic_coordinator_factory):
    from datetime import datetime as real_datetime, timezone

    from custom_components.keenetic_router_pro.sensor import client as client_module

    clock = {"now": real_datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc)}

    class _Clock(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(client_module, "datetime", _Clock)
    mac = "aa:bb:cc:dd:ee:ff"
    data = {"clients_by_mac": {mac: {"mac": mac, "active": True, "uptime": 3600}}}
    sensor = client_module.KeeneticClientUptimeSensor(
        keenetic_coordinator_factory(data), keenetic_entry, mac, "Phone"
    )
    return sensor, data["clients_by_mac"][mac], clock


def test_session_start_does_not_creep_when_the_router_counter_lags(
    monkeypatch, keenetic_entry, keenetic_coordinator_factory
) -> None:
    """Live S24/A16 sessions crept +90 s every ~35 min with no reconnect.

    The router's per-client uptime runs a few percent slower than the wall
    clock, so the recomputed start drifts forward until it crosses the
    tolerance and is re-published: a wrong value plus a recorder row.
    """
    from datetime import timedelta

    sensor, client, clock = _session_sensor(
        monkeypatch, keenetic_entry, keenetic_coordinator_factory
    )
    start = sensor.native_value

    for _ in range(6):
        clock["now"] += timedelta(minutes=35)
        client["uptime"] += 35 * 60 - 90
        assert sensor.native_value == start


def test_session_start_still_follows_a_reconnect(
    monkeypatch, keenetic_entry, keenetic_coordinator_factory
) -> None:
    from datetime import timedelta

    sensor, client, clock = _session_sensor(
        monkeypatch, keenetic_entry, keenetic_coordinator_factory
    )
    start = sensor.native_value

    clock["now"] += timedelta(minutes=10)
    client["uptime"] = 30
    assert sensor.native_value == clock["now"] - timedelta(seconds=30)
    assert sensor.native_value > start


# ---------- Uptime counters without a reading ----------


def test_router_uptime_is_unknown_without_a_reading(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    """A fake 0 on a TOTAL_INCREASING counter reads as a reset in statistics."""
    from custom_components.keenetic_router_pro.sensor.system import KeeneticUptimeSensor

    sensor = KeeneticUptimeSensor(
        keenetic_coordinator_factory({"system": {"uptime": "garbage"}}), keenetic_entry
    )
    assert sensor.native_value is None


def test_mesh_uptime_is_unknown_without_a_reading(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    from custom_components.keenetic_router_pro.sensor.mesh import KeeneticMeshUptimeSensor

    data = {"mesh_nodes": [{"id": "node", "cid": "node", "connected": False}]}
    sensor = KeeneticMeshUptimeSensor(
        keenetic_coordinator_factory(data), keenetic_entry, "node"
    )
    assert sensor.native_value is None

    data["mesh_nodes"][0]["uptime"] = "86400"
    assert sensor.native_value == 86400


# ---------- Interface toggles survive a router reboot ----------


@pytest.mark.parametrize(
    "call",
    [
        lambda client: client.async_set_interface_enabled("Wireguard0", False),
        lambda client: client.async_set_wifi_enabled("WifiMaster0/AccessPoint1", True),
    ],
    ids=["interface", "wifi"],
)
async def test_interface_toggles_are_saved_to_startup_config(call) -> None:
    """Running-config changes are lost on reboot unless saved (CLI manual 2.5).

    Crypto maps, client policies and rate limits already saved; the Wi-Fi,
    WAN and VPN switches did not, so the router undid them on its next boot.
    """
    client = _client()
    client._rci_parse = AsyncMock(return_value={})

    await call(client)

    commands = [c.args[0] for c in client._rci_parse.await_args_list]
    assert commands[-1] == "system configuration save"
    assert commands[0].startswith("interface ")


async def test_a_failed_save_does_not_fail_the_toggle() -> None:
    client = _client()

    async def _parse(command: str):
        if command == "system configuration save":
            raise KeeneticApiError("busy")
        return {}

    client._rci_parse = AsyncMock(side_effect=_parse)

    await client.async_set_interface_enabled("Wireguard0", True)


# ---------- Ping-check profile list on the WAN Connected sensor ----------


def test_ping_check_profile_list_does_not_carry_per_poll_counters(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    """Two profiles on one WAN exposed success_count, which moves every poll."""
    from custom_components.keenetic_router_pro.binary_sensor import (
        KeeneticWanConnectedSensor,
    )

    profiles = [
        {"profile": "_WEBADMIN_ISP", "status": "pass", "success_count": 7, "fail_count": 0,
         "check_hosts": ["8.8.8.8"], "check_addresses": ["8.8.8.8"]},
        {"profile": "custom", "status": "pass", "success_count": 3, "fail_count": 0,
         "check_hosts": ["1.1.1.1"], "check_addresses": ["1.1.1.1"]},
    ]
    wan = {
        "id": "ISP",
        "internet_access": True,
        "ping_check": {"passing": True, "status": "pass", "all_profiles": profiles},
    }
    data = {"wan_interfaces": [wan], "wan_by_id": {"ISP": wan}}
    sensor = KeeneticWanConnectedSensor(keenetic_coordinator_factory(data), keenetic_entry, "ISP")

    before = sensor.extra_state_attributes
    profiles[0]["success_count"] = 8
    profiles[1]["success_count"] = 4

    assert sensor.extra_state_attributes == before
    assert before["all_ping_check_profiles"] == [
        {"profile": "_WEBADMIN_ISP", "status": "pass", "check_hosts": ["8.8.8.8"]},
        {"profile": "custom", "status": "pass", "check_hosts": ["1.1.1.1"]},
    ]


# ---------- Diagnostics redaction against live payload keys ----------


async def test_diagnostics_redacts_identifiers_found_in_live_payloads() -> None:
    """Keys seen in a live NH dump (2026-10-03) that escaped redaction."""
    import re
    from types import SimpleNamespace

    from custom_components.keenetic_router_pro import diagnostics

    data = {
        "clients": [
            {
                "via": "80:07:94:46:ab:ab",
                "ip6": ["2001:db8::1234"],
                "neighbour": {"via": "80:07:94:46:ab:ab"},
            }
        ],
        "interfaces": {
            "GigabitEthernet1": {"description": "0677779709 - BKM ISP"},
            "Wireguard1": {
                "wireguard": {"peer": [{"local-endpoint-address": "100.64.20.190"}]}
            },
        },
        "crypto_maps": {
            "site": {"phase1": {"local_addr": "100.64.20.190", "remote_addr": "203.0.113.76"}}
        },
        "ndns": {
            "booked": "yahny",
            "address6": "2001:db8::1",
            "ttp": {
                "tunnel": [
                    {
                        "client": "198.51.100.4",
                        "target-local": "100.64.20.190:52604",
                        "target-remote": "198.51.100.10:443",
                        "destination": "100.64.20.190:80",
                    }
                ]
            },
        },
        "mesh_nodes": [{"backhaul": {"root": "8000.50:ff:20:f8:4e:39", "bridge": "8000.50:ff:20:f8:4e:39"}}],
    }
    entry = SimpleNamespace(
        title="Router", version=1, domain="keenetic_router_pro", source="user",
        data={}, options={},
        runtime_data=SimpleNamespace(
            coordinator=SimpleNamespace(data=data, update_interval=None, last_update_success=True),
            client=None,
        ),
    )

    dumped = repr(await diagnostics.async_get_config_entry_diagnostics(None, entry))

    assert not re.search(r"(?i)(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", dumped)
    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", dumped)
    assert "2001:db8" not in dumped
    assert "0677779709" not in dumped
    assert "yahny" not in dumped


# ---------- Mesh members: no MAC-keyed fallback once CIDs are known ----------

_EXTENDER_CLIENTS = [
    {"mac": "aa:bb:cc:00:00:01", "system-mode": "extender", "active": True, "name": "Giga"}
]


async def test_mesh_nodes_do_not_fall_back_to_mac_ids_after_cids_were_seen() -> None:
    """A blank member list from a known MWS controller is a glitch, not a topology.

    Publishing the MAC-keyed fallback then would add a second, MAC-keyed set
    of devices/entities next to the CID-keyed ones and mark those unavailable.
    """
    client = _client()
    client._rci_get = AsyncMock(
        side_effect=[
            {"member": [{"cid": "60ea4b5e-0ea5", "mac": "aa:bb:cc:00:00:01", "fw": "5.1.6"}]},
            {},
        ]
    )

    first = await client.async_get_mesh_nodes(clients=_EXTENDER_CLIENTS)
    assert [n["id"] for n in first] == ["60ea4b5e-0ea5"]

    with pytest.raises(KeeneticApiError):
        await client.async_get_mesh_nodes(clients=_EXTENDER_CLIENTS)


async def test_mesh_fallback_still_serves_a_controller_without_members() -> None:
    client = _client()
    client._rci_get = AsyncMock(return_value={})

    nodes = await client.async_get_mesh_nodes(clients=_EXTENDER_CLIENTS)

    assert [n["id"] for n in nodes] == ["aa:bb:cc:00:00:01"]


# ---------- Connection policy select ----------


def test_policy_select_reports_registration_from_the_hotspot_row(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    """host_policies only carries policy/access, so is_registered was always False."""
    from custom_components.keenetic_router_pro.select import KeeneticClientPolicySelect

    mac = "aa:bb:cc:dd:ee:ff"
    data = {
        "clients_by_mac": {mac: {"mac": mac, "registered": True, "active": True}},
        "host_policies": {mac: {"policy": "Policy0", "access": "permit"}},
        "policies": {"Policy0": "VPN"},
    }
    select = KeeneticClientPolicySelect(
        keenetic_coordinator_factory(data), keenetic_entry, None, mac, "Phone", None, {"Policy0": "VPN"}
    )

    assert select.extra_state_attributes["is_registered"] is True


# ---------- Uptime: hourly duration, immediate on restart ----------


def test_uptime_publishes_hourly_and_at_once_on_a_restart(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    from custom_components.keenetic_router_pro.sensor.system import KeeneticUptimeSensor

    data = {"system": {"uptime": 600_000}}
    sensor = KeeneticUptimeSensor(keenetic_coordinator_factory(data), keenetic_entry)
    assert sensor.native_value == 600_000

    data["system"]["uptime"] = 600_000 + 3_540
    assert sensor.native_value == 600_000

    data["system"]["uptime"] = 600_000 + 3_600
    assert sensor.native_value == 603_600

    data["system"]["uptime"] = 45  # reboot
    assert sensor.native_value == 45


def test_wan_uptime_follows_the_same_hourly_rule(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    wan = {"id": "ISP", "link_state": "up", "uptime": 10_000}
    data = {"wan_interfaces": [wan], "wan_by_id": {"ISP": wan}}
    from custom_components.keenetic_router_pro.sensor.network import KeeneticWanUptimeSensor

    sensor = KeeneticWanUptimeSensor(keenetic_coordinator_factory(data), keenetic_entry, "ISP")
    assert sensor.native_value == 10_000
    wan["uptime"] = 10_060
    assert sensor.native_value == 10_000


def test_wireguard_uptime_is_unavailable_while_the_profile_is_down(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    profile = {"enabled": False, "state": "down", "uptime": 0}
    data = {"wireguard": {"profiles": {"Wireguard0": profile}}}
    sensor = KeeneticWgUptimeSensor(
        keenetic_coordinator_factory(data), keenetic_entry, "Wireguard0"
    )
    assert sensor.available is False

    profile.update(enabled=True, state="up", uptime=120)
    assert sensor.available is True
    assert sensor.native_value == 120


def test_pppoe_uptime_is_unknown_without_a_reading(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    from custom_components.keenetic_router_pro.sensor.network import KeeneticPppoeUptimeSensor

    sensor = KeeneticPppoeUptimeSensor(
        keenetic_coordinator_factory({"wan_status": {"status": "down"}}), keenetic_entry
    )
    assert sensor.native_value is None


# ---------- VPN switches shadowed by a WAN switch ----------


def test_vpn_switch_of_an_interface_that_is_a_wan_is_pruned() -> None:
    """A VPN uplink is controlled by its WAN device's Enabled switch.

    Older releases also registered a VPN switch for it; setup never creates
    that one any more, so it sat in the registry as unavailable forever
    (two such switches on the live install, 2026-10-03).
    """
    import types
    from types import SimpleNamespace

    from custom_components.keenetic_router_pro import _async_prune_vpn_switches_shadowed_by_wan
    from test_hardening_1_10_0 import _patched_registries

    removed: list[str] = []
    entries = [
        SimpleNamespace(entity_id="switch.wg_pl_gdn", unique_id="e1_vpn_Wireguard0"),
        SimpleNamespace(entity_id="switch.openvpn", unique_id="e1_vpn_OpenVPN0"),
        SimpleNamespace(entity_id="switch.wan", unique_id="e1_wan_Wireguard0_enabled_switch"),
    ]
    er_mod = types.ModuleType("homeassistant.helpers.entity_registry")
    er_mod.async_get = lambda _hass: SimpleNamespace(
        async_remove=lambda entity_id: removed.append(entity_id)
    )
    er_mod.async_entries_for_config_entry = lambda _reg, _eid: entries
    data = {"wan_interfaces": [{"id": "Wireguard0"}, {"id": "GigabitEthernet1"}]}

    with _patched_registries(**{"homeassistant.helpers.entity_registry": er_mod}):
        _async_prune_vpn_switches_shadowed_by_wan(None, SimpleNamespace(entry_id="e1"), data)

    assert removed == ["switch.wg_pl_gdn"]


# ---------- OOM counter: compare router log time with HA's time zone ----------


def test_oom_clock_uses_the_home_assistant_time_zone(monkeypatch) -> None:
    """Router logs are local time; a Docker HA process often runs in UTC.

    Compared against the process clock, a router three hours ahead had every
    fresh event dropped as "future" until it could fall out of the window.
    """
    import sys
    import types
    from datetime import datetime, timedelta, timezone

    from custom_components.keenetic_router_pro.coordinator_parts.oom import local_now

    kyiv = timezone(timedelta(hours=3))
    fake_dt = types.ModuleType("homeassistant.util.dt")
    fake_dt.now = lambda: datetime(2026, 10, 3, 12, 0, tzinfo=kyiv)
    monkeypatch.setitem(sys.modules, "homeassistant.util.dt", fake_dt)
    monkeypatch.setattr(sys.modules["homeassistant.util"], "dt", fake_dt, raising=False)

    assert local_now() == datetime(2026, 10, 3, 12, 0)


# ---------- Mesh firmware_available between polls ----------


def test_mesh_firmware_available_survives_a_blank_poll() -> None:
    """Live: Giga's available_version went '' for one slow tick (2026-10-03 08:53)."""
    from custom_components.keenetic_router_pro.coordinator_parts.derived import (
        carry_firmware_available,
    )

    previous = [{"cid": "a", "firmware": "4.3.8", "firmware_available": "4.3.9"}]
    blank = [{"cid": "a", "firmware": "4.3.8", "firmware_available": ""}]
    assert carry_firmware_available(blank, previous)[0]["firmware_available"] == "4.3.9"

    newer = [{"cid": "a", "firmware": "4.3.8", "firmware_available": "4.4.0"}]
    assert carry_firmware_available(newer, previous)[0]["firmware_available"] == "4.4.0"

    installed = [{"cid": "a", "firmware": "4.3.9", "firmware_available": ""}]
    assert carry_firmware_available(installed, previous)[0]["firmware_available"] == ""

    other = [{"cid": "b", "firmware": "4.3.8", "firmware_available": ""}]
    assert carry_firmware_available(other, previous)[0]["firmware_available"] == ""


async def test_coordinator_carries_mesh_firmware_available() -> None:
    client = StageFixtureClient()
    coordinator = _coordinator(client)
    client.mesh_nodes = [{"id": "n1", "cid": "n1", "firmware": "1", "firmware_available": "2"}]
    coordinator.data = await _updated_data(coordinator)

    client.mesh_nodes = [{"id": "n1", "cid": "n1", "firmware": "1", "firmware_available": ""}]
    coordinator._refresh_count = 3
    data = await _updated_data(coordinator)

    assert data["mesh_nodes"][0]["firmware_available"] == "2"
