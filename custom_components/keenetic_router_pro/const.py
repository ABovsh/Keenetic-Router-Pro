"""Constants for the Keenetic Router Pro integration."""

DOMAIN = "keenetic_router_pro"
DEFAULT_PORT = 100
DEFAULT_SSL = False
FAST_SCAN_INTERVAL = 10
DEFAULT_PING_INTERVAL = 5
MIN_PING_INTERVAL = 5
MAX_PING_INTERVAL = 300
CONF_PING_INTERVAL = "ping_interval"
DATA_CLIENT = "client"
DATA_COORDINATOR = "coordinator"
DATA_PING_COORDINATOR = "ping_coordinator"
CONF_TRACKED_CLIENTS = "tracked_clients"
CONF_USE_CHALLENGE_AUTH = "use_challenge_auth"
CONF_CONNECTION_MODE = "connection_mode"
CONNECTION_MODE_DIRECT = "direct"
CONNECTION_MODE_KEENDNS_PROTECTED = "keendns_protected"
EVENT_NEW_DEVICE = f"{DOMAIN}_new_device"

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

# RCI endpoint paths used in more than one place.
RCI_SHOW_VERSION = "show/version"
RCI_HOTSPOT_HOST_PATHS = ("show/ip/hotspot/host", "ip/hotspot/host")
