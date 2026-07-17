# Changelog

All notable changes to this integration are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Entries are written for end users (HACS installs); each release is grouped by
what you actually notice on your dashboard. For per-commit detail, see the
git log.

## 1.7.73

### Security

- The "Use SSL" toggle in the setup and reconfigure dialogs now explains
  that disabling it sends credentials over plaintext HTTP, so the choice
  is informed at setup time (previously only a repair issue warned about
  it after the fact).

## 1.7.72

### Improvements

- Router capability probes (composite batching, hotspot paths, optional
  IPsec/DNS/NDNS endpoints) are re-tested automatically after a firmware
  update instead of staying disabled until Home Assistant restarts.
- Polling slows to 60s/120s while the router is confirmed unreachable and
  returns to the normal 30s interval on the first successful update.
- Entity actions that write to the router (switches, selects, buttons,
  firmware update) are now serialized to reduce load on the router CPU.
- Improved internal code structure and test coverage; no user-visible
  behavior changed.

## 1.7.71

### Improvements

- Per-interface traffic statistics are fetched in a single composite RCI
  request when the router supports it, instead of one request per interface.
- The client list is served from the per-tick composite prefetch, removing
  one HTTP request per update cycle.

### Bug fixes

- The automatic retry after an expired auth session now gets its own timeout
  window; a slow first response no longer makes the retry fail.

## 1.7.70

### Improvements

- Improved internal code-quality checks; no user-visible behavior changed.

## 1.7.69

### Bug fixes

- The "Keenetic Release Notes" link on firmware update entities pointed at the retired help.keenetic.com portal, which now redirects every article to the generic support homepage. The link now resolves to the exact per-model changelog on the current support site (e.g. `support.keenetic.ua/titan/kn-1812/en/…-latest-main-release.html`), matching the link in the router's own web UI. Works for the main router and mesh nodes, follows the firmware channel (main/preview/LTS), prefers the router's regional support site, and falls back to the support homepage if the model page can't be resolved.

## 1.7.68

### Improvements

- Raised the per-request HTTP timeout from 15s to 25s. On routers with a large ACL/client list, the `interfaces` call could occasionally exceed 15s even though the router was healthy, producing "Transient critical router fetch failure" log warnings that then self-resolved. The longer timeout still leaves headroom before the next 30s poll.

## 1.7.67

### Improvements

- Improved internal code-quality checks; no user-visible behavior changed.

## 1.7.66

### 🐛 Fixed

- Changing a client's Connection Policy no longer snaps back to the previous value in the UI. The dropdown now updates immediately after the router accepts the change, and the confirming re-read happens on the next poll instead of waiting up to ~3 minutes for the slow refresh tier.
- A policy change is no longer missed by the change-detection filter when nothing else about the client changed.
- A transient failure while reading host policies keeps the last known values instead of briefly showing "Default" for every client.

## 1.7.65

### Improvements

- CI supply-chain hardening: all GitHub Actions pinned to full commit SHAs, checkout steps no longer persist the repo token on the runner, Dependabot keeps action pins updated (with a 7-day release cooldown), and new CodeQL + zizmor scans report to the Security tab. No functional changes to the integration.

## 1.7.64

### 🐛 Fixed

- Fixed a crash that occurred when the router was temporarily unreachable via KeenDNS (SSL certificate error). WiFi temperature sensors would throw `AttributeError: 'list' object has no attribute 'items'` because the interfaces fallback value was incorrectly converted to a list of key names instead of preserving the previous data dict.

## 1.7.63

### Improvements

- Reduced router polling from 10s to 30s to cut Home Assistant database write load.

## 1.7.62

### 🐛 Fixed

- A single dropped router request (such as a brief `system info` timeout) no
  longer marks every Keenetic entity unavailable for that update. The last
  known values are kept for a few cycles; a genuine, sustained outage still
  shows the integration as unavailable.

## 1.7.61

Fourth deep-audit round: privacy in diagnostics, steadier counters, and
sign-in/data hardening.

### 🔒 Privacy

- Downloaded diagnostics no longer expose new-client MAC addresses, mesh-node
  MAC identifiers, or WAN/VPN addresses, peer endpoints, and KeenDNS/domain
  names. These now join the existing redaction of credentials, IPs, and SSIDs.

### 🐛 Fixed

- **Multi-peer WireGuard interfaces report total traffic** even when the router
  sends per-peer byte counters as text.
- **A backup/VPN uplink whose role is reported as a list is recognised**
  correctly, so the active connection is no longer shown as down.
- A WAN "global" flag sent as text ("false"/"no") is no longer read as true.
- Malformed or absurdly large router values (uptime, CPU/memory, signal,
  byte counters, mesh client counts) can no longer disrupt a sensor update,
  fabricate a false counter reset in long-term statistics, or publish a
  negative client count.
- **Sign-in to challenge-auth routers that accept the request but stall
  mid-reply** now surfaces as a retryable connection error instead of failing
  setup with an unclassified error.
- The device tracker refreshes its presence-source and link attributes when the
  router changes how it proves a device is home.

## 1.7.60

### 🐛 Fixed

