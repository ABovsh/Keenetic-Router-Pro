"""Tests for the per-tick ``clients_by_mac`` index used by ClientEntity.

Regression guard for the entity-layer perf path: ``ClientEntity._client``
must look up its own dict via the precomputed ``clients_by_mac`` index
instead of a linear scan of ``data['clients']`` on every read. Falling
back to the linear scan would silently regress CPU on routers with many
tracked clients.
"""

from __future__ import annotations

from custom_components.keenetic_router_pro.entity import ClientEntity


class _DummyCoordinator:
    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {}

    def async_add_listener(self, *_a, **_kw):
        return lambda: None


def _make_entity(coord: _DummyCoordinator, mac: str = "AA:BB:CC:00:00:01") -> ClientEntity:
    return ClientEntity(
        coordinator=coord,
        entry_id="entry",
        title="router",
        mac=mac,
        label="phone",
    )


def test_client_entity_uses_clients_by_mac_index() -> None:
    """Lookup goes through ``clients_by_mac`` when present."""
    target = {"mac": "aa:bb:cc:00:00:01", "ip": "10.0.0.5"}
    coord = _DummyCoordinator(
        {
            "clients_by_mac": {"aa:bb:cc:00:00:01": target},
            # The fallback list is intentionally wrong to prove the index
            # is consulted first. If the entity falls back to a linear
            # scan it would return ``decoy`` and this test would fail.
            "clients": [{"mac": "aa:bb:cc:00:00:01", "ip": "DECOY"}],
        }
    )
    entity = _make_entity(coord)

    assert entity._client is target
    assert entity._client["ip"] == "10.0.0.5"


def test_client_entity_falls_back_to_linear_scan_when_index_missing() -> None:
    """Older coordinator data without the index must still resolve."""
    target = {"mac": "aa:bb:cc:00:00:01", "ip": "10.0.0.6"}
    coord = _DummyCoordinator({"clients": [target]})
    entity = _make_entity(coord)

    assert entity._client is target


def test_client_entity_returns_none_for_unknown_mac() -> None:
    coord = _DummyCoordinator({"clients_by_mac": {}, "clients": []})
    entity = _make_entity(coord)

    assert entity._client is None
