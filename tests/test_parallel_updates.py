"""Contract test: every entity platform module declares PARALLEL_UPDATES.

Per HA convention, write-capable platforms serialize updates (=1) to avoid
hammering the router with concurrent commands, while read-only
coordinator-driven platforms are unlimited (=0).
"""

from __future__ import annotations

from custom_components.keenetic_router_pro import (
    binary_sensor,
    button,
    device_tracker,
    select,
    sensor,
    switch,
    update,
)

_EXPECTED = {
    switch: 1,
    select: 1,
    button: 1,
    update: 1,
    binary_sensor: 0,
    device_tracker: 0,
    sensor: 0,
}


def test_all_platforms_declare_parallel_updates() -> None:
    for module in _EXPECTED:
        assert hasattr(module, "PARALLEL_UPDATES"), (
            f"{module.__name__} is missing module-level PARALLEL_UPDATES"
        )


def test_write_platforms_serialize_updates() -> None:
    for module, expected in _EXPECTED.items():
        assert module.PARALLEL_UPDATES == expected, (
            f"{module.__name__}.PARALLEL_UPDATES == {module.PARALLEL_UPDATES}, "
            f"expected {expected}"
        )
