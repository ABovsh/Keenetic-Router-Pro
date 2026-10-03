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


def test_wireguard_uptime_is_not_gated(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    data = {"wireguard": {"profiles": {"Wireguard0": {"enabled": False, "state": "down"}}}}
    sensor = KeeneticWgUptimeSensor(
        keenetic_coordinator_factory(data), keenetic_entry, "Wireguard0"
    )
    assert sensor.available is True


# ---------- Zero-on-a-healthy-router counters ship disabled ----------


@pytest.mark.parametrize(
    "sensor_cls", [KeeneticIpsecViciOomTotalSensor, KeeneticWanDowntimeSensor]
)
def test_zero_on_healthy_counters_are_disabled_by_default(sensor_cls) -> None:
    assert sensor_cls._attr_entity_registry_enabled_default is False
