# Refactor plan — `custom_components/keenetic_router_pro/api.py`

**Audience:** an implementer LLM (e.g. Sonnet/Haiku/GPT-mini) executing the steps without re-deriving design. Follow phases in order. Do not skip Phase 0.

**Repo:** https://github.com/ABovsh/Keenetic-Router-Pro
**File under refactor:** `custom_components/keenetic_router_pro/api.py` (3201 LOC, single `KeeneticClient` god-class with ~70 methods)
**Release contract** (mandatory, see `feedback_keenetic_changelog_readme`):
- Every commit: bump `custom_components/keenetic_router_pro/manifest.json` `version`
- Every commit: add `CHANGELOG.md` entry
- README only on user-facing changes (this refactor is internal → no README updates)
- Direct push to `main` (no rc branch for refactor unless explicitly asked)

**Hard invariants** (never violate):
1. `KeeneticClient`'s **public method signatures and return shapes are frozen**. Consumers: `coordinator.py`, `switch.py`, `update.py`, `select.py`, `button.py`, `binary_sensor.py`, `device_tracker.py`, `__init__.py`, `config_flow.py`, `diagnostics.py`, `entity.py`.
2. `from .api import KeeneticClient, KeeneticApiError, KeeneticAuthError, KeeneticConnectionTarget, normalize_connection_target` must keep working (use a shim in `api.py` after the split, OR turn `api.py` into a package `api/__init__.py` re-exporting).
3. RCI command strings, RCI response key names, and parsing of router responses are **behaviour to preserve byte-for-byte**. Do not "clean up" parsing while moving it.
4. Auth flow logic (basic + digest challenge) must be moved verbatim — Keenetic firmware quirks live here.
5. No new runtime dependencies. Stay on `aiohttp` + stdlib.

---

## 0. Inventory (read before touching code)

`api.py` outline (line numbers are at HEAD = commit `bacc4727`; **re-run `grep -nE "^(class |async def |def )|^    (async def |def |@)" api.py` before editing** because lines drift after each phase):

