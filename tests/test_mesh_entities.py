"""Mesh entity ID and dynamic creation tests."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.keenetic_router_pro.sensor import _add_mesh_sensors
from custom_components.keenetic_router_pro.utils import mesh_unique_id, sanitize_mesh_id


def test_mesh_unique_id_is_entry_scoped_and_not_truncated() -> None:
    """Mesh IDs include the entry id and the full sanitized node token."""
    node_id = "aa:bb:cc:dd:ee:ff:11:22:33"

    unique_id = mesh_unique_id("entry_123", node_id, "firmware update")

    assert unique_id == "entry_123_mesh_aa_bb_cc_dd_ee_ff_11_22_33_firmware_update"
    assert "11_22_33" in unique_id
    assert sanitize_mesh_id("node cid/with spaces") == "node_cid_with_spaces"


def test_dynamic_mesh_sensor_helper_adds_new_nodes_and_ports_once() -> None:
    """Dynamic mesh listener helper adds newly discovered mesh entities once."""
    entry = SimpleNamespace(entry_id="entry_123", title="Router", data={})
    coordinator = SimpleNamespace(
        data={
            "mesh_nodes": [
                {
                    "cid": "aa:bb:cc:dd:ee:ff:11:22:33",
                    "ip": "192.0.2.10",
                    "port": [{"label": "1"}],
                }
            ]
        }
    )
    entities = []
    known_mesh_ids: set[str] = set()
    known_mesh_local_ip_ids: set[str] = set()
    known_mesh_port_keys: set[tuple[str, str]] = set()

    _add_mesh_sensors(
        entities,
        coordinator,
        entry,
        known_mesh_ids,
        known_mesh_local_ip_ids,
        known_mesh_port_keys,
    )

    assert len(entities) == 7
    assert len({entity.unique_id for entity in entities}) == len(entities)

    _add_mesh_sensors(
        entities,
        coordinator,
        entry,
        known_mesh_ids,
        known_mesh_local_ip_ids,
        known_mesh_port_keys,
    )

    assert len(entities) == 7

    coordinator.data["mesh_nodes"] = [
        {
            "cid": "aa:bb:cc:dd:ee:ff:11:22:33",
            "ip": "192.0.2.10",
            "port": [{"label": "1"}, {"label": "2"}],
        },
        {
            "cid": "ff:ee:dd:cc:bb:aa:99:88:77",
            "port": [],
        },
    ]

    _add_mesh_sensors(
        entities,
        coordinator,
        entry,
        known_mesh_ids,
        known_mesh_local_ip_ids,
        known_mesh_port_keys,
    )

    assert len(entities) == 13
    assert len({entity.unique_id for entity in entities}) == len(entities)
    assert any(entity.unique_id.endswith("_port_2_v2") for entity in entities)
    assert any(
        "ff_ee_dd_cc_bb_aa_99_88_77_uptime_v2" in entity.unique_id
        for entity in entities
    )
