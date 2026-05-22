"""AST tripwire for fingerprint ignore fields read by value entities."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path("custom_components/keenetic_router_pro")
SOURCES = [
    ROOT / "entity.py",
    ROOT / "binary_sensor.py",
    ROOT / "device_tracker.py",
    ROOT / "switch.py",
    ROOT / "update.py",
    *sorted((ROOT / "sensor").glob("*.py")),
]
SOURCE_ATTRS = {"_wan", "_client", "_cmap", "_node"}
VALUE_METHODS = {"native_value", "is_on", "state"}


def _literal_string_set(node: ast.AST | None) -> set[str]:
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return {elt.value for elt in node.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)}
    if (
        isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "frozenset"
        and node.args
    ):
        return _literal_string_set(node.args[0])
    return set()


def _base_names(cls: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in cls.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _class_ignore(cls: ast.ClassDef) -> set[str] | None:
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "_FINGERPRINT_IGNORE":
                    return _literal_string_set(stmt.value)
    return None


def _read_fields(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    fields: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "get":
            continue
        source = node.func.value
        if (
            isinstance(source, ast.Attribute)
            and isinstance(source.value, ast.Name)
            and source.value.id == "self"
            and source.attr in SOURCE_ATTRS
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            fields.add(node.args[0].value)
    return fields


def test_value_entities_do_not_ignore_fields_they_read_for_state() -> None:
    classes: dict[str, ast.ClassDef] = {}
    class_file: dict[str, Path] = {}
    for path in SOURCES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            classes[cls.name] = cls
            class_file[cls.name] = path

    descendants = {"_FingerprintedCoordinatorEntity"}
    changed = True
    while changed:
        changed = False
        for name, cls in classes.items():
            if name in descendants:
                continue
            if _base_names(cls) & descendants:
                descendants.add(name)
                changed = True

    ignore_cache: dict[str, set[str]] = {}

    def inherited_ignore(name: str) -> set[str]:
        if name in ignore_cache:
            return ignore_cache[name]
        cls = classes[name]
        own = _class_ignore(cls)
        if own is not None:
            ignore_cache[name] = own
            return own
        inherited: set[str] = set()
        for base in _base_names(cls):
            if base in classes:
                inherited |= inherited_ignore(base)
        ignore_cache[name] = inherited
        return inherited

    failures: list[str] = []
    for name in sorted(descendants - {"_FingerprintedCoordinatorEntity"}):
        if class_file[name].name == "entity.py":
            continue
        ignored = inherited_ignore(name)
        if not ignored:
            continue
        reads: set[str] = set()
        for stmt in classes[name].body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name in VALUE_METHODS:
                reads |= _read_fields(stmt)
        overlap = ignored & reads
        if overlap:
            failures.append(f"{class_file[name]}::{name} reads ignored fields {sorted(overlap)}")

    assert failures == []