| Lines | Symbol | Category |
|---|---|---|
| 1–28 | module header, imports, `_LOGGER`, constants/regex | helpers |
| 30–62 | `RCI_ROOT`, `_SENSITIVE_NAMES`, `_SENSITIVE_RESPONSE_RE`, `_CLI_TOKEN_RE`, `_DNS_PROXY_STAT_RE`, `_IPSEC_VICI_OOM_RE` | constants |
| 65–72 | `KeeneticApiError`, `KeeneticAuthError` | errors |
| 74–86 | `KeeneticConnectionTarget` dataclass + `base_url` property | target |
| 88–129 | `normalize_connection_target` | target |
| 131–144 | `_validate_cli_arg` | helpers |
| 146–155 | `_response_summary` | helpers |
| 157–171 | `_payload_summary` | helpers |
| 173–176 | `_to_int` | helpers |
| 178–181 | `_truthy` | helpers |
| 183–192 | `_cookie_header_from_response` | helpers |
| 194–202 | `_is_endpoint_missing` | helpers |
| 204–215 | `_dict_items` | helpers |
| 217–234 | `_nested_dict_items` | helpers |
| 236–292 | `KeeneticClient.__init__`, `__repr__`, `__str__` | client core |
| 294–299 | `_basic_auth_headers` | transport |
| 301–307 | `async_start` | transport |
| 309–340 | `_async_authenticate` | auth |
| 342–455 | `_async_authenticate_challenge` | auth |
| 457–473 | `_ensure_auth` | auth |
| 475–536 | `_request` | transport |
| 538–572 | `_handle_response` | transport |
| 574–582 | `_rci_get` | transport |
| 584–593 | `_rci_post` | transport |
| 595–598 | `_rci_parse` | transport |
| 600–619 | `_normalize_interfaces` | helpers (lift) |
| 621–652 | `async_ping_ip` | network |
| 654–677 | `async_ping_multiple` | network |
| 679–682 | `async_get_system_info` | system |
| 684–687 | `async_get_current_version_info` | system |
| 689–692 | `async_get_available_version_info` | system |
| 694–790 | `async_get_port_info` | network |
| 792–795 | `async_get_interfaces` | network |
| 797–816 | `async_get_interface_stat` | network |
| 818–845 | `async_get_clients` | clients |
| 847–875 | `async_get_ip_neighbours` | clients |
| 877–946 | `async_get_wireguard_status` | vpn |
| 948–1134 | `async_get_wifi_networks` | wifi |
| 1136–1141 | `async_set_wifi_enabled` | wifi |
| 1143–1150 | `async_set_wireguard_enabled` | vpn |
| 1152–1162 | `async_set_interface_enabled` | network |
| 1164–1168 | `async_reboot` | system |
| 1170–1239 | `async_get_vpn_tunnels` | vpn |
| 1241–1361 | `async_get_wan_status` (+ nested `_extract_ip`, `_build_result`, `_is_wan_iface`) | wan |
| 1363–1556 | `async_get_wan_interfaces` (+ nested `_is_wan`, `_extract_ip`, `_derive_enabled`, `_derive_internet_access`) | wan |
| 1558–1809 | `async_get_ping_check_status` (+ nested `_is_test_net_only`) | wan |
| 1811–1835 | `_parse_dns_proxy_stat` (`@staticmethod`) | dns (pure) |
| 1837–1938 | `async_get_dns_proxy_status` | dns |
| 1940–1981 | `_extract_parse_messages` (`@staticmethod`, has nested `_walk`) | helpers (lift) |
| 1983–2004 | `_parse_ipsec_vici_diagnostics` (`@classmethod`) | vpn (pure) |
| 2006–2018 | `async_get_ipsec_diagnostics` | vpn |
| 2020–2211 | `async_get_crypto_maps` (+ nested `_clean_addr`, `_clean_str`, `_to_int`, `_as_list`) | vpn |
| 2213–2254 | `async_set_crypto_map_enabled` | vpn |
| 2256–2371 | `async_get_mesh_nodes` | mesh |
| 2373–2409 | `_get_mesh_nodes_from_clients` | mesh |
| 2411–2420 | `async_reboot_mesh_node` | mesh |
| 2422–2504 | `async_get_traffic_stats` | network |
| 2506–2569 | `async_get_all_interface_stats` (+ nested `_bounded_interface_stat`) | network |
| 2571–2627 | `summarize_client_stats` (`@staticmethod`) | clients (pure) |
| 2629–2634 | `async_get_client_stats` | clients |
| 2636–2660 | `async_get_policies` | clients |
| 2662–2689 | `async_get_host_policies` | clients |
| 2691–2722 | `async_set_client_policy` | clients |
| 2724–2726 | `async_block_client` | clients |
| 2728–2730 | `async_unblock_client` | clients |
| 2732–2765 | `async_check_firmware_update` | system |
| 2767–2819 | `async_start_firmware_update` | system |
| 2821–3031 | `async_start_node_firmware_update` | system |
| 3033–3127 | `_authenticate_to_node` | system (private) |
| 3129–3149 | `async_get_update_progress` | system |
| 3151–3201 | `async_get_ndns_info` | system |

Instance state (set in `__init__`) that mixins must access via `self.`:
- `_host`, `_username`, `_password`, `_port`, `_ssl`, `_request_timeout`, `_use_challenge_auth`, `_base`
- `_session`, `_auth_header`, `_authenticated`, `_node_auth_headers`, `_auth_lock`
- Capability caches: `_mws_member_supported`, `_crypto_map_supported`, `_dns_proxy_supported`, `_ping_check_supported`, `_ndns_supported`, `_ipsec_diagnostics_supported`
- `_hotspot_subpath_skip`

Every mixin can rely on these being present because `KeeneticClient.__init__` runs first.

---

## 1. Target layout

Convert `api.py` (file) → `api/` (package). Final structure:

