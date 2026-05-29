"""Regression guards for the 1.7.50 hardening pass.

Covers numeric-safety coercion, IPv6 URL authority bracketing, WAN
connectivity heuristics, sensor state-class contracts, diagnostics
redaction and policy-select label disambiguation.
"""

from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace

import pytest

from custom_components.keenetic_router_pro.api.parsers.wan import (
    derive_wan_internet_access,
    is_ranked_wan_interface,
)
from custom_components.keenetic_router_pro.api.target import (
    normalize_connection_target,
)
from custom_components.keenetic_router_pro.diagnostics import TO_REDACT
from custom_components.keenetic_router_pro.utils import (
    bracket_host,
    bytes_to_gib,
    bytes_to_mib,
    coerce_byte_count,
    coerce_seconds,
    parse_memory_fraction,
)

ROOT = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components"
    / "keenetic_router_pro"
)


def _class_assignments(path: pathlib.Path, class_name: str) -> dict[str, str]:
    tree = ast.parse(path.read_text())
    cls = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    assignments: dict[str, str] = {}
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = ast.unparse(node.value)
    return assignments


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router", data={})


# --------------------------------------------------------------------------
# F01/F02/F03 — IPv6 URL authority bracketing
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("::1", "[::1]"),
        ("2001:db8::1", "[2001:db8::1]"),
        ("[::1]", "[::1]"),
        ("192.168.1.1", "192.168.1.1"),
        ("router.local", "router.local"),
        ("", ""),
    ],
)
def test_bracket_host(host: str, expected: str) -> None:
    assert bracket_host(host) == expected


def test_target_base_url_brackets_ipv6_literal() -> None:
    # A user enters an IPv6 literal bracketed; urlparse strips the brackets
    # into ``.hostname`` ("::1"), so base_url must re-bracket the authority.
    assert normalize_connection_target("[::1]", 100, False).base_url == "http://[::1]:100"


def test_target_base_url_ipv4_unchanged() -> None:
    target = normalize_connection_target("192.168.1.1", 100, False)
    assert target.base_url == "http://192.168.1.1:100"


# --------------------------------------------------------------------------
# F12/F13/F14/F16/F19/F20 — byte counter coercion
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value", [None, "", "abc", float("nan"), float("inf"), -1, -5.0, "-3"]
)
def test_coerce_byte_count_rejects_unusable(value) -> None:
    assert coerce_byte_count(value) is None


@pytest.mark.parametrize(
    ("value", "expected"), [(0, 0), ("123", 123), (123.9, 123), (1024**3, 1024**3)]
)
def test_coerce_byte_count_valid(value, expected) -> None:
    assert coerce_byte_count(value) == expected


def test_bytes_to_gib() -> None:
    assert bytes_to_gib(None) is None
    assert bytes_to_gib(float("nan")) is None
    assert bytes_to_gib(-1) is None
    assert bytes_to_gib(1024**3) == 1.0


def test_bytes_to_mib() -> None:
    assert bytes_to_mib(None) is None
    assert bytes_to_mib(-1) is None
    assert bytes_to_mib(1024**2) == 1.0


# --------------------------------------------------------------------------
# F08 — coerce_seconds rejects negative durations
# --------------------------------------------------------------------------
def test_coerce_seconds_rejects_negative() -> None:
    assert coerce_seconds(-5) == 0
    assert coerce_seconds(-5, default=None) is None
    assert coerce_seconds(42) == 42


# --------------------------------------------------------------------------
# F09 — parse_memory_fraction rejects NaN/inf and clamps
# --------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["nan/100", "inf/100", "50/nan", "50/0"])
def test_parse_memory_fraction_rejects_bad(text: str) -> None:
    assert parse_memory_fraction(text) is None


def test_parse_memory_fraction_valid_and_clamped() -> None:
    assert parse_memory_fraction("50/100") == 50.0
    assert parse_memory_fraction("150/100") == 100.0


# --------------------------------------------------------------------------
# F17/F18 — WAN connectivity heuristic
# --------------------------------------------------------------------------
def test_wan_internet_access_rejects_placeholder_address() -> None:
    iface = {"state": "up", "global": True, "address": "0.0.0.0"}
    assert derive_wan_internet_access(iface) is not True