- **Per-WAN "Uptime" sensors now keep counting instead of freezing.** A WAN
  uplink's uptime was being captured only when the link last went up/down and
  then held at that value for hours — so a connection that had been up all day
  could still read a few seconds, or simply stop updating. Uptime (and the
  WAN's IP) now refresh on every poll while the link is stable.

## 1.7.59

### 🐛 Fixed

- **A WAN whose cable is up but has no internet (provider-side outage) now
  shows as "not connected" instead of going unavailable.** Previously, when the
  link was up but the ISP handed out no address, the per-WAN "Connected" sensor
  went unavailable, so dashboards and other displays rendered the uplink as if
  it were disabled — hiding a real outage. It now reports off (no internet),
  matching the router's red "NO INTERNET ACCESS" badge, while the "Enabled"
  sensor still shows the interface is on.

## 1.7.58

### 🐛 Fixed

- **Per-client "Wi-Fi Session" sensors no longer spam the log with "state is
  not strictly increasing" warnings.** Session length is now reported as an
  instantaneous gauge, which also stops nonsense monotonic totals in long-term
  statistics. Router/PPPoE/WireGuard/mesh uptimes are unchanged.

### 🔧 Internal

- Updated a device-tracker import ahead of a Home Assistant 2027.6 removal; no
  behaviour change.

## 1.7.57

Third deep-audit round: outage behaviour, sign-in robustness and data
integrity.

### 🐛 Fixed

- **A rebooting router can no longer flip the whole dashboard to
  empty/zero values.** A reply that came back technically successful but
  with an empty or garbled core payload used to publish a "ghost" update
  (every WAN/Wi-Fi/port entity empty-but-available); it now counts as a
  failed poll, and a momentary glitch in one data family (Wi-Fi, WireGuard,
  VPN, ports, traffic) keeps the previous snapshot instead of knocking
  those entities out for a tick.
- **Adding an already-configured router now says so** instead of failing
  with "Unexpected error".
- **The options dialog opens even for a damaged or legacy entry** that is
  missing a stored connection field, falling back to the saved client list.
- **Sign-in to the router can no longer stall indefinitely** when the
  router accepts the connection but freezes mid-response.
- **Router identity is validated** before it is derived from an interface
  MAC, so placeholder values like "unknown" or a malformed zero MAC can't
  create unstable duplicate entries.
- **Multi-peer WireGuard interfaces now report total traffic** across all
  peers instead of the first peer only.
- **A backup WAN reported with a text "no" default-gateway flag** is no
  longer mistaken for the default connection.
- **A brief IPsec status glitch no longer drops tunnel entities** for one
  update cycle.
- A firmware update "available" indicator now respects the router's
  explicit "no update available" verdict, matching the Update entity.
- Several malformed-payload crashes were hardened: a non-text device
  vendor record or mesh-node address, a list-shaped client interface
  field, numeric mesh port labels, out-of-range CPU percentages, and
  oversized counters can no longer break sensors or fabricate spikes.
- New Wi-Fi networks created on the router now appear in Home Assistant
  without a reload, like WAN and VPN switches already did.

### 🔒 Privacy

- Connection error messages no longer embed the router address from
  low-level network errors.

## 1.7.56

Second deep-audit round: steadier counters, sign-in resilience, less personal
data in logs and diagnostics.

### 🐛 Fixed

- **Sign-in now works on routers that send several cookies** (e.g. a CSRF
  cookie before the session cookie) — previously only the first cookie was
  kept and authentication could loop endlessly. A sign-in that returns no
  session cookie at all is now treated as a connection problem instead of
  silently looping.
- **A garbled traffic counter can no longer fabricate a huge throughput
  spike** after the next good reading — malformed samples are skipped instead
  of being read as zero.
- **Ping-check based internet status now works with a single ping-check
  profile** (the most common setup) — it was silently dropped on firmwares
  that report one profile instead of a list.
- **WAN addresses nested as objects are recognised**, so a connected WAN is no
  longer shown as disconnected on that firmware shape; unassigned IPv6
  placeholder addresses no longer count as "online".
- **Rejected router commands are detected more reliably** ("No such command",
  "Bad parameter", "Already exists" now count as errors instead of silent
  success).
- A mesh node or update server that accepts a connection but then stalls can
  no longer hang a firmware update indefinitely.
- The OOM-events counter ignores future-dated log lines after a clock
  correction, and its "recent events" diagnostic now shows the newest lines
  instead of the oldest.
- A device connected through a mesh node is no longer occasionally shown
  offline while roaming (duplicate records now prefer the online one).
- A mesh port that disappears from the node now shows **unavailable** instead
  of a phantom "not_found" state; per-WAN session uptime now records
  long-term statistics like the other uptime sensors.
- Boolean garbage from the firmware can no longer appear as tiny CPU /
  memory / temperature readings, and an absurdly large "last seen" value can
  no longer crash the client sensors.
- Selecting a connection policy that has just disappeared from the router now
  shows an error instead of silently reverting the dropdown.

### 🔒 Privacy

- Device and mesh-node hostnames are now redacted from the downloadable
  diagnostics file, the new-device log line, and firmware-update error
  messages shown in the interface.

## 1.7.55

Deep reliability and privacy audit.

### 🐛 Fixed

- Transient client and traffic fetch failures no longer publish false away
  states, zero WAN counters, or recovery throughput spikes.
- Router-rejected control commands and firmware update starts now surface as
  errors instead of false success.
- WireGuard traffic and uptime sensors and newly appearing main-router ports
  now appear dynamically; removed profiles and ports become unavailable.
- Client policy choices refresh from the router and survive transient failures.
- Temporary composite-RCI failures no longer disable efficient batching for
  the rest of the session.
- Connection targets containing embedded URL credentials are rejected.

### 🔒 Privacy

- Diagnostics and router/mesh update logs no longer expose router hosts, node
  names, IPs, or CIDs/MACs.

### 🧹 Maintenance

- Cleared the integration and test static-analysis errors and expanded
  regression coverage.

## 1.7.54

Reliability round: no more false re-authentication prompts, steadier mesh and
client entities.

### 🐛 Fixed

- **An offline or rebooting router no longer asks you to re-enter the
  password.** Connection timeouts, refused connections and router-side errors
  during sign-in were treated as rejected credentials, so every outage could
  pop a "re-authenticate" notice that vanished once the router came back.
  The notice now appears only when the router actually rejects the
  username/password; an unreachable router just shows its entities as
  unavailable until it returns.
- **Mesh devices no longer duplicate after a momentary hiccup.** A brief
  timeout while reading the Wi-Fi System member list could re-key every mesh
  node for one update and leave ghost/duplicate extender devices behind. A
  failed read now keeps the previous mesh snapshot instead.
- **A device that was merely seen in the ARP table no longer fires a "new
  device connected" event**, so new-device notifications stop false-alarming
  on hosts that were pinged once or linger in the neighbour cache.
- **The per-client "Wi-Fi Session" and "Last Seen" sensors no longer freeze**
  for a connected client that is idle (no traffic between polls).
- **A mesh-node firmware update no longer gets stuck after a momentary node
  error.** A transient error page during node sign-in used to lock in an
  unusable login method until Home Assistant restarted.
- **The OOM-events counter is more exact** around log rollover edges (events
  in the same second can no longer be counted twice, and a Feb 29 log line is
  no longer dropped).
- **DNS proxy health sensors now work on firmware that reports a single
  proxy** instead of staying unavailable.
- Mesh client counts now read correctly on firmware that lists the associated
  stations instead of reporting a count.
- The mesh node **Reboot** button no longer repeats the node name twice in
  its friendly name.
- Malformed boolean values from the router can no longer appear as tiny
  traffic/throughput readings; they show as unavailable instead.

### 🔒 Privacy

- Tracked-client device names (e.g. personal phone names) are now redacted
  from the downloadable diagnostics file, matching the existing MAC/IP
  redaction.

## 1.7.53

No user-visible behavior changed.

### 🧹 Maintenance
- Validation workflows now run only when code is pushed (or on demand), instead of also on a weekly schedule.

## 1.7.52

Internal code cleanup. No user-visible behavior changed.

### 🧹 Maintenance

- Consolidated shared entity logic and removed dead code to keep the
  integration easier to maintain. No change to entities, states, or behavior.

## 1.7.51

Keeps a turned-off site-to-site IPsec tunnel visible and controllable.

### 🐛 Fixed

- A site-to-site IPsec tunnel that is switched **off** (or that has dropped and
  isn't currently negotiating) is now shown as **off** with its **Enabled**
  switch and status sensors still **available** — instead of disappearing into
  an **unavailable** state. Previously a disabled tunnel vanished from the
  router's live status view, so its switch went unavailable and could not be
  toggled back on from Home Assistant, and any automation that re-enables the
  tunnel by checking the switch state was stranded. You can now reliably turn a
  site-to-site tunnel back on from its switch, and recovery automations can tell
  "intentionally off" apart from "down".

## 1.7.50

Reliability and accuracy improvements for sensors, statistics and setup flows.

### 🐛 Fixed

- A WAN that is up but has no real address yet (e.g. `0.0.0.0` while waiting
  for a DHCP/PPP lease) is no longer reported as **Connected**, so outage and
  failover automations are no longer fired by a false "online" state.
- Wi-Fi and LAN/WAN traffic byte counters now report **unavailable** instead
  of dropping to `0` during a brief stats gap — this stops false counter
  "resets" from inflating long-term statistics.
- Uptime, memory, Wi-Fi temperature and traffic sensors now ignore malformed
  router values (negative, NaN or infinity) instead of publishing them.
- Reconfiguring with an invalid host or port now shows a form error instead of
  failing the dialog.
- Opening the options dialog while the integration is offline or its
  credentials were rejected now still lets you manage tracked clients.
- Setting up an entry with missing or corrupt connection data now retries
  cleanly instead of failing with an unexpected error.
- A router or mesh-node firmware update no longer stops early when the router
  returns an unexpected version payload.
- Routers reached over an IPv6 address now build valid device links and
  firmware-update URLs.
- When two router connection policies share the same name, the client
  **Connection Policy** selector now lists them distinctly so the right one is
  applied.

### 🔧 Changed

- Connected / Router / Disconnected client counts, the per-node mesh client
  count, and WireGuard RX/TX traffic now use the correct statistics type.
  **Long-term statistics for these specific sensors restart once after this
  update**; their current values and history graphs are unaffected.
- A mesh-node firmware update now polls the router more gently while the node
  reboots.

### 🔒 Privacy

- Client hostnames are now redacted from the downloadable diagnostics file.

## 1.7.49

Maintenance release focused on public HACS release quality and safer failure boundaries.

- Tightened config-flow and update fallback handling so credential failures, required setup failures, and firmware command rejections stay visible while optional firmware endpoints still degrade safely.
- Added release contract checks for HACS metadata, version consistency, required public docs, and Home Assistant translation wording.
- Polished setup/reconfigure wording and README guidance for local polling, optional KeenDNS protected mode, diagnostics redaction, plaintext HTTP warnings, and unsupported firmware-feature fallbacks.

## 1.7.48

### 🔧 Maintenance

- Reduced runtime router load by making coordinator refresh tiers explicit. Fast ticks now keep critical live data fresh while reusing slower diagnostic and interface-detail snapshots between their scheduled refreshes.

## 1.7.47

### 🔧 Maintenance

- Refactored internal coordinator, platform setup, and parser structure to make the integration easier to maintain and safer to extend. No entity IDs, configuration data, or user-facing behavior changed.

## 1.7.46

### 🐛 Bug fixes
- **DNS Proxy Status** no longer sticks at `degraded` on healthy
  routers. A handful of timeouts from DoH probes against unused
  upstreams are now treated as normal noise; the sensor only flags
  `degraded` when the failure rate is meaningfully high.
- **DNS Proxy Failed Requests** now produces a clean per-hour rate
  graph in HA Statistics instead of a sawtooth — useful for
  spotting actual DNS health regressions over time.
- **Mesh client counts** no longer flicker to zero on a transient
  router hiccup. The previous client snapshot is kept until the
  next successful poll.
- **New device** events no longer fire repeatedly for the same
  client when the router uses different MAC formatting between
  payloads.
- **IPsec site-to-site** stays connected during normal IKE
  re-keying instead of briefly flipping to disconnected.
- Reload and shutdown of the integration are now clean — no more
  spurious "fetch failed" warnings during normal HA restarts.

### ✨ Improvements
- **Site-to-site IPsec** data now updates every minute (matching
  WAN/traffic stats) instead of every 10 minutes, so `Connected`,
  `Tunnel state`, `IKE state`, and RX/TX sensors react much faster
  to real changes. The underlying router-side memory issue that
  forced the 10-minute throttle in 1.7.45 has been worked around
  by switching to a different router endpoint.
- New **IPsec VICI OOM Total** sensor: a single cumulative counter
  that survives HA restarts, so HA Statistics gives you a real
  "events per hour / per day" graph instead of a snapshot of the
  log window.
- Each integration refresh now uses fewer HTTP round-trips to the
  router on supported firmware (KeeneticOS 5.x), reducing router
  CPU load and giving slightly snappier sensor updates. Older
  firmwares automatically fall back to the previous behavior.

### 🧹 Cleanup (only affects diagnostic sensors)
- The `IPsec VICI Status` and `IPsec VICI Out Of Memory` sensors
  have been removed — replaced by the single cumulative counter
  above.
- All other IPsec entities keep their unique IDs and history.

### 🔧 Maintenance
- Internal cleanup, reliability fixes, and expanded test coverage
  across the coordinator, IPsec parsing, and client tracking.

## 1.7.45

### 🐛 Bug fixes
- Site-to-site IPsec (`crypto map`) polling cadence increased from 5 min to
  10 min. On some KeeneticOS firmwares (observed on 5.00.C.10), each
  `show/crypto/map` request triggers an `IpSec::Vici::Stats: out of memory`
  event inside the router's `ndm` process. Polling the endpoint less often
  reduces those events proportionally without affecting WAN, interface, or
  traffic statistics polling (still 10 s). The `Connected` / `Tunnel state`
  / `IKE state` / RX·TX sensors now update every 10 min instead of every
  5 min; for faster site-to-site state detection, use a ground-truth health
  check (e.g. an HA `ping` binary_sensor through the tunnel).

## 1.7.44 – 1.7.34

- Maintenance cleanup only. No user-visible behavior changed.

## 1.7.33

### Security

- Diagnostics and warning logs now redact additional mesh identifiers that can expose MAC addresses.

## 1.7.32

### Bug fixes

- WAN, mesh-node, and IPsec value sensors now continue updating correctly between state changes.
- Wi-Fi switch and Wi-Fi temperature entities now report unavailable correctly when the router cannot be refreshed.
- Tracked client device trackers no longer write duplicate state updates on each refresh.

### Security

- Diagnostics and debug logs no longer expose MAC addresses through indexed diagnostic data or full MAC lists.
- NDNS debug logging now records only payload shape instead of dumping the full router response.

## 1.7.31

- Maintenance cleanup only. No user-visible behavior changed.

## 1.7.30

### Improvements

- WAN, mesh-node, IPsec, and client entities now reduce unnecessary Home Assistant state writes while still updating immediately on meaningful state changes.
- Mesh client association counts are calculated once per refresh, reducing repeated work on mesh installations.

### Reliability

- Router data fetches now avoid silently swallowing unexpected errors in several optional data paths.

## 1.7.29

### Improvements

- WAN and mesh-node sensors now use faster lookups during refreshes, reducing per-tick work on routers with several WAN interfaces or mesh nodes.

## 1.7.28

### Bug fixes

- WAN sensors no longer fail when the router returns an unexpected interface summary payload.
- Mesh nodes on older firmware are no longer reported as connected when they are offline.
- DNS-over-HTTPS URI redaction now tolerates malformed port values.

## 1.7.27

### Bug fixes

- Port information is no longer dropped when the router returns a non-empty port list.
- Controller firmware updates now handle empty success responses from the router.
- Mesh node firmware updates now stop cleanly when staging fails and can fall back from controller-driven updates to direct-node updates when needed.
- Per-host policy lookups now match MAC addresses consistently across casing and formatting differences.
- Optional router data parsers now tolerate one malformed row without dropping the rest of that sensor group.
- Mesh nodes on older firmware that omit internet availability now use a safer connection fallback.

### Security

- DNS-over-HTTPS upstream URIs are redacted in Home Assistant state and diagnostics so embedded IDs or credentials are not exposed.

## 1.7.26 – 1.7.23

- Maintenance cleanup only. No user-visible behavior changed.

## 1.7.22

### Bug fixes

- Coordinator setup no longer fails after upgrading from earlier 1.7.x builds.

## 1.7.21

- Maintenance cleanup only. No user-visible behavior changed.

## 1.7.20 - Quiet polling and sensor accuracy

### Bug fixes

- **Stopped flooding the router log with errors for unsupported features.**
  Diagnostics for IPsec site-to-site tunnels, the DNS proxy, the Ping Check
  service, NDNS / KeenDNS, and captive-portal client lists are now skipped on
  routers that do not expose those features, after the very first
  not-found response. Previously the integration retried each missing
  endpoint on every poll, producing thousands of ndm errors per hour in the
  router log on hardware without Guest Wi-Fi, IPsec, or DNS-proxy support.
- **Active Connections sensor now records a live count instead of a total.**
  The sensor switched to an instantaneous measurement, so HA long-term
  statistics stop treating it as a monotonic running total and the graph
  reflects what the router actually shows.
- **Memory Usage sensor never reports below 0% or above 100%.** Transient
  firmware payloads where memfree briefly exceeds memtotal no longer
  produce nonsense percentages.
- **CPU, memory, traffic and uptime sensors reject NaN and infinity.**
  A malformed numeric value from the router can no longer poison HA
  recorder statistics for those sensors.
- **Prevented spurious 401 auth errors during high-concurrency polling.**
  Auth refreshes are now serialised, so several RCI calls hitting an
  expired session at once cannot race and overwrite each other's
  credentials mid-flight.
- **Ping Check and DNS proxy diagnostics now accept single-entry payloads.**
  Routers that return one profile or one DoH upstream as a dict instead of
  a list no longer cause those WAN / DNS diagnostics to disappear.

## 1.7.19 - Tracked-client and coordinator stability

### Bug fixes

- **Tracked-client diagnostic sensors now handle availability consistently.**
  RX/TX, RSSI, Link Speed, Wi-Fi Session and Last Seen now use the same
  availability rules when the router data changes or a client disappears.
- **Kept tracked device trackers available when a client disappears from the
  router table.** Missing clients now continue to render as Away instead of
  becoming Unavailable, while real coordinator failures still mark the tracker
  unavailable.
- **Hardened optional coordinator payloads.** Malformed ping-check, DNS, IPsec,
  interface-stat and crypto-map diagnostic payloads now fall back to empty data
  instead of breaking the refresh tick.
- **Stopped fast refreshes from mutating cached crypto-map data in place.**
  Cached site-to-site IPsec data is copied before enrichment, and incomplete
  crypto-map counter rows no longer raise errors.

## 1.7.17 - Router payload hardening

### Bug fixes

- **Mesh discovery failures no longer fail the whole coordinator refresh.**
  Optional extender discovery now falls back to an empty mesh list for that
  tick, while core router/client/WAN data continues updating.
- **WAN backup ordering now handles priorities returned as strings.** Backup
  connection labels stay correct when Keenetic RCI returns `"80"` instead of
  `80`.
- **Platform setup and dynamic entity listeners now tolerate malformed router
  payload rows.** Bad non-dict rows in mesh, port, WAN, Wi-Fi and crypto-map
  payloads are skipped instead of crashing entity setup.
- **Mesh and main-router port sensors now ignore malformed port rows.**
  Existing valid ports still render normally.

## 1.7.16 - Sensor payload hardening

### Bug fixes

- **System CPU and memory sensors now tolerate malformed numeric router
  values.** Odd RCI payloads no longer risk raising from direct `float(...)`
  conversion; unavailable values now stay unavailable.
- **Mesh CPU, memory and client-count sensors now use the shared Keenetic
  parsing helpers.** This keeps extender diagnostics consistent with the main
  router sensors and avoids duplicated fallback logic.
- **Tracked-client lookup now falls back safely when the MAC index contains
  unexpected values.** Client entities and device trackers reuse the same
  normalized raw-list fallback instead of assuming every indexed value is a
  dict.

## 1.7.15 - Tracked-client parsing hardening

### Bug fixes

- **Tracked-client Wi-Fi parsing now handles numeric fields returned as
  strings.** Link speed and Wi-Fi band/type inference no longer risk failing
  when a Keenetic RCI path returns `txrate` or RSSI-like fields as text.
- **Device-tracker fallback lookup now normalizes MAC address variants.** If
  coordinator data ever falls back to the raw client list instead of the
  precomputed MAC index, `AA-BB-...`, `aa:bb:...`, and compact MAC forms still
  resolve to the same tracked client.

## 1.7.14 - Offline tracked-client live metrics

### Improvements

- **Offline tracked clients now show less diagnostic noise.** Live Wi-Fi
  session fields are unavailable when the router says the client is away:
  Wi-Fi Session, Link Speed, RSSI, and WiFi Mode no longer show misleading
  zero/unknown values for disconnected clients.
- **Offline zero traffic counters are unavailable at the entity level.** When
  Keenetic keeps an offline hotspot row with reset `rxbytes`/`txbytes`, RX/TX
  are marked unavailable instead of appearing as meaningful live counters.

## 1.7.13 - Last Seen frontend rendering hardening

### Bug fixes

- **Tracked-client Last Seen now avoids Home Assistant's relative-time
  rendering more aggressively.** The entity explicitly clears `device_class`
  and uses `DD.MM.YYYY HH:MM:SS`, so offline clients should show exact local
  date/time instead of “15 minutes ago”.

## 1.7.12 - Exact tracked-client Last Seen display

### Improvements

- **Tracked-client Last Seen now displays exact local date and time.** Home
  Assistant timestamp sensors are commonly rendered as relative text such as
  “9 minutes ago”, so Last Seen is now exposed as formatted diagnostic text
  (`YYYY-MM-DD HH:MM:SS`) while still remaining unavailable for online clients.

## 1.7.11 - Offline tracked-client Last Seen fix

### Bug fixes

- **Offline tracked-client Last Seen now has a second fetch path.** Some
  Keenetic firmware exposes `show ip neighbour` correctly through `/rci/parse`
  even when `/rci/show/ip/neighbour` is empty. The integration now falls back
  to the parse command, so offline clients can show the router's actual
  last-seen timestamp.
- **Offline hotspot rows now prefer neighbour Last Seen.** If Keenetic keeps an
  offline client in the hotspot table with zero-ish live data, the coordinator
  now treats the neighbour table as the authoritative source for the offline
  timestamp.
- **Online tracked-client Last Seen is marked unavailable.** The entity now
  becomes unavailable while the client is online instead of showing a confusing
  `Unknown` timestamp.
- **Offline zero traffic counters are no longer shown as real `0.00 GB`
  values.** If Keenetic resets `rxbytes`/`txbytes` to zero for an offline
  hotspot row, RX/TX become unavailable rather than misleading.

## 1.7.10 - Cleaner tracked-client diagnostics

### Improvements

- **Tracked-client Last Seen now only appears when it is meaningful.** Online
  clients report `Unavailable` for Last Seen instead of a constantly changing
  “seen a few seconds ago” timestamp. Offline clients still use Keenetic's
  neighbour data to show the last time the router saw the device.
- **New tracked-client setups no longer create First Seen and DHCP Registered
  sensors.** These fields were diagnostic noise for the common dashboard use
  case; presence, IP, link speed, signal, traffic, Wi-Fi session, band and mode
  remain available.

## 1.7.9 - Router-scoped tracked clients

### Bug fixes

- **The same tracked client can now be added to multiple Keenetic routers
  without merging into one Home Assistant device.** Client devices are now
  scoped by config entry plus MAC address, so a phone tracked on Orange and
  Yakhny appears as two independent devices instead of one mixed device with
  `_2` entity IDs.
- **Tracked-client MAC formats are canonicalized everywhere.** `80:07:...`,
  `80-07-...`, and `8007...` now resolve to the same tracked client key within
  one router, preventing duplicate sensors and duplicate Connection Policy
  controls.
- **Placeholder IP addresses are no longer treated as real client IPs.**
  `0.0.0.0` and `::` are ignored for tracked-client setup, entity IP values,
  and configuration URLs.

## 1.7.8 - Better tracked-client seen times

### Improvements

- **Tracked-client Last Seen now keeps working after a device goes offline.**
  The coordinator merges Keenetic's IP-neighbour table with the hotspot client
  table, so registered clients that disappear from Wi-Fi can still show the
  last time the router saw them instead of falling back to `Unavailable`.
- **Tracked-client First Seen is back as a timestamp.** New setups get a
  diagnostic First Seen sensor sourced from Keenetic neighbour/hotspot data,
  shown as a Home Assistant timestamp instead of raw seconds.
- **Tracked-client Uptime is now labelled Wi-Fi Session.** The existing entity
  identity is preserved, but the dashboard label now describes what Keenetic
  actually reports for Wi-Fi clients: the current connection session duration.
- **Device tracker diagnostics now explain neighbour-based presence.** Tracked
  clients expose `last_seen_source`, `first_seen_source`, `neighbour_expired`,
  `neighbour_wireless`, and `neighbour_leasetime` attributes to make offline
  troubleshooting easier.

## 1.7.7 - Presence and polling safety fixes

### Bug fixes

- **Tracked clients no longer flip to away on a transient client-table
  failure.** If the Keenetic hotspot client table fails after a successful
  refresh, the coordinator keeps the previous client snapshot for that tick and
  marks it stale instead of publishing an empty table.
- **Interface statistics now respect the router polling concurrency cap.**
  Large interface batches are limited to four in-flight per-interface stat
  requests, matching the coordinator's RCI concurrency guard.

## 1.7.6 - Cleaner tracked-client diagnostics

### Improvements

- **Removed the separate tracked-client Link Status sensor from new setups.**
  Presence already uses the same Keenetic `link=up` signal, so the extra sensor
  was redundant.
- **Renamed tracked-client TX Rate to Link Speed.** For Wi-Fi clients,
  Keenetic's `txrate` is the useful current link-speed signal shown in Mbps, so
  the entity now uses the clearer dashboard label.

## 1.7.5 - Router-based tracked-client presence

### Bug fixes

- **Tracked clients no longer depend on ICMP ping from Home Assistant.** Device
  trackers now use the Keenetic client table directly: `link=up` or
  `active=true` means `home`, which works for clients in isolated or routed
  networks where HA cannot ping the device.
- **Tracked-client presence attributes are easier to understand.** Device
  trackers now expose `tracking_method: router_link` and `presence_source`
  (`link`, `active`, `inactive`, or `missing`) so the reason for `home`/`away`
  is visible.
- **Last Seen is now a timestamp.** The tracked-client `Last Seen` sensor shows
  when the router last saw the device instead of a raw “seconds ago” duration.

### Improvements

- **Removed the unused ICMP dependency.** The integration no longer installs
  `icmplib`, reducing setup complexity and avoiding host/container ICMP
  permission issues.
- **Reduced tracked-client sensor noise.** New setups no longer create the
  low-value `First Seen`, `Link Speed`, or `Port` tracked-client sensors by
  default, because they commonly showed raw seconds or `unknown` for Wi-Fi
  clients.

## 1.7.4 - Payload parsing, options, and Ping Check hardening

### Bug fixes

- **Single-host client payloads are no longer dropped.** Some Keenetic RCI
  responses collapse one host into a plain object instead of a list; the
  integration now keeps that client visible.
- **Mesh fallback nodes survive blank MWS responses.** If `show/mws/member`
  returns an empty or incomplete payload, extenders discovered from the
  hotspot client table remain available instead of disappearing for that tick.
- **MWS single-object payloads are parsed correctly.** `show/mws/member`
  responses that collapse one member or port into a dict now still produce
  the correct mesh node and port entities.
- **String booleans from Keenetic are handled consistently.** Mesh extender
  activity, MWS internet availability, firmware update progress, and client
  statistics no longer treat values like `"no"` as truthy.
- **Options now reuse the running router client.** Opening the integration
  options no longer performs an avoidable extra client setup when Home
  Assistant already has an active runtime client.
- **Ping Check parsing is more faithful to Keenetic behaviour.** Persistent
  `_WEBADMIN_*` profiles from the web UI are treated as authoritative, while
  TEST-NET one-off probe targets are ignored so they do not create false WAN
  outages.
- **Cached IPsec throughput is not resampled on fast coordinator ticks.**
  Crypto-map throughput now preserves the last real sample until the next
  slow crypto-map refresh, avoiding misleading zero/underestimated rates.
- **New-device logs no longer expose full client MAC/IP identifiers.** Home
  Assistant events still contain the full data for automations, but info logs
  now use masked suffixes.

## 1.7.3 - State freshness and direct mesh update hardening

### Bug fixes

- **Tracked-client uptime and last-seen sensors now update on their own ticks.**
  The client entity base still suppresses noisy uptime/last-seen-only writes
  for unrelated tracked-client entities, but the dedicated uptime and last-seen
  sensors now opt back into those fields.
- **Removed mesh and WAN sub-devices now become unavailable instead of stale.**
  Dynamically created mesh and WAN entities stay in Home Assistant after the
  router removes them, but their availability now correctly reflects that the
  underlying node/uplink is gone.
- **Direct mesh firmware updates recover from stale node cookies.** If a mesh
  node rotates its auth cookie during challenge auth, the integration now keeps
  the final cookie. If a cached cookie expires during direct node update calls,
  the cache is invalidated so the next attempt can authenticate cleanly.

## 1.7.2 - Stability fixes for mesh, auth, and translations

### Bug fixes

- **Raw aiohttp responses are now released on auth and mesh update paths.**
  Challenge auth, mesh-node auth, and direct node firmware update requests
  now close every response object explicitly.
- **`/rci/parse` arguments use strict single-token validation.**
  Interface names, MACs, policies, crypto-map names, and mesh CIDs now reject
  whitespace, quotes, shell separators, control characters, and expansion
  characters before command construction.
- **English translations are synced with `strings.json`.** Reconfigure and
  connection-mode UI strings no longer drift from Home Assistant's source
  translation file.
- **Mesh-node and tracked-client uptime sensors use `TOTAL_INCREASING`.**
  This matches router, PPPoE, and WireGuard uptime statistics while leaving
  `last_seen` as a resetting measurement.

### Improvements

- **Mesh unique IDs are entry-scoped and collision-resistant.** Mesh sensors,
  connect/update binary sensors, reboot buttons, and firmware update entities
  now use the full sanitized mesh node id with the config entry id. Existing
  mesh entity registry entries are migrated from the old truncated IDs.
- **Mesh entities are added dynamically.** Newly discovered mesh nodes and
  mesh ports are added by coordinator listeners across sensor, binary sensor,
  button, and update platforms without requiring a Home Assistant restart.

## 1.7.1 - HACS validation fixes for 1.7.0

Hotfix for two HACS validation errors that 1.7.0 tripped:

- **`min_ha_version` is not a valid `manifest.json` key** — that field
  is not accepted for custom integrations. The minimum HA version is now
  declared in `hacs.json` via the standard `homeassistant: "2024.5.0"` key,
  which is what HACS actually reads.
- **`CONFIG_SCHEMA` warning** — hassfest requires every integration
  that defines `async_setup` to declare a config schema, even when
  it has no YAML support. The integration root now exposes
  `CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)` — the
  canonical "UI-only, no YAML" helper.

This release exists so HACS accepts installs again.

## 1.7.0 - Hardening, modern HA APIs, and statistics fixes

> ⚠️ **Minimum Home Assistant version bumped to 2024.5.0.** This release
> uses HA's `runtime_data` config-entry pattern, which is unavailable
> on older HA core versions. If you are still on 2024.4 or earlier,
> stay on 1.6.8 until you upgrade HA.

### 🔒 Security

- **`SECURITY.md` shipped.** Documents what the integration redacts
  (diagnostics, logs, repr) and — honestly — what it cannot protect:
  HA stores config-entry passwords as plaintext in
  `<config>/.storage/core.config_entries`, and no integration can fix
  that. If you have ever shared a HA backup or a `.storage/` snapshot,
  rotate your router admin password.
- **Cancellation safety in every error-handling path.** Every
  `except Exception` block in the API client, coordinator, firmware-
  update flow, and config flow now re-raises `asyncio.CancelledError`
  before falling through to its generic handler. The old broad catch
  swallowed HA's shutdown signal during integration reload, sometimes
  producing hangs that needed a HA restart to resolve.

### 🐛 Bug fixes

- **Uptime sensors no longer produce a sawtooth in long-term graphs.**
  Router uptime, PPPoE uptime, and WireGuard tunnel uptime were
  declared as `MEASUREMENT`, which made HA's recorder treat each poll
  as a separate gauge value and store a 1-week sawtooth in the LTS
  table. They are now `TOTAL_INCREASING` — the right state class for
  a monotonic counter that resets on reboot/reconnect — and the
  long-term-statistics graph for those sensors is now smooth.
- **Reauth and reconfigure use the modern HA helper.** Both flows now
  call `async_update_reload_and_abort` instead of the deprecated
  `async_update_entry` + `async_abort` pair. The previous pattern
  occasionally left users running with stale credentials until they
  manually reloaded the integration; the new flow reloads in the
  same step.

### ✨ Improvements

- **Modern Home Assistant runtime storage.** The integration now uses
  Home Assistant's current config-entry runtime storage. If you happen to
  write custom blueprints or scripts that poke at
  `hass.data["keenetic_router_pro"]`, they need updating.
- **`min_ha_version: 2024.5.0` declared in the manifest.** HACS will
  refuse to install on older HA cores rather than letting you hit a
  cryptic `runtime_data` AttributeError at setup time.

## 1.6.8 - Performance improvements

### Performance

- **Coordinator builds an O(1) MAC-keyed client index.** Per-client entities
  (sensors, switches, device-trackers) used to scan the full client list on
  every coordinator tick to find their own row. The coordinator now publishes
  `clients_by_mac`, and entities look themselves up directly. On a network
  with hundreds of tracked devices this turns an O(N²) per-tick cost into
  O(N).
- **Per-client entities skip no-op state writes.** `ClientEntity` now compares
  a fingerprint of its client row (excluding `last-seen` / `uptime` ticks) and
  short-circuits `_handle_coordinator_update` when nothing meaningful changed.
  Idle clients no longer trigger HA state writes every poll cycle.
- **Interface stats fetched in parallel.** `async_get_all_interface_stats`
  now uses `asyncio.gather` instead of sequential awaits, cutting WAN-stats
  fetch latency on multi-interface routers.
- **Interface list shared across the polling stages.** Stage 1 now fetches
  `iface_list` once and passes it through to stage 2, mesh fetch, and the
  WAN-status projection — eliminating ~3 redundant `show interface` round-trips
  per coordinator tick.
- **Mesh fetch reuses the already-fetched client list.** `_get_mesh_nodes_from_clients`
  accepts a pre-fetched `clients=` argument so we don't re-call
  `async_get_clients()` when the coordinator just fetched it.

### Notes

- Configuration is unchanged and entity unique IDs are preserved.
- 1.6.6 mesh `device_info` None-guard and 1.6.7 plaintext-HTTP repair card
  are preserved.

## 1.6.7 - Plaintext-HTTP repair warning

### 🔒 Security

- **Repair card now warns when the integration is configured for plaintext
  HTTP to a non-loopback router.** When SSL is disabled and the host is not
  a loopback address, the integration raises a Home Assistant Repair issue
  explaining that your router username, NDW2 password hash, and session
  cookie traverse the LAN unencrypted on every poll. The card links to the
  remediation steps in `SECURITY.md` and is automatically cleared once you
  reconfigure the entry to use HTTPS. No configuration changes required —
  existing setups will see the card on next reload.

## 1.6.6 - Mesh and client bug fixes

### Fixes

- **Mesh device info no longer crashes when a node briefly disappears.** The
  `MeshEntity.device_info` property could raise `AttributeError` when the
  underlying mesh node had been removed from the router response between
  ticks; it now safely returns the fallback router device info.
- **Hotspot client fetch no longer swallows unrelated exceptions.** The fallback
  loop in `async_get_clients` previously caught `Exception` indiscriminately,
  hiding unexpected errors; it now narrows to `KeeneticApiError` and logs
  fallthroughs at debug level.

## 1.6.5 - IPsec VICI diagnostics

### Improvements

- **Added IPsec VICI diagnostic sensors.** The integration now summarizes
  recent `IpSec::Vici::Stats: out of memory` router log entries so these
  firmware/IPsec-stat issues are visible in Home Assistant without manually
  scraping logs.
- **Reduced IPsec crypto-map polling pressure.** Site-to-site IPsec tunnel
  data now uses the very-slow coordinator cadence, matching other diagnostic
  endpoints and avoiding unnecessary hits to Keenetic's IPsec statistics path.

## 1.6.4 - KeenDNS protected web app access

### Improvements

- **Added a KeenDNS protected web app connection mode.** The integration can
  now be configured with a password-protected KeenDNS app hostname over HTTPS
  while keeping the existing direct/local API mode unchanged.
- **Setup and reconfigure now show mode-specific fields.** KeenDNS protected
  mode hides direct-only port, SSL and challenge-auth options and uses the
  HTTPS/443 Basic Auth defaults automatically.
- **Full URL input is normalized safely.** Setup and reconfigure accept either
  a bare host name or a full `https://...` URL, reject paths/query strings, and
  store a clean host/port/SSL target.
- **Clearer 502 errors for protected apps.** Bad Gateway responses now point to
  the KeenDNS published application/upstream configuration instead of looking
  like a generic router API failure.

### Documentation

- Documented the protected-access setup and the minimal `HTTP Proxy`
  permission needed for full proxied RCI access.
- Added a warning that verbose curl logs expose Basic Auth headers and should
  be followed by password rotation when shared.

## 1.6.3 - WireGuard entity cleanup

### Fixes

- **Removed duplicate WireGuard entities from the main router device.** The old
  WireGuard-specific RX/TX/Uptime sensors are no longer created because the
  per-interface/WAN device model already exposes the relevant state in the
  correct place.
- **Removed duplicate VPN controls on WireGuard WAN devices.** VPN uplinks now
  keep the WAN `Enabled` switch only, instead of showing both `Enabled` and a
  separate `WireGuard` switch.
- **Duration sensors now request whole-second display precision.** WAN and
  PPPoE uptime sensors set HA's suggested precision to `0`, avoiding noisy
  values like `212.00 s` where Home Assistant respects the hint.

## 1.6.2 - Interface device organization and VLAN WAN throughput

### Fixes

- **VLAN WAN throughput now works.** WAN VLAN interfaces such as
  `GigabitEthernet0/Vlan5` are no longer skipped when collecting interface
  statistics. The integration now uses Keenetic's working
  `show interface <name> stat` command first and keeps the older RCI GET form
  as a fallback.

### Improvements

- **WAN interfaces now have an Enable switch on their own HA device.** The
  switch uses the same Keenetic interface up/down control as the web UI and is
  grouped with the WAN's status, IP, role, counters and throughput sensors.
- **VPN controls are grouped with the interface they control.** VPN switches
  now attach to the matching WAN device when the VPN is an uplink; otherwise
  they appear under their own VPN/interface device instead of the main router.
- **Throughput sensors expose raw stat details.** Per-WAN throughput entities
  now include the raw `rxbytes`, `txbytes`, `rxspeed`, `txspeed`, stat
  interface and stat timestamp as attributes for easier troubleshooting.

## 1.6.1 - Mesh firmware update start fix

### Fixes

- **Mesh node firmware updates now use the controller MWS command first.**
  KeeneticOS starts extender updates with `mws member <member> update start`;
  the previous direct-node component update path could fail with "Could not
  start firmware update on node ...". The direct-node path remains as a
  fallback for older or unusual setups.

## 1.6.0 - DNS over HTTPS diagnostics

### Improvements

- **New DNS Proxy Status sensor** — shows whether the router's DNS proxy is
  healthy, degraded, down or unknown. This helps detect the failure mode where
  raw IP connectivity still works but DNS over HTTPS stops answering.
- **New DNS Proxy Failed Requests sensor** — exposes failed upstream DNS proxy
  requests from the router's own stats so you can build Home Assistant
  automations around DNS/DoH trouble without scraping router logs.

## 1.5.1 - Stability and reload hygiene

### Bug fixes

- **Memory leak when reloading the integration** — every "Reload" action (or
  options-flow change, which reloads the integration) used to leave behind an
  invisible event listener bound to the previous coordinator. Over enough
  reloads this could grow Home Assistant's memory footprint and cause
  duplicate "new device" events. The listener is now properly unregistered
  on unload.
- **Cleaner shutdown when Home Assistant is stopping** — the ICMP ping loop
  now correctly propagates cancellation during HA shutdown instead of
  swallowing it. Stopping HA mid-tick no longer logs spurious "ping failed"
  noise.
- **Friendlier error if the router host is missing from the config entry** —
  if a config entry somehow ends up without a `host` value (e.g. after a
  migration glitch), setup now fails fast with a clear "please reconfigure"
  message instead of crashing later with an opaque `NoneType` error.

### Improvements

- **Slightly faster fast-tick** — the 10-second coordinator tick no longer
  rebuilds two intermediate cache dicts on every iteration. Negligible by
  itself, but Home Assistant runs this loop ~8 600 times per day.
- **Cleaner setup-error logs** — fixed a duplicated error message when an
  unexpected exception happens during initial config-flow setup. The
  traceback was already included; the duplicate string is gone.

## 1.5.0 - Security hardening

### Security

- **Diagnostics downloads no longer leak your router password.** Before this
  release, the "Download diagnostics" button on the integration card produced
  a JSON file that could include your Keenetic credentials, session cookies,
  Wi-Fi PSKs, MAC addresses and SSIDs in plain text. If you attached that
  file to a GitHub issue or shared it for support, you were leaking secrets.
  The diagnostics dump is now passed through Home Assistant's redaction
  helper and all of those fields are replaced with `**REDACTED**` before the
  file is written.
- **Password input is now masked in the UI.** The router password field in
  the initial setup, re-auth and reconfigure dialogs is now a proper
  password input — characters render as dots instead of plain text. Prevents
  shoulder-surfing and accidental screenshot leaks during setup.
- **Router client details can no longer leak credentials in logs.** If a
  debug-log line or traceback includes the API client object, username and
  password now show as `<redacted>` while host/port/SSL stay visible for
  troubleshooting.

### Documentation

- New [`SECURITY.md`](SECURITY.md) explaining where Home Assistant stores
  the router password (`/config/.storage/core.config_entries`, plain text by
  HA design — this is not specific to this integration), recommended file
  permissions, password-rotation procedure, and what the integration
  redacts in logs and diagnostics.

### Notes

- No config-entry schema change → no migration required, just upgrade and
  restart.
- If you previously shared a diagnostics dump publicly, consider rotating your
  router password as a precaution.

## 1.4.0 - Bug fixes and throughput units

### Bug fixes

- **Re-auth and reconfigure flows finally work.** Previously, when Home
  Assistant prompted you to re-enter the router password (after a
  credential change or session expiry), submitting the form silently did
  nothing — the dialog re-rendered with cryptic error strings instead of
  completing. Both flows now correctly close on success.
- **Mesh nodes no longer get stuck after a password change.** If you
  rotated the password on a mesh node, the integration kept using the old
  cached auth token until you restarted Home Assistant. The bad token is
  now evicted automatically on the first `401 Unauthorized` response.
- **Local-IP sensor is more robust across upgrades.** The sensor no longer
  depends on a fragile API-client attribute name.

### Improvements

- **WAN and IPsec throughput shown in Mbit/s, not bytes/s.** All
  networking equipment and ISP plans are quoted in megabits per second,
  so the previous `B/s` reading required mental math. Sensors now report
  in Mbit/s with two decimal places, and Home Assistant offers automatic
  unit conversion (kbit/s ↔ Mbit/s ↔ Gbit/s) directly in the entity
  customisation dialog — no template tricks needed.

## 1.3.0 - Fork hardening and performance

This is the first release of the maintained fork. The Home Assistant domain
stays `keenetic_router_pro` so existing dashboards, automations, and entity
history carry over unchanged from the upstream version.

### Security

- Safer Basic Auth header construction that no longer risks leaking
  credentials into debug logs.
- Support for the newer NDW2 challenge-auth scheme used by recent Keenetic
  firmwares, including session-cookie reuse so we don't re-authenticate on
  every call.
- Automatic one-shot re-authentication after expired session cookies before
  surfacing failure to Home Assistant.
- Sensitive values (passwords, PSKs, cookies, `Authorization` headers, keys,
  secrets) are now redacted from API error excerpts and debug logs.
- Raw config-flow form input is no longer logged at debug level — your
  password is no longer written to `home-assistant.log` if debug logging
  is enabled.
- CLI arguments sent to `/rci/parse` are now validated against an allow-list
  to prevent command-injection style input.
- The reconfigure form no longer pre-fills the existing password as a
  default value.

### Improvements

- **Proper re-auth and reconfigure flows.** When your router password
  changes, HA now correctly prompts you to re-enter it instead of marking
  the integration as permanently failed.
- **Lower router CPU load.** Slow-changing data (firmware version, mesh
  topology, NDNS info) is now polled on a much longer cycle. Interface
  statistics are only fetched for interfaces that back enabled sensors.
- Connected/disconnected/extender counts are now derived from already-
  fetched client data instead of issuing extra API calls.
- Fixed a class of bugs where device URLs could appear as `http://None` in
  the device registry.
- Wi-Fi presence-tracking interval is configurable from 5 to 300 seconds
  via the integration's options.
- The integration no longer requires the `pyqrcode` and `pypng`
  dependencies — the Wi-Fi QR-image platform was removed.

### Removed

- USB device polling (controller and mesh nodes) — produced more noise
  than value and added load to slower routers.
- Wi-Fi QR-code image platform.
- Non-English translation files (English only is shipped — translations
  can be contributed back via PR if there is demand).

### Documentation

- Lighter, more practical README focused on install / config /
  troubleshooting.
- Manifest documentation and issue-tracker links repointed to the fork.
