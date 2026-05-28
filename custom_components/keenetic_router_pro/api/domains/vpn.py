"""VPN domain methods for KeeneticClient."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List

import aiohttp

from ...const import (
    DOMAIN,
    FIELD_CONNECTED,
    INTERFACE_CONF_DISABLED,
    IPSEC_STATE_ESTABLISHED,
    LINK_STATE_DOWN,
    LINK_STATE_UP,
)
from ...utils import coerce_bool, first_present
from ..constants import _IPSEC_VICI_OOM_RE
from ..errors import KeeneticApiError
from ..helpers import (
    _as_list,
    _clean_addr,
    _clean_str,
    _extract_log_entries,
    _extract_parse_messages,
    _is_endpoint_missing,
    _normalize_interfaces,
    _to_int,
    _validate_cli_arg,
)

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.vpn")


# Regexes for the deterministic strongSwan ``ipsec statusall`` text dump
# served by RCI endpoint ``show/ipsec``. We parse the brief Security
# Associations block because it carries everything we need on one line
# per record and is stable across strongSwan 5.x/6.x. The verbose
# multi-line section below it is intentionally ignored.
_IPSEC_PHASE1_HEADER_RE = re.compile(
    r"^\s*(?P<name>\S+?)\[(?P<sa_id>\d+)\]:\s+"
    r"(?P<state>ESTABLISHED|CONNECTING|REKEYING|DELETING)\s+"
    r".*?,\s+"
    r"(?P<local>\S+)\[(?P<local_id>[^\]]+)\]\.\.\."
    r"(?P<remote>\S+)\[(?P<remote_id>[^\]]+)\]\s*$"
)
_IPSEC_PHASE1_SPI_RE = re.compile(
    r"^\s*(?P<name>\S+?)\[(?P<sa_id>\d+)\]:\s+"
    r"(?P<ver>IKEv?\d?)\s+SPIs:\s+"
    r"(?P<local_spi>\S+?)_i\s+(?P<remote_spi>\S+?)_r\*?,\s+"
    r"(?:rekeying in (?P<rekey>.+?)|reauthentication disabled)\s*$"
)
_IPSEC_PHASE1_PROPOSAL_RE = re.compile(
    r"^\s*(?P<name>\S+?)\[(?P<sa_id>\d+)\]:\s+IKE proposal:\s+(?P<proposal>\S+)\s*$"
)
_IPSEC_PHASE2_HEADER_RE = re.compile(
    r"^\s*(?P<name>\S+?)\{(?P<sa_id>\d+)\}:\s+"
    r"(?P<state>INSTALLED|REKEYING|DELETING|REKEYED)"
    r"(?:,\s+(?P<mode>TUNNEL|TRANSPORT))?"
    r"(?:,\s+reqid\s+(?P<reqid>\d+))?"
    r"(?:,\s+(?P<proto>ESP|AH))?"
    r"(?P<encap>\s+in\s+UDP)?"
    r".*?SPIs:\s+(?P<local_spi>\S+?)_i\s+(?P<remote_spi>\S+?)_o\s*$"
)
_IPSEC_PHASE2_COUNTERS_RE = re.compile(
    r"^\s*(?P<name>\S+?)\{(?P<sa_id>\d+)\}:\s+"
    r"(?P<proposal>\S+?),\s+"
    r"(?P<bytes_i>\d+)\s+bytes_i"
    r"(?:\s+\((?P<pkts_i>\d+)\s+pkts(?:,\s+(?P<last_i>\d+)s ago)?\))?,\s+"
    r"(?P<bytes_o>\d+)\s+bytes_o"
    r"(?:\s+\((?P<pkts_o>\d+)\s+pkts(?:,\s+(?P<last_o>\d+)s ago)?\))?"
    r"(?:,\s+rekeying in (?P<rekey>.+?))?\s*$"
)
_IPSEC_CONNECTION_LINE_RE = re.compile(
    r"^\s*(?P<name>\S+?):\s+\S+\.\.\.\S+\s+IKEv?\d?"
)
_IPSEC_CONNECTION_CHILD_RE = re.compile(
    r"^\s*(?P<name>\S+?):\s+child:\s+.+?\s+(?P<mode>TUNNEL|TRANSPORT),"
)
_IPSEC_SA_HEADER_RE = re.compile(
    r"^Security Associations\s+\((?P<up>\d+)\s+up,\s+(?P<connecting>\d+)\s+connecting\)"
)


class VpnMixin:
    async def async_get_wireguard_status(
        self,
        interfaces: Dict[str, Any] | None = None,
        iface_list: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        """Return WireGuard interfaces and their status."""
        if iface_list is None:
            if interfaces is None:
                interfaces = await self.async_get_interfaces()
            iface_list = _normalize_interfaces(interfaces)

        profiles: Dict[str, Any] = {}

        for item in iface_list:
            itype = (item.get("type") or "").lower()
            traits = [t.lower() for t in item.get("traits", []) if isinstance(t, str)]
            name = (
                item.get("id")
                or item.get("interface-name")
                or item.get("name")
                or item.get("ifname")
            )
            if not name:
                continue

            is_wg = itype == "wireguard" or "wireguard" in "".join(traits)
            if not is_wg:
                continue

            wg_info = item.get("wireguard") or {}
            if not isinstance(wg_info, dict):
                wg_info = {}
            description = item.get("description") or name

            remote = None
            rx_val = first_present(wg_info, "rxbytes")
            if rx_val is None:
                rx_val = first_present(item, "rxbytes")
            tx_val = first_present(wg_info, "txbytes")
            if tx_val is None:
                tx_val = first_present(item, "txbytes")

            peer = wg_info.get("peer")

            if isinstance(peer, list) and peer:
                p = peer[0]
                if not isinstance(p, dict):
                    p = {}
                remote = p.get("remote-endpoint-address")
                if rx_val is None:
                    rx_val = p.get("rxbytes")
                if tx_val is None:
                    tx_val = p.get("txbytes")
            elif isinstance(peer, dict):
                remote = peer.get("remote-endpoint-address")
                if rx_val is None:
                    rx_val = peer.get("rxbytes")
                if tx_val is None:
                    tx_val = peer.get("txbytes")

            profiles[name] = {

                "label": description,
                "enabled": str(item.get("state", "")).lower() == LINK_STATE_UP,
                "state": item.get("state"),
                "address": item.get("address"),
                "remote": remote,
                "uptime": item.get("uptime"),
                "rx": rx_val,
                "tx": tx_val,
                "rxbytes": rx_val,
                "txbytes": tx_val,
            }

        return {"profiles": profiles}


    async def async_set_wireguard_enabled(self, interface_name: str, enabled: bool) -> None:
        """Enable or disable a WireGuard interface via RCI parse.

        Kept for backwards compatibility; delegates to the generic
        async_set_interface_enabled which works for any interface type
        (WireGuard, OpenVPN, SSTP, IPsec, ...).
        """
        await self.async_set_interface_enabled(interface_name, enabled)

    async def async_get_vpn_tunnels(
        self,
        interfaces: Dict[str, Any] | None = None,
        iface_list: List[Dict[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Auto-discover VPN-like interfaces (WireGuard, OpenVPN, IPsec, ...).

        Returns:
            {
              "profiles": {
                 "Wireguard0": {...},
                 "Wireguard1": {...},
                 "OpenVpn0": {...},
                 ...
              }
            }
        """
        if iface_list is None:
            if interfaces is None:
                interfaces = await self.async_get_interfaces()
            iface_list = _normalize_interfaces(interfaces)

        VPN_TYPES = {
            "wireguard",
            "openvpn",
            "ipsec",
            "l2tp",
            "pptp",
            "sstp",
            "zerotier",
            "tor",
        }

        profiles: dict[str, dict[str, Any]] = {}

        for item in iface_list:
            itype = str(item.get("type") or "").lower()
            if itype not in VPN_TYPES:
                continue

            iface_id = (
                item.get("id")
                or item.get("interface-name")
                or item.get("name")
            )
            if not iface_id:
                continue

            label = (
                item.get("description")
                or item.get("interface-name")
                or iface_id
            )

            state = str(item.get("state") or "").lower()
            summary = item.get("summary") or {}
            if not isinstance(summary, dict):
                summary = {}
            layer = summary.get("layer") or {}
            if not isinstance(layer, dict):
                layer = {}
            conf = str(layer.get("conf") or "").lower()

            enabled = not (conf == INTERFACE_CONF_DISABLED or state == LINK_STATE_DOWN)

            profiles[str(iface_id)] = {
                "id": iface_id,
                "type": item.get("type") or itype,
                "label": str(label),
                "enabled": enabled,
                "state": item.get("state"),
            }

        return {"profiles": profiles}

    @classmethod
    def _parse_ipsec_vici_diagnostics(
        cls,
        lines: List[str],
        entries: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        """Summarize recent IPsec VICI memory errors from router log lines.

        ``lines`` is the legacy text-only input — each entry is a raw log
        line. ``entries`` is the modern shape: each entry is a dict with
        ``time`` and ``message`` fields, preserving the per-record
        timestamp emitted by the router. When ``entries`` is supplied,
        ``events`` is populated with ``(timestamp, line)`` tuples so the
        coordinator can dedup against a persisted ``last_seen_ts`` and
        maintain a monotonically increasing OOM counter.
        """
        matches: List[Dict[str, Any]] = []
        if entries is not None:
            for ent in entries:
                if not isinstance(ent, dict):
                    continue
                msg = ent.get("message")
                if not isinstance(msg, str):
                    continue
                m = _IPSEC_VICI_OOM_RE.search(msg)
                if not m:
                    continue
                matches.append({
                    "line": msg.strip(),
                    "code": m.group("code"),
                    "time": ent.get("time"),
                })
        else:
            for line in lines:
                if not isinstance(line, str):
                    continue
                m = _IPSEC_VICI_OOM_RE.search(line)
                if not m:
                    continue
                matches.append({
                    "line": line.strip(),
                    "code": m.group("code"),
                    "time": None,
                })

        last_match = matches[0] if entries is not None and matches else (
            matches[-1] if matches else None
        )
        scanned = len(entries) if entries is not None else len(lines)
        return {
            "status": "warning" if matches else "ok",
            "vici_out_of_memory_count": len(matches),
            "last_vici_out_of_memory": last_match.get("line") if last_match else None,
            "last_error_code": last_match.get("code") if last_match else None,
            "recent_matches": [m["line"] for m in matches[-5:]],
            "events": [(m["time"], m["line"]) for m in matches],
            "scanned_log_lines": scanned,
        }

    async def async_get_ipsec_diagnostics(self) -> Dict[str, Any]:
        """Return low-cadence diagnostics for IPsec/VICI router log errors.

        Uses RCI ``show/log`` (structured JSON with ``time`` + ``message``
        per record). Falls back to ``show log 200 once`` text mode if the
        JSON endpoint is unavailable. The JSON shape preserves per-event
        timestamps so the coordinator can build a monotonic OOM counter
        with timestamp dedup.
        """
        # Larger window than 200: at the post-1.7.45 cadence the router
        # generates ~1 OOM per 10 min, and we poll every 5 min — so a
        # 1000-line window comfortably covers two polling intervals
        # plus router log churn from DHCP/Wi-Fi.
        try:
            data = await self._rci_post("", {"show": {"log": {"max-lines": 1000, "once": True}}})
            entries = _extract_log_entries(data)
            if entries:
                diag = self._parse_ipsec_vici_diagnostics([], entries=entries)
                diag["command"] = "show log 1000 once"
                return diag
        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            _LOGGER.debug("show/log JSON path failed: %s — falling back to text", err)
        # Fallback to text-only path (no per-event timestamps).
        try:
            data = await self._rci_parse("show log 200 once")
            lines = _extract_parse_messages(data)
            diag = self._parse_ipsec_vici_diagnostics(lines)
            diag["command"] = "show log 200 once"
            return diag
        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            _LOGGER.debug("Error getting IPsec diagnostics: %s", err)
            return {}

    @classmethod
    def _parse_ipsec_statusall(cls, text: str) -> Dict[str, Dict[str, Any]]:
        """Parse strongSwan ``ipsec statusall`` text into normalized dicts.

        The output mirrors the dict shape produced by
        ``async_get_crypto_maps`` so existing sensors/binary_sensors
        and entity wiring keep working without changes. The key
        difference is the data source: stroke (this) instead of vici
        (crypto_maps), avoiding the per-call OOM leak in ``ndm``'s
        ``IpSec::Vici::Stats`` handler on KeeneticOS 5.x.
        """
        if not isinstance(text, str) or not text:
            return {}

        # Per-tunnel accumulator. We see the same tunnel name across
        # several brief lines (phase1 header, phase1 SPI, phase1
        # proposal, phase2 header, phase2 counters) — merge into one
        # record per name.
        tunnels: Dict[str, Dict[str, Any]] = {}

        def _t(name: str) -> Dict[str, Any]:
            t = tunnels.get(name)
            if t is None:
                t = {
                    "name": name,
                    "enabled": True,        # presence in statusall ≡ loaded ≡ enabled
                    "remote_peer": None,
                    "mode": None,
                    "ipsec_profile_name": name,
                    "state": "UNDEFINED",
                    "ike_state": "UNDEFINED",
                    FIELD_CONNECTED: False,
                    "via": None,
                    "local_endpoint": None,
                    "remote_endpoint": None,
                    "rx_bytes": 0,
                    "tx_bytes": 0,
                    "rx_packets": 0,
                    "tx_packets": 0,
                    "phase1": None,
                    "phase2_sa_list": [],
                    "raw_config": {},
                    "raw_status": {},
                }
                tunnels[name] = t
            return t

        in_sa_section = False
        in_connections_section = False
        for raw in text.splitlines():
            line = raw.rstrip()
            if not line.strip():
                continue

            # Section markers
            if line.startswith("Connections:"):
                in_connections_section = True
                in_sa_section = False
                continue
            if _IPSEC_SA_HEADER_RE.match(line):
                in_sa_section = True
                in_connections_section = False
                continue
            if line.startswith("Listening IP addresses") or line.startswith("Status of IKE"):
                in_connections_section = False
                in_sa_section = False
                continue

            if in_connections_section:
                m = _IPSEC_CONNECTION_LINE_RE.match(line)
                if m:
                    name = m.group("name")
                    _t(name)  # mark as configured
                    continue
                m = _IPSEC_CONNECTION_CHILD_RE.match(line)
                if m:
                    t = _t(m.group("name"))
                    t["mode"] = (m.group("mode") or "").lower() or t["mode"]
                    continue
                continue

            if not in_sa_section:
                continue

            m = _IPSEC_PHASE1_HEADER_RE.match(line)
            if m:
                t = _t(m.group("name"))
                t["ike_state"] = m.group("state")
                t["local_endpoint"] = _clean_addr(m.group("local"))
                t["remote_endpoint"] = _clean_addr(m.group("remote"))
                t["remote_peer"] = m.group("remote_id") or t["remote_peer"]
                p1 = t["phase1"] or {}
                p1.update({
                    "name": m.group("name"),
                    "ike_state": m.group("state"),
                    "unique_id": _to_int(m.group("sa_id")),
                    "local_addr": _clean_addr(m.group("local")),
                    "remote_addr": _clean_addr(m.group("remote")),
                })
                t["phase1"] = p1
                continue

            m = _IPSEC_PHASE1_SPI_RE.match(line)
            if m:
                t = _t(m.group("name"))
                p1 = t["phase1"] or {"name": m.group("name")}
                ver = m.group("ver") or ""
                p1["ike_version"] = "2" if "2" in ver else ("1" if "1" in ver else None)
                p1["local_spi"] = m.group("local_spi")
                p1["remote_spi"] = m.group("remote_spi")
                if m.group("rekey"):
                    p1["rekey_in"] = m.group("rekey").strip()
                t["phase1"] = p1
                continue

            m = _IPSEC_PHASE1_PROPOSAL_RE.match(line)
            if m:
                t = _t(m.group("name"))
                p1 = t["phase1"] or {"name": m.group("name")}
                p1["proposal"] = m.group("proposal")
                t["phase1"] = p1
                continue

            m = _IPSEC_PHASE2_HEADER_RE.match(line)
            if m:
                t = _t(m.group("name"))
                sa_id = _to_int(m.group("sa_id"))
                sa = next(
                    (s for s in t["phase2_sa_list"] if s.get("unique_id") == sa_id),
                    None,
                )
                if sa is None:
                    sa = {"unique_id": sa_id}
                    t["phase2_sa_list"].append(sa)
                sa["sa_state"] = m.group("state")
                if m.group("mode"):
                    sa["mode"] = m.group("mode")
                if m.group("reqid"):
                    sa["request_id"] = _to_int(m.group("reqid"))
                if m.group("proto"):
                    sa["protocol"] = m.group("proto")
                sa["encapsulation"] = bool(m.group("encap"))
                sa["local_spi"] = m.group("local_spi")
                sa["remote_spi"] = m.group("remote_spi")
                continue

            m = _IPSEC_PHASE2_COUNTERS_RE.match(line)
            if m:
                t = _t(m.group("name"))
                sa_id = _to_int(m.group("sa_id"))
                sa = next(
                    (s for s in t["phase2_sa_list"] if s.get("unique_id") == sa_id),
                    None,
                )
                if sa is None:
                    sa = {"unique_id": sa_id}
                    t["phase2_sa_list"].append(sa)
                sa["ipsec_cypher"] = m.group("proposal")
                sa["in_bytes"] = m.group("bytes_i")
                sa["out_bytes"] = m.group("bytes_o")
                if m.group("pkts_i"):
                    sa["in_packets"] = m.group("pkts_i")
                if m.group("pkts_o"):
                    sa["out_packets"] = m.group("pkts_o")
                if m.group("last_i"):
                    sa["in_time"] = _to_int(m.group("last_i"))
                if m.group("last_o"):
                    sa["out_time"] = _to_int(m.group("last_o"))
                if m.group("rekey"):
                    sa["rekey_in"] = m.group("rekey").strip()
                continue

        # Roll up counters across SAs and derive overall state.
        for name, t in tunnels.items():
            rx_b = tx_b = rx_p = tx_p = 0
            installed = False
            for sa in t["phase2_sa_list"]:
                rx_b += _to_int(sa.get("in_bytes"))
                tx_b += _to_int(sa.get("out_bytes"))
                rx_p += _to_int(sa.get("in_packets"))
                tx_p += _to_int(sa.get("out_packets"))
                if str(sa.get("sa_state", "")).upper() == "INSTALLED":
                    installed = True
            t["rx_bytes"] = rx_b
            t["tx_bytes"] = tx_b
            t["rx_packets"] = rx_p
            t["tx_packets"] = tx_p

            ike_up = t["ike_state"] in ("ESTABLISHED", "REKEYING")
            if installed and ike_up:
                t["state"] = IPSEC_STATE_ESTABLISHED
                t[FIELD_CONNECTED] = True
            elif ike_up:
                t["state"] = t["ike_state"] if t["ike_state"] == "REKEYING" else "PHASE1_ONLY"
            elif t["ike_state"] == "CONNECTING":
                t["state"] = t["ike_state"]
            else:
                t["state"] = "UNDEFINED"

        return tunnels

    async def async_get_ipsec_status(self) -> Dict[str, Dict[str, Any]]:
        """Return site-to-site IPsec tunnels via the safe stroke path.

        Replaces ``async_get_crypto_maps`` for periodic polling. Unlike
        ``show/crypto/map`` (which dispatches a Vici ``Stats`` query
        inside ``ndm`` and leaks memory on KeeneticOS 5.x), this calls
        ``show/ipsec`` — a stroke-based endpoint that returns the
        strongSwan ``ipsec statusall`` text dump and never triggers the
        upstream OOM. Empirically verified: 90+ rapid calls produce
        zero ``IpSec::Vici::Stats: out of memory`` events.

        Output shape matches ``async_get_crypto_maps`` so downstream
        sensors/binary_sensors keep working unchanged.
        """
        try:
            data = await self._rci_get("show/ipsec")
        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            _LOGGER.debug("show/ipsec unavailable: %s", err)
            return {}

        if not isinstance(data, dict):
            return {}
        text = data.get("ipsec_statusall")
        if not isinstance(text, str) or not text:
            return {}
        return self._parse_ipsec_statusall(text)

    async def async_get_crypto_maps(self) -> Dict[str, Dict[str, Any]]:
        """Return site-to-site IPsec tunnels (`crypto map` entries).

        Endpoint: rci/show/crypto/map

        Site-to-site IPsec tunnels do NOT appear as virtual interfaces
        in /rci/show/interface, so they need their own data path and
        their own entity model — they can't piggyback on the existing
        per-WAN / per-VPN-client plumbing that other VPN types use.

        The router response looks like (tunnel that never came up):
            {
              "crypto_map": {
                "TEST": {
                  "config": {
                    "remote_peer": "192.0.2.1",
                    "enabled": "yes",              # NOTE: string, not bool
                    "crypto_ipsec_profile_name": "TEST",
                    "mode": "tunnel"
                  },
                  "status": {
                    "primary_peer": true,
                    "initiator": true,
                    "ike_state": "UNDEFINED",
                    "state": "UNDEFINED",
                    "via": "PPPoE0",
                    "local-endpoint-address": "78.188.13.104",
                    "remote-endpoint-address": "192.0.2.1"
                  }
                }
              }
            }

        A fully established tunnel additionally has `status.phase1`
        (dict) and `status.phase2_sa_list.phase2_sa` (list of SA dicts
        with in_bytes / out_bytes counters). We treat those as optional
        because the router only populates them once SA negotiation has
        actually happened.

        We normalise to:
            {
              "<name>": {
                "name": "TEST",
                "enabled": True,                   # config.enabled == "yes"
                "remote_peer": "192.0.2.1",
                "mode": "tunnel",
                "ipsec_profile_name": "TEST",
                "state": "UNDEFINED",              # status.state
                "ike_state": "UNDEFINED",          # status.phase1.ike_state
                                                   #   or status.ike_state
                FIELD_CONNECTED: False,                # state == PHASE2_ESTABLISHED
                "via": "PPPoE0" or None,
                "local_endpoint": "78.188.13.104" or None,
                "remote_endpoint": "192.0.2.1" or None,
                "rx_bytes": 1506697,               # sum across phase2 SAs
                "tx_bytes": 129642,                # sum across phase2 SAs
                "rx_packets": 2950,
                "tx_packets": 2360,
                "phase1": {...} or None,           # raw, for v2 sensors
                "phase2_sa_list": [...] or [],     # raw, normalised to list
                "raw_status": {...},               # raw status for diag
                "raw_config": {...},
              }
            }
        """
        if self._crypto_map_supported is False:
            return {}
        try:
            data = await self._rci_get("show/crypto/map")
        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            if _is_endpoint_missing(err):
                self._crypto_map_supported = False
            _LOGGER.debug("show/crypto/map unavailable: %s", err)
            return {}
        self._crypto_map_supported = True

        if not isinstance(data, dict):
            return {}
        raw_maps = data.get("crypto_map") or {}

        if not isinstance(raw_maps, dict):
            return {}

        result: Dict[str, Dict[str, Any]] = {}
        for name, entry in raw_maps.items():
            if not isinstance(entry, dict):
                continue

            config = entry.get("config") or {}
            status = entry.get("status") or {}
            if not isinstance(config, dict):
                config = {}
            if not isinstance(status, dict):
                status = {}

            # phase1 may live either under status.phase1 (when router
            # has negotiated) or — on some firmwares — the ike_state
            # field alone is promoted to status.ike_state with no
            # phase1 block. Handle both.
            phase1 = status.get("phase1")
            if not isinstance(phase1, dict):
                phase1 = None

            ike_state = None
            if phase1:
                ike_state = _clean_str(phase1.get("ike_state"))
            if not ike_state:
                ike_state = _clean_str(status.get("ike_state"))

            # phase2 SA list — present only when SAs have been set up.
            p2_wrapper = status.get("phase2_sa_list") or {}
            if not isinstance(p2_wrapper, dict):
                p2_wrapper = {}
            phase2_sa_list = _as_list(p2_wrapper.get("phase2_sa"))

            rx_bytes = 0
            tx_bytes = 0
            rx_packets = 0
            tx_packets = 0
            for sa in phase2_sa_list:
                if not isinstance(sa, dict):
                    continue
                rx_bytes += _to_int(sa.get("in_bytes"))
                tx_bytes += _to_int(sa.get("out_bytes"))
                rx_packets += _to_int(sa.get("in_packets"))
                tx_packets += _to_int(sa.get("out_packets"))

            state = _clean_str(status.get("state"))
            connected = state == IPSEC_STATE_ESTABLISHED
            local_endpoint = _clean_addr(status.get("local-endpoint-address"))
            remote_endpoint = _clean_addr(status.get("remote-endpoint-address"))

            result[name] = {
                "name": name,
                "enabled": coerce_bool(config.get("enabled")),
                "remote_peer": _clean_str(config.get("remote_peer")),
                "mode": _clean_str(config.get("mode")),
                "ipsec_profile_name": _clean_str(
                    config.get("crypto_ipsec_profile_name")
                ),
                "state": state,
                "ike_state": ike_state,
                FIELD_CONNECTED: connected,
                "via": _clean_str(status.get("via")),
                "local_endpoint": local_endpoint,
                "remote_endpoint": remote_endpoint,
                "rx_bytes": rx_bytes,
                "tx_bytes": tx_bytes,
                "rx_packets": rx_packets,
                "tx_packets": tx_packets,
                "phase1": phase1,
                "phase2_sa_list": phase2_sa_list,
                "raw_config": config,
                "raw_status": status,
            }

        return result

    async def async_set_crypto_map_enabled(
        self, name: str, enabled: bool
    ) -> None:
        """Enable or disable a site-to-site IPsec `crypto map` entry.

        Unlike VPN-client interfaces (which are toggled via
        `interface X up/down`), site-to-site tunnels live under the
        `crypto map <name>` configuration sub-mode. The CLI pattern is:

            crypto map <name>
              enable     (or: no enable)

        We send this as a single RCI parse call with an embedded
        newline. Changes are runtime-only until persisted, so we
        follow up with `system configuration save` so the toggle
        survives a reboot — matching the user's expectation that a
        Home Assistant switch toggle is permanent.
        """
        name = _validate_cli_arg(name, "crypto map name")
        verb = "enable" if enabled else "no enable"
        cmd = f"crypto map {name}\n{verb}"
        _LOGGER.debug(
            "Set crypto map %s enabled=%s via: %r", name, enabled, cmd
        )
        await self._rci_parse(cmd)
        # Persist so the change survives a reboot. Without this the
        # toggle is lost on the next router restart and the user sees
        # the switch "flip back" with no obvious reason.
        try:
            await self._rci_parse("system configuration save")
        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            _LOGGER.warning(
                "crypto map %s toggled to enabled=%s but "
                "'system configuration save' failed: %s — change will "
                "be lost on reboot",
                name,
                enabled,
                err,
            )
