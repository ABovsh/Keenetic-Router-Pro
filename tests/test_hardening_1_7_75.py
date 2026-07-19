"""Hardening tests for the 1.7.75 deep-audit round.

One test per verified defect:

* A-F01 — a host-policies refresh request arriving while a tick is already
  in flight must survive that tick's success, so the second change still
  gets its fast confirming fetch instead of waiting for the slow tier.
* C-F01 — the ClientEntity change-detection fingerprint must ignore the
  per-tick volatile neighbour fields (nested ``neighbour`` dict and the
  ``neighbour-expired``/``neighbour-leasetime`` copies), or the write
  suppression silently does nothing for any ARP/ND-known client.
* D-F01 — WiFi radio temperature sensors must reject implausible finite
  readings so a one-off firmware glitch cannot poison long-term statistics.
* E-F01 — the options flow must keep working from the preserved
  tracked-client list when the live runtime client's auth has been
  invalidated, matching the offline-client branch's graceful fallback.
* E-F02 — every ``async_abort(reason=...)`` in the config flow must have a
  matching ``config.abort`` translation in strings.json and en.json.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from test_config_flow_options import (
    ConfigEntries,
    _entry,
    _pin_flow_result_helpers,
)
from test_coordinator_update_flow import FakeKeeneticClient

from custom_components.keenetic_router_pro.api import KeeneticAuthError
from custom_components.keenetic_router_pro.config_flow import KeeneticOptionsFlow
from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from custom_components.keenetic_router_pro.entity import ClientEntity
from custom_components.keenetic_router_pro.sensor.wifi import (
    KeeneticWifi5TemperatureSensor,
    KeeneticWifi24TemperatureSensor,
)

_COMPONENT_DIR = (
    Path(__file__).parent.parent / "custom_components" / "keenetic_router_pro"
)


# ---------------------------------------------------------------------------
# A-F01: mid-tick host-policies request must survive the tick's success
# ---------------------------------------------------------------------------


class _MidTickRequestClient(FakeKeeneticClient):
    """Simulates the user flipping a policy select while a tick is fetching."""

    coordinator: KeeneticCoordinator | None = None

    async def async_get_host_policies(self) -> dict[str, Any]:
        if self.coordinator is not None:
            self.coordinator.request_host_policies_refresh()
        return await super().async_get_host_policies()


def test_host_policies_request_during_tick_survives_success() -> None:
    client = _MidTickRequestClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]
    client.coordinator = coordinator

    coordinator.request_host_policies_refresh()
    asyncio.run(coordinator._async_update_data())

    assert coordinator._host_policies_refresh_pending is True


def test_host_policies_pending_cleared_when_no_new_request() -> None:
    client = FakeKeeneticClient()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]

    coordinator.request_host_policies_refresh()
    asyncio.run(coordinator._async_update_data())

    assert coordinator._host_policies_refresh_pending is False


# ---------------------------------------------------------------------------
# C-F01: fingerprint must ignore volatile neighbour fields
# ---------------------------------------------------------------------------


class _DummyCoordinator:
    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {}

    def async_add_listener(self, *_a: Any, **_kw: Any):
        return lambda: None


def _client_entity(client_dict: dict) -> ClientEntity:
    coord = _DummyCoordinator({"clients_by_mac": {"aa:bb:cc:00:00:01": client_dict}})
    return ClientEntity(
        coordinator=coord,  # type: ignore[arg-type]
        entry_id="entry",
        title="router",
        mac="AA:BB:CC:00:00:01",
        label="phone",
    )


_NEIGHBOUR_CLIENT = {
    "mac": "aa:bb:cc:00:00:01",
    "ip": "10.0.0.5",
    "link": "up",
    "last-seen": 100,
    "uptime": 50,
    "neighbour": {
        "mac": "aa:bb:cc:00:00:01",
        "address": "10.0.0.5",
        "last-seen": 1,
        "leasetime": 129,
        "expired": False,
        "wireless": True,
    },
    "neighbour-expired": False,
    "neighbour-leasetime": 129,
    "neighbour-wireless": True,
}


def test_fingerprint_ignores_volatile_neighbour_fields() -> None:
    entity = _client_entity(_NEIGHBOUR_CLIENT)

    next_tick = {
        **_NEIGHBOUR_CLIENT,
        "last-seen": 130,
        "neighbour": {
            **_NEIGHBOUR_CLIENT["neighbour"],
            "last-seen": 2,
            "leasetime": 99,
            "expired": True,
        },
        "neighbour-expired": True,
        "neighbour-leasetime": 99,
    }

    assert entity._client_fingerprint(_NEIGHBOUR_CLIENT) == entity._client_fingerprint(
        next_tick
    )


def test_fingerprint_still_reacts_to_meaningful_changes() -> None:
    entity = _client_entity(_NEIGHBOUR_CLIENT)

    changed = {**_NEIGHBOUR_CLIENT, "ip": "10.0.0.99"}
    assert entity._client_fingerprint(_NEIGHBOUR_CLIENT) != entity._client_fingerprint(
        changed
    )


# ---------------------------------------------------------------------------
# D-F01: WiFi temperature sensors must reject implausible readings
# ---------------------------------------------------------------------------


def _temp_sensor(
    cls: type, master: str, temperature: Any
) -> KeeneticWifi24TemperatureSensor | KeeneticWifi5TemperatureSensor:
    coord = _DummyCoordinator({"interfaces": {master: {"temperature": temperature}}})
    entry = SimpleNamespace(entry_id="entry", title="router")
    return cls(coord, entry)


def test_wifi_temperature_rejects_implausible_values() -> None:
    for cls, master in (
        (KeeneticWifi24TemperatureSensor, "WifiMaster0"),
        (KeeneticWifi5TemperatureSensor, "WifiMaster1"),
    ):
        assert _temp_sensor(cls, master, 300).native_value is None
        assert _temp_sensor(cls, master, -60).native_value is None


def test_wifi_temperature_keeps_real_values() -> None:
    assert _temp_sensor(
        KeeneticWifi24TemperatureSensor, "WifiMaster0", 47
    ).native_value == 47.0
    assert _temp_sensor(
        KeeneticWifi5TemperatureSensor, "WifiMaster1", 0
    ).native_value == 0.0


# ---------------------------------------------------------------------------
# E-F01: options flow must survive auth failure from the live runtime client
# ---------------------------------------------------------------------------


async def _open_options_with_auth_failing_runtime() -> dict[str, Any]:
    class RuntimeClient:
        async def async_get_clients(self) -> list[dict[str, Any]]:
            raise KeeneticAuthError("session revoked on router")

    entry = _entry(RuntimeClient())
    flow = KeeneticOptionsFlow(entry)
    _pin_flow_result_helpers(flow)
    flow.hass = SimpleNamespace(config_entries=ConfigEntries())
    return await flow.async_step_init(None)


def test_options_flow_survives_runtime_client_auth_error() -> None:
    result = asyncio.run(_open_options_with_auth_failing_runtime())

    assert result["type"] == "form"
    assert result["step_id"] == "init"


# ---------------------------------------------------------------------------
# E-F02: every config-flow abort reason must have a translation
# ---------------------------------------------------------------------------


def test_config_abort_reasons_have_translations() -> None:
    source = (_COMPONENT_DIR / "config_flow.py").read_text(encoding="utf-8")
    reasons = set(re.findall(r'async_abort\(reason="([a-z_]+)"\)', source))
    assert reasons, "expected at least one abort reason in config_flow.py"

    for strings_file in (
        _COMPONENT_DIR / "strings.json",
        _COMPONENT_DIR / "translations" / "en.json",
    ):
        abort_keys = set(
            json.loads(strings_file.read_text(encoding="utf-8"))["config"]["abort"]
        )
        missing = reasons - abort_keys
        assert not missing, f"{strings_file.name} missing config.abort keys: {missing}"
