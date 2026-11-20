"""An away client is a miss in the coordinator's MAC index, not a list scan.

``clients_by_mac`` indexes every valid MAC in the published client list, so
a miss in that index is final. Rescanning ``clients`` cost one row visit per
client for every absent tracked MAC on every entity update.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.keenetic_router_pro.device_tracker import KeeneticClientTracker
from custom_components.keenetic_router_pro.entity import ClientEntity

_AWAY = "aa:bb:cc:dd:ee:ff"


class _CountingRows(list):
    visits = 0

    def __iter__(self):
        for row in super().__iter__():
            type(self).visits += 1
            yield row


def _rows() -> _CountingRows:
    _CountingRows.visits = 0
    return _CountingRows(
        {"mac": f"02:00:00:00:{i // 256:02x}:{i % 256:02x}", "active": True}
        for i in range(200)
    )


def _coordinator(data: dict) -> SimpleNamespace:
    return SimpleNamespace(data=data, async_add_listener=lambda *_a, **_k: lambda: None)


def _entities(coordinator):
    entry = SimpleNamespace(entry_id="e1", title="Router")
    return (
        ClientEntity(coordinator, "e1", "Router", _AWAY, "Phone"),
        KeeneticClientTracker(coordinator, entry, _AWAY, "Phone"),
    )


def _lookups(entity):
    if isinstance(entity, KeeneticClientTracker):
        return entity._client_from_main
    return entity._client


def test_absent_client_on_an_indexed_snapshot_visits_no_rows() -> None:
    rows = _rows()
    coordinator = _coordinator({"clients": rows, "clients_by_mac": {}})
    for entity in _entities(coordinator):
        assert _lookups(entity) is None
    assert _CountingRows.visits == 0


def test_client_reappearing_in_the_index_is_found_again() -> None:
    rows = _rows()
    coordinator = _coordinator({"clients": rows, "clients_by_mac": {}})
    entities = _entities(coordinator)
    for entity in entities:
        assert _lookups(entity) is None
    back = {"mac": _AWAY, "active": True}
    coordinator.data = {"clients": [*rows, back], "clients_by_mac": {_AWAY: back}}
    for entity in entities:
        assert _lookups(entity) is back


def test_snapshot_without_an_index_still_scans_the_client_list() -> None:
    back = {"mac": _AWAY.upper().replace(":", "-"), "active": True}
    coordinator = _coordinator({"clients": [*_rows(), back]})
    for entity in _entities(coordinator):
        assert _lookups(entity) is back
