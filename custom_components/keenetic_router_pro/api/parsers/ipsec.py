"""IPsec parser helpers."""

from __future__ import annotations

import re
from typing import Any

from ...const import FIELD_CONNECTED, IPSEC_STATE_ESTABLISHED
from ..constants import _IPSEC_VICI_OOM_RE
from ..helpers import _clean_addr, _to_int


# Regexes for the deterministic strongSwan ``ipsec statusall`` text dump
# served by RCI endpoint ``show/ipsec``. We parse the brief Security
# Associations block because it carries everything we need on one line
# per record and is stable across strongSwan 5.x/6.x. The verbose
# multi-line section below it is intentionally ignored.
_IPSEC_PHASE1_HEADER_RE = re.compile(
    r"^\s*(?P<name>\S+)\[(?P<sa_id>\d+)\]:\s+"
    r"(?P<state>ESTABLISHED|CONNECTING|REKEYING|DELETING)\s+"
    r"[^,\n]*,\s+"
    r"(?P<local>\S+)\[(?P<local_id>[^\]]+)\]\.\.\."
    r"(?P<remote>\S+)\[(?P<remote_id>[^\]]+)\]\s*$"
)
_IPSEC_PHASE1_SPI_RE = re.compile(
    r"^\s*(?P<name>\S+)\[(?P<sa_id>\d+)\]:\s+"
    r"(?P<ver>IKEv?\d?)\s+SPIs:\s+"
    r"(?P<local_spi>\S+?)_i\s+(?P<remote_spi>\S+?)_r\*?,\s+"
    r"(?:rekeying in (?P<rekey>[^,\n]+)|reauthentication disabled)\s*$"
)
_IPSEC_PHASE1_PROPOSAL_RE = re.compile(
    r"^\s*(?P<name>\S+)\[(?P<sa_id>\d+)\]:\s+IKE proposal:\s+(?P<proposal>\S+)\s*$"
)
_IPSEC_PHASE2_HEADER_RE = re.compile(
    r"^\s*(?P<name>\S+)\{(?P<sa_id>\d+)\}:\s+"
    r"(?P<state>INSTALLED|REKEYING|DELETING|REKEYED)"
    r"(?P<rest>[^\n]*)$"
)
_IPSEC_PHASE2_SPI_RE = re.compile(
    r"^(?P<local_spi>\S+?)_i\s+(?P<remote_spi>\S+?)_o\s*$"
)
_IPSEC_PHASE2_COUNTERS_RE = re.compile(
    r"^\s*(?P<name>\S+)\{(?P<sa_id>\d+)\}:\s+"
    r"(?P<proposal>\S+?),\s+"
    r"(?P<bytes_i>\d+)\s+bytes_i"
    r"(?:\s+\((?P<pkts_i>\d+)\s+pkts(?:,\s+(?P<last_i>\d+)s ago)?\))?,\s+"
    r"(?P<bytes_o>\d+)\s+bytes_o"
    r"(?:\s+\((?P<pkts_o>\d+)\s+pkts(?:,\s+(?P<last_o>\d+)s ago)?\))?"
    r"(?:,\s+rekeying in (?P<rekey>[^,\n]+))?\s*$"
)
_IPSEC_CONNECTION_LINE_RE = re.compile(
    r"^\s*(?P<name>\S+?):\s+\S+\.\.\.\S+\s+IKEv?\d?"
)
_IPSEC_CONNECTION_CHILD_RE = re.compile(
    r"^\s*(?P<name>\S+):\s+child:\s+(?P<rest>[^\n]*)$"
)
_IPSEC_SA_HEADER_RE = re.compile(
    r"^Security Associations\s+\((?P<up>\d+)\s+up,\s+(?P<connecting>\d+)\s+connecting\)"
)