```
custom_components/keenetic_router_pro/api/
  __init__.py          # public re-exports
  errors.py
  target.py
  constants.py         # RCI_ROOT, regex, _SENSITIVE_NAMES
  helpers.py           # module-level _to_int, _truthy, _dict_items, _nested_dict_items,
                       # _validate_cli_arg, _response_summary, _payload_summary,
                       # _cookie_header_from_response, _is_endpoint_missing,
                       # _normalize_interfaces (lifted from class), _extract_parse_messages (lifted)
  transport.py         # _Transport base: __init__ state init, async_start, _basic_auth_headers,
                       # _request, _handle_response, _rci_get, _rci_post, _rci_parse, __repr__
  auth.py              # _AuthMixin: _async_authenticate, _async_authenticate_challenge, _ensure_auth
  client.py            # class KeeneticClient(_AuthMixin, SystemMixin, NetworkMixin, WanMixin,
                       #                       ClientsMixin, WifiMixin, VpnMixin, DnsMixin,
                       #                       MeshMixin, _Transport): pass
  domains/
    __init__.py        # empty
    system.py          # SystemMixin
    network.py         # NetworkMixin (interfaces, ports, ping, traffic, interface stats)
    wan.py             # WanMixin (wan_status, wan_interfaces, ping_check)
    clients.py         # ClientsMixin (clients, neighbours, policies, block, stats summary)
    wifi.py            # WifiMixin
    vpn.py             # VpnMixin (wireguard, vpn_tunnels, ipsec, crypto_maps)
    dns.py             # DnsMixin
    mesh.py            # MeshMixin
```

MRO: list mixins **before** `_Transport` so domain methods resolve, `_AuthMixin` first so any `_ensure_auth` override would dominate, `_Transport` last because it owns `__init__`. Verify with `KeeneticClient.__mro__` in a smoke test.

`api/__init__.py` must contain:
```python
from .client import KeeneticClient
from .errors import KeeneticApiError, KeeneticAuthError
from .target import KeeneticConnectionTarget, normalize_connection_target
__all__ = ["KeeneticClient", "KeeneticApiError", "KeeneticAuthError",
           "KeeneticConnectionTarget", "normalize_connection_target"]
```

Delete the old `api.py` file in the **same commit** that creates the `api/` package — Python will resolve `from .api import …` to the package's `__init__.py`. (Do **not** leave both `api.py` and `api/` — Python will pick one and the other becomes dead.)

---

## 2. Phase 0 — Safety net (tests BEFORE any move)

Create `tests/` at repo root if missing. Add `tests/conftest.py`:
```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
```
(So `custom_components.keenetic_router_pro` is importable without HA.)

Add `tests/unit/test_pure_parsers.py` covering pure functions that take strings/dicts and return dicts/lists (no HTTP):

| Function | Test inputs |
|---|---|
| `KeeneticClient._parse_dns_proxy_stat` | A realistic multi-line `stat` block (capture from a router or craft per regex `_DNS_PROXY_STAT_RE`); empty string; malformed lines |
| `KeeneticClient._parse_ipsec_vici_diagnostics` | Lines containing `IpSec::Vici::Stats: out of memory`, lines containing `out of memory [CODE]`, unrelated lines |
| `KeeneticClient.summarize_client_stats` | List with mix of online/offline/wired/wifi/blocked clients; empty list |
| `KeeneticClient._normalize_interfaces` | dict-form, list-form, garbage form |
| `KeeneticClient._extract_parse_messages` | Nested dict with `message` keys at multiple depths; list inputs; `None` |
| `_to_int`, `_truthy`, `_dict_items`, `_nested_dict_items`, `_validate_cli_arg`, `_response_summary`, `_payload_summary`, `_is_endpoint_missing` | Boundary cases; `_validate_cli_arg` must raise on whitespace/empty/non-matching `_CLI_TOKEN_RE` |

Capture golden outputs by running once against the **current** `api.py`, paste into the test as expected values. The point of Phase 0 is "lock current behaviour", not "test what behaviour should be".

For nested closures inside `async_get_wan_status` / `async_get_wan_interfaces` / `async_get_crypto_maps`, test indirectly: stub `self._rci_get` / `self._rci_post` / `self._rci_parse` on a `KeeneticClient` instance (use `unittest.mock.AsyncMock`) and call the public method with crafted fake RCI responses.

Commit (manifest bump → e.g. `1.x.y+1`, CHANGELOG entry: `Internal: add unit tests for api.py pure parsers (no behaviour change).`).

**Stop and run tests.** Do not proceed to Phase 1 until tests pass.

---

## 3. Phase 1 — Extract helpers, errors, target, constants

Goal: zero functional change. Move-only.

