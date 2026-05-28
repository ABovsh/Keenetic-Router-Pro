"""Integration-root lifecycle tests with small Home Assistant fakes."""

from __future__ import annotations

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from custom_components.keenetic_router_pro import (
    ISSUE_INSECURE_HTTP,
    PLATFORMS,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticAuthError
from custom_components.keenetic_router_pro.const import DOMAIN, EVENT_NEW_DEVICE


class _FakeConfigEntries:
    """Capture platform forwarding/unloading calls from async_setup_entry."""

    def __init__(self) -> None:
        self.forwarded: list[tuple[Any, list[str]]] = []
        self.unloaded: list[tuple[Any, list[str]]] = []
        self.reloads: list[str] = []
        self.unload_result = True

    async def async_forward_entry_setups(self, entry: Any, platforms: list[str]) -> None:
        self.forwarded.append((entry, platforms))

    async def async_unload_platforms(self, entry: Any, platforms: list[str]) -> bool:
        self.unloaded.append((entry, platforms))
        return self.unload_result

    async def async_reload(self, entry_id: str) -> None:
        self.reloads.append(entry_id)


class _FakeBus:
    """Capture Home Assistant events fired by the integration."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def async_fire(self, event_type: str, event_data: dict[str, Any]) -> None:
        self.events.append((event_type, event_data))


class _FakeEntry:
    """Minimal ConfigEntry stand-in for lifecycle tests."""

    entry_id = "entry_123"
    title = "Router"

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.runtime_data = None
        self.unload_callbacks: list[Any] = []
        self.update_listeners: list[Any] = []

    def async_on_unload(self, callback: Any) -> None:
        self.unload_callbacks.append(callback)

    def add_update_listener(self, listener: Any) -> Any:
        self.update_listeners.append(listener)
        return lambda: None


class _FakeClient:
    """Fake KeeneticClient that records startup parameters."""

    instances: list["_FakeClient"] = []
    start_error: Exception | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started_with = None
        _FakeClient.instances.append(self)

    async def async_start(self, session: Any) -> None:
        if self.start_error:
            raise self.start_error
        self.started_with = session


class _FakeCoordinator:
    """Fake coordinator with a live payload and listener capture."""

    instances: list["_FakeCoordinator"] = []

    def __init__(self, hass: Any, client: Any) -> None:
        self.hass = hass
        self.client = client
        self.listeners: list[Any] = []
        self.data = {
            "mesh_nodes": [],
            "new_clients": {"aa:bb:cc:dd:ee:ff"},
            "clients_by_mac": {
                "aa:bb:cc:dd:ee:ff": {
                    "mac": "AA-BB-CC-DD-EE-FF",
                    "name": "Kitchen Tablet",
                    "ip": "192.0.2.40",
                    "hostname": "tablet",
                    "interface": {"name": "Home"},
                    "ssid": "Main",
                }
            },
            "clients": [
                {
                    "mac": "AA-BB-CC-DD-EE-FF",
                    "name": "Fallback Should Not Be Used",
                    "ip": "192.0.2.41",
                }
            ],
        }
        _FakeCoordinator.instances.append(self)

    async def async_config_entry_first_refresh(self) -> None:
        return None

    def async_add_listener(self, listener: Any) -> Any:
        self.listeners.append(listener)
        return lambda: None


def _hass() -> SimpleNamespace:
    return SimpleNamespace(
        config_entries=_FakeConfigEntries(),
        bus=_FakeBus(),
    )


def test_setup_entry_starts_client_forwards_platforms_and_fires_new_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful startup wires runtime data, platforms, repair issue and event listener."""
    import custom_components.keenetic_router_pro as integration

    _FakeClient.instances.clear()
    _FakeClient.start_error = None
    _FakeCoordinator.instances.clear()
    created_issues: list[dict[str, Any]] = []

    monkeypatch.setattr(integration, "KeeneticClient", _FakeClient)
    monkeypatch.setattr(integration, "KeeneticCoordinator", _FakeCoordinator)
    monkeypatch.setattr(
        integration,
        "async_get_clientsession",
        lambda hass: "shared-session",
    )
    monkeypatch.setattr(
        integration.ir,
        "async_create_issue",
        lambda *args, **kwargs: created_issues.append(kwargs),
    )
    monkeypatch.setattr(
        integration,
        "_async_migrate_mesh_unique_ids",
        lambda *_args, **_kwargs: None,
    )

    hass = _hass()
    entry = _FakeEntry(
        {
            "host": TEST_HOST,
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            "ssl": False,
        }
    )

    assert asyncio.run(async_setup_entry(hass, entry)) is True

    client = _FakeClient.instances[0]
    coordinator = _FakeCoordinator.instances[0]

    assert client.kwargs["host"] == TEST_HOST
    assert client.kwargs["username"] == TEST_USERNAME
    assert client.kwargs["password"] == TEST_PASSWORD
    assert client.started_with == "shared-session"
    assert entry.runtime_data is not None
    assert entry.runtime_data.client is client
    assert entry.runtime_data.coordinator is coordinator
    assert hass.config_entries.forwarded == [(entry, PLATFORMS)]
    assert created_issues[0]["translation_key"] == ISSUE_INSECURE_HTTP
    assert len(entry.unload_callbacks) == 2

    coordinator.listeners[0]()

    assert hass.bus.events == [
        (
            EVENT_NEW_DEVICE,
            {
                "mac": "aa:bb:cc:dd:ee:ff",
                "name": "Kitchen Tablet",
                "ip": "192.0.2.40",
                "hostname": "tablet",
                "interface": {"name": "Home"},
                "ssid": "Main",
            },
        )
    ]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (KeeneticAuthError("bad credentials"), ConfigEntryAuthFailed),
        (KeeneticApiError("router offline"), ConfigEntryNotReady),
    ],
)
def test_setup_entry_maps_client_startup_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: type[Exception],
) -> None:
    """Startup errors should surface through HA's auth/retry mechanisms."""
    import custom_components.keenetic_router_pro as integration

    _FakeClient.instances.clear()
    _FakeClient.start_error = error

    monkeypatch.setattr(integration, "KeeneticClient", _FakeClient)
    monkeypatch.setattr(
        integration,
        "async_get_clientsession",
        lambda hass: "shared-session",
    )

    hass = _hass()
    entry = _FakeEntry(
        {
            "host": TEST_HOST,
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
        }
    )

    with pytest.raises(expected):
        asyncio.run(async_setup_entry(hass, entry))

    assert hass.config_entries.forwarded == []


