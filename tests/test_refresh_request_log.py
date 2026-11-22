"""Characterize the coordinator's HTTP reads before changing its refresh planner."""

from __future__ import annotations

import asyncio
from collections import Counter
from copy import deepcopy
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest
from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.keenetic_router_pro.api import KeeneticClient
from custom_components.keenetic_router_pro.coordinator import KeeneticCoordinator
from tests.fixtures.clients_rci import CLIENTS, HOST_POLICIES, IP_NEIGHBOURS
from tests.fixtures.dns_rci import NDNS_INFO
from tests.fixtures.mesh_rci import MESH_NODES
from tests.fixtures.system_rci import AVAILABLE_VERSION, CURRENT_VERSION, SYSTEM_INFO
from tests.fixtures.wan_rci import INTERFACES, INTERFACE_STATS
from tests.test_aiohttp_lifecycle import _Response

Request = tuple[str, str, str | tuple[tuple[str, str], ...], tuple[str, ...]]

_HOSTS = {
    "host": [
        *CLIENTS,
        {
            **CLIENTS[0],
            "mac": "aa:bb:cc:00:00:01",
            "name": "Extender",
            "ip": "192.0.2.2",
            "system-mode": "extender",
        },
    ],
}
_PAYLOADS = {
    "components/check-update": AVAILABLE_VERSION,
    "crypto/map": {"Site": {"enable": True, "peer": "198.51.100.1"}},
    "ip/hotspot/host": {"host": [{"mac": mac, **policy} for mac, policy in HOST_POLICIES.items()]},
    "ip/policy": {"Kids": {"description": "Kids"}, "Guests": {"description": "Guests"}},
    "show/interface": INTERFACES,
    "show/ip/hotspot": {"host": _HOSTS},
    "show/ip/hotspot/host": _HOSTS,
    "show/ip/neighbour": {"neighbour": IP_NEIGHBOURS},
    "show/ipsec": {"ipsec_statusall": ""},
    "show/mws/member": {"member": [MESH_NODES[0], {**MESH_NODES[1], "cid": "extender-1"}]},
    "show/ndns": NDNS_INFO,
    "show/ping-check": {"pingcheck": []},
    "show/system": SYSTEM_INFO,
    "show/version": CURRENT_VERSION,
}


def _batch_paths(tree: dict, prefix: str = "") -> list[str]:
    paths = []
    for name, children in tree.items():
        path = f"{prefix}/{name}" if prefix else name
        if isinstance(children, list):
            paths.extend(f"{path}?name={target['name']}" for target in children)
        elif children:
            paths.extend(_batch_paths(children, path))
        else:
            paths.append(path)
    return paths


class RecordingSession:
    """Serve canned router payloads at the aiohttp boundary."""

    def __init__(self, *, batch: bool) -> None:
        self.batch = batch
        self.requests: Counter[Request] = Counter()
        self.payloads = dict(_PAYLOADS)
        self.status: dict[str, int] = {}
        self.missing_stat: str | None = None
        self.pause_system = False
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()
        self.log_changed = asyncio.Event()

    async def get(self, url: str, **kwargs: Any) -> _Response:
        return await self.request("GET", url, **kwargs)

    async def request(
        self, method: str, url: str, *, params: dict | None = None,
        json: Any = None, **_kwargs: Any,
    ) -> _Response:
        parsed = urlsplit(url)
        path = parsed.path
        query = (*parse_qsl(parsed.query, keep_blank_values=True), *(params or {}).items())
        target = json if path == "/rci/parse" else tuple(sorted(query))
        is_batch = path == "/rci/" and method == "POST"
        children = tuple(sorted(_batch_paths(json))) if is_batch else ()
        self.requests[method, path, target, children] += 1
        self.log_changed.set()
        if self.pause_system and (path == "/rci/show/system" or "show/system" in children):
            self.entered.set()
            await self.resume.wait()
        if is_batch:
            return _Response(self._batch_payload(json)) if self.batch else _Response(status=405)
        if path == "/rci/":
            return _Response()
        if path == "/rci/parse":
            show, interface, name, stat = json.split()
            assert (show, interface, stat) == ("show", "interface", "stat")
            return _Response(deepcopy(INTERFACE_STATS[name]))
        if path == "/rci/show/interface/stat":
            return _Response(deepcopy(INTERFACE_STATS[params["name"]]))
        subpath = path.removeprefix("/rci/")
        if subpath in self.status:
            return _Response(status=self.status[subpath])
        return _Response(deepcopy(self.payloads[subpath]))

    def _batch_payload(self, tree: dict, prefix: str = "") -> dict:
        result = {}
        for name, children in tree.items():
            path = f"{prefix}/{name}" if prefix else name
            if path in self.status:
                # Omit an unavailable child so the real client tries its standalone endpoint.
                continue
            if isinstance(children, list):
                result[name] = [
                    {"status": [{"status": "error"}]} if target["name"] == self.missing_stat
                    else deepcopy(INTERFACE_STATS[target["name"]])
                    for target in children
                ]
            elif children:
                result[name] = self._batch_payload(children, path)
            else:
                result[name] = deepcopy(self.payloads[path])
        return result

    def take_log(self) -> Counter[Request]:
        log, self.requests = self.requests, Counter()
        return log

    async def wait_for_log(self, expected: Counter[Request]) -> None:
        while not expected <= self.requests:
            self.log_changed.clear()
            await self.log_changed.wait()