def _phase2_header_fields(match: re.Match[str]) -> dict[str, Any] | None:
    """Return normalized Phase 2 header fields without backtracking-heavy regex."""
    rest = match.group("rest") or ""
    if "SPIs:" not in rest:
        return None
    before_spi, spi_text = rest.rsplit("SPIs:", 1)
    spi_match = _IPSEC_PHASE2_SPI_RE.match(spi_text.strip())
    if not spi_match:
        return None

    fields: dict[str, Any] = {
        "sa_state": match.group("state"),
        "encapsulation": False,
        "local_spi": spi_match.group("local_spi"),
        "remote_spi": spi_match.group("remote_spi"),
    }
    for segment in (part.strip() for part in before_spi.split(",")):
        if not segment:
            continue
        if segment in {"TUNNEL", "TRANSPORT"}:
            fields["mode"] = segment
            continue
        if segment.startswith("reqid "):
            fields["request_id"] = _to_int(segment.removeprefix("reqid ").strip())
            continue
        if segment.startswith(("ESP", "AH")):
            fields["protocol"] = segment.split()[0]
            fields["encapsulation"] = "in UDP" in segment
            continue
        if "in UDP" in segment:
            fields["encapsulation"] = True
    return fields


def _connection_child_mode(text: str) -> str | None:
    """Extract child SA mode from a connection line tail."""
    child_selector = text.split(",", 1)[0].strip()
    if not child_selector:
        return None
    mode = child_selector.rsplit(maxsplit=1)[-1]
    if mode in {"TUNNEL", "TRANSPORT"}:
        return mode
    return None


