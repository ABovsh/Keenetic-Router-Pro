"""Focused regressions for the September 2026 hardening round."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from homeassistant.const import UnitOfInformation
from homeassistant.exceptions import HomeAssistantError

from custom_components.keenetic_router_pro.entity import CounterDeadbandMixin
from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from custom_components.keenetic_router_pro.api import KeeneticClient
from custom_components.keenetic_router_pro.number import (
    _MAX_KBPS,
    KeeneticClientRateLimitNumber,
)
from custom_components.keenetic_router_pro.sensor.client import KeeneticClientRxSensor
from custom_components.keenetic_router_pro.sensor.mesh import KeeneticMeshMemorySensor
from custom_components.keenetic_router_pro.sensor.traffic import KeeneticLanRxSensor
from custom_components.keenetic_router_pro.sensor.wifi import KeeneticWifi24RxSensor
from custom_components.keenetic_router_pro.sensor.wireguard import KeeneticWgRxSensor
from custom_components.keenetic_router_pro.utils import normalize_mesh_node_address
from test_coordinator_stages import StageFixtureClient


class _Counter(CounterDeadbandMixin):
    _COUNTER_DEADBAND = 100


def test_counter_deadband_exposes_any_decrease_and_clears_missing_baseline() -> None:
    """A reset must reach TOTAL_INCREASING even when it is below the band."""
    counter = _Counter()
    assert counter._publish_counter(1_000) == 1_000
    assert counter._publish_counter(1_050) == 1_000
    assert counter._publish_counter(999) == 999
    assert counter._publish_counter(0) == 0
    assert counter._publish_counter(None) is None
    assert counter._publish_counter(7) == 7


def test_existing_counter_units_remain_stable_without_statistics_migration() -> None:
    """Same-ID TOTAL_INCREASING history must not be silently reinterpreted."""
    assert KeeneticLanRxSensor._attr_native_unit_of_measurement == UnitOfInformation.GIGABYTES
    assert KeeneticWifi24RxSensor._attr_native_unit_of_measurement == UnitOfInformation.GIGABYTES
    assert KeeneticClientRxSensor.native_unit_of_measurement.fget(None) == UnitOfInformation.GIGABYTES
    assert KeeneticWgRxSensor.native_unit_of_measurement.fget(None) == UnitOfInformation.MEGABYTES


def test_mesh_memory_uses_a_two_percent_deadband(keenetic_entry, keenetic_coordinator_factory) -> None:
    data = {"mesh_nodes": [{"id": "node", "memory": "31/100"}]}
    sensor = KeeneticMeshMemorySensor(keenetic_coordinator_factory(data), keenetic_entry, "node")
    assert sensor.native_value == 31
    data["mesh_nodes"][0]["memory"] = "32/100"
    assert sensor.native_value == 31
    data["mesh_nodes"][0]["memory"] = "33/100"
    assert sensor.native_value == 33


class _FailOnceOomStore:
    def __init__(self) -> None:
        self.saves: list[dict[str, object]] = []

    async def async_load(self) -> None:
        return None

    async def async_save(self, state: dict[str, object]) -> None:
        if not self.saves:
            self.saves.append({"failed": True})
            raise OSError("storage busy")
        self.saves.append(dict(state))


class _AlwaysFailOomStore:
    def __init__(self) -> None:
        self.attempts = 0

    async def async_load(self) -> None:
        return None

    async def async_save(self, _state: dict[str, object]) -> None:
        self.attempts += 1
        raise OSError("storage busy")


def test_oom_save_failure_retries_latest_snapshot_without_another_event() -> None:
    client = StageFixtureClient()
    client.ipsec_diagnostics = {
        "events": [("May 1 12:00:00", "IpSec::Vici::Stats: out of memory")]
    }
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]
    store = _FailOnceOomStore()
    coordinator._oom_store = store  # type: ignore[assignment]

    coordinator.data = asyncio.run(coordinator._async_update_data())
    assert coordinator._oom_state_dirty is True
    client.ipsec_diagnostics = {"events": []}
    asyncio.run(coordinator._async_update_data())

    assert coordinator._oom_state_dirty is False
    assert store.saves[-1] == coordinator._oom_state


def test_oom_save_retry_uses_bounded_exponential_backoff() -> None:
    client = StageFixtureClient()
    client.ipsec_diagnostics = {
        "events": [("May 1 12:00:00", "IpSec::Vici::Stats: out of memory")]
    }
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]
    store = _AlwaysFailOomStore()
    coordinator._oom_store = store  # type: ignore[assignment]

    for _ in range(5):
        coordinator.data = asyncio.run(coordinator._async_update_data())

    assert store.attempts == 3
    assert coordinator._oom_save_backoff_ticks == 4
    assert coordinator._oom_save_retry_after == 2


def test_optional_source_freshness_expires_after_three_failed_attempts() -> None:
    coordinator = object.__new__(KeeneticCoordinator)
    coordinator.data = {"mesh_nodes_fresh": True}
    coordinator._source_failure_streaks = {}

    for _ in range(3):
        assert coordinator._source_is_fresh(
            "mesh_nodes", attempted=True, failed=True
        ) is True
    assert coordinator._source_is_fresh(
        "mesh_nodes", attempted=True, failed=True
    ) is False
    coordinator.data["mesh_nodes_fresh"] = False
    assert coordinator._source_is_fresh(
        "mesh_nodes", attempted=False, failed=False
    ) is False
    assert coordinator._source_is_fresh(
        "mesh_nodes", attempted=True, failed=False
    ) is True


def test_entities_follow_optional_source_freshness(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    mesh_data = {
        "mesh_nodes": [{"id": "node", "memory": "31/100"}],
        "mesh_nodes_fresh": False,
    }
    mesh = KeeneticMeshMemorySensor(
        keenetic_coordinator_factory(mesh_data), keenetic_entry, "node"
    )
    assert mesh.available is False

    traffic_data = {
        "interface_stats": {"GigabitEthernet0": {"rxbytes": 1024}},
        "interface_stats_fresh": False,
    }
    traffic = KeeneticLanRxSensor(
        keenetic_coordinator_factory(traffic_data), keenetic_entry
    )
    assert traffic.available is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("192.0.2.8", "192.0.2.8"),
        ("[2001:db8::8]", "2001:db8::8"),
        ("2001:db8::8", "2001:db8::8"),
        ("mesh.example", None),
        ("https://192.0.2.8", None),
        ("192.0.2.8/path", None),
    ],
)
def test_mesh_fallback_accepts_only_literal_ip_addresses(value: str, expected: str | None) -> None:
    assert normalize_mesh_node_address(value) == expected


def test_mesh_update_rejects_hostname_before_direct_request() -> None:
    client = KeeneticClient("192.0.2.1", "admin", "secret")
    client._session = SimpleNamespace()
    with pytest.raises(HomeAssistantError, match="literal IP"):
        asyncio.run(client.async_start_node_firmware_update("mesh.example"))


class _RateLimitApi:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, int]] = []

    async def async_set_client_rate_limit(self, mac: str, value: int) -> None:
        self.calls.append((mac, value))
        if self.error:
            raise self.error


def _rate_limit(api: _RateLimitApi) -> KeeneticClientRateLimitNumber:
    return KeeneticClientRateLimitNumber(
        SimpleNamespace(data={}),
        SimpleNamespace(entry_id="entry", title="Router"),
        api, "aa:bb:cc:dd:ee:ff", "Laptop",
    )


def test_bandwidth_limit_updates_only_after_router_success() -> None:
    api = _RateLimitApi()
    entity = _rate_limit(api)
    asyncio.run(entity.async_set_native_value(2048))
    assert api.calls == [("aa:bb:cc:dd:ee:ff", 2048)]
    assert entity.native_value == 2048

    failing = _rate_limit(_RateLimitApi(HomeAssistantError("router rejected value")))
    failing._limit_kbps = 1024
    with pytest.raises(HomeAssistantError):
        asyncio.run(failing.async_set_native_value(2048))
    assert failing.native_value == 1024


def test_bandwidth_limit_invalid_restore_becomes_zero_and_advertises_bounds() -> None:
    entity = _rate_limit(_RateLimitApi())

    async def invalid_last_state() -> SimpleNamespace:
        return SimpleNamespace(state="unknown")

    entity.async_get_last_state = invalid_last_state  # type: ignore[method-assign]
    asyncio.run(entity.async_added_to_hass())
    assert entity.native_value == 0
    assert entity._attr_native_min_value == 0
    assert entity._attr_native_max_value == _MAX_KBPS
    assert entity._attr_native_step == 64
