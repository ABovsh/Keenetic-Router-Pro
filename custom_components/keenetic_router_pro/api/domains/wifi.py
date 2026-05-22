"""Wi-Fi domain methods for KeeneticClient."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ...const import DOMAIN, LINK_STATE_UP
from ..helpers import _normalize_interfaces, _validate_cli_arg

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.wifi")


class WifiMixin:
    async def async_get_wifi_networks(
        self,
        interfaces: Dict[str, Any] | None = None,
        iface_list: List[Dict[str, Any]] | None = None,
    ) -> List[Dict[str, Any]]:


        if iface_list is None:
            if interfaces is None:
                interfaces = await self.async_get_interfaces()
            iface_list = _normalize_interfaces(interfaces)

        bridge_labels: Dict[str, str] = {}
        for item in iface_list:
            itype = (item.get("type") or "").lower()
            if itype != "bridge":
                continue

            bid = item.get("id") or item.get("interface-name")
            if not bid:
                continue

            label = (
                item.get("interface-name")
                or item.get("description")
                or bid
            )
            bridge_labels[str(bid)] = str(label)

        ap_items: List[Dict[str, Any]] = []
        for item in iface_list:
            raw_id = (
                item.get("id")
                or item.get("interface-name")
                or item.get("name")
                or item.get("ifname")
            )
            if not raw_id:
                continue
            raw_id = str(raw_id)

            itype = (item.get("type") or "").lower()
            traits_raw = item.get("traits") or []
            if not isinstance(traits_raw, list):
                traits_raw = []
            traits = [t.lower() for t in traits_raw if isinstance(t, str)]
            id_lower = raw_id.lower()

            is_ap = (
                "accesspoint" in id_lower
                or itype == "accesspoint"
                or ("wifi" in "".join(traits) and "accesspoint" in "".join(traits))
            )
            if not is_ap:
                continue

            ssid = (item.get("ssid") or "").strip()
            group = str(item.get("group") or "").strip()
            if not ssid and not group:
                continue

            clone = dict(item)
            clone["__id"] = raw_id
            ap_items.append(clone)

        groups: Dict[str, Dict[str, Any]] = {}
        for item in ap_items:
            raw_id = item["__id"]
            ssid = (item.get("ssid") or "").strip()
            group = str(item.get("group") or "").strip()
            base_id = raw_id.split("/")[0]

            group_key = group or ssid or base_id

            g = groups.setdefault(
                group_key,
                {
                    "ssid": "",
                    "group": group,
                    "aps": [],
                },
            )

            # A real broadcast SSID from any AP in the group always wins.
            # This matters because Keenetic omits the `ssid` field on
            # disabled APs: on dual-band networks the 2.4 GHz AP may come
            # first with no SSID, and if we let a bridge-label fallback
            # latch in here, we would never pick up the real SSID from
            # the 5 GHz AP that arrives later.
            if ssid:
                g["ssid"] = ssid

            g["aps"].append(item)

        # Second pass: any group that still has no real SSID (e.g. every
        # AP in the group is disabled and the firmware stripped the field
        # from all of them) falls back to the bridge label or group id,
        # so the entry at least has *some* logical name for display.
        for g in groups.values():
            if g["ssid"]:
                continue
            grp = g["group"]
            if grp and grp in bridge_labels:
                g["ssid"] = bridge_labels[grp]
            elif grp:
                g["ssid"] = grp

        wifi_networks: List[Dict[str, Any]] = []

        for g in groups.values():
            logical_name = (g["ssid"] or "").strip()
            group = g["group"]

            if not logical_name:
                if group and group in bridge_labels:
                    logical_name = bridge_labels[group]
                elif group:
                    logical_name = group
                else:
                    logical_name = "Wi-Fi"

            per_band: Dict[str, Dict[str, Any]] = {}

            for ap in g["aps"]:
                raw_id = ap["__id"]
                band = str(ap.get("band") or "").strip()

                if not band:
                    base_id = raw_id.split("/")[0].lower()
                    chan = str(ap.get("channel") or "")
                    if "wifimaster0" in base_id:
                        band = "2.4"
                    elif "wifimaster1" in base_id:
                        band = "5"
                    elif chan:
                        try:
                            ch = int(chan)
                            band = "2.4" if 1 <= ch <= 14 else "5"
                        except ValueError:
                            pass

                if band:
                    b_lower = band.lower()
                    if "2.4" in b_lower or b_lower == "2":
                        band_label = "2.4 GHz"
                    elif "5" in b_lower:
                        band_label = "5 GHz"
                    else:
                        band_label = band
                else:
                    band_label = ""

                key = band_label or "default"
                if key in per_band:
                    continue
                per_band[key] = ap

            for band_label, ap in per_band.items():
                raw_id = ap["__id"]
                state = str(ap.get("state", "")).lower()
                enabled = state == LINK_STATE_UP

                vis_name = logical_name
                if band_label:
                    vis_name = f"{logical_name} {band_label}"

                net: Dict[str, Any] = {
                    "id": raw_id,          
                    "name": vis_name,      
                    "ssid": logical_name,
                    "band": band_label,
                    "enabled": enabled,
                    "state": ap.get("state"),
                    "group": group or None,
                    "channel": ap.get("channel"),
                    "tx_power": ap.get("tx-power") or ap.get("tx_power"),
                }

                for k in list(net.keys()):
                    if any(
                        pat in k.lower()
                        for pat in ("password", "pass", "psk", "wpa", "key", "secret")
                    ):
                        net.pop(k, None)

                wifi_networks.append(net)

        return wifi_networks




    async def async_set_wifi_enabled(self, interface_name: str, enabled: bool) -> None:
        """Enable or disable a Wi-Fi interface via RCI parse."""
        interface_name = _validate_cli_arg(interface_name, "interface name")
        cmd = f"interface {interface_name} {'up' if enabled else 'down'}"
        _LOGGER.debug("Set Wi-Fi %s enabled=%s via: %s", interface_name, enabled, cmd)
        await self._rci_parse(cmd)
