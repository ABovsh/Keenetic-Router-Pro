"""Hardening round 1.7.61 — verified deep-audit findings.

Each test pins one verified defect from the 1.7.61 audit. IDs in the docstrings
map to the audit findings (A=API, B=coordinator/utils, C=sensors, D=diagnostics).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

from custom_components.keenetic_router_pro.api import KeeneticApiError, KeeneticClient
from custom_components.keenetic_router_pro.coordinator import _first_stat_int
from custom_components.keenetic_router_pro.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.keenetic_router_pro.sensor.client import _coerce_optional_int
from custom_components.keenetic_router_pro.sensor.mesh import KeeneticMeshClientsSensor
from custom_components.keenetic_router_pro.utils import (
    coerce_bool,
    coerce_float,
    coerce_seconds,
)

HUGE_INT = 10 ** 400  # float(HUGE_INT) raises OverflowError, not ValueError
MAC = "AA:BB:CC:DD:EE:FF"
MAC_LOWER = MAC.lower()


# ---- B01/B02/B04 — numeric coercion safety in utils ----

def test_coerce_seconds_survives_overflowing_int() -> None:
    """B01: float(huge_int) raises OverflowError; must fall back to default."""
    assert coerce_seconds(HUGE_INT, default=None) is None


def test_coerce_float_survives_overflowing_int() -> None:
    """B02: coerce_float must not raise OverflowError on an absurd int."""
    assert coerce_float(HUGE_INT) is None


def test_coerce_bool_rejects_non_finite_float() -> None:
    """B04: NaN != 0 is True; a NaN flag must not read as truthy."""
    assert coerce_bool(float("nan")) is False
    assert coerce_bool(float("inf")) is False  # non-finite flag is garbage, not "on"
    assert coerce_bool(1) is True  # ordinary truthy still works


# ---- B03 — garbled byte counter must not become a real zero ----

def test_first_stat_int_returns_none_for_garbled_counter() -> None:
    """B03: a non-numeric counter must be None (unavailable), not 0 (false reset)."""
    assert _first_stat_int({"rxbytes": "n/a"}, "rxbytes") is None
    assert _first_stat_int({"rxbytes": "1234"}, "rxbytes") == 1234


# ---- C01 — client int coercion rejects bool / non-finite ----

def test_coerce_optional_int_rejects_bool_and_non_finite() -> None:
    """C01: bool is an int subclass; inf raises OverflowError in int()."""
    assert _coerce_optional_int(True) is None
    assert _coerce_optional_int(float("inf")) is None
    assert _coerce_optional_int("300") == 300


# ---- C04 — mesh client count cannot be negative / boolean ----

def _mesh_sensor(associations: Any) -> KeeneticMeshClientsSensor:
    cid = "node-1"
    coordinator = SimpleNamespace(
        data={"mesh_nodes_by_cid": {cid: {"cid": cid, "associations": associations}}}
    )
    entry = SimpleNamespace(entry_id="entry_1", title="Router")
    return KeeneticMeshClientsSensor(coordinator, entry, cid)


def test_mesh_client_count_clamps_negative_and_rejects_bool() -> None:
    """C04: a count sensor must never publish -1 or a boolean-derived 1."""
    assert _mesh_sensor("-1").native_value == 0
    assert _mesh_sensor(True).native_value == 0
    assert _mesh_sensor("3").native_value == 3
    assert _mesh_sensor([{"mac": MAC}, {"mac": MAC}]).native_value == 2


# ---- A09 — WireGuard multi-peer string counters must sum ----

async def test_wireguard_sums_string_peer_counters() -> None:
    """A09: peer rxbytes/txbytes as numeric strings must be summed, not dropped."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    iface_list = [
        {
            "id": "Wireguard0",
            "type": "WireGuard",
            "state": "up",
            "wireguard": {
                "peer": [
                    {"rxbytes": "1000", "txbytes": "10"},
                    {"rxbytes": "2000", "txbytes": "20"},
                ]
            },
        }
    ]
    result = await client.async_get_wireguard_status(iface_list=iface_list)
    profile = result["profiles"]["Wireguard0"]
    assert profile["rxbytes"] == 3000
    assert profile["txbytes"] == 30


# ---- A07 — wan_status recognizes list-shaped role ----