def test_wan_internet_access_accepts_real_address() -> None:
    iface = {"state": "up", "global": True, "address": "203.0.113.5"}
    assert derive_wan_internet_access(iface) is True


def test_wan_internet_access_string_false_global() -> None:
    iface = {"state": "up", "global": "false", "address": "203.0.113.5"}
    assert derive_wan_internet_access(iface) is False


def test_is_ranked_wan_string_false_global() -> None:
    assert is_ranked_wan_interface({"global": "false", "priority": 100}) is False


# --------------------------------------------------------------------------
# F15/F21/F22 — sensor state-class contracts
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("relative_path", "class_name"),
    [
        ("sensor/clients.py", "KeeneticConnectedClientsSensor"),
        ("sensor/clients.py", "KeeneticRouterClientsSensor"),
        ("sensor/clients.py", "KeeneticDisconnectedClientsSensor"),
        ("sensor/clients.py", "KeeneticExtenderCountSensor"),
        ("sensor/mesh.py", "KeeneticMeshClientsSensor"),
    ],
)
def test_count_sensors_use_measurement(relative_path: str, class_name: str) -> None:
    assignments = _class_assignments(ROOT / relative_path, class_name)
    assert assignments.get("_attr_state_class") == "SensorStateClass.MEASUREMENT", (
        f"{class_name} is an instantaneous count and must use MEASUREMENT"
    )


@pytest.mark.parametrize("class_name", ["KeeneticWgRxSensor", "KeeneticWgTxSensor"])
def test_wireguard_byte_sensors_total_increasing(class_name: str) -> None:
    assignments = _class_assignments(ROOT / "sensor/wireguard.py", class_name)
    assert assignments.get("_attr_state_class") == "SensorStateClass.TOTAL_INCREASING"
    assert assignments.get("_attr_device_class") == "SensorDeviceClass.DATA_SIZE"


# --------------------------------------------------------------------------
# F12/F13 — byte sensors report unavailable (not 0) on missing stats
# --------------------------------------------------------------------------
def test_wifi_rx_sensor_none_on_missing_stats() -> None:
    from custom_components.keenetic_router_pro.sensor.wifi import (
        KeeneticWifi24RxSensor,
    )

    coordinator = SimpleNamespace(data={"interface_stats": {}})
    sensor = KeeneticWifi24RxSensor(coordinator, _entry())
    assert sensor.native_value is None

    coordinator.data = {"interface_stats": {"WifiMaster0": {"rxbytes": 1024**3}}}
    assert sensor.native_value == 1.0


def test_traffic_sensor_none_on_missing_stats() -> None:
    from custom_components.keenetic_router_pro.sensor.traffic import KeeneticLanRxSensor

    coordinator = SimpleNamespace(data={"interface_stats": {}})
    sensor = KeeneticLanRxSensor(coordinator, _entry())
    assert sensor.native_value is None


# --------------------------------------------------------------------------
# O6 — diagnostics redacts client hostnames
# --------------------------------------------------------------------------
def test_diagnostics_redacts_hostname() -> None:
    assert "hostname" in TO_REDACT


# --------------------------------------------------------------------------
# F23 — policy select disambiguates duplicate descriptions
# --------------------------------------------------------------------------
def test_policy_select_disambiguates_duplicate_descriptions() -> None:
    from custom_components.keenetic_router_pro.select import KeeneticClientPolicySelect

    coordinator = SimpleNamespace(data={"host_policies": {}})
    policies = {"Policy0": "VPN", "Policy1": "VPN"}  # identical descriptions
    sensor = KeeneticClientPolicySelect(
        coordinator=coordinator,
        entry=_entry(),
        api_client=SimpleNamespace(),
        mac="aa:bb:cc:dd:ee:ff",
        label="x",
        initial_ip=None,
        policies=policies,
    )

    policy_options = [
        option
        for option in sensor.options
        if option not in ("Default", "Deny (Blocked)")
    ]
    assert len(policy_options) == len(set(policy_options)), "labels must be unique"
    assert {sensor._display_to_id[o] for o in policy_options} == {"Policy0", "Policy1"}