1. Create `api/` as a directory next to `api.py`.
2. Create `api/errors.py` containing only `KeeneticApiError`, `KeeneticAuthError` (verbatim from lines 65–72).
3. Create `api/target.py` containing `KeeneticConnectionTarget` dataclass + `normalize_connection_target` (74–129). Imports it needs: `from dataclasses import dataclass`, `from typing import Any`, `from urllib.parse import urlparse`, `from .errors import KeeneticApiError`.
4. Create `api/constants.py` with `RCI_ROOT`, `_SENSITIVE_NAMES`, `_SENSITIVE_RESPONSE_RE`, `_CLI_TOKEN_RE`, `_DNS_PROXY_STAT_RE`, `_IPSEC_VICI_OOM_RE` (verbatim from lines 30–62).
5. Create `api/helpers.py` with all module-level `_*` helpers from lines 131–234 verbatim. Required imports inside `helpers.py`: `import aiohttp`, `from typing import Any, Dict, List`, `from .constants import _SENSITIVE_RESPONSE_RE, _CLI_TOKEN_RE`. Also lift `KeeneticClient._normalize_interfaces` (600–619) and `KeeneticClient._extract_parse_messages` (1940–1981) to **module-level** functions named `_normalize_interfaces(raw)` and `_extract_parse_messages(data)` (drop `self`/`@staticmethod`). They never use `self`.
6. In the (still-monolithic) `api.py`:
   - Delete the moved blocks.
   - At top: `from .api.errors import KeeneticApiError, KeeneticAuthError` ❌ — circular. Instead: **rename old `api.py` → temporarily keep as monolith but import from new submodules.** Concretely: keep `api.py` as a sibling of `api/` is impossible. So do this differently:
   
   **Correct order for Phase 1:**
   - a. Create `api/` package with `errors.py`, `target.py`, `constants.py`, `helpers.py` populated as above.
   - b. Create `api/_legacy.py` and **move the entire current `api.py` content into it** minus the symbols already in errors/target/constants/helpers. Replace duplicated definitions with `from .errors import ...`, `from .target import ...`, `from .constants import ...`, `from .helpers import ...`.
   - c. Replace `KeeneticClient._normalize_interfaces` calls with module-level `from .helpers import _normalize_interfaces` calls (`self._normalize_interfaces(x)` → `_normalize_interfaces(x)`). Same for `_extract_parse_messages`.
   - d. Create `api/__init__.py`:
     ```python
     from ._legacy import KeeneticClient
     from .errors import KeeneticApiError, KeeneticAuthError
     from .target import KeeneticConnectionTarget, normalize_connection_target
     __all__ = [...]
     ```
   - e. **Delete the original `api.py` file.** Now `from .api import KeeneticClient` resolves to the package.
7. Run tests from Phase 0 — must pass unchanged.
8. Smoke load: `python -c "from custom_components.keenetic_router_pro.api import KeeneticClient; print(KeeneticClient.__mro__)"`.
9. Commit: bump manifest, CHANGELOG: `Internal: convert api.py to api/ package; extract helpers/errors/target/constants (no behaviour change).`

---

## 4. Phase 2 — Transport + Auth split

1. Create `api/transport.py` with class `_Transport`:
   - Move `__init__` (238–282), `__repr__` (284–290), `__str__ = __repr__` (292), `_basic_auth_headers` (294–299), `async_start` (301–307), `_request` (475–536), `_handle_response` (538–572), `_rci_get` (574–582), `_rci_post` (584–593), `_rci_parse` (595–598).
   - Imports: `aiohttp`, `asyncio`, `base64`, `logging`, `typing`, `from .constants import RCI_ROOT, _SENSITIVE_NAMES`, `from .errors import KeeneticApiError, KeeneticAuthError`, `from .helpers import _payload_summary, _response_summary, _cookie_header_from_response, _is_endpoint_missing`, `from .target import normalize_connection_target`.
   - `_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.transport")` (import `DOMAIN` from `..const`).
2. Create `api/auth.py` with `class _AuthMixin:` containing `_async_authenticate` (309–340), `_async_authenticate_challenge` (342–455), `_ensure_auth` (457–473). Imports: `asyncio`, `base64`, `hashlib`, `logging`, `from .errors import KeeneticAuthError`, `from .constants import RCI_ROOT`. **No** `__init__` — relies on `_Transport.__init__`.
3. Edit `api/_legacy.py`: change `class KeeneticClient:` → `class KeeneticClient(_AuthMixin, _Transport):` with `from .transport import _Transport; from .auth import _AuthMixin`. Delete the moved methods from `_legacy.py`. `_legacy.py` now contains only the ~60 domain methods (still one file, ~2400 LOC).
4. Run tests + smoke load. Verify `KeeneticClient(...)` still constructs with the same args (`host, username, password, port, ssl, request_timeout, use_challenge_auth`).
5. Commit: manifest bump, CHANGELOG: `Internal: extract transport + auth from KeeneticClient (no behaviour change).`

