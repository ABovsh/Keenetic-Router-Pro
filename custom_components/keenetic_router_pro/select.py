"""Select entities for Keenetic Router Pro (client connection policy)."""
from __future__ import annotations
from typing import Any
from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .api import KeeneticClient
from .coordinator import KeeneticCoordinator
from .entity import ClientEntity
from .utils import iter_tracked_clients

DEFAULT_POLICY_OPTION = "Default"
DENY_POLICY_OPTION = "Deny (Blocked)"


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Keenetic Router Pro select entities from a config entry."""
    runtime = entry.runtime_data
    coordinator: KeeneticCoordinator = runtime.coordinator
    client: KeeneticClient = runtime.client
    entities: list[SelectEntity] = []

    policies = coordinator.data.get("policies", {})
    if not isinstance(policies, dict):
        policies = {}

    for mac, name, initial_ip in iter_tracked_clients(entry):
        entities.append(
            KeeneticClientPolicySelect(
                coordinator=coordinator,
                entry=entry,
                api_client=client,
                mac=mac,
                label=name,
                initial_ip=initial_ip,
                policies=policies,
            )
        )

    if entities:
        async_add_entities(entities)


class KeeneticClientPolicySelect(ClientEntity, SelectEntity):
    """Select entity for client connection policy, attached to client device."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:shield-account"

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        api_client: KeeneticClient,
        mac: str,
        label: str,
        initial_ip: str | None,
        policies: dict[str, str],
    ) -> None:
        """Initialize the policy select entity."""
        ClientEntity.__init__(
            self,
            coordinator=coordinator,
            entry_id=entry.entry_id,
            title=entry.title,
            mac=mac,
            label=label,
            initial_ip=initial_ip,
        )
        self._api_client = api_client
        self._id_to_display: dict[str, str] = {}
        self._display_to_id: dict[str, str] = {}
        self._policies: dict[str, str] = {}
        self._set_policies(policies)

    def _set_policies(self, policies: dict[str, str]) -> None:
        """Rebuild policy display mappings from a coordinator snapshot."""
        self._policies = dict(policies)
        self._id_to_display = {}
        self._display_to_id = {}
        self._id_to_display["__default__"] = DEFAULT_POLICY_OPTION
        self._display_to_id[DEFAULT_POLICY_OPTION] = "__default__"
        self._id_to_display["__deny__"] = DENY_POLICY_OPTION
        self._display_to_id[DENY_POLICY_OPTION] = "__deny__"

        used_labels: set[str] = {DEFAULT_POLICY_OPTION, DENY_POLICY_OPTION}
        for policy_id, description in self._policies.items():
            label = str(description or policy_id)
            # Disambiguate policies that share a description so each dropdown
            # label maps to exactly one policy id (no duplicate entries, and
            # selecting a label can't silently apply the wrong policy).
            if label in used_labels:
                label = f"{label} ({policy_id})"
            used_labels.add(label)
            self._id_to_display[policy_id] = label
            self._display_to_id[label] = policy_id

    @property
    def _fingerprint_source(self) -> dict[str, Any] | None:
        """Include policy state in the change-detection fingerprint.

        The base ClientEntity fingerprint covers only the hotspot client
        row, but this entity's state comes from ``host_policies`` /
        ``policies`` — without these keys a policy-only change would be
        suppressed as a no-op and the dropdown would keep the stale value.
        """
        client = self._client
        if client is None:
            return None
        data = self.coordinator.data or {}
        host_policies = data.get("host_policies")
        host_info = (
            host_policies.get(self._mac)
            if isinstance(host_policies, dict)
            else None
        )
        policies = data.get("policies")
        # Copy the nested dicts so the stored fingerprint is a stable
        # snapshot, not an alias of the live coordinator data (an in-place
        # mutation of an aliased dict would compare equal to itself and
        # suppress the state write).
        return {
            **client,
            "_host_policy": dict(host_info) if isinstance(host_info, dict) else host_info,
            "_policies": dict(policies) if isinstance(policies, dict) else policies,
        }

    def _sync_policies(self) -> None:
        """Apply policy changes published by the coordinator."""
        policies = self.coordinator.data.get("policies")
        if isinstance(policies, dict) and policies != self._policies:
            self._set_policies(policies)

    @property
    def unique_id(self) -> str:
        """Return unique ID for entity."""
        return f"{self._entry_id}_client_{self._mac}_policy"

    @property
    def name(self) -> str:
        """Return name of the entity."""
        return "Connection Policy"

    @property
    def options(self) -> list[str]:
        """Return list of available options."""
        self._sync_policies()
        policy_names = sorted(
            self._id_to_display[policy_id] for policy_id in self._policies
        )
        return [DEFAULT_POLICY_OPTION] + policy_names + [DENY_POLICY_OPTION]

    @property
    def current_option(self) -> str | None:
        """Return current selected policy."""
        self._sync_policies()
        host_policies = self.coordinator.data.get("host_policies", {})

        host_info = host_policies.get(self._mac, {})
        access = host_info.get("access")
        policy_id = host_info.get("policy")

        if access == "deny":
            return DENY_POLICY_OPTION

        if policy_id and policy_id in self._id_to_display:
            return self._id_to_display[policy_id]

        return DEFAULT_POLICY_OPTION

    async def async_select_option(self, option: str) -> None:
        """Change the selected policy."""
        if option == DEFAULT_POLICY_OPTION:
            await self._api_client.async_set_client_policy(self._mac, "default")
            self._apply_optimistic_policy(policy_id=None, access="permit")
        elif option == DENY_POLICY_OPTION:
            await self._api_client.async_set_client_policy(self._mac, "deny")
            self._apply_optimistic_policy(policy_id=None, access="deny")
        else:
            policy_id = self._display_to_id.get(option)
            if not policy_id or policy_id in ("__default__", "__deny__"):
                # Option list was rebuilt between validation and dispatch
                # (coordinator refresh mid-service-call). Fail loudly instead
                # of silently reverting the dropdown.
                raise HomeAssistantError(
                    f"Connection policy option is no longer available: {option}"
                )
            await self._api_client.async_set_client_policy(self._mac, policy_id)
            self._apply_optimistic_policy(policy_id=policy_id, access="permit")

        # host_policies lives in the slow fetch tier (~3 min); without this
        # the confirming refresh below would republish the stale cached
        # snapshot and the dropdown would snap back to the old value.
        self.coordinator.request_host_policies_refresh()
        await self.coordinator.async_request_refresh()

    def _apply_optimistic_policy(self, policy_id: str | None, access: str) -> None:
        """Publish the router-accepted policy into coordinator data now.

        Fast coordinator ticks carry ``host_policies`` forward from the
        previous snapshot, so this patched value survives until the next
        real fetch confirms (or corrects) it.
        """
        data = self.coordinator.data
        if not isinstance(data, dict):
            return
        host_policies = dict(data.get("host_policies") or {})
        host_info = dict(host_policies.get(self._mac) or {})
        host_info["policy"] = policy_id
        host_info["access"] = access
        host_policies[self._mac] = host_info
        data["host_policies"] = host_policies
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes."""
        self._sync_policies()
        host_policies = self.coordinator.data.get("host_policies", {})
        host_info = host_policies.get(self._mac, {})

        current_policy_id = host_info.get("policy")
        current_policy_desc = None
        if current_policy_id and current_policy_id in self._id_to_display:
            current_policy_desc = self._id_to_display[current_policy_id]

        return {
            "mac": self._mac.upper(),
            "client_name": self.hostname or self._label,
            "policy_id": current_policy_id,
            "policy_description": current_policy_desc,
            "access": host_info.get("access"),
            "available_policies": list(self._policies.values()),
            "is_registered": host_info.get("registered", False),
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available