async def _router(batch: bool) -> tuple[KeeneticCoordinator, RecordingSession]:
    session = RecordingSession(batch=batch)
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    await client.async_start(session)  # type: ignore[arg-type]
    assert session.take_log() == Counter({("GET", "/rci/", (), ()): 1})
    return KeeneticCoordinator(object(), client), session  # type: ignore[arg-type]


async def _tick(coordinator: KeeneticCoordinator, session: RecordingSession) -> Counter[Request]:
    coordinator.data = await coordinator._async_update_data()
    return session.take_log()


def _get(path: str, **params: str) -> Request:
    return "GET", f"/rci/{path}", tuple(sorted(params.items())), ()


def _parse(command: str) -> Request:
    return "POST", "/rci/parse", command, ()


def _batch(*paths: str) -> Request:
    return "POST", "/rci/", (), tuple(sorted(paths))


# Literal request groups. The profile and tick tables below select complete
# multisets, without computing cadence from the coordinator's production rules.
_BATCH_PRIMARY = (
    "show/interface", "show/ip/hotspot", "show/ip/neighbour", "show/ping-check", "show/system",
)
_GET_PRIMARY = (
    _get("show/interface"), _get("show/ip/hotspot/host"), _get("show/ip/neighbour"),
    _get("show/ping-check"), _get("show/system"),
)
_BATCH_NO_CLIENTS = ("show/interface", "show/ip/neighbour", "show/ping-check", "show/system")
_GET_NO_CLIENTS = (
    _get("show/interface"), _get("show/ip/neighbour"), _get("show/ping-check"), _get("show/system"),
)
_BATCH_MESH = ("show/mws/member",)
_BATCH_SLOW = ("show/ipsec", "show/mws/member")
_BATCH_VERSION = ("show/ndns", "show/version")
_BATCH_CHECK = ("components/check-update",)
_HOST_POLICIES = (_get("ip/hotspot/host"),)
_POLICY_LIST = (_get("ip/policy"),)
_GET_IPSEC = (_get("crypto/map"),)
_GET_MESH = (_get("show/mws/member"),)
_GET_SLOW = (*_GET_IPSEC, _get("show/ipsec"), *_GET_MESH)
_GET_VERSION = (_get("show/ndns"), _get("show/version"))
_GET_CHECK = (_get("components/check-update"),)
_SELECTOR_READS = (*_HOST_POLICIES, *_POLICY_LIST)
_GET_VERY_SLOW = (*_GET_SLOW, *_GET_VERSION, *_SELECTOR_READS)