---

## 5. Phase 3 — Domain split

For each domain, repeat the recipe:

**Recipe per domain mixin:**
1. Create `api/domains/<name>.py` with:
   ```python
   """<Domain> domain methods for KeeneticClient."""
   from __future__ import annotations
   import logging
   from typing import Any, Dict, List
   # any specific imports from .const, .utils, ..constants, ..errors, ..helpers
   from ..errors import KeeneticApiError
   from ...const import DOMAIN
   _LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.api.<name>")
   
   class <Name>Mixin:
       <move methods here verbatim, preserving signatures and bodies>
   ```
2. Cut the methods from `api/_legacy.py` and paste into the new mixin. Preserve all comments and nested closures.
3. If a nested closure duplicates a util that already lives in `helpers.py` (e.g. `_to_int`, `_clean_str` patterns), **do not deduplicate in this phase** — keep verbatim. Dedup is Phase 5.
4. Add `<Name>Mixin` to `KeeneticClient` bases in `_legacy.py` (or by now: rename `_legacy.py` → `client.py`):
   ```python
   class KeeneticClient(_AuthMixin, SystemMixin, NetworkMixin, WanMixin,
                        ClientsMixin, WifiMixin, VpnMixin, DnsMixin,
                        MeshMixin, _Transport):
       pass
   ```
5. Run tests + smoke load after **each** sub-phase commit. Each commit = manifest bump + CHANGELOG entry: `Internal: extract <domain> mixin from api client (no behaviour change).`

**Order** (each = own commit):

| Sub-phase | Mixin | Methods to move (current line ranges) | Notes |
|---|---|---|---|
| 3a | `SystemMixin` | `async_get_system_info` (679–682), `async_get_current_version_info` (684–687), `async_get_available_version_info` (689–692), `async_reboot` (1164–1168), `async_check_firmware_update` (2732–2765), `async_start_firmware_update` (2767–2819), `async_start_node_firmware_update` (2821–3031), `_authenticate_to_node` (3033–3127), `async_get_update_progress` (3129–3149), `async_get_ndns_info` (3151–3201) | Largest single chunk. `_authenticate_to_node` writes to `self._node_auth_headers`; that attr already lives on `_Transport.__init__`. |
| 3b | `ClientsMixin` | `async_get_clients` (818–845), `async_get_ip_neighbours` (847–875), `summarize_client_stats` (2571–2627, `@staticmethod`), `async_get_client_stats` (2629–2634), `async_get_policies` (2636–2660), `async_get_host_policies` (2662–2689), `async_set_client_policy` (2691–2722), `async_block_client` (2724–2726), `async_unblock_client` (2728–2730) | Keep `@staticmethod` on `summarize_client_stats`. |
| 3c | `WifiMixin` | `async_get_wifi_networks` (948–1134), `async_set_wifi_enabled` (1136–1141) | |
| 3d | `VpnMixin` | `async_get_wireguard_status` (877–946), `async_set_wireguard_enabled` (1143–1150), `async_get_vpn_tunnels` (1170–1239), `_parse_ipsec_vici_diagnostics` (1983–2004, `@classmethod`), `async_get_ipsec_diagnostics` (2006–2018), `async_get_crypto_maps` (2020–2211), `async_set_crypto_map_enabled` (2213–2254) | Biggest parser surface; do alone. |
| 3e | `NetworkMixin` | `async_ping_ip` (621–652), `async_ping_multiple` (654–677), `async_get_port_info` (694–790), `async_get_interfaces` (792–795), `async_get_interface_stat` (797–816), `async_set_interface_enabled` (1152–1162), `async_get_traffic_stats` (2422–2504), `async_get_all_interface_stats` (2506–2569) | |
| 3f | `WanMixin` | `async_get_wan_status` (1241–1361), `async_get_wan_interfaces` (1363–1556), `async_get_ping_check_status` (1558–1809) | Heavy nested closures — paste verbatim. |
| 3g | `DnsMixin` | `_parse_dns_proxy_stat` (1811–1835, `@staticmethod`), `async_get_dns_proxy_status` (1837–1938) | |
| 3h | `MeshMixin` | `async_get_mesh_nodes` (2256–2371), `_get_mesh_nodes_from_clients` (2373–2409), `async_reboot_mesh_node` (2411–2420) | |

