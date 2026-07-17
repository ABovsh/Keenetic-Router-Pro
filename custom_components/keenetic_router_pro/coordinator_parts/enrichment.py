"""CPU-only WAN/crypto-map enrichment for coordinator ticks.

These are pure transforms of already-fetched data — no RCI calls — extracted
from ``KeeneticCoordinator._async_update_data_unlocked`` so the two blocks
can be unit-tested directly. Behavior is unchanged from the inline
implementation.
"""

from __future__ import annotations

from typing import Any

from ..utils import first_present
from .derived import counter_rate_bytes_per_second, order_wan_interfaces


def _first_stat_int(stats: dict[str, Any], *keys: str) -> int | None:
    """Return the first usable integer stat, or None for absent/garbage values.

    Booleans are rejected (``int(False) == 0`` would look like a counter
    reset and fabricate a throughput spike on the next real sample).
    """
    value = first_present(stats, *keys, default=None)
    if value is None or isinstance(value, bool):
        return None
    # A non-numeric/garbled sample must stay None (sensor unavailable), NOT
    # collapse to 0 — a fake 0 reads as a TOTAL_INCREASING counter reset and
    # corrupts long-term traffic statistics.
    try:
        return int(value)
    except (OverflowError, TypeError, ValueError):
        return None


def enrich_wan_interfaces(
    wan_interfaces: list[dict[str, Any]],
    interface_stats: dict[str, Any] | None,
    ping_check_status: dict[str, Any],
    prev_wan_interfaces: list[dict[str, Any]] | None,
    now_ts: float,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Stamp per-tick stats/throughput/ping-check onto WAN interfaces.

    Mutates each dict in ``wan_interfaces`` in place (matching the previous
    inline behavior) and returns the ordered list plus an id-keyed index.
    """
    prev_wan_by_id: dict[str, dict[str, Any]] = {}
    for prev in prev_wan_interfaces or []:
        pid = prev.get("id")
        if pid:
            prev_wan_by_id[pid] = prev

    for wan in wan_interfaces:
        wan_id = wan.get("id")
        stats = (interface_stats or {}).get(wan_id) or {}
        rx_bytes = _first_stat_int(
            stats,
            "rxbytes",
            "rx-bytes",
            "rx_bytes",
        )
        tx_bytes = _first_stat_int(
            stats,
            "txbytes",
            "tx-bytes",
            "tx_bytes",
        )
        wan["rx_bytes"] = rx_bytes
        wan["tx_bytes"] = tx_bytes
        wan["rx_packets"] = _first_stat_int(
            stats,
            "rxpackets",
            "rx-packets",
        )
        wan["tx_packets"] = _first_stat_int(
            stats,
            "txpackets",
            "tx-packets",
        )
        wan["rx_speed_raw"] = _first_stat_int(
            stats,
            "rxspeed",
            "rx-speed",
            "rx_rate",
        )
        wan["tx_speed_raw"] = _first_stat_int(
            stats,
            "txspeed",
            "tx-speed",
            "tx_rate",
        )
        wan["stats_interface"] = stats.get("interface_name") or wan_id
        wan["stats_timestamp"] = stats.get("timestamp")
        wan["_sample_ts"] = now_ts

        # --- Authoritative ping-check override ---
        # When the router itself reports a ping-check result for
        # this WAN, trust it over the heuristic. Three cases:
        #   passing=True  -> internet_access=True (ping check ok)
        #   passing=False -> internet_access=False (real outage,
        #                    the case the feature request is about)
        #   passing=None  -> no real profile attached / mixed state
        #                    -> keep the heuristic value from api.py
        pc = ping_check_status.get(wan_id)
        if pc is not None:
            wan["ping_check"] = pc
            passing = pc.get("passing")
            if passing is True or passing is False:
                wan["internet_access"] = passing
                wan["internet_access_source"] = "ping_check"
            else:
                wan["internet_access_source"] = "heuristic"
        else:
            wan["ping_check"] = None
            wan["internet_access_source"] = "heuristic"

        prev = prev_wan_by_id.get(wan_id)
        if prev and prev.get("_sample_ts"):
            dt = now_ts - float(prev.get("_sample_ts") or 0)
            wan["rx_throughput"] = counter_rate_bytes_per_second(
                rx_bytes,
                prev.get("rx_bytes"),
                dt,
            )
            wan["tx_throughput"] = counter_rate_bytes_per_second(
                tx_bytes,
                prev.get("tx_bytes"),
                dt,
            )
        else:
            wan["rx_throughput"] = 0.0
            wan["tx_throughput"] = 0.0

    ordered = order_wan_interfaces(wan_interfaces)
    wan_by_id = {
        w.get("id"): w
        for w in ordered
        if isinstance(w, dict) and w.get("id")
    }
    return ordered, wan_by_id


def enrich_crypto_maps(
    crypto_maps: dict[str, dict[str, Any]],
    prev_crypto_maps: dict[str, Any] | None,
    now_ts: float,
    ipsec_status_refresh: bool,
) -> None:
    """Stamp per-tick throughput deltas onto crypto maps, in place.

    Same delta pattern as ``enrich_wan_interfaces``. Counters reset to zero
    whenever a phase2 SA rekeys or the tunnel bounces — the negative-delta
    clamp (in ``counter_rate_bytes_per_second``) keeps throughput sensors
    from spiking to absurd negative values on those events.
    """
    prev_cmap_by_name: dict[str, dict[str, Any]] = {}
    for pname, pentry in (prev_crypto_maps or {}).items():
        if isinstance(pentry, dict):
            prev_cmap_by_name[pname] = dict(pentry)

    for cmap_name, cmap in crypto_maps.items():
        prev_cmap = prev_cmap_by_name.get(cmap_name)
        if not ipsec_status_refresh and prev_cmap:
            cmap["_sample_ts"] = prev_cmap.get("_sample_ts")
            cmap["rx_throughput"] = prev_cmap.get("rx_throughput", 0.0)
            cmap["tx_throughput"] = prev_cmap.get("tx_throughput", 0.0)
            continue

        cmap["_sample_ts"] = now_ts
        if prev_cmap and prev_cmap.get("_sample_ts"):
            dt = now_ts - float(prev_cmap.get("_sample_ts") or 0)
            # Pass raw values: counter_rate rejects None/bool
            # samples itself; pre-coercing None to 0 would
            # fabricate a reset + spike pair.
            cmap["rx_throughput"] = counter_rate_bytes_per_second(
                cmap.get("rx_bytes"),
                prev_cmap.get("rx_bytes"),
                dt,
            )
            cmap["tx_throughput"] = counter_rate_bytes_per_second(
                cmap.get("tx_bytes"),
                prev_cmap.get("tx_bytes"),
                dt,
            )
        else:
            cmap["rx_throughput"] = 0.0
            cmap["tx_throughput"] = 0.0
