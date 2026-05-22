"""Regression guard for the ``runtime_data`` migration.

The integration root must expose a ``KeeneticRuntimeData`` dataclass
that platforms read via ``entry.runtime_data``. Reverting to the old
``hass.data[DOMAIN][entry.entry_id]`` dict pattern would break every
platform's ``async_setup_entry``.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "keenetic_router_pro"


def test_runtime_data_class_defined() -> None:
    """The dataclass must expose only the live runtime dependencies."""
    import ast

    tree = ast.parse((ROOT / "__init__.py").read_text())
    runtime = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "KeeneticRuntimeData"
        ),
        None,
    )
    assert runtime is not None, "KeeneticRuntimeData dataclass missing"
    field_names = {
        node.target.id
        for node in runtime.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    for field in ("client", "coordinator"):
        assert field in field_names, f"KeeneticRuntimeData is missing field '{field}'"
    assert "ping_coordinator" not in field_names


def test_async_setup_entry_assigns_runtime_data() -> None:
    src = (ROOT / "__init__.py").read_text()
    assert "entry.runtime_data = KeeneticRuntimeData(" in src, (
        "async_setup_entry should populate entry.runtime_data with KeeneticRuntimeData"
    )


def test_no_legacy_hass_data_setdefault() -> None:
    src = (ROOT / "__init__.py").read_text()
    assert "hass.data.setdefault(DOMAIN" not in src, (
        "Legacy hass.data[DOMAIN][entry_id] storage should be removed in favour of runtime_data"
    )


def test_platforms_read_runtime_data() -> None:
    """Every platform setup should fetch its dependencies from entry.runtime_data."""
    platform_files = [
        ROOT / "button.py",
        ROOT / "binary_sensor.py",
        ROOT / "device_tracker.py",
        ROOT / "select.py",
        ROOT / "switch.py",
        ROOT / "update.py",
        ROOT / "sensor" / "__init__.py",
    ]
    for path in platform_files:
        src = path.read_text()
        assert "entry.runtime_data" in src, (
            f"{path.name} should read from entry.runtime_data, not hass.data[DOMAIN][...]"
        )
        assert "hass.data[DOMAIN][entry.entry_id]" not in src, (
            f"{path.name} still uses legacy hass.data[DOMAIN][entry.entry_id] lookup"
        )
