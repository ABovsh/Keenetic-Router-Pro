"""Unit tests for coordinator-published lookup indexes."""

from __future__ import annotations

from custom_components.keenetic_router_pro.coordinator import _mesh_associations
from custom_components.keenetic_router_pro.utils import normalize_mac


def _clients_by_mac(clients: list[object]) -> dict[str, dict]:
    return {
        normalize_mac(c.get("mac")): c
        for c in clients
        if isinstance(c, dict) and c.get("mac")
    }


def _wan_by_id(wans: list[object]) -> dict[str, dict]:
    return {w.get("id"): w for w in wans if isinstance(w, dict) and w.get("id")}


def _mesh_nodes_by_cid(nodes: object) -> dict[str, dict]:
    return {
        (n.get("cid") or n.get("id")): n
        for n in (nodes if isinstance(nodes, list) else [])
        if isinstance(n, dict) and (n.get("cid") or n.get("id"))
    }


def test_clients_by_mac_skips_malformed_rows_and_normalizes_case() -> None:
    good = {"mac": "AA-BB-CC-DD-EE-FF", "name": "phone"}

    assert _clients_by_mac(["bad", {"name": "no-mac"}, good]) == {
        "aa:bb:cc:dd:ee:ff": good
    }


def test_wan_by_id_skips_rows_without_id() -> None:
    wan = {"id": "PPPoE0", "type": "PPPoE"}

    assert _wan_by_id([{"type": "bad"}, "bad", wan]) == {"PPPoE0": wan}


def test_mesh_nodes_by_cid_prefers_cid_then_id_and_skips_missing() -> None:
    cid_node = {"cid": "mesh-cid", "id": "fallback"}
    id_node = {"id": "node-id"}

    assert _mesh_nodes_by_cid([cid_node, id_node, {"name": "bad"}, "bad"]) == {
        "mesh-cid": cid_node,
        "node-id": id_node,
    }


def test_mesh_associations_handles_empty_and_mixed_rows() -> None:
    assert _mesh_associations([]) == {"total": 0, "by_node": {}}
    assert _mesh_associations(None) == {"total": 0, "by_node": {}}

    assert _mesh_associations(
        [
            {"cid": "node-a", "associations": "2"},
            "bad",
            {"id": "node-b", "associations": "bad"},
            {"associations": 5},
            {"cid": "node-c", "associations": 3},
        ]
    ) == {
        "total": 5,
        "by_node": {"node-a": 2, "node-b": 0, "node-c": 3},
    }
