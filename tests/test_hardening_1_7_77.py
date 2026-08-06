"""Hardening tests for the 1.7.77 audit round.

- The live dedup path (`_FingerprintedCoordinatorEntity._handle_coordinator_update`)
  must apply the same volatile-key exclusion as `ClientEntity._client_fingerprint`:
  the nested ``neighbour`` dict and its ``neighbour-expired`` /
  ``neighbour-leasetime`` copies are refreshed by the ARP/ND merge on every fast
  tick, so a fingerprint that keeps them writes state every poll.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.keenetic_router_pro.api.client import KeeneticClient
from custom_components.keenetic_router_pro.api.errors import KeeneticApiError
from custom_components.keenetic_router_pro.api.target import (
    normalize_connection_target,
)
from custom_components.keenetic_router_pro.entity import ClientEntity


class _DummyCoordinator:
    def __init__(self, data: dict) -> None:
        self.data = data
        self.last_update_success = True

    def async_add_listener(self, *_a, **_kw):
        return lambda: None


_CLIENT = {
    "mac": "aa:bb:cc:00:00:01",
    "ip": "10.0.0.5",
    "link": "up",
    "neighbour": {"expired": False, "leasetime": 129, "last-seen": 3},
    "neighbour-expired": False,
    "neighbour-leasetime": 129,
}


def _make_entity(client: dict) -> tuple[ClientEntity, list[int]]:
    coord = _DummyCoordinator({"clients_by_mac": {"aa:bb:cc:00:00:01": client}})
    entity = ClientEntity(
        coordinator=coord,
        entry_id="entry",
        title="router",
        mac="AA:BB:CC:00:00:01",
        label="phone",
    )
    writes: list[int] = []
    entity.async_write_ha_state = lambda: writes.append(1)  # type: ignore[method-assign]
    return entity, writes


def test_live_dedup_ignores_volatile_neighbour_fields() -> None:
    client = dict(_CLIENT)
    entity, writes = _make_entity(client)

    entity._handle_coordinator_update()
    assert writes == [1]

    # Next fast tick: only the volatile neighbour counters moved.
    client.update(
        {
            "neighbour": {"expired": False, "leasetime": 128, "last-seen": 4},
            "neighbour-leasetime": 128,
        }
    )
    entity._handle_coordinator_update()

    assert writes == [1], "state write not suppressed for volatile neighbour churn"


def test_live_dedup_still_writes_on_meaningful_change() -> None:
    client = dict(_CLIENT)
    entity, writes = _make_entity(client)

    entity._handle_coordinator_update()
    client["ip"] = "10.0.0.6"
    entity._handle_coordinator_update()

    assert writes == [1, 1]


# --- A-F01: firmware-update start must classify 404 by HTTP status ---------


async def test_update_start_5xx_mentioning_404_is_not_treated_as_missing() -> None:
    """A 502 whose body text contains "404" must surface, not fall through."""
    client = KeeneticClient("192.168.1.1", "user", "pass")
    client._rci_get = AsyncMock(return_value={"ndw": {"components": "base,wifi"}})
    client._request = AsyncMock(
        side_effect=KeeneticApiError("502 Bad Gateway: /404.html", status=502)
    )
    client._rci_post = AsyncMock(return_value={"status": "accepted"})

    with pytest.raises(HomeAssistantError):
        await client.async_start_firmware_update()
    client._rci_post.assert_not_awaited()


async def test_update_start_404_without_literal_404_text_falls_back() -> None:
    """A real 404 with an empty body must still fall back to system/update."""
    client = KeeneticClient("192.168.1.1", "user", "pass")
    client._rci_get = AsyncMock(return_value={"ndw": {"components": "base,wifi"}})
    client._request = AsyncMock(side_effect=KeeneticApiError("HTTP error", status=404))
    client._rci_post = AsyncMock(return_value={"status": "accepted"})

    assert await client.async_start_firmware_update() is True
    client._rci_post.assert_awaited_once_with(
        "system/update", {"confirm": True}, allow_text=True
    )


# --- A-F02: bare IPv6 host must be accepted, not rejected as a bad port ----


def test_bare_ipv6_host_is_bracketed() -> None:
    target = normalize_connection_target("2001:db8::1", 8080, False)
    assert target.base_url == "http://[2001:db8::1]:8080"
