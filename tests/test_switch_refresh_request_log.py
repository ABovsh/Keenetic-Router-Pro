"""Exercise switch confirmations through the real client's HTTP boundary."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from copy import deepcopy
from typing import Any

import pytest
from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.keenetic_router_pro.api import KeeneticClient
from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from custom_components.keenetic_router_pro.sensor.wireguard import KeeneticWgUptimeSensor
from custom_components.keenetic_router_pro.switch import (
    KeeneticCryptoMapEnabledSwitch,
    KeeneticVpnSwitch,
    KeeneticWanEnabledSwitch,
    KeeneticWifiSwitch,
)
from tests.fixtures.system_rci import SYSTEM_INFO
from tests.test_aiohttp_lifecycle import _Response
from tests.test_refresh_request_log import (
    _BATCH_PRIMARY,
    _batch,
    _expected,
    _get,
    _parse,
    _tick,
    RecordingSession,
    Request,
)

_WIFI = "WifiMaster0/AccessPoint0"
_WAN = "PPPoE0"
_VPN = "Wireguard0"
_SAVE = _parse("system configuration save")
_SCOPE_FAILURE = {
    "wifi": "show/mws/member",
    "wan": "show/ping-check",
    "vpn": "show/interface",
    "ipsec": "crypto/map",
}


class SwitchingSession(RecordingSession):
    """Apply writes to fake RCI responses and optionally hold one read in flight."""

    def __init__(self, *, batch: bool, family: str) -> None:
        super().__init__(batch=batch)
        self.family = family
        self.wan_id = _WAN
        self.hold_path: str | None = None
        self.payloads = deepcopy(self.payloads)
        interfaces = self.payloads["show/interface"]
        interfaces[_WIFI] = {
            "id": _WIFI, "type": "AccessPoint", "group": "Bridge0",
            "ssid": "Main", "state": "up",
        }
        if family == "vpn":
            interfaces[_VPN].update({"global": False, "role": [], "priority": None})
        self._set_enabled(True)

    def command(self, enabled: bool) -> str:
        if self.family == "ipsec":
            return "crypto map Site\n" + ("enable" if enabled else "no enable")
        target = {"wifi": _WIFI, "wan": self.wan_id, "vpn": _VPN}[self.family]
        return f"interface {target} {'up' if enabled else 'down'}"

    def _set_enabled(self, enabled: bool) -> None:
        if self.family == "ipsec":
            self.payloads["crypto/map"]["Site"]["enable"] = enabled
            return
        target = {"wifi": _WIFI, "wan": self.wan_id, "vpn": _VPN}[self.family]
        interfaces = self.payloads["show/interface"]
        interfaces[target]["state"] = "up" if enabled else "down"
        if self.family == "wifi":
            host_payload = self.payloads["show/ip/hotspot/host"]
            host_payload["host"][0]["active"] = enabled
            node = self.payloads["show/mws/member"]["member"][1]
            node.update({"internet-available": enabled, "associations": int(enabled)})
        if self.family == "wan":
            interfaces[target]["summary"]["layer"]["conf"] = "running" if enabled else "disabled"
            backup = _VPN if target == _WAN else _WAN
            interfaces[target]["defaultgw"] = enabled
            interfaces[backup]["defaultgw"] = not enabled
            self.payloads["show/ping-check"] = {
                "pingcheck": [
                    {"profile": "Main", "interface": {target: {"status": "pass" if enabled else "fail"}}},
                ],
            }

    async def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        target = kwargs.get("json")
        if url.endswith("/rci/parse") and target in (
            self.command(True), self.command(False), _SAVE[2],
        ):
            self.requests[_parse(target)] += 1
            if target != _SAVE[2]:
                self._set_enabled(target == self.command(True))
            return _Response({"status": "ok"})
        response = await super().request(method, url, **kwargs)
        if self.hold_path and url.endswith(self.hold_path):
            self.entered.set()
            await self.resume.wait()
        return response


async def _warm(batch: bool, family: str, entry):
    session = SwitchingSession(batch=batch, family=family)
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    await client.async_start(session)  # type: ignore[arg-type]
    session.take_log()
    coordinator = KeeneticCoordinator(object(), client)  # type: ignore[arg-type]
    # Wi-Fi still needs hotspot and mesh presence when entities do not request hosts.
    coordinator.needs_client_data = family != "wifi"
    assert await _tick(coordinator, session) == _expected(batch, "startup", "startup")
    switch = {
        "wifi": lambda: KeeneticWifiSwitch(coordinator, entry, client, _WIFI, "Main"),
        "wan": lambda: KeeneticWanEnabledSwitch(coordinator, entry, client, _WAN),
        "vpn": lambda: KeeneticVpnSwitch(coordinator, entry, client, _VPN, {}),
        "ipsec": lambda: KeeneticCryptoMapEnabledSwitch(coordinator, entry, client, "Site"),
    }[family]()
    assert switch.available and switch.is_on
    return coordinator, session, switch


def _confirming_reads(batch: bool, family: str, *, medium: bool = False) -> Counter[Request]:
    stats = "stats" if medium else "none"
    if family == "wifi":
        return _expected(batch, "mesh request", stats)
    reads = _expected(batch, "fast", stats)
    if family == "ipsec":
        reads[_get("crypto/map")] += 1
        if batch:
            del reads[_batch(*_BATCH_PRIMARY)]
            reads[_batch(*_BATCH_PRIMARY, "show/ipsec")] += 1
        else:
            reads[_get("show/ipsec")] += 1
    return reads


async def _write(switch, session: SwitchingSession, enabled: bool) -> None:
    await (switch.async_turn_on() if enabled else switch.async_turn_off())
    assert session.take_log() == Counter((_parse(session.command(enabled)), _SAVE))


def _assert_confirmed(coordinator: KeeneticCoordinator, switch, family: str, enabled: bool) -> None:
    assert switch.available and switch.is_on is enabled
    data = coordinator.data
    if family == "wifi":
        assert data["mesh_nodes_by_cid"]["extender-1"]["connected"] is enabled
        event = "connected_clients" if enabled else "disconnected_clients"
        assert "aa:bb:cc:dd:ee:ff" in data[event]
    elif family == "wan":
        active, previous = (_WAN, _VPN) if enabled else (_VPN, _WAN)
        assert data["wan_status"]["interface"] == active
        assert data["active_wan"] == active
        assert data["wan_failover"] == {"from": previous, "to": active}
        assert data["wan_observation_fresh"]
        assert data["wan_by_id"][_WAN]["internet_access"] is enabled
    elif family == "vpn":
        assert data["wireguard"]["profiles"][_VPN]["enabled"] is enabled
    else:
        assert data["crypto_maps_fresh"]
        assert data["crypto_maps"]["Site"]["_sample_ts"] is not None


@pytest.fixture(params=["wifi", "wan", "vpn", "ipsec"])
def family(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.mark.parametrize("batch", [True, False], ids=["batch", "405-fallback"])
@pytest.mark.parametrize("count", [7, 8], ids=["fast", "medium"])
async def test_switch_confirmation_reads(batch, count, family, keenetic_entry) -> None:
    coordinator, session, switch = await _warm(batch, family, keenetic_entry)
    coordinator._refresh_count = count

    await _write(switch, session, False)

    reads = await _tick(coordinator, session)
    _assert_confirmed(coordinator, switch, family, False)
    assert reads == _confirming_reads(batch, family, medium=count == 8)

    coordinator._refresh_count = 11
    profile = "fast without clients" if family == "wifi" else "fast"
    assert await _tick(coordinator, session) == _expected(batch, profile)


async def test_switch_confirmation_survives_newer_write(family, keenetic_entry) -> None:
    coordinator, session, switch = await _warm(True, family, keenetic_entry)
    coordinator._refresh_count = 7
    await _write(switch, session, False)
    # With batch firmware the first read has already captured the old interface
    # rows. IPsec config is read outside that batch so hold its response instead.
    session.hold_path = "/rci/crypto/map" if family == "ipsec" else "/rci/"
    async with asyncio.timeout(2), asyncio.TaskGroup() as tasks:
        running = tasks.create_task(_tick(coordinator, session))
        await session.entered.wait()
        await switch.async_turn_on()
        session.resume.set()
    session.hold_path = None

    assert running.result() == _confirming_reads(True, family) + Counter(
        (_parse(session.command(True)), _SAVE)
    )
    _assert_confirmed(coordinator, switch, family, False)

    # Skip the next medium projection so a dropped scope cannot appear to work.
    coordinator._refresh_count = 11
    assert await _tick(coordinator, session) == _confirming_reads(True, family)
    _assert_confirmed(coordinator, switch, family, True)
    coordinator._refresh_count = 13
    profile = "fast without clients" if family == "wifi" else "fast"
    assert await _tick(coordinator, session) == _expected(True, profile)


async def test_switch_confirmation_retries_failed_evidence(family, keenetic_entry) -> None:
    coordinator, session, switch = await _warm(False, family, keenetic_entry)
    coordinator._refresh_count = 7
    await _write(switch, session, False)
    read = _SCOPE_FAILURE[family]
    session.status[read] = 503

    assert await _tick(coordinator, session) == _confirming_reads(False, family)
    if family == "wan":
        assert not coordinator.data["wan_observation_fresh"]
        assert coordinator.data["wan_by_id"][_WAN]["internet_access"] is True
    del session.status[read]
    coordinator._refresh_count = 11
    assert await _tick(coordinator, session) == _confirming_reads(False, family)
    assert switch.available and switch.is_on is False
    if family == "wan":
        assert coordinator.data["wan_by_id"][_WAN]["internet_access"] is False
        assert coordinator.data["wan_observation_fresh"]


@pytest.mark.parametrize("failure", ["empty system", "cancelled"])
async def test_switch_confirmation_retries_unsuccessful_tick(family, failure, keenetic_entry) -> None:
    coordinator, session, switch = await _warm(True, family, keenetic_entry)
    coordinator._refresh_count = 7
    await _write(switch, session, False)

    if failure == "empty system":
        session.payloads["show/system"] = {}
        with pytest.raises(UpdateFailed, match="Critical router payload empty"):
            await _tick(coordinator, session)
        session.payloads["show/system"] = SYSTEM_INFO
    else:
        session.hold_path = "/rci/"
        async with asyncio.timeout(2), asyncio.TaskGroup() as tasks:
            running = tasks.create_task(_tick(coordinator, session))
            await session.entered.wait()
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
        session.hold_path = None
    session.take_log()
    assert coordinator._refresh_count == 7
    assert await _tick(coordinator, session) == _confirming_reads(True, family)
    _assert_confirmed(coordinator, switch, family, False)


async def test_ranked_wireguard_wan_switch_updates_wireguard_availability(keenetic_entry) -> None:
    coordinator, session, _switch = await _warm(True, "wan", keenetic_entry)
    session.wan_id = _VPN
    coordinator._refresh_count = 7
    switch = KeeneticWanEnabledSwitch(coordinator, keenetic_entry, coordinator.client, _VPN)
    uptime = KeeneticWgUptimeSensor(coordinator, keenetic_entry, _VPN)
    assert uptime.available

    await _write(switch, session, False)

    assert await _tick(coordinator, session) == _confirming_reads(True, "wan")
    assert switch.available and not switch.is_on
    assert not uptime.available


async def test_ipsec_confirmation_reprobes_cached_component_absence(keenetic_entry) -> None:
    coordinator, session, switch = await _warm(True, "ipsec", keenetic_entry)
    coordinator.client._crypto_map_supported = False
    coordinator._refresh_count = 7

    await _write(switch, session, False)

    assert await _tick(coordinator, session) == _confirming_reads(True, "ipsec")
    _assert_confirmed(coordinator, switch, "ipsec", False)


@pytest.mark.parametrize("confirm_wan", [False, True], ids=["ordinary", "wan-confirmation"])
async def test_neighbour_only_client_can_become_a_new_device(confirm_wan, keenetic_entry) -> None:
    coordinator, session, switch = await _warm(False, "wan", keenetic_entry)
    coordinator.needs_client_data = False
    coordinator._refresh_count = 7
    mac = "12:34:56:78:9a:bc"
    session.payloads["show/ip/hotspot/host"]["host"].append({"mac": mac, "active": True})
    session.payloads["show/ip/neighbour"]["neighbour"].append(
        {"mac": mac, "address-family": "ipv4", "address": "192.0.2.90"}
    )
    if confirm_wan:
        await _write(switch, session, False)

    assert await _tick(coordinator, session) == _expected(False, "fast without clients")
    assert coordinator.data["clients_by_mac"][mac]["neighbour-only"]
    assert mac not in coordinator.data["new_clients"]
    assert mac not in coordinator.data["connected_clients"]

    coordinator._refresh_count = 9
    assert await _tick(coordinator, session) == _expected(False, "slow")
    assert mac in coordinator.data["connected_clients"]
    assert mac in coordinator.data["new_clients"]

    assert await _tick(coordinator, session) == _expected(False, "fast without clients", "stats")
    assert mac not in coordinator.data["connected_clients"]
    assert mac not in coordinator.data["new_clients"]


@pytest.mark.parametrize("batch", [True, False], ids=["batch", "405-fallback"])
async def test_active_vpn_switch_confirms_wan_status(batch, keenetic_entry) -> None:
    coordinator, session, switch = await _warm(batch, "vpn", keenetic_entry)
    interfaces = session.payloads["show/interface"]
    interfaces[_WAN].update(state="down", defaultgw=False, address=None)
    interfaces[_VPN].update({"global": True, "role": ["inet"], "priority": 50, "defaultgw": True})
    coordinator._refresh_count = 8
    assert await _tick(coordinator, session) == _expected(batch, "fast", "stats")
    assert coordinator.data["wan_status"]["status"] == "connected"
    assert coordinator.data["wan_status"]["ip"]
    assert coordinator.data["active_wan"] == _VPN

    await _write(switch, session, False)
    interfaces[_VPN].update(defaultgw=False, address=None)
    coordinator._refresh_count = 11

    assert await _tick(coordinator, session) == _confirming_reads(batch, "vpn")
    assert switch.available and not switch.is_on
    assert coordinator.data["wan_status"]["status"] == "down"
    assert coordinator.data["wan_status"]["ip"] is None
    assert coordinator.data["wan_failover"] == {"from": _VPN, "to": None}


@pytest.mark.parametrize("batch", [True, False], ids=["batch", "405-fallback"])
@pytest.mark.parametrize("status", [404, 503], ids=["missing", "transient"])
async def test_wifi_confirmation_distinguishes_neighbour_failures(batch, status, keenetic_entry):
    coordinator, session, switch = await _warm(batch, "wifi", keenetic_entry)
    coordinator._refresh_count = 7
    session.status["show/ip/neighbour"] = status
    await _write(switch, session, False)
    extra_get = Counter([_get("show/ip/neighbour")]) if batch else Counter()

    assert await _tick(coordinator, session) == _confirming_reads(batch, "wifi") + extra_get
    assert switch.available and not switch.is_on

    coordinator._refresh_count = 11
    profile = "fast without clients" if status == 404 else "mesh request"
    assert await _tick(coordinator, session) == _expected(batch, profile) + extra_get


@pytest.mark.parametrize("failure", ["scope", "critical", "cancelled"])
async def test_switch_confirmation_expires_after_three_attempts(failure, keenetic_entry, caplog):
    coordinator, session, switch = await _warm(True, "ipsec", keenetic_entry)
    caplog.set_level(logging.DEBUG, logger=KeeneticCoordinator.__module__)
    await _write(switch, session, False)
    for count in (7, 11, 13):
        coordinator._refresh_count = count
        if failure == "scope":
            session.status["crypto/map"] = 503
            assert await _tick(coordinator, session) == _confirming_reads(True, "ipsec")
        elif failure == "critical":
            session.payloads["show/system"] = {}
            with pytest.raises(UpdateFailed, match="Critical router payload empty"):
                await _tick(coordinator, session)
            assert session.take_log() == _confirming_reads(True, "ipsec")
        else:
            session.hold_path = "/rci/"
            session.entered.clear()
            async with asyncio.timeout(2), asyncio.TaskGroup() as tasks:
                running = tasks.create_task(_tick(coordinator, session))
                await session.entered.wait()
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
            assert session.take_log() == Counter([_batch(*_BATCH_PRIMARY, "show/ipsec")])

    session.status.clear()
    session.payloads["show/system"] = SYSTEM_INFO
    session.hold_path = None
    coordinator._refresh_count = 17
    assert await _tick(coordinator, session) == _expected(True, "fast")
    assert switch.is_on  # The next normally scheduled IPsec read will observe the change.
    assert any(
        record.levelno == logging.DEBUG
        and "ipsec" in record.message
        and "3 attempts" in record.message
        for record in caplog.records
    )


@pytest.mark.parametrize("batch", [True, False], ids=["batch", "405-fallback"])
async def test_wan_confirmation_projects_backup_traffic_totals(batch, keenetic_entry) -> None:
    coordinator, session, switch = await _warm(batch, "wan", keenetic_entry)
    interfaces = session.payloads["show/interface"]
    interfaces[_WAN].update(rxbytes=1000, txbytes=2000)
    interfaces[_VPN].update(description="Backup ISP", rxbytes=3000, txbytes=4000)
    coordinator._refresh_count = 8
    assert await _tick(coordinator, session) == _expected(batch, "fast", "stats")
    assert coordinator.data["traffic_stats"]["total_rx"] == 1000
    assert coordinator.data["traffic_stats"]["total_tx"] == 2000

    await _write(switch, session, False)
    coordinator._refresh_count = 11
    assert await _tick(coordinator, session) == _confirming_reads(batch, "wan")
    assert coordinator.data["active_wan"] == _VPN
    assert coordinator.data["traffic_stats"]["total_rx"] == 3000
    assert coordinator.data["traffic_stats"]["total_tx"] == 4000
