"""Regression tests for the 1.10.1 audit round."""

from __future__ import annotations

import asyncio
import json
import types
from types import SimpleNamespace
from typing import Any

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from test_coordinator_update_flow import FakeKeeneticClient
from test_hardening_1_10_0 import _patched_registries

from custom_components.keenetic_router_pro import async_remove_config_entry_device
from custom_components.keenetic_router_pro.config_flow import KeeneticOptionsFlow
from custom_components.keenetic_router_pro.const import (
    CONF_CLIENT_SENSORS,
    CONF_TRACKED_CLIENTS,
)
from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from custom_components.keenetic_router_pro.diagnostics import (
    _MAC_SET_FIELDS,
    async_get_config_entry_diagnostics,
)
from custom_components.keenetic_router_pro.sensor.crypto import (
    KeeneticCryptoMapRxThroughputSensor,
)

MAC = "AA:BB:CC:DD:EE:FF"
MAC_LOWER = MAC.lower()


# --- 1. Diagnostics must redact every MAC-set field, not just new_clients ---


async def test_diagnostics_redacts_all_mac_sets() -> None:
    """Only ``new_clients`` was stripped; the three sibling sets leaked MACs."""
    coordinator_data = {
        "new_clients": {MAC_LOWER},
        "online_clients": {MAC_LOWER},
        "connected_clients": {MAC_LOWER},
        "disconnected_clients": {MAC_LOWER},
    }
    entry = SimpleNamespace(
        title="router",
        version=1,
        domain="keenetic_router_pro",
        source="user",
        data={"host": TEST_HOST, "username": TEST_USERNAME, "password": TEST_PASSWORD},
        options={},
        runtime_data=SimpleNamespace(
            coordinator=SimpleNamespace(data=coordinator_data),
            client=SimpleNamespace(),
        ),
    )

    result = await async_get_config_entry_diagnostics(None, entry)
    serialized = json.dumps(result, sort_keys=True)

    assert MAC_LOWER not in serialized
    assert MAC not in serialized
    for field in _MAC_SET_FIELDS:
        assert result["coordinator_data"][field] == {"<redacted-mac-set>": 1}


def test_mac_set_fields_cover_every_set_the_coordinator_publishes() -> None:
    """The coordinator publishes four MAC sets; all four must be listed."""
    assert set(_MAC_SET_FIELDS) == {
        "new_clients",
        "online_clients",
        "connected_clients",
        "disconnected_clients",
    }


# --- 2. Crypto-map throughput needs the same deadband as WAN throughput ---


