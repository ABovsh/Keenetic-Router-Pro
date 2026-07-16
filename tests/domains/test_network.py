"""Network domain parser behavior."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient
from tests.fixtures.network_rci import (
    INTERFACE_STATS_BY_NAME,
    INTERFACE_STATS_PARSE,
    MULTI_WAN_INTERFACES,
    NESTED_PORT_INTERFACES,
    PING_PARSE_RESPONSES,
)


async def test_async_get_port_info_accepts_top_level_and_skips_bad_rows() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    payload = {
        "0": {"type": "Port", "label": "0", "link": "up", "speed": "1000"},
        "bad": "ignored",
        "Bridge0": {"type": "Bridge"},
    }

    assert await client.async_get_port_info(payload) == [
        {"label": "0", "appearance": "Port", "link": "up", "speed": "1000", "duplex": None}
    ]


async def test_async_get_traffic_stats_handles_numeric_strings_and_bad_values() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_traffic_stats(
        iface_list=[{"id": "ISP", "type": "PPPoE", "state": "up", "rxbytes": "10", "txbytes": "20", "rxspeed": "8388608", "txspeed": "4194304"}]
    )

    assert result == {"download_speed": 1.0, "upload_speed": 0.5, "total_rx": "10", "total_tx": "20"}


@pytest.mark.parametrize("exc", [KeeneticApiError("boom"), aiohttp.ClientError("boom"), asyncio.TimeoutError(), ValueError("bad json")])
async def test_network_error_paths_return_empty_or_false(exc: Exception) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_parse = AsyncMock(side_effect=exc)

    assert await client.async_ping_ip("192.0.2.10") is False


@pytest.mark.parametrize(
    ("response_key", "expected"),
    [
        ("success", True),
        ("timeout", False),
        ("destination_unreachable", False),
    ],
)
async def test_async_ping_ip_router_responses_map_to_reachability(
    response_key: str,
    expected: bool,
) -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_parse = AsyncMock(return_value=PING_PARSE_RESPONSES[response_key])

    assert await client.async_ping_ip("1.1.1.1") is expected


async def test_async_ping_multiple_keeps_results_when_one_ping_raises() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client.async_ping_ip = AsyncMock(side_effect=[True, RuntimeError("boom"), False])

    assert await client.async_ping_multiple(["1.1.1.1", "8.8.8.8", "9.9.9.9"]) == {
        "1.1.1.1": True,
        "8.8.8.8": False,
        "9.9.9.9": False,
    }


async def test_async_ping_multiple_propagates_cancelled_error() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client.async_ping_ip = AsyncMock(
        side_effect=[True, asyncio.CancelledError(), False]
    )

    with pytest.raises(asyncio.CancelledError):
        await client.async_ping_multiple(["1.1.1.1", "8.8.8.8", "9.9.9.9"])


async def test_async_ping_multiple_empty_input_returns_empty_without_work() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client.async_ping_ip = AsyncMock()

    assert await client.async_ping_multiple([]) == {}
    client.async_ping_ip.assert_not_called()


async def test_async_get_port_info_accepts_nested_gigabit_port_shapes() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    assert await client.async_get_port_info(deepcopy(NESTED_PORT_INTERFACES)) == [
        {"label": "1", "appearance": "Port", "link": "up", "speed": "1000", "duplex": "full"},
        {"label": "2", "appearance": "Port", "link": "down"},
        {"label": "3", "appearance": "Port", "link": "up", "speed": "100", "duplex": "full"},
    ]


async def test_async_get_interface_stat_prefers_parse_payload_with_counters() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_parse = AsyncMock(return_value=deepcopy(INTERFACE_STATS_PARSE))
    client._rci_get = AsyncMock()

    assert await client.async_get_interface_stat("PPPoE0") == INTERFACE_STATS_PARSE
    client._rci_get.assert_not_called()


async def test_async_get_interface_stat_falls_back_to_get_when_parse_has_no_counters() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_parse = AsyncMock(return_value={"name": "PPPoE0"})
    client._rci_get = AsyncMock(return_value=deepcopy(INTERFACE_STATS_PARSE))

    assert await client.async_get_interface_stat("PPPoE0") == INTERFACE_STATS_PARSE
    client._rci_get.assert_awaited_once_with("show/interface/stat", params={"name": "PPPoE0"})


async def test_async_set_interface_enabled_emits_up_down_commands() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_parse = AsyncMock()

    await client.async_set_interface_enabled("PPPoE0", True)
    await client.async_set_interface_enabled("PPPoE0", False)

    assert [call.args[0] for call in client._rci_parse.await_args_list] == [
        "interface PPPoE0 up",
        "interface PPPoE0 down",
    ]


async def test_async_get_traffic_stats_uses_first_up_wan_speed_and_totals() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    result = await client.async_get_traffic_stats(interfaces=deepcopy(MULTI_WAN_INTERFACES))

    assert result == {
        "download_speed": 0.0,
        "upload_speed": 0.0,
        "total_rx": 0,
        "total_tx": 0,
    }


async def test_async_get_all_interface_stats_targets_wans_and_skips_lan_bridges() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    interfaces = deepcopy(MULTI_WAN_INTERFACES)
    interfaces["Bridge0"] = {"id": "Bridge0", "type": "Bridge", "state": "up"}

    async def fake_stat(name: str) -> dict:
        return deepcopy(INTERFACE_STATS_BY_NAME[name])

    client.async_get_interface_stat = fake_stat  # type: ignore[method-assign]

    assert await client.async_get_all_interface_stats(interfaces=interfaces) == {
        "PPPoE0": {
            **INTERFACE_STATS_BY_NAME["PPPoE0"],
            "interface_name": "PPPoE0",
            "interface_type": "pppoe",
            "link": "up",
            "state": "up",
        },
        "Wireguard0": {
            **INTERFACE_STATS_BY_NAME["Wireguard0"],
            "interface_name": "Wireguard0",
            "interface_type": "wireguard",
            "link": "up",
            "state": "up",
        },
        "GigabitEthernet1": {
            **INTERFACE_STATS_BY_NAME["GigabitEthernet1"],
            "interface_name": "GigabitEthernet1",
            "interface_type": "gigabitethernet",
            "link": "up",
            "state": "up",
        },
    }


async def test_async_get_all_interface_stats_propagates_cancelled_error() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)

    async def fake_stat(name: str) -> dict:
        if name == "PPPoE0":
            raise asyncio.CancelledError()
        return deepcopy(INTERFACE_STATS_BY_NAME[name])

    client.async_get_interface_stat = fake_stat  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await client.async_get_all_interface_stats(
            interfaces=deepcopy(MULTI_WAN_INTERFACES)
        )


async def test_async_get_all_interface_stats_uses_single_batch_call_on_success() -> None:
    """When the composite POST succeeds cleanly, no per-call GETs happen."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    interfaces = deepcopy(MULTI_WAN_INTERFACES)

    batch_response = {
        "show": {
            "interface": {
                "stat": [
                    deepcopy(INTERFACE_STATS_BY_NAME["PPPoE0"]),
                    deepcopy(INTERFACE_STATS_BY_NAME["Wireguard0"]),
                    deepcopy(INTERFACE_STATS_BY_NAME["GigabitEthernet1"]),
                ]
            }
        }
    }
    client._rci_batch = AsyncMock(return_value=batch_response)
    client.async_get_interface_stat = AsyncMock(
        side_effect=AssertionError("must not fan out on clean batch success")
    )

    result = await client.async_get_all_interface_stats(interfaces=interfaces)

    assert result == {
        "PPPoE0": {
            **INTERFACE_STATS_BY_NAME["PPPoE0"],
            "interface_name": "PPPoE0",
            "interface_type": "pppoe",
            "link": "up",
            "state": "up",
        },
        "Wireguard0": {
            **INTERFACE_STATS_BY_NAME["Wireguard0"],
            "interface_name": "Wireguard0",
            "interface_type": "wireguard",
            "link": "up",
            "state": "up",
        },
        "GigabitEthernet1": {
            **INTERFACE_STATS_BY_NAME["GigabitEthernet1"],
            "interface_name": "GigabitEthernet1",
            "interface_type": "gigabitethernet",
            "link": "up",
            "state": "up",
        },
    }
    client._rci_batch.assert_awaited_once()
    client.async_get_interface_stat.assert_not_called()