async def test_wan_status_recognizes_list_role_uplink() -> None:
    """A07: an active uplink with role=['inet'] must count as WAN, not 'down'."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    iface_list = [
        {
            "id": "GigabitEthernet1",  # no WAN keyword in the name
            "type": "Ethernet",
            "state": "up",
            "role": ["inet"],
            "security-level": "private",  # not 'public'
            "address": "203.0.113.5",
        }
    ]
    result = await client.async_get_wan_status(iface_list=iface_list)
    assert result["status"] != "down"


# ---- A08 — global flag coerced from string ----

async def test_wan_interface_global_flag_coerced_from_string() -> None:
    """A08: global='false' must become False, not True via bool('false')."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    iface_list = [
        {
            "id": "Wireguard0",
            "type": "WireGuard",
            "state": "up",
            "role": ["inet"],  # ranked as WAN via role
            "global": "false",
            "priority": 50,
        }
    ]
    wans = await client.async_get_wan_interfaces(iface_list=iface_list)
    assert wans, "interface should be ranked as a WAN"
    assert wans[0]["global"] is False


# ---- A01 — challenge-auth body-read timeout is reclassified ----

class _FakeResp:
    def __init__(self, status: int, headers: dict[str, str], text_exc: Exception | None = None):
        self.status = status
        self.headers = headers
        self._text_exc = text_exc

    async def __aenter__(self) -> "_FakeResp":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def text(self) -> str:
        if self._text_exc is not None:
            raise self._text_exc
        return ""

    async def read(self) -> bytes:
        return b""


class _FakeAuthSession:
    def __init__(self, post_resp: _FakeResp) -> None:
        self._post_resp = post_resp

    async def get(self, *_a: object, **_k: object) -> _FakeResp:
        return _FakeResp(200, {"X-NDM-Challenge": "abc", "X-NDM-Realm": "r"})

    async def post(self, *_a: object, **_k: object) -> _FakeResp:
        return self._post_resp


async def test_challenge_auth_body_read_timeout_is_api_error() -> None:
    """A01: a stalled response-body read must surface as KeeneticApiError."""
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._use_challenge_auth = True
    client._session = _FakeAuthSession(
        _FakeResp(200, {}, text_exc=asyncio.TimeoutError())
    )
    with pytest.raises(KeeneticApiError):
        await client._async_authenticate_challenge()


# ---- D01/D02/D03 — diagnostics redaction gaps ----

async def test_diagnostics_redacts_new_clients_mesh_ids_and_addresses() -> None:
    """D01/D02/D03: MAC sets, mesh node ids, and address/endpoint keys must not leak."""
    peer_ip = "198.51.100.7"
    fqdn = "node.example.net"
    coordinator_data = {
        "new_clients": {MAC_LOWER},
        "mesh_nodes": [{"id": MAC, "cid": None, "fqdn": fqdn}],
        "wan_interfaces": [
            {"id": "PPPoE0", "remote": peer_ip, "raw": {"global-address": peer_ip}}
        ],
        "ndns": {"name": "router", "domain": "myrouter.keenetic.pro"},
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
    serialized = json.dumps(result, sort_keys=True)  # must be JSON-native (D01)

    assert MAC not in serialized
    assert MAC_LOWER not in serialized
    assert peer_ip not in serialized
    assert fqdn not in serialized
    assert "myrouter.keenetic.pro" not in serialized


# ---- C05 — device tracker writes when presence evidence changes ----

def test_device_tracker_writes_on_presence_source_change(
    keenetic_entry,
    keenetic_coordinator_factory,
) -> None:
    """C05: active->link change (same connected/ip/hostname) must still write."""
    from custom_components.keenetic_router_pro.device_tracker import (
        KeeneticClientTracker,
    )

    mac = "aa:bb:cc:dd:ee:ff"

    def client(*, active: bool, link: str | None) -> dict[str, Any]:
        c = {"mac": mac, "ip": "192.0.2.40", "name": "Phone", "active": active}
        if link is not None:
            c["link"] = link
        return c

    coordinator = keenetic_coordinator_factory(
        {"clients_by_mac": {mac: client(active=True, link=None)}}
    )
    tracker = KeeneticClientTracker(coordinator, keenetic_entry, mac, "Phone")

    writes = {"n": 0}
    tracker.async_write_ha_state = lambda: writes.__setitem__("n", writes["n"] + 1)  # type: ignore[method-assign]

    # Prime the suppression key.
    tracker._handle_coordinator_update()
    baseline = writes["n"]

    # Same is_connected (still home) and same ip/hostname, but presence source
    # flips active -> link. The attribute changed, so a write must happen.
    coordinator.data = {"clients_by_mac": {mac: client(active=True, link="up")}}
    tracker._handle_coordinator_update()

    assert writes["n"] > baseline