def test_crypto_map_throughput_holds_within_deadband(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    """A 0.2 % move must not write a new value; only WAN had this damping."""
    data: dict[str, Any] = {"crypto_maps": {"OfficeVPN": {"rx_throughput": 1_000_000}}}
    coordinator = keenetic_coordinator_factory(data)
    sensor = KeeneticCryptoMapRxThroughputSensor(coordinator, keenetic_entry, "OfficeVPN")

    assert sensor.native_value == 8_000_000

    data["crypto_maps"]["OfficeVPN"]["rx_throughput"] = 1_002_000
    assert sensor.native_value == 8_000_000


def test_crypto_map_throughput_publishes_a_real_move(
    keenetic_entry, keenetic_coordinator_factory
) -> None:
    """Clearing the band must still publish, or the sensor would latch."""
    data: dict[str, Any] = {"crypto_maps": {"OfficeVPN": {"rx_throughput": 1_000_000}}}
    coordinator = keenetic_coordinator_factory(data)
    sensor = KeeneticCryptoMapRxThroughputSensor(coordinator, keenetic_entry, "OfficeVPN")

    assert sensor.native_value == 8_000_000

    # 10 % of 8 Mbit/s is 800 kbit/s (1.14.0 band), so clear it properly.
    data["crypto_maps"]["OfficeVPN"]["rx_throughput"] = 1_200_000
    assert sensor.native_value == 9_600_000


# --- 3. A failed OOM Store load must be retried, not latched ---


class _FlakyStore:
    """Fails the first load, then returns a real persisted total."""

    def __init__(self) -> None:
        self.loads = 0
        self.saved: list[dict[str, Any]] = []

    async def async_load(self) -> dict[str, Any] | None:
        self.loads += 1
        if self.loads == 1:
            raise OSError("storage busy")
        return {"last_seen_iso": None, "last_seen_count": 0, "total": 7}

    async def async_save(self, data: dict[str, Any]) -> None:
        self.saved.append(dict(data))


def _oom_client() -> FakeKeeneticClient:
    client = FakeKeeneticClient()

    async def ipsec_diagnostics() -> dict[str, Any]:
        return {"events": [("May 1 12:00:00", "IpSec::Vici::Stats: out of memory")]}

    client.async_get_ipsec_diagnostics = ipsec_diagnostics  # type: ignore[assignment]
    return client


def test_failed_oom_store_load_is_retried_on_the_next_tick() -> None:
    """A transient load error latched the counter at 0 for the whole session."""
    store = _FlakyStore()
    coordinator = KeeneticCoordinator(object(), _oom_client())  # type: ignore[arg-type]
    coordinator._oom_store = store  # type: ignore[assignment]

    asyncio.run(coordinator._async_update_data())

    assert store.loads == 1
    assert coordinator._oom_state_loaded is False


def test_retried_oom_store_load_does_not_overwrite_the_persisted_total() -> None:
    """The reset-to-zero state was saved over the real total on disk."""
    store = _FlakyStore()
    coordinator = KeeneticCoordinator(object(), _oom_client())  # type: ignore[arg-type]
    coordinator._oom_store = store  # type: ignore[assignment]

    asyncio.run(coordinator._async_update_data())
    coordinator.data = None
    data = asyncio.run(coordinator._async_update_data())

    assert store.loads == 2
    assert coordinator._oom_state_loaded is True
    assert data["ipsec_diagnostics"]["oom_total"] == 8


# --- 4. The options flow must update the entry once, not twice ---


class _RecordingConfigEntries:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def async_update_entry(self, entry, *, data=None, options=None):
        self.calls.append({"data": data, "options": options})
        if data is not None:
            entry.data = data
        if options is not None:
            entry.options = options


def _options_entry() -> SimpleNamespace:
    return SimpleNamespace(
        data={
            "host": TEST_HOST,
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            CONF_TRACKED_CLIENTS: [
                {"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.0.2.10", "name": "Phone"}
            ],
        },
        options={},
        runtime_data=None,
    )


def test_options_flow_writes_data_and_options_in_one_entry_update() -> None:
    """Two separate writes fired the update listener twice, reloading twice."""
    entry = _options_entry()
    flow = KeeneticOptionsFlow(entry)
    flow.hass = SimpleNamespace(config_entries=_RecordingConfigEntries())
    flow._available_clients = [
        {"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.0.2.10", "name": "Phone"},
        {"mac": "11:22:33:44:55:66", "ip": "192.0.2.20", "name": "Laptop"},
    ]
    flow.async_create_entry = lambda **kwargs: {
        "type": "create_entry",
        "data": kwargs["data"],
    }

    result = asyncio.run(
        flow.async_step_init(
            {
                "tracked_clients": ["11:22:33:44:55:66"],
                CONF_CLIENT_SENSORS: "basic",
            }
        )
    )

    calls = flow.hass.config_entries.calls
    assert len(calls) == 1
    assert calls[0]["data"][CONF_TRACKED_CLIENTS][0]["mac"] == "11:22:33:44:55:66"
    # Home Assistant writes result["data"] to entry.options after the flow
    # finishes. It must already match, or that write counts as a change and
    # fires the update listener a second time.
    assert entry.options == result["data"]


# --- 5. The UI delete hook must refuse a device that still has entities ---


def _registry_modules(entities_for_device):
    er_mod = types.ModuleType("homeassistant.helpers.entity_registry")
    er_mod.async_get = lambda _hass: object()
    er_mod.async_entries_for_device = entities_for_device
    return {"homeassistant.helpers.entity_registry": er_mod}


def test_ui_device_delete_refused_while_disabled_entities_remain() -> None:
    """Deleting a device whose entities are merely turned off destroys them."""
    seen: dict[str, Any] = {}

    def _entries_for_device(_reg, device_id, include_disabled_entities):
        seen["include_disabled"] = include_disabled_entities
        return [SimpleNamespace(entity_id="switch.x", disabled_by="user")]

    with _patched_registries(**_registry_modules(_entries_for_device)):
        allowed = asyncio.run(
            async_remove_config_entry_device(
                None, SimpleNamespace(entry_id="e1"), SimpleNamespace(id="dev_1")
            )
        )

    assert allowed is False
    assert seen["include_disabled"] is True


def test_ui_device_delete_allowed_once_no_entities_remain() -> None:
    """The orphaned mesh node / rotated-MAC case the hook exists for."""
    with _patched_registries(
        **_registry_modules(lambda _reg, _did, include_disabled_entities: [])
    ):
        allowed = asyncio.run(
            async_remove_config_entry_device(
                None, SimpleNamespace(entry_id="e1"), SimpleNamespace(id="dev_1")
            )
        )

    assert allowed is True