After 3h, `api/_legacy.py` (or `client.py`) should contain only the `KeeneticClient` class declaration with bases and no body methods of its own (only `pass`). Rename `_legacy.py` → `client.py` in the final sub-phase commit; update `api/__init__.py` import.

After **every** sub-phase: tests + smoke. If a test fails, the move was not verbatim — diff old vs new and fix; do not "improve" code.

---

## 6. Phase 4 — Pure-function tests for moved parsers

Now that mixins exist, expand `tests/unit/`:
- `test_dns.py` — full `_parse_dns_proxy_stat` coverage
- `test_vpn.py` — `_parse_ipsec_vici_diagnostics`, crypto_map parsing via mocked `_rci_get`
- `test_wan.py` — `async_get_wan_status`/`async_get_wan_interfaces` derivation logic with mocked RCI fixtures
- `test_clients.py` — `summarize_client_stats` exhaustive

Capture fixtures by stubbing `self._rci_get = AsyncMock(return_value=<dict>)` then asserting the public method output. Use fixtures already gathered in Phase 0; add edge cases.

Commit: manifest bump, CHANGELOG: `Internal: expand unit test coverage for parsers (no behaviour change).`

---

## 7. Phase 5 — Opportunistic dedup (only after green tests)

Only after Phases 0–4 are green:

1. The nested helpers in `async_get_crypto_maps` (`_clean_addr` 2105, `_clean_str` 2114, `_to_int` 2120, `_as_list` 2126) — `_to_int` shadows the module-level one. Promote `_clean_addr`, `_clean_str`, `_as_list` to `helpers.py`; delete the shadowing inner `_to_int` (use module-level via `from ..helpers import _to_int`).
2. Nested `_extract_ip` appears in both `async_get_wan_status` (1261) and `async_get_wan_interfaces` (1439). Verify identical, then lift to `helpers.py` as `_extract_iface_ip` and import from both.
3. Nested `_is_wan_iface` / `_is_wan` — verify identical, then lift.

Each dedup = its own commit + manifest bump + CHANGELOG + tests must stay green.

**Do not** in this pass:
- Convert response dicts to TypedDicts / dataclasses
- Rewrite the auth challenge logic
- Change any RCI command string
- Add new abstractions (no `RequestBuilder`, no `ResponseParser` base class)

---

## 8. Per-commit checklist (the model MUST run these for every commit)

1. `python -m pytest tests/ -q` → all green
2. `python -c "from custom_components.keenetic_router_pro.api import KeeneticClient, KeeneticApiError, KeeneticAuthError, KeeneticConnectionTarget, normalize_connection_target; print(KeeneticClient.__mro__)"` → no ImportError, MRO sane
3. `grep -rn "from .api import\|from custom_components.keenetic_router_pro.api import" custom_components/keenetic_router_pro/ | grep -v "api/"` — verify no consumer broke
4. Bump `custom_components/keenetic_router_pro/manifest.json` `version` (semver patch)
5. Add a `CHANGELOG.md` entry under a new version heading
6. `git add -A && git commit -m "<phase>: <summary>"` and `git push origin main`

If any step fails: **do not push**. Revert the working tree, diff against last good commit, and identify whether the move was verbatim.

---

## 9. Out of scope (do NOT do)

- Renaming any public method on `KeeneticClient`
- Changing default values of `__init__` params
- Touching `coordinator.py`, `switch.py`, `update.py`, etc. (consumers must not change)
- Adding `pydantic`, `attrs`, `httpx`, or any new dependency
- Rewriting parsers "more cleanly"
- Splitting into multiple PyPI packages
- Changing logger names (consumers may grep logs)

---

## 10. Estimated effort

- Phase 0: ~3 h (fixture capture is the work)
- Phase 1: ~1 h
- Phase 2: ~1 h
- Phase 3 (a–h): ~30 min each ≈ 4 h
- Phase 4: ~2 h
- Phase 5: ~1 h

Total ≈ 12 h split across 12–14 commits, each independently revertable.
