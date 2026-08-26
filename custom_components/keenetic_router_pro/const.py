"""Constants for the Keenetic Router Pro integration."""

DOMAIN = "keenetic_router_pro"
DEFAULT_PORT = 100
DEFAULT_SSL = False
FAST_SCAN_INTERVAL = 60

# Deadband for every cumulative data counter, stated once here in bytes and
# converted into each sensor's own unit at its base class. Counters are the
# integration's largest recorder cost: on a live link they move on every poll,
# so undamped they cost a row per poll forever.
#
# The step is bounded from both sides. Too narrow (50 MB) and the counters stay
# sampling-capped rather than resolution-capped — that step bound only just, so
# the faster 1.13.0 poll simply produced more rows. Too wide (250 MB, measured
# live) and a quiet 3.7 GB/day link publishes 0.6 points an hour: whole hours
# hold no row, and the hourly traffic statistics lump their delta into the next
# hour.
#
# 100 MB sits between them — at least one point an hour on that quiet link,
# and, replayed over 183 restart-free minutes of live traffic, it takes the 32
# data-size counters from 8 424 rows/day to 1 519.
COUNTER_DEADBAND_BYTES = 100_000_000
CONF_TRACKED_CLIENTS = "tracked_clients"
FIELD_CONNECTED = "connected"
INTERFACE_CONF_DISABLED = "disabled"
CONF_USE_CHALLENGE_AUTH = "use_challenge_auth"
CONF_CONNECTION_MODE = "connection_mode"
CONNECTION_MODE_DIRECT = "direct"
CONNECTION_MODE_KEENDNS_PROTECTED = "keendns_protected"
EVENT_NEW_DEVICE = f"{DOMAIN}_new_device"
# Fired for every client that joins or leaves, not only the first time a MAC is
# ever seen. Automations can trigger on these directly instead of watching an
# entity's state and working out what changed.
EVENT_CLIENT_CONNECTED = f"{DOMAIN}_client_connected"
EVENT_CLIENT_DISCONNECTED = f"{DOMAIN}_client_disconnected"
EVENT_WAN_FAILOVER = f"{DOMAIN}_wan_failover"

# WAN-status strings produced by ``KeeneticClient.async_get_wan_status`` and
# consumed by sensors. ``CONNECTED`` means link is up *and* an IP is leased;
# ``LINK_UP`` means physical link only (ISP outage / no DHCP); ``DOWN`` means
# the interface itself is down.
WAN_STATUS_CONNECTED = "connected"
WAN_STATUS_LINK_UP = "link_up"
WAN_STATUS_DOWN = "down"

# IPsec ``crypto map`` connection state from ``show/crypto/map``.
IPSEC_STATE_ESTABLISHED = "PHASE2_ESTABLISHED"

# Truthy strings accepted from router payloads (Keenetic firmware mixes
# booleans, "true"/"false" strings, and link/up/online for the same field).
TRUTHY_STRINGS = ("true", "yes", "1", "up", "online")

# Link/interface state strings reported by /rci/show/interface for the
# ``state`` field. Used across api/domains/* and entity/sensor modules.
LINK_STATE_UP = "up"
LINK_STATE_DOWN = "down"

# Interface ``id``/role tokens that mark a WAN/uplink interface in the
# router config. Used to detect WAN candidates without scanning every
# string field for substrings.
UPLINK_ROLE_TOKENS = ("inet", "internet", "wan")

# RCI endpoint paths used in more than one place.
RCI_SHOW_VERSION = "show/version"
RCI_HOTSPOT_HOST_PATHS = ("show/ip/hotspot/host", "ip/hotspot/host")

# ---- Entity-family options (options flow) ----
# A large network can produce several hundred entities. These let the user pick
# how much of the per-client and per-interface detail is actually created,
# instead of trimming rows off entities they never wanted.
CONF_CLIENT_SENSORS = "client_sensors"
CLIENT_SENSORS_FULL = "full"
CLIENT_SENSORS_BASIC = "basic"
CLIENT_SENSORS_OFF = "off"
# Existing entries default to "full" so nothing disappears on upgrade.
DEFAULT_CLIENT_SENSORS = CLIENT_SENSORS_FULL
# The sensors a "basic" install keeps: presence-adjacent facts, no counters.
BASIC_CLIENT_SENSOR_KEYS = frozenset({"ip", "uptime", "last_seen", "connection_type"})