async def test_async_get_all_interface_stats_falls_back_per_call_on_batch_error_entry() -> None:
    """A partial error record in the batch is fetched per-call; others use the batch."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    interfaces = deepcopy(MULTI_WAN_INTERFACES)

    batch_response = {
        "show": {
            "interface": {
                "stat": [
                    deepcopy(INTERFACE_STATS_BY_NAME["PPPoE0"]),
                    {"status": [{"status": "error", "message": "not found"}]},
                    deepcopy(INTERFACE_STATS_BY_NAME["GigabitEthernet1"]),
                ]
            }
        }
    }
    client._rci_batch = AsyncMock(return_value=batch_response)

    async def fake_stat(name: str) -> dict:
        assert name == "Wireguard0"
        return deepcopy(INTERFACE_STATS_BY_NAME["Wireguard0"])

    client.async_get_interface_stat = AsyncMock(side_effect=fake_stat)

    result = await client.async_get_all_interface_stats(interfaces=interfaces)

    assert result["PPPoE0"]["rxbytes"] == INTERFACE_STATS_BY_NAME["PPPoE0"]["rxbytes"]
    assert result["Wireguard0"]["rx-bytes"] == INTERFACE_STATS_BY_NAME["Wireguard0"]["rx-bytes"]
    assert result["GigabitEthernet1"]["rxbytes"] == INTERFACE_STATS_BY_NAME["GigabitEthernet1"]["rxbytes"]
    client.async_get_interface_stat.assert_awaited_once_with("Wireguard0")


async def test_async_get_all_interface_stats_falls_back_fully_when_batch_returns_none() -> None:
    """Batch unsupported/failed (_rci_batch returns None) -> full per-call fan-out."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    interfaces = deepcopy(MULTI_WAN_INTERFACES)

    client._rci_batch = AsyncMock(return_value=None)

    async def fake_stat(name: str) -> dict:
        return deepcopy(INTERFACE_STATS_BY_NAME[name])

    client.async_get_interface_stat = AsyncMock(side_effect=fake_stat)

    result = await client.async_get_all_interface_stats(interfaces=interfaces)

    assert set(result.keys()) == {"PPPoE0", "Wireguard0", "GigabitEthernet1"}
    assert client.async_get_interface_stat.await_count == 3


