"""VPN domain methods for KeeneticClient."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

import aiohttp

from ...const import (
    DOMAIN,
    INTERFACE_CONF_DISABLED,
    LINK_STATE_DOWN,
    LINK_STATE_UP,
)
from ...utils import coerce_byte_count, first_present
from ..errors import KeeneticApiError
from ..helpers import (
    _is_endpoint_missing,
    _normalize_interfaces,
    _validate_cli_arg,
)
from ..parsers.ipsec import (
    merge_crypto_map_config,
    parse_ipsec_statusall,
)

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.vpn")


def _crypto_map_config_entries(data: Any) -> Dict[str, Dict[str, Any]]:
    """Return the per-map dicts of a ``crypto/map`` config payload."""
    if not isinstance(data, dict):
        return {}
    return {name: cfg for name, cfg in data.items() if isinstance(cfg, dict)}


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
    def _parse_ipsec_statusall(cls, text: str) -> Dict[str, Dict[str, Any]]:
        """Parse strongSwan ``ipsec statusall`` text into normalized dicts.

        The data source is stroke (``show/ipsec``) rather than the VICI
        ``show/crypto/map`` path, avoiding the per-call OOM leak in
        ``ndm``'s ``IpSec::Vici::Stats`` handler on KeeneticOS 5.x.
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
        return _crypto_map_config_entries(data)

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

        A transient failure of either read is raised, not merged: half a
        picture publishes every established tunnel as down (or makes it
        vanish) for a whole slow tick, while the coordinator can keep its
        previous snapshot. "Not found" from both reads means the router has
        no IPsec component; that is latched like the other capabilities.
        """
        if self._crypto_map_supported is False:
            return {}
        status_data, config = await asyncio.gather(
            self._rci_get("show/ipsec"),
            self._rci_get("crypto/map"),
            return_exceptions=True,
        )

        if isinstance(status_data, asyncio.CancelledError):
            raise status_data
        if isinstance(config, asyncio.CancelledError):
            raise config

        status_missing = isinstance(status_data, BaseException) and _is_endpoint_missing(status_data)
        config_missing = isinstance(config, BaseException) and _is_endpoint_missing(config)
        if status_missing and config_missing:
            self._crypto_map_supported = False
            _LOGGER.debug("No IPsec component on this router; not polling it again")
            return {}
        for result in (status_data, config):
            if isinstance(result, BaseException) and not _is_endpoint_missing(result):
                raise result
        self._crypto_map_supported = True

        status: Dict[str, Dict[str, Any]] = {}
        if isinstance(status_data, dict):
            text = status_data.get("ipsec_statusall")
            if isinstance(text, str) and text:
                status = self._parse_ipsec_statusall(text)

        return merge_crypto_map_config(
            status,
            {} if isinstance(config, BaseException) else _crypto_map_config_entries(config),
        )


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
