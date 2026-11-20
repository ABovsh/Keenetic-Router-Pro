"""Low-level async API client for Keenetic Router Pro integration (Basic Auth to /rci)."""

from __future__ import annotations

from .auth import _AuthMixin
from .domains.clients import ClientsMixin
from .domains.dns import DnsMixin
from .domains.mesh import MeshMixin
from .domains.network import NetworkMixin
from .domains.system import SystemMixin
from .domains.vpn import VpnMixin
from .domains.wan import WanMixin
from .domains.wifi import WifiMixin
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
    """Router API facade: transport, authentication and domain mixins."""