def parse_ipsec_vici_diagnostics(
    lines: list[str],
    entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize recent IPsec VICI memory errors from router log lines."""
    matches: list[dict[str, Any]] = []
    if entries is not None:
        for ent in entries:
            if not isinstance(ent, dict):
                continue
            msg = ent.get("message")
            if not isinstance(msg, str):
                continue
            match = _IPSEC_VICI_OOM_RE.search(msg)
            if not match:
                continue
            matches.append(
                {
                    "line": msg.strip(),
                    "code": match.group("code"),
                    "time": ent.get("time"),
                }
            )
    else:
        for line in lines:
            if not isinstance(line, str):
                continue
            match = _IPSEC_VICI_OOM_RE.search(line)
            if not match:
                continue
            matches.append(
                {
                    "line": line.strip(),
                    "code": match.group("code"),
                    "time": None,
                }
            )

    last_match = None
    if matches:
        last_match = matches[0] if entries is not None else matches[-1]
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


def parse_ipsec_statusall(text: str) -> dict[str, dict[str, Any]]:
    """Parse strongSwan ``ipsec statusall`` text into normalized dicts."""
    if not isinstance(text, str) or not text:
        return {}

    tunnels: dict[str, dict[str, Any]] = {}

    def _t(name: str) -> dict[str, Any]:
        tunnel = tunnels.get(name)
        if tunnel is None:
            tunnel = {
                "name": name,
                "enabled": True,
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
            tunnels[name] = tunnel
        return tunnel

    in_sa_section = False
    in_connections_section = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue

        if line.startswith("Connections:"):
            in_connections_section = True
            in_sa_section = False
            continue
        if _IPSEC_SA_HEADER_RE.match(line):
            in_sa_section = True
            in_connections_section = False
            continue
        if line.startswith(("Listening IP addresses", "Status of IKE")):
            in_connections_section = False
            in_sa_section = False
            continue

        if in_connections_section:
            match = _IPSEC_CONNECTION_LINE_RE.match(line)
            if match:
                _t(match.group("name"))
                continue
            match = _IPSEC_CONNECTION_CHILD_RE.match(line)
            if match:
                mode = _connection_child_mode(match.group("rest"))
                if mode:
                    tunnel = _t(match.group("name"))
                    tunnel["mode"] = mode.lower()
                continue
            continue

        if not in_sa_section:
            continue

        match = _IPSEC_PHASE1_HEADER_RE.match(line)
        if match:
            tunnel = _t(match.group("name"))
            tunnel["ike_state"] = match.group("state")
            tunnel["local_endpoint"] = _clean_addr(match.group("local"))
            tunnel["remote_endpoint"] = _clean_addr(match.group("remote"))
            tunnel["remote_peer"] = match.group("remote_id") or tunnel["remote_peer"]
            phase1 = tunnel["phase1"] or {}
            phase1.update(
                {
                    "name": match.group("name"),
                    "ike_state": match.group("state"),
                    "unique_id": _to_int(match.group("sa_id")),
                    "local_addr": _clean_addr(match.group("local")),
                    "remote_addr": _clean_addr(match.group("remote")),
                }
            )
            tunnel["phase1"] = phase1
            continue

        match = _IPSEC_PHASE1_SPI_RE.match(line)
        if match:
            tunnel = _t(match.group("name"))
            phase1 = tunnel["phase1"] or {"name": match.group("name")}
            ver = match.group("ver") or ""
            ike_version = None
            if "2" in ver:
                ike_version = "2"
            elif "1" in ver:
                ike_version = "1"
            phase1["ike_version"] = ike_version
            phase1["local_spi"] = match.group("local_spi")
            phase1["remote_spi"] = match.group("remote_spi")
            if match.group("rekey"):
                phase1["rekey_in"] = match.group("rekey").strip()
            tunnel["phase1"] = phase1
            continue

        match = _IPSEC_PHASE1_PROPOSAL_RE.match(line)
        if match:
            tunnel = _t(match.group("name"))
            phase1 = tunnel["phase1"] or {"name": match.group("name")}
            phase1["proposal"] = match.group("proposal")
            tunnel["phase1"] = phase1
            continue

        match = _IPSEC_PHASE2_HEADER_RE.match(line)
        if match:
            fields = _phase2_header_fields(match)
            if fields is not None:
                tunnel = _t(match.group("name"))
                sa_id = _to_int(match.group("sa_id"))
                sa = next(
                    (
                        item
                        for item in tunnel["phase2_sa_list"]
                        if item.get("unique_id") == sa_id
                    ),
                    None,
                )
                if sa is None:
                    sa = {"unique_id": sa_id}
                    tunnel["phase2_sa_list"].append(sa)
                sa.update(fields)
                continue

        match = _IPSEC_PHASE2_COUNTERS_RE.match(line)
        if match:
            tunnel = _t(match.group("name"))
            sa_id = _to_int(match.group("sa_id"))
            sa = next(
                (
                    item
                    for item in tunnel["phase2_sa_list"]
                    if item.get("unique_id") == sa_id
                ),
                None,
            )
            if sa is None:
                sa = {"unique_id": sa_id}
                tunnel["phase2_sa_list"].append(sa)
            sa["ipsec_cypher"] = match.group("proposal")
            sa["in_bytes"] = match.group("bytes_i")
            sa["out_bytes"] = match.group("bytes_o")
            if match.group("pkts_i"):
                sa["in_packets"] = match.group("pkts_i")
            if match.group("pkts_o"):
                sa["out_packets"] = match.group("pkts_o")
            if match.group("last_i"):
                sa["in_time"] = _to_int(match.group("last_i"))
            if match.group("last_o"):
                sa["out_time"] = _to_int(match.group("last_o"))
            if match.group("rekey"):
                sa["rekey_in"] = match.group("rekey").strip()
            continue

    for tunnel in tunnels.values():
        rx_bytes = tx_bytes = rx_packets = tx_packets = 0
        installed = False
        for sa in tunnel["phase2_sa_list"]:
            rx_bytes += _to_int(sa.get("in_bytes"))
            tx_bytes += _to_int(sa.get("out_bytes"))
            rx_packets += _to_int(sa.get("in_packets"))
            tx_packets += _to_int(sa.get("out_packets"))
            if str(sa.get("sa_state", "")).upper() == "INSTALLED":
                installed = True
        tunnel["rx_bytes"] = rx_bytes
        tunnel["tx_bytes"] = tx_bytes
        tunnel["rx_packets"] = rx_packets
        tunnel["tx_packets"] = tx_packets

        ike_up = tunnel["ike_state"] in ("ESTABLISHED", "REKEYING")
        if installed and ike_up:
            tunnel["state"] = IPSEC_STATE_ESTABLISHED
            tunnel[FIELD_CONNECTED] = True
        elif ike_up:
            tunnel["state"] = (
                tunnel["ike_state"]
                if tunnel["ike_state"] == "REKEYING"
                else "PHASE1_ONLY"
            )
        elif tunnel["ike_state"] == "CONNECTING":
            tunnel["state"] = tunnel["ike_state"]
        else:
            tunnel["state"] = "UNDEFINED"

    return tunnels