# Each profile lists batch children, extra batch-mode GETs, and standalone GETs.
_PRIMARY = {
    "fast": (_BATCH_PRIMARY, (), _GET_PRIMARY),
    "fast without clients": (_BATCH_NO_CLIENTS, (), _GET_NO_CLIENTS),
    "host-policy request": (
        _BATCH_PRIMARY,
        _HOST_POLICIES,
        (*_GET_PRIMARY, *_HOST_POLICIES),
    ),
    "mesh request": ((*_BATCH_PRIMARY, *_BATCH_MESH), (), (*_GET_PRIMARY, *_GET_MESH)),
    "slow without selectors": (
        (*_BATCH_PRIMARY, *_BATCH_SLOW),
        _GET_IPSEC,
        (*_GET_PRIMARY, *_GET_SLOW),
    ),
    "slow": (
        (*_BATCH_PRIMARY, *_BATCH_SLOW),
        (*_GET_IPSEC, *_HOST_POLICIES),
        (*_GET_PRIMARY, *_GET_SLOW, *_HOST_POLICIES),
    ),
    "IPsec latched off": (
        (*_BATCH_PRIMARY, *_BATCH_MESH),
        _HOST_POLICIES,
        (*_GET_PRIMARY, *_GET_MESH, *_HOST_POLICIES),
    ),
    "very slow without selectors": (
        (*_BATCH_PRIMARY, *_BATCH_SLOW, *_BATCH_VERSION),
        _GET_IPSEC,
        (*_GET_PRIMARY, *_GET_SLOW, *_GET_VERSION),
    ),
    "very slow": (
        (*_BATCH_PRIMARY, *_BATCH_SLOW, *_BATCH_VERSION),
        (*_GET_IPSEC, *_SELECTOR_READS),
        (*_GET_PRIMARY, *_GET_VERY_SLOW),
    ),
    "update-check request": (
        (*_BATCH_PRIMARY, *_BATCH_CHECK),
        (),
        (*_GET_PRIMARY, *_GET_CHECK),
    ),
    "very slow with update check": (
        (*_BATCH_PRIMARY, *_BATCH_SLOW, *_BATCH_VERSION, *_BATCH_CHECK),
        (*_GET_IPSEC, *_SELECTOR_READS),
        (*_GET_PRIMARY, *_GET_VERY_SLOW, *_GET_CHECK),
    ),
    # Startup probes mesh separately, before MWS support is known.
    "startup": (
        (*_BATCH_PRIMARY, "show/ipsec", *_BATCH_VERSION, *_BATCH_CHECK),
        (*_GET_IPSEC, *_SELECTOR_READS, *_GET_MESH),
        (*_GET_PRIMARY, *_GET_VERY_SLOW, *_GET_CHECK),
    ),
}

_STATS = {
    "none": ((), ()),
    "stats": (
        (_batch("show/interface/stat?name=PPPoE0", "show/interface/stat?name=Wireguard0"),),
        (_get("show/interface/stat", name="PPPoE0"), _get("show/interface/stat", name="Wireguard0")),
    ),
    # The startup parse probe for Wireguard0 receives the hyphenated keys
    # from the fixture and retries via GET, latching GET mode for later ticks.
    "startup": (
        (_batch("show/interface/stat?name=PPPoE0", "show/interface/stat?name=Wireguard0"),),
        (
            _parse("show interface PPPoE0 stat"), _parse("show interface Wireguard0 stat"),
            _get("show/interface/stat", name="Wireguard0"),
        ),
    ),
}

_CADENCE = (
    (0, "startup", "startup"),
    (1, "fast", "none"),
    (2, "fast", "stats"),
    (3, "slow", "none"),
    (4, "fast", "stats"),
    (5, "fast", "none"),
    (6, "slow", "stats"),
    (7, "fast", "none"),
    (8, "fast", "stats"),
    (9, "slow", "none"),
    (10, "fast", "stats"),
    (11, "fast", "none"),
    (12, "slow", "stats"),
    (13, "fast", "none"),
    (14, "fast", "stats"),
    (15, "very slow", "none"),
    (16, "fast", "stats"),
    (17, "fast", "none"),
    (18, "slow", "stats"),
    (19, "fast", "none"),
    (20, "fast", "stats"),
    (21, "slow", "none"),
    (22, "fast", "stats"),
    (23, "fast", "none"),
    (24, "slow", "stats"),
    (25, "fast", "none"),
    (26, "fast", "stats"),
    (27, "slow", "none"),
    (28, "fast", "stats"),
    (29, "fast", "none"),
    (30, "very slow", "stats"),
    (1439, "fast", "none"),
    (1440, "very slow with update check", "stats"),
    (1441, "fast", "none"),
)


