"""Hardening tests for the 1.7.74 deep-audit round.

One test per verified defect:

* A-F01 — ``_is_endpoint_missing`` must trust the real HTTP status when the
  error carries one, so a transient 5xx whose body mentions "not found"
  cannot permanently latch a capability cache off.
* C-F01 — ``coerce_byte_count`` must reject finite-but-absurd magnitudes the
  same way ``coerce_seconds`` does, protecting TOTAL_INCREASING statistics.
* B-F01 — the new-device listener must not re-fire ``EVENT_NEW_DEVICE`` on
  the first failed refresh after a success (coordinator data is unchanged).
* B-F02 — a pending one-shot host-policies refresh must survive a tick that
  raises, so the fast confirmation retries on the next tick.
* D-F02 — switching connection mode in reconfigure must not prefill the old
  mode's port/SSL into the new mode's form.
* D-F01 — diagnostics must keep ``entry.domain`` (the constant integration
  slug) readable despite the "domain" FQDN redaction key.
"""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SSL
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.keenetic_router_pro.api import (
    KeeneticApiError,
    KeeneticClient,
    _is_endpoint_missing,
)
from custom_components.keenetic_router_pro.config_flow import (
    DEFAULT_PORT,
    KeeneticRouterProConfigFlow,
)
from custom_components.keenetic_router_pro.const import (
    CONF_CONNECTION_MODE,
    CONNECTION_MODE_DIRECT,
    CONNECTION_MODE_KEENDNS_PROTECTED,
)
from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from custom_components.keenetic_router_pro.utils import coerce_byte_count


# ---------------------------------------------------------------------------
# A-F01: endpoint-missing detection must be status-aware
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.headers: dict[str, str] = {}
        self._body = body

    async def text(self) -> str:
        return self._body


def _http_error(status: int, body: str) -> KeeneticApiError:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    with pytest.raises(KeeneticApiError) as excinfo:
        asyncio.run(client._handle_response(_FakeResp(status, body), "show/x"))
    return excinfo.value


def test_transient_5xx_with_not_found_body_is_not_endpoint_missing() -> None:
    err = _http_error(500, 'Core::Scgi: not found: "member" (busy)')
    assert _is_endpoint_missing(err) is False


def test_5xx_body_containing_404_digits_is_not_endpoint_missing() -> None:
    err = _http_error(503, "queue depth 40404 exceeded")
    assert _is_endpoint_missing(err) is False


def test_real_404_is_endpoint_missing() -> None:
    err = _http_error(404, "not found")
    assert _is_endpoint_missing(err) is True


def test_statusless_error_keeps_substring_fallback() -> None:
    # Errors built from /rci/parse text output carry no HTTP status; the
    # historical substring heuristic must keep working for them.
    assert _is_endpoint_missing(KeeneticApiError('not found: "crypto/map"'))
    assert not _is_endpoint_missing(KeeneticApiError("Connection refused"))


# ---------------------------------------------------------------------------
# C-F01: byte counters need an upper bound
# ---------------------------------------------------------------------------


def test_coerce_byte_count_rejects_absurd_magnitude() -> None:
    assert coerce_byte_count(1e19) is None
    assert coerce_byte_count(10**20) is None
    assert coerce_byte_count(float("1e100")) is None


def test_coerce_byte_count_keeps_large_real_counters() -> None:
    # A saturated 64-bit-ish counter within physical reality must survive.
    assert coerce_byte_count(2**62) == 2**62
    assert coerce_byte_count("123456789012345") == 123456789012345


# ---------------------------------------------------------------------------
# B-F01: new-device listener must skip failed ticks
# ---------------------------------------------------------------------------


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def async_fire(self, event_type: str, event_data: dict[str, Any]) -> None:
        self.events.append((event_type, event_data))


class _FakeEntry:
    entry_id = "entry_hard_174"
    title = "Router"

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.runtime_data = None
        self.unload_callbacks: list[Any] = []

    def async_on_unload(self, callback: Any) -> None:
        self.unload_callbacks.append(callback)

    def add_update_listener(self, listener: Any) -> Any:
        return lambda: None


class _FakeListenerCoordinator:
    def __init__(self, hass: Any, client: Any) -> None:
        self.hass = hass
        self.client = client
        self.listeners: list[Any] = []
        self.last_update_success = True
        self.data = {
            "new_clients": {"aa:bb:cc:dd:ee:ff"},
            "clients_by_mac": {
                "aa:bb:cc:dd:ee:ff": {
                    "mac": "AA-BB-CC-DD-EE-FF",
                    "name": "Tablet",
                    "ip": "192.0.2.40",
                }
            },
            "clients": [],
            "mesh_nodes": [],
        }
        _FakeListenerCoordinator.last_instance = self

    async def async_config_entry_first_refresh(self) -> None:
        return None

    def async_add_listener(self, listener: Any) -> Any:
        self.listeners.append(listener)
        return lambda: None


