"""Low-level async API client for Keenetic Router Pro integration (Basic Auth to /rci)."""

from __future__ import annotations

from typing import Any, Dict, List

from .auth import _AuthMixin
from .domains.clients import ClientsMixin
from .domains.dns import DnsMixin
from .domains.mesh import MeshMixin
from .domains.network import NetworkMixin
from .domains.system import SystemMixin
from .domains.vpn import VpnMixin
from .domains.wan import WanMixin
from .domains.wifi import WifiMixin
from .helpers import _extract_parse_messages, _normalize_interfaces
from .transport import _Transport


class KeeneticClient(
    _AuthMixin,
    SystemMixin,
    NetworkMixin,
    WanMixin,
    ClientsMixin,
    WifiMixin,
    VpnMixin,
    DnsMixin,
    MeshMixin,
    _Transport,
):
    def _normalize_interfaces(self, raw: Any) -> List[Dict[str, Any]]:
        """Back-compat shim — delegates to module-level helper.

        Kept on the class because coordinator.py calls it as
        `self.client._normalize_interfaces(...)`.
        """
        return _normalize_interfaces(raw)

    @staticmethod
    def _extract_parse_messages(data: Any) -> List[str]:
        """Back-compat shim for tests and private callers."""
        return _extract_parse_messages(data)
