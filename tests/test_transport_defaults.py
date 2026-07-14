"""Pins the default per-request HTTP timeout for the API transport.

Raised 15s -> 25s: the ``interfaces`` RCI call was intermittently exceeding
15s under router load (Yakhny, large ACL/device list), producing repeated
"Transient critical router fetch failure" log noise even though the router
was healthy and the call eventually succeeded. 25s still leaves headroom
before the next 30s (FAST_SCAN_INTERVAL) poll tick.
"""

from custom_components.keenetic_router_pro.api.client import KeeneticClient


def test_default_request_timeout_is_25_seconds() -> None:
    client = KeeneticClient("192.168.3.1", "admin", "secret")
    assert client._request_timeout == 25
