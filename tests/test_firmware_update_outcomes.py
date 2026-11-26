"""Firmware outcomes through the real RCI client and a fake HTTP session."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import aiohttp
import pytest
from homeassistant.components.update import UpdateEntityFeature
from homeassistant.exceptions import HomeAssistantError

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.keenetic_router_pro import update
from custom_components.keenetic_router_pro.api import KeeneticAuthError, KeeneticClient
from custom_components.keenetic_router_pro.const import FIRMWARE_UPDATE_POLL_S
from tests.test_api_lifecycle import FakeResponse

NODE_IP = "192.0.2.20"
NODE_ID = "node-1"
OLD_VERSION = "5.1"
NEW_VERSION = "5.2"
WINDOW = 15 * 60


class Clock:
    now = 0.0

    async def sleep(self, seconds):
        self.now += seconds


class FirmwareSession:
    def __init__(self, clock, scenario):
        self.clock = clock
        self.scenario = scenario
        self.commands = []
        self.responses = []
        self.progress_reads = 0
        self.version_reads = 0
        self.events = []
        self.dispatched_at = None
        self.wait_error = None
        self.auth_endpoint = None
        self.auth_failures = 0
        self.auth_attempts = []
        self.authentications = 0
        self.commit_response = None

    async def get(self, url, **kwargs):
        return await self.request("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self.request("POST", url, **kwargs)

    async def request(self, method, url, **kwargs):
        address = urlsplit(url)
        path = address.path
        if method == "POST" and path == self.auth_endpoint:
            self.auth_attempts.append(kwargs["headers"].get("Cookie"))
            if len(self.auth_attempts) <= self.auth_failures:
                if path != "/rci/":
                    self.commands.append(path)
                response = FakeResponse(401)
                self.responses.append(response)
                return response
        if method == "POST" and path != "/rci/":
            self.commands.append(path)
            self.dispatched_at = self.clock.now
            response = self._command(path, kwargs)
        elif path == "/rci/":
            response = FakeResponse(json_data={})
        elif path == "/rci/system/update/status":
            self.progress_reads += 1
            if self.progress_reads <= 3:
                response = FakeResponse(json_data={"in-progress": True, "progress": 35})
            elif self.scenario == "progress-before-success":
                response = FakeResponse(json_data={"in-progress": True, "progress": 65})
            else:
                response = FakeResponse(503, text="rebooting")
        elif path == "/rci/show/system":
            response = self._version_response(path)
        elif path in ("/rci/show/version", "/rci/show/mws/member"):
            if self.dispatched_at is None or address.hostname == NODE_IP:
                response = FakeResponse(
                    json_data={
                        "title": OLD_VERSION,
                        "ndw": {"components": "" if self.scenario == "legacy" else "base"},
                    }
                )
            else:
                response = self._version_response(path)
        else:
            raise AssertionError(f"Unexpected request: {method} {path}")
        self.responses.append(response)
        return response

    def _command(self, path, kwargs):
        if path == "/rci/parse":
            assert kwargs["json"] == f"mws member {NODE_ID} update start"
            if self.scenario == "lost-mws":
                raise aiohttp.ServerDisconnectedError()
            if self.scenario == "commit-timeout":
                return FakeResponse(405)
            if self.scenario == "refused":
                return FakeResponse(
                    headers={"Content-Type": "application/json"},
                    json_data=[{"level": "error", "msg": "update unavailable"}],
                )
        if path == "/rci/components/commit":
            if self.scenario == "commit-timeout":
                raise TimeoutError
            if self.scenario == "auth-error":
                raise KeeneticAuthError("session expired")
            if self.scenario == "transport-cancelled":
                raise asyncio.CancelledError
            if self.scenario == "empty-503":
                return FakeResponse(503)
            if self.commit_response is not None:
                return self.commit_response
        if self.scenario == "refused":
            return FakeResponse(400, text="update refused")
        return FakeResponse(204)

    def _version_response(self, path):
        elapsed = self.clock.now - self.dispatched_at
        self.version_reads += 1
        if self.wait_error is not None:
            raise self.wait_error
        if self.scenario == "never-back" or (20 <= elapsed < 40):
            return FakeResponse(401 if self.scenario == "reboot-401" else 503)
        version = OLD_VERSION if elapsed < 40 or self.scenario == "old-version" else NEW_VERSION
        self.events.append(("version", version, self.clock.now))
        if path == "/rci/show/mws/member":
            return FakeResponse(json_data=[{"cid": NODE_ID, "state": "up", "fw": version}])
        return FakeResponse(json_data={"title": version})


def make_update(monkeypatch, target, scenario):
    clock = Clock()
    session = FirmwareSession(clock, scenario)
    monkeypatch.setattr(update, "monotonic", lambda: clock.now)
    monkeypatch.setattr(update.asyncio, "sleep", clock.sleep)
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, port=80)
    client._session = session
    client._authenticated = True
    client._auth_header = {"Cookie": "stale"}
    client._authenticate_to_node = AsyncMock(return_value={"Cookie": "node"})
    client.async_get_clients = AsyncMock(
        return_value=[{"mac": "00:11:22:33:44:55", "system-mode": "extender", "active": True}]
    )

    async def authenticate():
        session.authentications += 1
        client._auth_header = {"Cookie": "fresh"}
        client._authenticated = True

    client._async_authenticate_configured = authenticate
    node = {"cid": NODE_ID, "ip": NODE_IP, "firmware": OLD_VERSION, "connected": True}
    coordinator = SimpleNamespace(
        data={
            "system": {"title": OLD_VERSION},
            "mesh_nodes": [node],
        },
        request_update_check=lambda: session.events.append(("check", clock.now)),
    )

    async def refresh():
        session.events.append(("refresh", clock.now))

    coordinator.async_request_refresh = refresh
    entry = SimpleNamespace(entry_id="entry", title="Router")
    if target == "mesh":
        entity = update.KeeneticMeshFirmwareUpdate(coordinator, entry, NODE_ID, client)
    else:
        entity = update.KeeneticFirmwareUpdate(coordinator, entry, client)
    writes = []
    entity.async_write_ha_state = lambda: writes.append((clock.now, entity.in_progress))
    return entity, session, clock, writes


@pytest.mark.parametrize("target", ["router", "mesh"])
@pytest.mark.parametrize(
    "scenario",
    ["commit-timeout", "refused", "reboot-401", "old-version", "never-back"],
)
async def test_firmware_outcome_matrix(monkeypatch, target, scenario):
    entity, session, clock, writes = make_update(monkeypatch, target, scenario)

    if scenario == "refused":
        with pytest.raises(HomeAssistantError):
            await entity.async_install(None, False)
        assert clock.now == 0
        assert not session.events
    elif scenario in ("old-version", "never-back"):
        message = "still reports" if scenario == "old-version" else "did not return"
        with pytest.raises(HomeAssistantError, match=message):
            await entity.async_install(None, False)
        assert clock.now == WINDOW
        if scenario == "never-back":
            assert not session.events
        else:
            assert session.events[-2:] == [("check", WINDOW), ("refresh", WINDOW)]
    else:
        await entity.async_install(None, False)
        assert 40 <= clock.now < WINDOW
        assert session.events[-3:] == [
            ("version", NEW_VERSION, clock.now),
            ("check", clock.now),
            ("refresh", clock.now),
        ]
        assert any(event[:2] == ("version", OLD_VERSION) for event in session.events)
        if target == "router":
            assert session.progress_reads >= 4  # Real API converts the 503 to {}.

    if target == "router":
        assert session.commands == ["/rci/components/commit"]
    elif scenario in ("commit-timeout", "refused"):
        expected = ["/rci/parse", "/rci/components/commit"]
        if scenario == "refused":
            expected.append("/rci/system/update")
        assert session.commands == expected
    else:
        assert session.commands == ["/rci/parse"]

    assert all(response.closed for response in session.responses)
    assert entity.in_progress is False
    if scenario != "refused":
        assert writes[0][1] is not False
        assert writes[-1][1] is False
        progress_writes = writes[:-1]
        assert all(
            current_time - previous_time >= FIRMWARE_UPDATE_POLL_S and current != previous
            for (previous_time, previous), (current_time, current)
            in zip(progress_writes, progress_writes[1:])
        )
        if target == "router":
            assert sum(value == 35 for _, value in writes) == 1


@pytest.mark.parametrize("scenario", ["lost-mws", "auth-error", "empty-503", "transport-cancelled"])
async def test_ambiguous_dispatch_only_sends_one_command(monkeypatch, scenario):
    target = "mesh" if scenario == "lost-mws" else "router"
    entity, session, _, _ = make_update(monkeypatch, target, scenario)

    await entity.async_install(None, False)

    assert session.commands == ["/rci/parse" if target == "mesh" else "/rci/components/commit"]
    assert session.events[-3][:2] == ("version", NEW_VERSION)


@pytest.mark.parametrize(
    ("target", "scenario", "endpoint"),
    [
        ("router", "normal", "/rci/"),
        ("router", "normal", "/rci/components/commit"),
        ("router", "legacy", "/rci/system/update"),
        ("mesh", "normal", "/rci/parse"),
    ],
)
@pytest.mark.parametrize("failures", [1, 2])
async def test_firmware_http_401_reauthenticates_and_retries_once(
    monkeypatch, target, scenario, endpoint, failures,
):
    entity, session, clock, _ = make_update(monkeypatch, target, scenario)
    session.auth_endpoint = endpoint
    session.auth_failures = failures

    if failures == 1:
        await entity.async_install(None, False)
        assert session.events[-3][:2] == ("version", NEW_VERSION)
    else:
        with pytest.raises(HomeAssistantError, match="authentication rejected"):
            await entity.async_install(None, False)
        assert not session.version_reads
        assert not session.progress_reads
        assert clock.now == 0
        assert session.commands == ([] if endpoint == "/rci/" else [endpoint, endpoint])

    assert session.authentications == 1
    assert session.auth_attempts == ["stale", "fresh"]
    assert all(response.closed for response in session.responses)


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
async def test_firmware_commit_with_inconclusive_http_status_polls_outcome(monkeypatch, status):
    entity, session, _, _ = make_update(monkeypatch, "router", "normal")
    session.commit_response = FakeResponse(status, text="<html>Gateway unavailable</html>")

    await entity.async_install(None, False)

    assert session.commands == ["/rci/components/commit"]
    assert session.events[-3][:2] == ("version", NEW_VERSION)


async def test_firmware_commit_with_400_rci_error_fails_before_polling(monkeypatch):
    entity, session, clock, _ = make_update(monkeypatch, "router", "normal")
    session.commit_response = FakeResponse(
        400, headers={"Content-Type": "application/json"}, text='{"error": "update refused"}'
    )

    with pytest.raises(HomeAssistantError, match="did not accept"):
        await entity.async_install(None, False)

    assert session.commands == ["/rci/components/commit"]
    assert not session.version_reads
    assert clock.now == 0


def test_mesh_firmware_update_retains_published_features(monkeypatch):
    entity, _, _, _ = make_update(monkeypatch, "mesh", "normal")

    assert entity._attr_supported_features == (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES
    )
    assert entity.in_progress is False


async def test_finished_update_skips_progress_write_in_same_poll(monkeypatch):
    entity, _, clock, writes = make_update(monkeypatch, "router", "progress-before-success")

    await entity.async_install(None, False)

    assert [value for time, value in writes if time == clock.now] == [False]


@pytest.mark.parametrize("target", ["router", "mesh"])
async def test_firmware_cancelled_read_clears_progress(monkeypatch, target):
    entity, session, _, writes = make_update(monkeypatch, target, "cancel")
    session.wait_error = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await entity.async_install(None, False)

    assert len(session.commands) == 1
    assert not session.events
    assert writes[0][1] is not False
    assert writes[-1][1] is False
    assert entity.in_progress is False


@pytest.mark.parametrize("target", ["router", "mesh"])
@pytest.mark.parametrize("phase", ["dispatch", "wait"])
async def test_firmware_task_cancellation_propagates(monkeypatch, target, phase):
    entity, session, clock, writes = make_update(monkeypatch, target, "cancel")
    blocked = asyncio.Event()
    original_request = session.request

    async def request(method, url, **kwargs):
        response = await original_request(method, url, **kwargs)
        if phase == "dispatch" and session.commands:
            async with response:
                blocked.set()
                await asyncio.Future()
        return response

    async def sleep(seconds):
        if phase == "wait":
            blocked.set()
            await asyncio.Future()
        await clock.sleep(seconds)

    monkeypatch.setattr(session, "request", request)
    monkeypatch.setattr(update.asyncio, "sleep", sleep)
    task = asyncio.create_task(entity.async_install(None, False))
    await blocked.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(session.commands) == 1
    assert not session.events
    assert writes[-1][1] is False