class _FakeStartClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def async_start(self, session: Any) -> None:
        return None


def test_new_device_listener_skips_tick_after_update_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import custom_components.keenetic_router_pro as integration

    monkeypatch.setattr(integration, "KeeneticClient", _FakeStartClient)
    monkeypatch.setattr(integration, "KeeneticCoordinator", _FakeListenerCoordinator)
    monkeypatch.setattr(integration, "async_get_clientsession", lambda hass: "session")
    monkeypatch.setattr(
        integration.ir, "async_create_issue", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        integration, "_async_migrate_mesh_unique_ids", lambda *a, **k: None
    )

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_forward_entry_setups=_async_noop,
        ),
        bus=_FakeBus(),
    )
    entry = _FakeEntry(
        {
            "host": TEST_HOST,
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            "ssl": False,
        }
    )

    assert asyncio.run(integration.async_setup_entry(hass, entry)) is True
    coordinator = _FakeListenerCoordinator.last_instance

    # First failed refresh after a success: HA still notifies listeners,
    # with `data` (and therefore `new_clients`) unchanged.
    coordinator.last_update_success = False
    coordinator.listeners[0]()
    assert hass.bus.events == []

    # A successful tick fires normally.
    coordinator.last_update_success = True
    coordinator.listeners[0]()
    assert len(hass.bus.events) == 1


async def _async_noop(*args: Any, **kwargs: Any) -> None:
    return None


# ---------------------------------------------------------------------------
# B-F02: pending host-policies refresh survives a failed tick
# ---------------------------------------------------------------------------


class _AlwaysFailingClient:
    _rci_batch_supported: bool | None = False
    _hotspot_subpath_winner: str | None = None
    host = TEST_HOST

    def clear_tick_cache(self) -> None:
        return None

    async def prefetch_tick(self, tree: dict) -> bool:
        return False

    def __getattr__(self, name: str) -> Any:
        if name.startswith("async_get_"):

            async def _fail(*args: Any, **kwargs: Any) -> Any:
                raise KeeneticApiError("router offline")

            return _fail
        raise AttributeError(name)


def test_host_policies_pending_survives_failed_tick() -> None:
    coordinator = KeeneticCoordinator(object(), _AlwaysFailingClient())  # type: ignore[arg-type]
    coordinator.request_host_policies_refresh()

    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())

    assert coordinator._host_policies_refresh_pending is True


# ---------------------------------------------------------------------------
# D-F02: reconfigure mode switch must reset port/SSL prefill
# ---------------------------------------------------------------------------


class _ReconfEntries:
    def __init__(self, entry: SimpleNamespace) -> None:
        self.entry = entry

    def async_get_entry(self, entry_id: str) -> SimpleNamespace | None:
        return self.entry if entry_id == self.entry.entry_id else None


def _schema_default(schema: vol.Schema, key_name: str) -> Any:
    for marker in schema.schema:
        if getattr(marker, "schema", None) == key_name:
            default = getattr(marker, "default", None)
            return default() if callable(default) else default
    raise AssertionError(f"{key_name} not in schema")


def test_reconfigure_keendns_to_direct_resets_port_and_ssl_defaults() -> None:
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={
            CONF_CONNECTION_MODE: CONNECTION_MODE_KEENDNS_PROTECTED,
            CONF_HOST: "https://rsi.example.keenetic.pro",
            CONF_PORT: 443,
            CONF_SSL: True,
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
        },
    )
    flow = KeeneticRouterProConfigFlow()
    flow.hass = SimpleNamespace(config_entries=_ReconfEntries(entry))
    flow.context = {"entry_id": "entry-1"}
    flow._selected_connection_mode = CONNECTION_MODE_DIRECT
    captured: dict[str, Any] = {}
    flow.async_show_form = lambda **kwargs: captured.update(kwargs) or {
        "type": "form",
        "step_id": kwargs["step_id"],
    }

    asyncio.run(flow.async_step_reconfigure_connection(None))

    schema = captured["data_schema"]
    assert _schema_default(schema, CONF_PORT) == DEFAULT_PORT
    assert _schema_default(schema, CONF_SSL) is False


# ---------------------------------------------------------------------------
# D-F01: diagnostics keeps the constant integration domain readable
# ---------------------------------------------------------------------------


def test_diagnostics_preserves_integration_domain() -> None:
    from custom_components.keenetic_router_pro.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = SimpleNamespace(
        title="Router",
        version=1,
        domain="keenetic_router_pro",
        source="user",
        data={},
        options={},
        runtime_data=None,
    )

    result = asyncio.run(async_get_config_entry_diagnostics(None, entry))

    assert result["entry"]["domain"] == "keenetic_router_pro"