@pytest.mark.parametrize(
    "entry_data",
    [
        {"host": "127.0.0.1", "username": TEST_USERNAME, "password": TEST_PASSWORD, "ssl": False},
        {"host": TEST_HOST, "username": TEST_USERNAME, "password": TEST_PASSWORD, "ssl": True},
    ],
)
def test_setup_entry_does_not_create_http_repair_for_loopback_or_ssl(
    monkeypatch: pytest.MonkeyPatch,
    entry_data: dict[str, Any],
) -> None:
    """Only plaintext non-loopback connections should create the security repair."""
    import custom_components.keenetic_router_pro as integration

    _FakeClient.instances.clear()
    _FakeClient.start_error = None
    _FakeCoordinator.instances.clear()
    created_issues: list[dict[str, Any]] = []
    deleted_issues: list[tuple[Any, str, str]] = []

    monkeypatch.setattr(integration, "KeeneticClient", _FakeClient)
    monkeypatch.setattr(integration, "KeeneticCoordinator", _FakeCoordinator)
    monkeypatch.setattr(
        integration,
        "async_get_clientsession",
        lambda hass: "shared-session",
    )
    monkeypatch.setattr(
        integration,
        "_async_migrate_mesh_unique_ids",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        integration.ir,
        "async_create_issue",
        lambda *args, **kwargs: created_issues.append(kwargs),
    )
    monkeypatch.setattr(
        integration.ir,
        "async_delete_issue",
        lambda *args: deleted_issues.append(args),
    )

    hass = _hass()
    entry = _FakeEntry(entry_data)

    assert asyncio.run(async_setup_entry(hass, entry)) is True

    assert created_issues == []
    assert deleted_issues == [
        (hass, DOMAIN, f"{ISSUE_INSECURE_HTTP}_{entry.entry_id}"),
    ]


def test_setup_entry_rejects_unrecoverable_entry_without_host() -> None:
    """A config entry missing host/ip should fail clearly before client creation."""
    hass = _hass()
    entry = _FakeEntry({"username": TEST_USERNAME, "password": TEST_PASSWORD})

    with pytest.raises(ConfigEntryNotReady, match="missing 'host'"):
        asyncio.run(async_setup_entry(hass, entry))


def test_unload_entry_clears_http_repair_issue_only_after_platform_unload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repair issue cleanup should follow a successful platform unload."""
    import custom_components.keenetic_router_pro as integration

    deleted: list[tuple[Any, str, str]] = []
    monkeypatch.setattr(
        integration.ir,
        "async_delete_issue",
        lambda *args: deleted.append(args),
    )

    hass = _hass()
    entry = _FakeEntry({"host": TEST_HOST, "username": TEST_USERNAME, "password": TEST_PASSWORD})

    assert asyncio.run(async_unload_entry(hass, entry)) is True
    assert hass.config_entries.unloaded == [(entry, PLATFORMS)]
    assert deleted == [
        (hass, DOMAIN, f"{ISSUE_INSECURE_HTTP}_{entry.entry_id}"),
    ]

    deleted.clear()
    hass.config_entries.unload_result = False

    assert asyncio.run(async_unload_entry(hass, entry)) is False
    assert deleted == []
