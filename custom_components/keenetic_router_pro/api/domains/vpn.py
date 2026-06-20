"""VPN domain methods for KeeneticClient."""

from __future__ import annotations

import asyncio
import logging
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
from ...utils import coerce_bool, coerce_byte_count, first_present
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
from ..parsers.ipsec import (
    merge_crypto_map_config,
    parse_ipsec_statusall,
    parse_ipsec_vici_diagnostics,
)

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.vpn")


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
                # Sum traffic across ALL peers — taking only the first
                # undercounts multi-peer WireGuard interfaces.
                dict_peers = [p for p in peer if isinstance(p, dict)]
                first = dict_peers[0] if dict_peers else {}
                remote = first.get("remote-endpoint-address")
                if rx_val is None and dict_peers:
                    # Keenetic commonly reports counters as numeric strings;
                    # coerce each peer before summing so multi-peer string
                    # counters are not dropped (which undercounts to one peer).
                    nums = [
                        c for c in (coerce_byte_count(p.get("rxbytes")) for p in dict_peers)
                        if c is not None
                    ]
                    rx_val = sum(nums) if nums else first.get("rxbytes")
                if tx_val is None and dict_peers:
                    nums = [
                        c for c in (coerce_byte_count(p.get("txbytes")) for p in dict_peers)
                        if c is not None
                    ]
                    tx_val = sum(nums) if nums else first.get("txbytes")
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
        return parse_ipsec_vici_diagnostics(lines, entries=entries)

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
        return parse_ipsec_statusall(text)

    async def async_get_crypto_map_config(self) -> Dict[str, Dict[str, Any]]:
        """Return site-to-site crypto-map *configuration* (the ``enable`` flag).

        Endpoint: ``GET /rci/crypto/map`` — the configuration tree, not the
        ``show/crypto/map`` status view. It carries each map's administrative
        ``enable`` boolean and peer without dispatching the Vici ``Stats``
        query that leaks memory on KeeneticOS 5.x, so it is safe to poll.

        Returns ``{<name>: <config dict>}``; ``{}`` if the IPsec component is
        absent or the endpoint is unavailable.
        """
        try:
            data = await self._rci_get("crypto/map")
        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            _LOGGER.debug("crypto/map config unavailable: %s", err)
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            name: cfg for name, cfg in data.items() if isinstance(cfg, dict)
        }

    async def async_get_ipsec_status(self) -> Dict[str, Dict[str, Any]]:
        """Return site-to-site IPsec tunnels via the safe stroke path.

        Replaces ``async_get_crypto_maps`` for periodic polling. Unlike
        ``show/crypto/map`` (which dispatches a Vici ``Stats`` query
        inside ``ndm`` and leaks memory on KeeneticOS 5.x), this calls
        ``show/ipsec`` — a stroke-based endpoint that returns the
        strongSwan ``ipsec statusall`` text dump and never triggers the
        upstream OOM. Empirically verified: 90+ rapid calls produce
        zero ``IpSec::Vici::Stats: out of memory`` events.

        The runtime status is merged with the ``crypto/map`` config tree so
        that a tunnel toggled **off** stays visible as a known, available,
        ``off`` entry instead of disappearing from the status dump (which
        would make its switch/sensors go *unavailable* and strand recovery
        automations that guard on the switch reading ``"on"``). Both reads
        run in parallel.

        Output shape matches ``async_get_crypto_maps`` so downstream
        sensors/binary_sensors keep working unchanged.
        """
        status_data, config = await asyncio.gather(
            self._rci_get("show/ipsec"),
            self.async_get_crypto_map_config(),
            return_exceptions=True,
        )

        if isinstance(status_data, asyncio.CancelledError):
            raise status_data
        if isinstance(config, asyncio.CancelledError):
            raise config

        status: Dict[str, Dict[str, Any]] = {}
        if isinstance(status_data, dict):
            text = status_data.get("ipsec_statusall")
            if isinstance(text, str) and text:
                status = self._parse_ipsec_statusall(text)
        elif isinstance(status_data, BaseException):
            _LOGGER.debug("show/ipsec unavailable: %s", status_data)

        if isinstance(config, BaseException):
            _LOGGER.debug("crypto/map config unavailable: %s", config)
            config = {}

        return merge_crypto_map_config(status, config)

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
