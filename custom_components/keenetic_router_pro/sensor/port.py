"""Common presentation for controller and mesh Ethernet ports."""

from __future__ import annotations

from typing import Any

from ..const import LINK_STATE_DOWN, LINK_STATE_UP


class PortSensorMixin:
    """Render one port row from the current coordinator snapshot."""

    _port_label: str
    _attr_icon = "mdi:ethernet"

    def _port_rows(self) -> list[Any]:
        raise NotImplementedError

    def _port_row(self) -> dict[str, Any] | None:
        for row in self._port_rows():
            if isinstance(row, dict) and str(row.get("label")) == self._port_label:
                return row
        return None

    @property
    def name(self) -> str:
        return f"Port {self._port_label}"

    @property
    def native_value(self) -> str | None:
        row = self._port_row()
        return row.get("link", "unknown") if row is not None else None

    @property
    def available(self) -> bool:
        return bool(getattr(super(), "available", True)) and self.native_value is not None

    @property
    def icon(self) -> str:
        return "mdi:ethernet-off" if self.native_value == LINK_STATE_DOWN else self._attr_icon

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        row = self._port_row()
        if row is None:
            return None
        attrs = {"label": row.get("label"), "appearance": row.get("appearance")}
        if row.get("link") == LINK_STATE_UP:
            attrs.update(speed=row.get("speed"), duplex=row.get("duplex"))
        return attrs