def _expected(batch: bool, primary: str, stats: str = "none") -> Counter[Request]:
    children, extra_gets, standalone = _PRIMARY[primary]
    batch_stats, standalone_stats = _STATS[stats]
    if batch:
        return Counter((_batch(*children), *extra_gets, *batch_stats))
    rejected_probe = (_batch(*children),) if primary == "startup" else ()
    return Counter((*rejected_probe, *standalone, *standalone_stats))


@pytest.fixture(params=[True, False], ids=["batch", "405-fallback"])
def batch(request: pytest.FixtureRequest) -> bool:
    return request.param


async def _warm(batch: bool) -> tuple[KeeneticCoordinator, RecordingSession]:
    coordinator, session = await _router(batch)
    assert await _tick(coordinator, session) == _expected(batch, "startup", "startup")
    assert "extender-1" in coordinator.data["mesh_nodes_by_cid"]
    return coordinator, session


async def test_poll_cadence(batch: bool) -> None:
    coordinator, session = await _router(batch)
    for number, primary, stats in _CADENCE:
        if number == 1439:
            coordinator._refresh_count = number  # Jump to the daily window with warm caches.
        assert coordinator._refresh_count == number
        assert await _tick(coordinator, session) == _expected(batch, primary, stats), number


async def test_partial_interface_stat_batch_retries_only_missing_target() -> None:
    coordinator, session = await _warm(True)
    coordinator._refresh_count = 2
    session.missing_stat = "PPPoE0"
    assert await _tick(coordinator, session) == (
        _expected(True, "fast", "stats") + Counter([_parse("show interface PPPoE0 stat")])
    )
    assert set(coordinator.data["interface_stats"]) == {"PPPoE0", "Wireguard0"}


@pytest.mark.parametrize(
    ("number", "needs_clients", "needs_policies", "profile"),
    [
        (1, False, False, "fast without clients"),
        (3, False, False, "slow without selectors"),
        (15, True, False, "very slow without selectors"),
        (15, True, True, "very slow"),
    ],
)
async def test_client_and_selector_demand(
    batch: bool, number: int, needs_clients: bool, needs_policies: bool, profile: str,
) -> None:
    coordinator, session = await _warm(batch)
    coordinator._refresh_count = number
    coordinator.needs_client_data = needs_clients
    coordinator.needs_policies = needs_policies
    assert await _tick(coordinator, session) == _expected(batch, profile)
    assert "extender-1" in coordinator.data["mesh_nodes_by_cid"]


async def test_ipsec_capability_latches_off(batch: bool) -> None:
    coordinator, session = await _warm(batch)
    session.status.update({"show/ipsec": 404, "crypto/map": 404})
    coordinator._refresh_count = 3
    # Batch firmware omits the unavailable child, so both endpoints can return 404.
    probe = Counter([_get("show/ipsec")]) if batch else Counter()
    assert await _tick(coordinator, session) == _expected(batch, "slow") + probe
    coordinator._refresh_count = 9
    assert await _tick(coordinator, session) == _expected(batch, "IPsec latched off")


_SCOPES = (
    ("request_host_policies_refresh", "host-policy request", "none"),
    ("request_full_refresh", "very slow", "stats"),
    ("request_mesh_refresh", "mesh request", "none"),
    ("request_update_check", "update-check request", "none"),
)


