"""Recorder-load hardening shipped in 1.9.0.

Every test here pins a behaviour whose whole purpose is to stop the recorder
writing a row that carries no information.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.keenetic_router_pro.sensor.client import (
    KeeneticClientRxSensor,
    KeeneticClientTxRateSensor,
    KeeneticClientTxSensor,
    KeeneticClientUptimeSensor,
)
from custom_components.keenetic_router_pro.sensor.network import (
    KeeneticWanRxThroughputSensor,
)
from custom_components.keenetic_router_pro.sensor.traffic import (
    KeeneticInterfaceRxSensor,
    KeeneticInterfaceTxSensor,
)
from custom_components.keenetic_router_pro.utils import (
    quantize_data_rate,
    quantize_link_speed,
)

MAC = "aa:bb:cc:dd:ee:ff"


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router", data={})


def _coordinator(data: dict) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        last_update_success=True,
        client=SimpleNamespace(_host="192.0.2.1", _ssl=False),
    )


def _client_coordinator(uptime: int) -> SimpleNamespace:
    return _coordinator(
        {"clients_by_mac": {MAC: {"mac": MAC, "active": True, "uptime": uptime}}}
    )


def test_traffic_sensor_omits_every_per_poll_counter() -> None:
    """Packets AND dropped advance every poll and forced a row each tick.

    The GiB state is rounded to 2 dp, so on most interfaces it is unchanged
    between polls — but HA only dedups when state AND every attribute match, so
    either moving counter defeated it. Measured on live hardware: rxdropped
    moved on every tick of a perfectly healthy WAN. Errors stay, because it
    sits at 0 until something is actually wrong.
    """
    stats = {
        "GigabitEthernet1": {
            "rxbytes": 3 * 1024**3,
            "txbytes": 3 * 1024**3,
            "rxpackets": 42,
            "txpackets": 43,
            "rxerrors": 0,
            "txerrors": 0,
            "rxdropped": 7,
            "txdropped": 8,
        }
    }
    coordinator = _coordinator({"interface_stats": stats})

    for sensor_cls, direction in (
        (KeeneticInterfaceRxSensor, "rx"),
        (KeeneticInterfaceTxSensor, "tx"),
    ):
        attrs = sensor_cls(
            coordinator, _entry(), "GigabitEthernet1", "WAN"
        ).extra_state_attributes
        assert f"{direction}packets" not in attrs
        assert f"{direction}dropped" not in attrs
        assert attrs[f"{direction}errors"] == 0


@pytest.mark.parametrize(
    ("bits_per_second", "expected"),
    [
        (0.0, 0.0),
        (4000.0, 0.0),  # ~500 B/s of ARP/keepalive noise on an idle link
        (4999.0, 0.0),
        (6000.0, 10000.0),
        (8210655.09599553, 8210000.0),  # real traffic keeps its shape
    ],
)
def test_quantize_data_rate_sends_background_noise_to_zero(
    bits_per_second: float, expected: float
) -> None:
    assert quantize_data_rate(bits_per_second) == expected


def test_idle_wan_throughput_reports_a_flat_zero() -> None:
    """An unused backup link must not write a row per poll."""
    coordinator = _coordinator(
        {"wan_by_id": {"PPPoE0": {"rx_throughput": 300.0}}}
    )
    sensor = KeeneticWanRxThroughputSensor(coordinator, _entry(), "PPPoE0")
    assert sensor.native_value == 0.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, 0), (2, 0), (4, 6), (87, 84), (300, 300), (866, 864)],
)
def test_quantize_link_speed_snaps_to_mcs_steps(raw: int, expected: int) -> None:
    assert quantize_link_speed(raw) == expected


def test_session_start_holds_still_while_the_uptime_counter_advances() -> None:
    """The whole point of the timestamp: a steady session writes one row.

    The router's uptime counter and our clock are not locked together, so a
    naively recomputed start drifts a second or two per poll — which would cost
    exactly the recorder row this change removes.
    """
    coordinator = _client_coordinator(100)
    sensor = KeeneticClientUptimeSensor(coordinator, _entry(), MAC, "Laptop")

    first = sensor.native_value
    assert first is not None

    # Drift stays inside the 90 s tolerance, as it does in production where the
    # wall clock advances alongside the router's counter.
    for uptime in (115, 130, 145, 160):
        coordinator.data["clients_by_mac"][MAC]["uptime"] = uptime
        assert sensor.native_value == first


def test_session_start_moves_when_the_client_reconnects() -> None:
    """A roam resets uptime to a small value — that is a new session."""
    coordinator = _client_coordinator(3600)
    sensor = KeeneticClientUptimeSensor(coordinator, _entry(), MAC, "Laptop")
    old_start = sensor.native_value

    coordinator.data["clients_by_mac"][MAC]["uptime"] = 30
    new_start = sensor.native_value

    assert new_start is not None
    assert old_start is not None
    assert new_start > old_start
    assert (datetime.now().astimezone() - new_start) < timedelta(seconds=60)


def test_session_start_is_recomputed_after_the_client_drops_out() -> None:
    """Losing the session clears the cache so a stale start never resurfaces."""
    coordinator = _client_coordinator(3600)
    sensor = KeeneticClientUptimeSensor(coordinator, _entry(), MAC, "Laptop")
    assert sensor.native_value is not None

    coordinator.data["clients_by_mac"][MAC]["uptime"] = 0
    assert sensor.native_value is None

    coordinator.data["clients_by_mac"][MAC]["uptime"] = 3600
    assert sensor.native_value is not None


@pytest.mark.parametrize(
    "sensor_cls",
    [
        KeeneticClientRxSensor,
        KeeneticClientTxSensor,
        KeeneticClientTxRateSensor,
        KeeneticInterfaceRxSensor,
        KeeneticInterfaceTxSensor,
    ],
)
def test_high_churn_diagnostics_are_opt_in_for_new_installs(sensor_cls) -> None:
    """Existing installs keep whatever the registry already enabled."""
    assert sensor_cls._attr_entity_registry_enabled_default is False


def test_rate_limit_zero_removes_the_shape_entry_instead_of_setting_zero() -> None:
    """A rate of 0 would block the host; the cap is removed with `no ...`."""
    import asyncio

    from custom_components.keenetic_router_pro.api.domains.clients import (
        ClientsMixin,
    )

    sent: list[str] = []

    class _Api(ClientsMixin):
        async def _rci_parse(self, cmd: str) -> None:
            sent.append(cmd)

    api = _Api()
    asyncio.run(api.async_set_client_rate_limit("AA-BB-CC-DD-EE-FF", 2048))
    assert sent[0] == "ip traffic-shape host aa:bb:cc:dd:ee:ff rate 2048"

    sent.clear()
    asyncio.run(api.async_set_client_rate_limit("AA:BB:CC:DD:EE:FF", 0))
    assert sent[0] == "no ip traffic-shape host aa:bb:cc:dd:ee:ff"


def test_rate_limit_rejects_a_negative_rate() -> None:
    import asyncio

    from custom_components.keenetic_router_pro.api.domains.clients import (
        ClientsMixin,
    )

    class _Api(ClientsMixin):
        async def _rci_parse(self, cmd: str) -> None:  # pragma: no cover - unused
            raise AssertionError("must not reach the router")

    with pytest.raises(ValueError):
        asyncio.run(_Api().async_set_client_rate_limit("aa:bb:cc:dd:ee:ff", -1))


def test_session_sensor_uses_a_new_unique_id_not_the_seconds_counter_one() -> None:
    """Reusing `_uptime` would fork the statistic_id of an existing numeric series.

    The old sensor was MEASUREMENT/seconds and has long-term statistics behind
    it on every install that has been running a while. A timestamp under the
    same id makes those graphs unreadable with no migration path, so this is a
    new entity — and `_async_prune_client_entities` removes the old one.
    """
    coordinator = _client_coordinator(100)
    sensor = KeeneticClientUptimeSensor(coordinator, _entry(), MAC, "Laptop")
    assert sensor.unique_id.endswith("_session_start")
    assert not sensor.unique_id.endswith("_uptime")


def test_prune_removes_retired_and_deselected_client_entities() -> None:
    """Dialling `client_sensors` back must not leave unavailable orphans."""
    import sys

    from custom_components.keenetic_router_pro import (
        _async_prune_client_entities,
    )

    removed: list[str] = []

    class _Registry:
        def async_get_entity_id(self, platform, domain, unique_id):
            return f"sensor.{unique_id}"

        def async_remove(self, entity_id):
            removed.append(entity_id)

    module = type(sys)("homeassistant.helpers.entity_registry")
    module.async_get = lambda _hass: _Registry()
    sys.modules["homeassistant.helpers.entity_registry"] = module

    entry = SimpleNamespace(
        entry_id="entry_123",
        title="Router",
        data={"tracked_clients": [{"mac": MAC, "name": "Laptop"}]},
        options={"client_sensors": "basic"},
    )
    _async_prune_client_entities(object(), entry)

    suffixes = {name.rsplit("_client_", 1)[1] for name in removed}
    # The retired seconds counter, plus everything "basic" does not create.
    assert f"{MAC}_uptime" in suffixes
    assert f"{MAC}_rx" in suffixes
    assert f"{MAC}_txrate" in suffixes
    # …but never the sensors "basic" still creates.
    assert f"{MAC}_ip" not in suffixes
    assert f"{MAC}_session_start" not in suffixes
