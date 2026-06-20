"""WAN domain methods for KeeneticClient."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

import aiohttp

from ...const import (
    DOMAIN,
    LINK_STATE_DOWN,
    LINK_STATE_UP,
    UPLINK_ROLE_TOKENS,
    WAN_STATUS_CONNECTED,
    WAN_STATUS_DOWN,
    WAN_STATUS_LINK_UP,
)
from ...utils import coerce_bool
from ..errors import KeeneticApiError
from ..helpers import _dict_items, _is_endpoint_missing, _normalize_interfaces, iface_label
from ..parsers.wan import (
    derive_wan_enabled,
    derive_wan_internet_access,
    extract_wan_ip,
    is_ranked_wan_interface,
)

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.wan")


class WanMixin:
    async def async_get_wan_status(
        self,
        interfaces: Dict[str, Any] | None = None,
        iface_list: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        """Get WAN interface status including external IP address.

        Status values:
          - "connected"  → interface up AND IP present
          - "link_up"    → interface up but no IP (ISP issue)
          - "down"       → no WAN interface found or down
        """
        if iface_list is None:
            if interfaces is None:
                interfaces = await self.async_get_interfaces()
            iface_list = _normalize_interfaces(interfaces)

        # ---------- yardımcı: sonuç oluştur ----------
        def _build_result(
            iface: Dict[str, Any], wan_type: str
        ) -> Dict[str, Any]:
            wan_ip = extract_wan_ip(iface, prefer_global_address=True)
            link_state = str(iface.get("state") or "").lower()
            if link_state == LINK_STATE_UP:
                status = WAN_STATUS_CONNECTED if wan_ip else WAN_STATUS_LINK_UP
            else:
                status = WAN_STATUS_DOWN
            return {
                "status": status,
                "ip": wan_ip,
                "interface": iface.get("id") or iface.get("interface-name"),
                "uptime": iface.get("uptime"),
                "gateway": (
                    iface.get("gateway")
                    or iface.get("remote")
                    or iface.get("default-gateway")
                ),
                "type": wan_type,
                "link": link_state,
            }

        # ---------- yardımcı: WAN keyword eşleşmesi ----------
        WAN_KEYWORDS = ("wan", "internet", "isp", "broadband")

        def _is_wan_iface(iface: Dict[str, Any]) -> bool:
            """Interface'in WAN olup olmadığını birden fazla ipucuyla belirle."""
            # security-level: public → Keenetic'te WAN demek
            sec = str(iface.get("security-level") or "").lower()
            if sec == "public":
                return True
            # role: inet — may be a single string or a list of role tokens.
            role = iface.get("role")
            if isinstance(role, list):
                if any(str(item).lower() in UPLINK_ROLE_TOKENS for item in role):
                    return True
            elif str(role or "").lower() in UPLINK_ROLE_TOKENS:
                return True
            # İsim tabanlı arama
            name_fields = [
                iface.get("name"),
                iface.get("ifname"),
                iface.get("id"),
                iface.get("interface-name"),
                iface.get("description"),
                iface.get("type"),
            ]
            name_joined = " ".join(str(v) for v in name_fields if v).lower()
            return any(k in name_joined for k in WAN_KEYWORDS)

        for iface in iface_list:
            itype = str(iface.get("type") or "").lower()
            state = str(iface.get("state") or "").lower()
            if itype == "pppoe" and state == LINK_STATE_UP:
                return _build_result(iface, "pppoe")

        for iface in iface_list:
            state = str(iface.get("state") or "").lower()
            if state == LINK_STATE_UP and _is_wan_iface(iface):
                return _build_result(iface, "ethernet")

        for iface in iface_list:
            if _is_wan_iface(iface):
                return _build_result(iface, "ethernet")

        return {"status": WAN_STATUS_DOWN, "ip": None, "link": LINK_STATE_DOWN}

    async def async_get_wan_interfaces(
        self,
        interfaces: Dict[str, Any] | None = None,
        iface_list: List[Dict[str, Any]] | None = None,
    ) -> List[Dict[str, Any]]:
        """Return per-uplink info for every configured WAN interface.

        Enumerates *all* uplink-capable interfaces Keenetic knows about —
        not just the currently active one — so Home Assistant can expose
        a full picture of the multi-WAN / failover configuration.

        WAN detection logic (derived from real show/interface output):
          - `global: true` — interface has a routable, "public-facing" role
          - `priority` is set — interface participates in Keenetic's
            uplink priority ordering (this is what puts an interface into
            the "Connection priorities" list in the web UI)
          - `role` contains "inet" — explicit uplink tag
          Any interface matching (`global=true` AND `priority` is set),
          OR with `role` containing "inet", is treated as a WAN.

          Interfaces that are merely carriers for a PPPoE/VLAN (e.g. the
          raw GigabitEthernet1 below PPPoE0) are *not* WANs — they have
          `global: false` and no `priority`, so they fail the filter
          naturally. They show up as `via` / `underlying` on the WAN that
          rides on top of them.

        Each entry in the returned list contains:
            id                 interface id (PPPoE0, Wireguard0, ...)
            description        human-readable description from the router
                               UI ("Telekom", "Zurich"), falls back to id
            interface_name     the "interface-name" field (e.g. "ISP")
            type               interface type (PPPoE / Wireguard / ...)
            link_state         "up" / "down"
            enabled            bool — True when the interface is configured
                               up (summary.layer.conf != "disabled")
            global             bool — has a global (public) role
            defaultgw          bool — currently the default gateway
            priority           int — Keenetic uplink priority (higher wins)
            role               list[str] — e.g. ["inet"]
            security_level     "public" / "private" / "protected"
            ip                 current public IP, if any
            mask               subnet mask, if any
            uptime             seconds since the session came up
            underlying         id of the physical/logical interface this
                               session rides on (PPPoE `via`), if any
            remote             remote peer address (PPPoE/tunnel)
            mac                L2 address if applicable
            internet_access    bool — best-effort ping-check / reachability
                               heuristic (see _derive_internet_access)
            summary_layers     nested summary.layer dict (conf/link/ipv4/...)
            raw                the untouched interface dict, for consumers
                               that want a field we didn't pull out
        """
        if iface_list is None:
            if interfaces is None:
                interfaces = await self.async_get_interfaces()
            iface_list = _normalize_interfaces(interfaces)

        wans: List[Dict[str, Any]] = []
        for iface in iface_list:
            if not is_ranked_wan_interface(iface):
                continue
            iface_id = iface.get("id") or iface.get("interface-name")
            if not iface_id:
                continue

            role = iface.get("role")
            if isinstance(role, str):
                role_list = [role]
            elif isinstance(role, list):
                role_list = [str(r) for r in role]
            else:
                role_list = []

            wans.append({
                "id": iface_id,
                "description": iface_label(iface, iface_id),
                "interface_name": iface.get("interface-name"),
                "type": iface.get("type"),
                "link_state": str(iface.get("state") or LINK_STATE_DOWN).lower(),
                "enabled": derive_wan_enabled(iface),
                "global": coerce_bool(iface.get("global")),
                "defaultgw": coerce_bool(iface.get("defaultgw")),
                "priority": iface.get("priority"),
                "role": role_list,
                "security_level": iface.get("security-level"),
                "ip": extract_wan_ip(iface),
                "mask": iface.get("mask"),
                "uptime": iface.get("uptime"),
                "underlying": iface.get("via"),
                "remote": iface.get("remote"),
                "mac": iface.get("mac"),
                "internet_access": derive_wan_internet_access(iface),
                "summary_layers": (
                    iface["summary"].get("layer") or {}
                    if isinstance(iface.get("summary"), dict)
                    else {}
                ),
                "raw": iface,
            })

        return wans

    async def async_get_ping_check_status(self) -> Dict[str, Any]:
        """Return the router's ping-check results per interface.

        This is the authoritative "is the internet actually reachable
        through this WAN" signal — the same data that drives the red
        "NO INTERNET ACCESS (PING CHECK)" badge in the Keenetic web UI
        and that the router itself uses to decide when to fail over to
        a backup uplink.

        Endpoint: rci/show/ping-check
        Example response:
            {
              "pingcheck": [
                {
                  "profile": "default",
                  "host": ["captive.keenetic.net"],
                  "port": 80,
                  "update-interval": 30,
                  "max-fails": 3,
                  "mode": "icmp",
                  "interface": {
                    "PPPoE0": {
                      "successcount": 7,
                      "failcount": 0,
                      "status": "pass",
                      "ipcache": [
                        {"host": "captive.keenetic.net",
                         "addresses": ["135.181.129.158", "..."]}
                      ]
                    }
                  }
                }
              ]
            }

        Returns a flat dict keyed by interface id:
            {
              "PPPoE0": {
                "status": "pass",                 # "pass" | "fail"
                "success_count": 7,
                "fail_count": 0,
                "profile": "default",             # winning profile name
                "check_hosts": ["captive.keenetic.net"],
                "check_addresses": ["135.181.129.158", ...],
                "check_port": 80,
                "check_mode": "icmp",
                "update_interval": 30,
                "max_fails": 3,
                "all_profiles": [                 # every profile touching
                  {"profile": "...", "status": "...", ...}   # this iface
                ],
              }
            }

        A router may have multiple profiles bound to the same interface.

        IMPORTANT: profiles named `_WEBADMIN_<InterfaceId>` are NOT
        transient — current Keenetic firmware persists user-enabled
        Ping Check configurations under that name when the user toggles
        "Check the Availability of the Internet (Ping Check)" in the
        web UI. They have real `update-interval`, `max-fails`, real
        check hosts and live counters, and they ARE the authoritative
        ping-check signal for that WAN.

        We instead identify *truly* transient profiles by their target
        address: one-off connection tests target IANA documentation /
        TEST-NET ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24).
        Those are the only profiles we ignore.

        When multiple authoritative profiles report on the same interface,
        the aggregate status is "fail" if any profile is failing (matches
        how Keenetic itself treats the WAN as unusable for routing).
        """
        if self._ping_check_supported is False:
            return {}
        try:
            data = await self._rci_get("show/ping-check") or {}
        except asyncio.CancelledError:
            raise
        except (KeeneticApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as err:
            if _is_endpoint_missing(err):
                self._ping_check_supported = False
            _LOGGER.debug("show/ping-check unavailable: %s", err)
            return {}
        self._ping_check_supported = True
        # Firmware may collapse a single ping-check profile to a dict.
        # _dict_items would descend into that profile's dict values (its
        # "interface" map) and lose the profile itself, so wrap it explicitly.
        raw = data.get("pingcheck") or []
        if isinstance(raw, dict) and (
            "interface" in raw or "profile" in raw or "host" in raw
        ):
            raw_profiles: list[dict[str, Any]] = [raw]
        else:
            raw_profiles = _dict_items(raw)

        # Collect per-interface observations from every profile that
        # actually has results (profile without `interface` block is
        # just a definition with nothing attached yet).
        observations: Dict[str, List[Dict[str, Any]]] = {}
        for profile_entry in raw_profiles:
            if not isinstance(profile_entry, dict):
                continue
            iface_map = profile_entry.get("interface")
            if not isinstance(iface_map, dict) or not iface_map:
                continue

            profile_name = str(profile_entry.get("profile") or "")
            host = profile_entry.get("host")
            if isinstance(host, str):
                hosts = [host]
            elif isinstance(host, list):
                hosts = [str(h) for h in host if h]
            else:
                hosts = []

            for iface_id, iface_result in iface_map.items():
                if not isinstance(iface_result, dict):
                    continue
                ipcache = iface_result.get("ipcache") or []
                addresses: List[str] = []
                cache_hosts: List[str] = []
                if isinstance(ipcache, list):
                    for entry in ipcache:
                        if not isinstance(entry, dict):
                            continue
                        h = entry.get("host")
                        if h:
                            cache_hosts.append(str(h))
                        addrs = entry.get("addresses") or []
                        if isinstance(addrs, list):
                            addresses.extend(str(a) for a in addrs if a)

                # Prefer ipcache hosts over profile-level host list when
                # both exist (ipcache reflects what the router actually
                # resolved and probed).
                effective_hosts = cache_hosts or hosts

                observation = {
                    "profile": profile_name,
                    "status": str(iface_result.get("status") or "").lower() or None,
                    "success_count": iface_result.get("successcount"),
                    "fail_count": iface_result.get("failcount"),
                    "check_hosts": effective_hosts,
                    "check_addresses": addresses,
                    "check_port": profile_entry.get("port"),
                    "check_mode": profile_entry.get("mode"),
                    "update_interval": profile_entry.get("update-interval"),
                    "max_fails": profile_entry.get("max-fails"),
                }
                observations.setdefault(iface_id, []).append(observation)

        # Per interface, pick "authoritative" profiles and aggregate.
        #
        # We only ignore profiles whose check targets fall entirely
        # inside IANA TEST-NET / documentation ranges, because those
        # are the one-off connection tests the web UI fires when the
        # user clicks "test connection" — they intentionally target
        # unroutable addresses and would otherwise produce permanent
        # false "fail" results.
        #
        # We do NOT filter by profile name. In particular,
        # `_WEBADMIN_<InterfaceId>` profiles are persistent, real,
        # user-enabled Ping Check configurations created from the
        # router's web UI — they are the authoritative ping-check
        # signal for that WAN and MUST be honoured.
        def _is_test_net_only(observation: Dict[str, Any]) -> bool:
            addrs = observation.get("check_addresses") or []
            hosts = observation.get("check_hosts") or []
            # If the router exposes resolved addresses, trust those over
            # host labels. ``ipcache.host`` may be a symbolic probe name
            # while the address itself is the TEST-NET one-off target.
            candidates = [str(x) for x in (list(addrs) or list(hosts)) if x]
            if not candidates:
                return False
            test_net_prefixes = ("192.0.2.", "198.51.100.", "203.0.113.")
            return all(c.startswith(test_net_prefixes) for c in candidates)

        result: Dict[str, Any] = {}
        for iface_id, obs_list in observations.items():
            real = [o for o in obs_list if not _is_test_net_only(o)]

            if not real:
                # Only TEST-NET probe profiles exist — don't trust them,
                # fall back to the link+IP heuristic downstream.
                result[iface_id] = {
                    "status": None,
                    "passing": None,
                    "profile": None,
                    "success_count": None,
                    "fail_count": None,
                    "check_hosts": [],
                    "check_addresses": [],
                    "check_port": None,
                    "check_mode": None,
                    "update_interval": None,
                    "max_fails": None,
                    "all_profiles": obs_list,
                    "ignored_profiles": [o.get("profile") for o in obs_list],
                }
                continue

            effective = real

            # Aggregate status: any "fail" wins, all "pass" -> "pass",
            # otherwise whatever the last-seen status is (typically a
            # profile in "pending"/"checking" state that's newly added).
            statuses = [o.get("status") for o in effective if o.get("status")]
            if not statuses:
                agg_status: str | None = None
                agg_bool: bool | None = None
            elif any(s == "fail" for s in statuses):
                agg_status = "fail"
                agg_bool = False
            elif all(s == "pass" for s in statuses):
                agg_status = "pass"
                agg_bool = True
            else:
                # Mixed or unknown state — surface as None so the
                # sensor goes "unavailable" rather than lying.
                agg_status = statuses[-1]
                agg_bool = None

            # The "winning" profile is the first fail (if any), else the
            # first pass — gives the most useful single-profile summary
            # for attribute display.
            primary: Dict[str, Any] | None = None
            for o in effective:
                if o.get("status") == "fail":
                    primary = o
                    break
            if primary is None:
                for o in effective:
                    if o.get("status") == "pass":
                        primary = o
                        break
            if primary is None and effective:
                primary = effective[0]

            flat: Dict[str, Any] = {
                "status": agg_status,
                "passing": agg_bool,
                "profile": (primary or {}).get("profile"),
                "success_count": (primary or {}).get("success_count"),
                "fail_count": (primary or {}).get("fail_count"),
                "check_hosts": (primary or {}).get("check_hosts") or [],
                "check_addresses": (primary or {}).get("check_addresses") or [],
                "check_port": (primary or {}).get("check_port"),
                "check_mode": (primary or {}).get("check_mode"),
                "update_interval": (primary or {}).get("update_interval"),
                "max_fails": (primary or {}).get("max_fails"),
                "all_profiles": obs_list,
                "ignored_profiles": [
                    o.get("profile") for o in obs_list if o not in effective
                ],
            }
            result[iface_id] = flat

        return result