@pytest.mark.parametrize(("request_method", "profile", "stats"), _SCOPES)
async def test_newer_scope_request_during_tick(
    batch: bool, request_method: str, profile: str, stats: str,
) -> None:
    coordinator, session = await _warm(batch)
    coordinator._refresh_count = 7
    request_scope = getattr(coordinator, request_method)
    request_scope()
    session.pause_system = True
    async with asyncio.timeout(2), asyncio.TaskGroup() as tasks:
        running = tasks.create_task(_tick(coordinator, session))
        await session.entered.wait()
        request_scope()
        session.resume.set()
    assert running.result() == _expected(batch, profile, stats)

    # Tick 8 adds its scheduled stats read, and the new generation is still due.
    assert await _tick(coordinator, session) == _expected(batch, profile, "stats")
    coordinator._refresh_count = 11
    assert await _tick(coordinator, session) == _expected(batch, "fast")


@pytest.mark.parametrize(("request_method", "profile", "stats"), _SCOPES)
async def test_scope_survives_failed_critical_tick(
    batch: bool, request_method: str, profile: str, stats: str,
) -> None:
    coordinator, session = await _warm(batch)
    coordinator._refresh_count = 7
    getattr(coordinator, request_method)()
    previous = coordinator.data
    session.payloads["show/system"] = {}
    with pytest.raises(UpdateFailed, match="Critical router payload empty"):
        await _tick(coordinator, session)
    assert session.take_log() == _expected(batch, profile)
    assert coordinator.data is previous
    assert coordinator._refresh_count == 7

    session.payloads["show/system"] = SYSTEM_INFO
    assert await _tick(coordinator, session) == _expected(batch, profile, stats)
    assert await _tick(coordinator, session) == _expected(batch, "fast", "stats")


@pytest.mark.parametrize(("request_method", "profile", "stats"), _SCOPES)
async def test_scope_survives_cancelled_tick(
    batch: bool, request_method: str, profile: str, stats: str,
) -> None:
    coordinator, session = await _warm(batch)
    coordinator._refresh_count = 7
    getattr(coordinator, request_method)()
    previous = coordinator.data
    session.pause_system = True
    # Cancel in the primary-read stage. A standalone mesh GET can only start
    # after the paused system read finishes.
    if batch:
        in_flight = Counter([_batch(*_PRIMARY[profile][0])])
    else:
        in_flight = _expected(False, profile) - Counter(_GET_MESH)
    async with asyncio.timeout(2), asyncio.TaskGroup() as tasks:
        running = tasks.create_task(_tick(coordinator, session))
        await session.entered.wait()
        await session.wait_for_log(in_flight)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    assert session.take_log() == in_flight
    assert coordinator.data is previous
    assert coordinator._refresh_count == 7

    session.pause_system = False
    assert await _tick(coordinator, session) == _expected(batch, profile, stats)
    assert await _tick(coordinator, session) == _expected(batch, "fast", "stats")


async def test_update_check_retry_ignores_off_cadence_full_refresh(batch: bool) -> None:
    coordinator, session = await _warm(batch)
    previous = coordinator.data["system"]["release-available"]
    coordinator._refresh_count = 7
    coordinator.request_update_check()
    session.status["components/check-update"] = 503
    # The omitted batch child triggers one standalone attempt at the endpoint.
    failed_child = Counter(_GET_CHECK) if batch else Counter()
    assert await _tick(coordinator, session) == (
        _expected(batch, "update-check request") + failed_child
    )
    assert coordinator.data["system"]["release-available"] == previous

    coordinator.request_full_refresh()
    assert await _tick(coordinator, session) == _expected(batch, "very slow", "stats")
    assert await _tick(coordinator, session) == _expected(batch, "slow")
    coordinator._refresh_count = 14
    assert await _tick(coordinator, session) == _expected(batch, "fast", "stats")
    assert await _tick(coordinator, session) == (
        _expected(batch, "very slow with update check") + failed_child
    )
    assert coordinator.data["system"]["release-available"] == previous
    assert await _tick(coordinator, session) == _expected(batch, "fast", "stats")

    del session.status["components/check-update"]
    session.payloads["components/check-update"] = {**AVAILABLE_VERSION, "title": "4.4.0"}
    coordinator._refresh_count = 30
    assert await _tick(coordinator, session) == _expected(
        batch, "very slow with update check", "stats"
    )
    assert coordinator.data["system"]["release-available"] == "4.4.0"
    coordinator._refresh_count = 45
    assert await _tick(coordinator, session) == _expected(batch, "very slow")