async def test_async_get_all_interface_stats_falls_back_fully_on_shape_mismatch() -> None:
    """Batch response with a length mismatch is treated as unusable -> full fan-out."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    interfaces = deepcopy(MULTI_WAN_INTERFACES)

    batch_response = {
        "show": {
            "interface": {
                "stat": [deepcopy(INTERFACE_STATS_BY_NAME["PPPoE0"])]
            }
        }
    }
    client._rci_batch = AsyncMock(return_value=batch_response)

    async def fake_stat(name: str) -> dict:
        return deepcopy(INTERFACE_STATS_BY_NAME[name])

    client.async_get_interface_stat = AsyncMock(side_effect=fake_stat)

    result = await client.async_get_all_interface_stats(interfaces=interfaces)

    assert set(result.keys()) == {"PPPoE0", "Wireguard0", "GigabitEthernet1"}
    assert client.async_get_interface_stat.await_count == 3


async def test_async_get_interface_stat_latches_get_only_after_get_success() -> None:
    """Once GET path returns real stats, parse-mode is skipped on future calls."""
    from custom_components.keenetic_router_pro.api import KeeneticClient
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    parse_calls = []
    get_calls = []

    async def fake_parse(cmd: str):
        parse_calls.append(cmd)
        return {}  # No useful keys -> falls through to GET.

    async def fake_get(subpath: str, **kw):
        get_calls.append((subpath, kw.get("params")))
        return {"rxbytes": 1, "txbytes": 2}

    client._rci_parse = fake_parse
    client._rci_get = fake_get

    result = await client.async_get_interface_stat("Wireguard0")
    assert result["rxbytes"] == 1
    assert client._iface_stat_get_only is True
    assert len(parse_calls) == 1
    assert len(get_calls) == 1

    # Second call must skip _rci_parse entirely.
    result = await client.async_get_interface_stat("Wireguard0")
    assert result["rxbytes"] == 1
    assert len(parse_calls) == 1, "parse-mode must be latched off"
    assert len(get_calls) == 2
