"""Tests for ClientEntity change-detection fingerprint."""

from __future__ import annotations

from custom_components.keenetic_router_pro.entity import ClientEntity


class _DummyCoordinator:
    """Stand-in for KeeneticCoordinator with a settable ``data`` attr."""

    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {}

    def async_add_listener(self, *_a, **_kw):  # CoordinatorEntity.__init__ calls this on attach
        return lambda: None


def _make_entity(client_dict: dict) -> ClientEntity:
    coord = _DummyCoordinator({"clients_by_mac": {"aa:bb:cc:00:00:01": client_dict}})
    entity = ClientEntity(
        coordinator=coord,
        entry_id="entry",
        title="router",
        mac="AA:BB:CC:00:00:01",
        label="phone",
    )
    return entity


def test_fingerprint_excludes_last_seen_and_uptime() -> None:
    client = {"mac": "aa:bb:cc:00:00:01", "ip": "10.0.0.5", "link": "up", "last-seen": 100, "uptime": 50}
    entity = _make_entity(client)

    fp1 = entity._client_fingerprint(client)
    fp2 = entity._client_fingerprint({**client, "last-seen": 200, "uptime": 75})

    assert fp1 == fp2
    assert "last-seen" not in fp1
    assert "uptime" not in fp1


def test_fingerprint_picks_up_link_and_ip_changes() -> None:
    client = {"mac": "aa:bb:cc:00:00:01", "ip": "10.0.0.5", "link": "up"}
    entity = _make_entity(client)

    fp1 = entity._client_fingerprint(client)
    fp_link = entity._client_fingerprint({**client, "link": "down"})
    fp_ip = entity._client_fingerprint({**client, "ip": "10.0.0.6"})

    assert fp1 != fp_link
    assert fp1 != fp_ip


def test_fingerprint_returns_none_for_missing_client() -> None:
    entity = _make_entity({"mac": "aa:bb:cc:00:00:01"})
    assert entity._client_fingerprint(None) is None
    assert entity._client_fingerprint({}) is None


def test_handle_coordinator_update_skips_when_only_noise_changed() -> None:
    """When only last-seen/uptime tick, _handle_coordinator_update must not
    forward to super() (which would write_ha_state)."""
    client = {"mac": "aa:bb:cc:00:00:01", "ip": "10.0.0.5", "link": "up", "last-seen": 1}
    coord = _DummyCoordinator({"clients_by_mac": {"aa:bb:cc:00:00:01": client}})
    entity = ClientEntity(
        coordinator=coord,
        entry_id="entry",
        title="router",
        mac="AA:BB:CC:00:00:01",
        label="phone",
    )

    super_calls = 0

    def fake_super_handle():
        nonlocal super_calls
        super_calls += 1

    # CoordinatorEntity.async_write_ha_state is what super().__handle__ would
    # call — patch the entire super() method via the bound parent.
    import custom_components.keenetic_router_pro.entity as entity_module
    original = entity_module.CoordinatorEntity._handle_coordinator_update
    entity_module.CoordinatorEntity._handle_coordinator_update = lambda self: fake_super_handle()
    try:
        # First tick: cold cache, should forward.
        entity._handle_coordinator_update()
        assert super_calls == 1

        # Second tick with only last-seen changed: should skip.
        coord.data["clients_by_mac"]["aa:bb:cc:00:00:01"] = {**client, "last-seen": 2}
        entity._handle_coordinator_update()
        assert super_calls == 1, "noise-only change must not forward to super()"

        # Third tick with link change: should forward.
        coord.data["clients_by_mac"]["aa:bb:cc:00:00:01"] = {**client, "last-seen": 3, "link": "down"}
        entity._handle_coordinator_update()
        assert super_calls == 2
    finally:
        entity_module.CoordinatorEntity._handle_coordinator_update = original
