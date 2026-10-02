"""A retired mesh node must be removable from the UI.

The delete hook refused every device that still had entities, so a mesh
node replaced by another one (Air -> Speedster, 2026-10-01) stayed in Home
Assistant forever with a set of unavailable entities. Removing the device
from the config entry takes its entities with it. That is safe only when the
router, with fresh mesh data, no longer reports the node.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from test_hardening_1_10_0 import _patched_registries
from test_hardening_1_10_1 import _registry_modules

from custom_components.keenetic_router_pro import async_remove_config_entry_device
from custom_components.keenetic_router_pro.const import DOMAIN

ENTRY_ID = "e1"
RETIRED_CID = "07e6c1fe-119e-11eb-8f23-f31a295147db"
LIVE_CID = "77ee78e0-431f-11eb-8f23-391bcb4202e0"


def _entry(data):
    coordinator = SimpleNamespace(data=data)
    return SimpleNamespace(entry_id=ENTRY_ID, runtime_data=SimpleNamespace(coordinator=coordinator))


def _mesh_device(cid: str) -> SimpleNamespace:
    token = cid.replace("-", "_")
    return SimpleNamespace(id="dev", identifiers={(DOMAIN, f"{ENTRY_ID}_mesh_{token}")})


def _allowed(entry, device) -> bool:
    with _patched_registries(
        **_registry_modules(
            lambda _reg, _did, include_disabled_entities: [SimpleNamespace(entity_id="sensor.x")]
        )
    ):
        return asyncio.run(async_remove_config_entry_device(None, entry, device))


def _data(*cids: str, fresh: bool = True) -> dict:
    return {"mesh_nodes": [{"cid": c, "id": c} for c in cids], "mesh_nodes_fresh": fresh}


def test_retired_mesh_node_with_entities_can_be_deleted() -> None:
    assert _allowed(_entry(_data(LIVE_CID)), _mesh_device(RETIRED_CID)) is True


def test_reported_mesh_node_is_still_protected() -> None:
    assert _allowed(_entry(_data(LIVE_CID)), _mesh_device(LIVE_CID)) is False


def test_stale_mesh_data_does_not_prove_retirement() -> None:
    assert _allowed(_entry(_data(LIVE_CID, fresh=False)), _mesh_device(RETIRED_CID)) is False


def test_empty_mesh_list_does_not_prove_retirement() -> None:
    # A router that is still booting reports no nodes for a few minutes.
    assert _allowed(_entry(_data()), _mesh_device(RETIRED_CID)) is False


def test_without_coordinator_data_nothing_is_retired() -> None:
    entry = SimpleNamespace(entry_id=ENTRY_ID, runtime_data=None)
    assert _allowed(entry, _mesh_device(RETIRED_CID)) is False


def test_non_mesh_device_keeps_the_entity_guard() -> None:
    device = SimpleNamespace(id="dev", identifiers={(DOMAIN, ENTRY_ID)})
    assert _allowed(_entry(_data(LIVE_CID)), device) is False
