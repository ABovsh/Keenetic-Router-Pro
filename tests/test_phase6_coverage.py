"""Phase 6 coverage tests for reachable diagnostic and API behavior."""

from __future__ import annotations

from conftest import (
    TEST_BASE_URL_ALT,
    TEST_HOST,
    TEST_HOST_ALT,
    TEST_PASSWORD,
    TEST_USERNAME,
)

from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.keenetic_router_pro.api import (
    KeeneticApiError,
    KeeneticAuthError,
    KeeneticClient,
)
from custom_components.keenetic_router_pro.api.domains.dns import _redact_doh_uri
from custom_components.keenetic_router_pro.entity import (
    ClientEntity,
    ControllerEntity,
    CryptoMapEntity,
    InterfaceEntity,
    MeshEntity,
    WanEntity,
)
from custom_components.keenetic_router_pro.sensor.client import (
    KeeneticClientConnectionTypeSensor,
    KeeneticClientIpSensor,
    KeeneticClientLastSeenSensor,
    KeeneticClientRssiSensor,
    KeeneticClientRxSensor,
    KeeneticClientTxRateSensor,
    KeeneticClientTxSensor,
    KeeneticClientUptimeSensor,
    KeeneticClientWifiBandSensor,
    KeeneticClientWifiModeSensor,
)
from custom_components.keenetic_router_pro.sensor.crypto import (
    KeeneticCryptoMapIkeStateSensor,
    KeeneticCryptoMapRxBytesSensor,
    KeeneticCryptoMapRxThroughputSensor,
    KeeneticCryptoMapStateSensor,
    KeeneticCryptoMapTxBytesSensor,
    KeeneticCryptoMapTxThroughputSensor,
)
from custom_components.keenetic_router_pro.sensor.clients import (
    KeeneticConnectedClientsSensor,
    KeeneticDisconnectedClientsSensor,
    KeeneticExtenderCountSensor,
    KeeneticRouterClientsSensor,
)
from custom_components.keenetic_router_pro.sensor.dns import (
    KeeneticDnsProxyFailedRequestsSensor,
    KeeneticDnsProxyStatusSensor,
)
from custom_components.keenetic_router_pro.sensor.ipsec import (
    KeeneticIpsecViciOomTotalSensor,
)
from custom_components.keenetic_router_pro.sensor.system import (
    KeeneticFirmwareVersionSensor,
    KeeneticMemoryUsageSensor,
    KeeneticUptimeSensor,
)
from custom_components.keenetic_router_pro.sensor.wifi import (
    KeeneticWifi24RxSensor,
    KeeneticWifi24TemperatureSensor,
    KeeneticWifi24TxSensor,
    KeeneticWifi5RxSensor,
    KeeneticWifi5TemperatureSensor,
    KeeneticWifi5TxSensor,
)
from custom_components.keenetic_router_pro.utils import (
    get_client_device_info,
    get_crypto_map_device_info,
    get_main_device_info,
    get_mesh_device_info,
    get_vpn_interface_device_info,
    get_wan_device_info,
    mesh_unique_id,
    sanitize_mesh_id,
    usable_ip,
)


class _Response:
    def __init__(self, status: int = 200, text: str = "", headers: dict | None = None, json_data=None) -> None:
        self.status = status
        self.headers = headers or {}
        self._text = text
        self._json_data = json_data if json_data is not None else {}
        self.read_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def text(self) -> str:
        return self._text

    async def json(self):
        return self._json_data

    async def read(self) -> bytes:
        self.read_called = True
        return self._text.encode()


class _Session:
    def __init__(self, *, gets: list[_Response] | None = None, posts: list[_Response] | None = None) -> None:
        self.gets = list(gets or [])
        self.posts = list(posts or [])
        self.get_calls: list[dict] = []
        self.post_calls: list[dict] = []

    async def get(self, url: str, **kwargs):
        self.get_calls.append({"url": url, **kwargs})
        return self.gets.pop(0)

    async def post(self, url: str, **kwargs):
        self.post_calls.append({"url": url, **kwargs})
        return self.posts.pop(0)


def _entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_123", title="Router", data={})


def _coordinator(data: dict) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        last_update_success=True,
        client=SimpleNamespace(_host=TEST_HOST, _ssl=False),
    )


async def test_system_simple_methods_and_reboot_delegate_to_rci() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(side_effect=[{"hostname": "router"}, None, {"title": "4.2"}, None])
    client._rci_parse = AsyncMock()

    assert await client.async_get_system_info() == {"hostname": "router"}
    assert await client.async_get_system_info() == {}
    assert await client.async_get_current_version_info() == {"title": "4.2"}
    assert await client.async_get_available_version_info() == {}

    await client.async_reboot()
    client._rci_parse.assert_awaited_once_with("system reboot")


async def test_basic_auth_sets_header_and_reports_failures() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    session = _Session(gets=[_Response(200)])
    client._session = session

    await client._async_authenticate()

    assert client._authenticated is True
    assert client._auth_header["Authorization"].startswith("Basic ")
    assert session.get_calls[0]["headers"] == client._auth_header

    failing = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    failing._session = _Session(gets=[_Response(403, text="forbidden")])
    with pytest.raises(KeeneticAuthError, match="status 403"):
        await failing._async_authenticate()


async def test_challenge_auth_cookie_and_rejection_paths() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._session = _Session(
        gets=[
            _Response(
                401,
                headers={
                    "X-NDM-Challenge": "challenge",
                    "X-NDM-Realm": "Keenetic",
                    "Set-Cookie": "sysauth=old; path=/",
                },
            )
        ],
        posts=[_Response(204, headers={"Set-Cookie": "sysauth=new; path=/"})],
    )

    await client._async_authenticate_challenge()

    assert client._authenticated is True
    assert client._auth_header == {"Cookie": "sysauth=new"}
    assert client._session.post_calls[0]["headers"] == {"Cookie": "sysauth=old"}

    missing = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    missing._session = _Session(gets=[_Response(401)])
    with pytest.raises(KeeneticAuthError, match="X-NDM-Challenge"):
        await missing._async_authenticate_challenge()

    rejected = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    rejected._session = _Session(
        gets=[_Response(401, headers={"X-NDM-Challenge": "c", "X-NDM-Realm": "r"})],
        posts=[_Response(401)],
    )
    with pytest.raises(KeeneticAuthError, match="rejected"):
        await rejected._async_authenticate_challenge()


async def test_ensure_auth_uses_selected_authenticator() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, use_challenge_auth=True)

    async def authenticate() -> None:
        client._authenticated = True

    client._async_authenticate_challenge = AsyncMock(side_effect=authenticate)

    await client._ensure_auth()
    await client._ensure_auth()

    client._async_authenticate_challenge.assert_awaited_once()


def test_dns_and_ipsec_diagnostic_sensors_pin_icons_and_bad_counts() -> None:
    data = {
        "dns_proxy": {
            "status": "degraded",
            "failed_requests": "7",
            "client_path_uses_doh": True,
            "proxy_count": 1,
            "doh_server_count": 1,
            "dns_server_count": 2,
            "active_dns_server_count": 1,
            "requests_sent": 9,
            "proxies": [{"name": "Proxy"}],
        },
        "ipsec_diagnostics": {
            "status": "warning",
            "vici_out_of_memory_count": "3",
            "last_vici_out_of_memory": "now",
            "last_error_code": "12",
            "recent_matches": ["vici"],
            "scanned_log_lines": 100,
            "command": "show log",
        },
    }
    coordinator = _coordinator(data)
    entry = _entry()

    dns = KeeneticDnsProxyStatusSensor(coordinator, entry)
    assert dns.native_value == "degraded"
    assert dns.icon == "mdi:dns-outline"
    assert dns.extra_state_attributes["failed_requests"] == "7"
    assert KeeneticDnsProxyFailedRequestsSensor(coordinator, entry).native_value == 7

    data_with_total = dict(data)
    data_with_total["ipsec_diagnostics"] = dict(data["ipsec_diagnostics"])
    data_with_total["ipsec_diagnostics"]["oom_total"] = 42
    data_with_total["ipsec_diagnostics"]["oom_last_seen"] = "2026-05-27T17:33:48"
    coord_total = _coordinator(data_with_total)
    oom = KeeneticIpsecViciOomTotalSensor(coord_total, entry)
    assert oom.native_value == 42
    attrs = oom.extra_state_attributes
    assert attrs["last_event_router_time"] == "2026-05-27T17:33:48"

    empty = _coordinator({"dns_proxy": {}, "ipsec_diagnostics": {}})
    assert KeeneticDnsProxyStatusSensor(empty, entry).extra_state_attributes is None
    assert KeeneticDnsProxyFailedRequestsSensor(empty, entry).native_value is None
    assert KeeneticIpsecViciOomTotalSensor(empty, entry).native_value is None

    bad = _coordinator(
        {
            "dns_proxy": {"failed_requests": "bad", "status": "down"},
            "ipsec_diagnostics": {"oom_total": "bad"},
        }
    )
    assert KeeneticDnsProxyStatusSensor(bad, entry).icon == "mdi:dns-outline"
    assert KeeneticDnsProxyFailedRequestsSensor(bad, entry).native_value is None
    assert KeeneticIpsecViciOomTotalSensor(bad, entry).native_value is None


def test_system_and_wifi_sensors_cover_alternate_payload_shapes() -> None:
    coordinator = _coordinator(
        {
            "system": {
                "memtotal": "1000",
                "memfree": "250",
                "system": {"uptime_sec": "42"},
                "release": "4.2.1",
                "fw-update-sandbox": "stable",
                "arch": "mips",
                "ndm": {"exact": "ndm"},
                "bsp": {"exact": "bsp"},
            },
            "interfaces": {
                "WifiMaster0/AccessPoint0": {"temperature": "41.5"},
                "WifiMaster1/AccessPoint0": {"temperature": "bad"},
                "WifiMaster1/AccessPoint1": {"temperature": "43"},
            },
            "interface_stats": {
                "WifiMaster0": {"rxbytes": 2 * 1024**3, "txbytes": 3 * 1024**3},
                "WifiMaster1": {"rxbytes": 4 * 1024**3, "txbytes": 5 * 1024**3},
            },
        }
    )
    entry = _entry()

    assert KeeneticMemoryUsageSensor(coordinator, entry).native_value == pytest.approx(75.0)
    assert KeeneticUptimeSensor(coordinator, entry).native_value == 42
    firmware = KeeneticFirmwareVersionSensor(coordinator, entry)
    assert firmware.native_value == "4.2.1"
    assert firmware.extra_state_attributes == {
        "release": "4.2.1",
        "channel": "stable",
        "architecture": "mips",
        "ndm_version": "ndm",
        "bsp_version": "bsp",
    }

    assert KeeneticWifi24TemperatureSensor(coordinator, entry).native_value == pytest.approx(41.5)
    assert KeeneticWifi24TemperatureSensor(coordinator, entry).available is True
    assert KeeneticWifi5TemperatureSensor(coordinator, entry).native_value == pytest.approx(43.0)
    assert KeeneticWifi24RxSensor(coordinator, entry).native_value == pytest.approx(2.0)
    assert KeeneticWifi24TxSensor(coordinator, entry).native_value == pytest.approx(3.0)
    assert KeeneticWifi5RxSensor(coordinator, entry).native_value == pytest.approx(4.0)
    assert KeeneticWifi5TxSensor(coordinator, entry).native_value == pytest.approx(5.0)


def test_clients_summary_sensors_count_mesh_and_router_clients() -> None:
    coordinator = _coordinator(
        {
            "client_stats": {
                "connected": 5,
                "disconnected": 2,
                "total": 7,
                "per_ap": {"Guest": 2},
            },
            "mesh_associations": {"total": 3},
            "mesh_nodes": [
                {"name": "Extender 1", "ip": "192.0.2.2", "mode": "ap", "connected": True},
                {"name": "Extender 2", "ip": "192.0.2.3", "mode": "ap", "connected": False},
            ],
        }
    )
    entry = _entry()

    connected = KeeneticConnectedClientsSensor(coordinator, entry)
    assert connected.native_value == 5
    assert connected.extra_state_attributes == {"total": 7, "per_ap": {"Guest": 2}}

    router = KeeneticRouterClientsSensor(coordinator, entry)
    assert router.native_value == 2
    assert router.extra_state_attributes["mesh_clients"] == 3

    assert KeeneticDisconnectedClientsSensor(coordinator, entry).native_value == 2
    extenders = KeeneticExtenderCountSensor(coordinator, entry)
    assert extenders.native_value == 2
    assert extenders.extra_state_attributes["connected"] == 1


@pytest.mark.parametrize(
    ("client", "expected_type", "expected_band", "expected_icon"),
    [
        ({"mac": "aa:bb:cc:dd:ee:ff", "active": True, "port": 1, "speed": 1000}, "Ethernet (1000 Mbps)", None, "mdi:ethernet"),
        ({"mac": "aa:bb:cc:dd:ee:ff", "active": True, "mws": {"ap": "WifiMaster1/AccessPoint0", "mode": "11ax", "ht": "80", "security": "wpa3", "authenticated": True, "roam": True}}, "WiFi 5 GHz (Mesh)", "5 GHz", "mdi:wifi-strength-4"),
        ({"mac": "aa:bb:cc:dd:ee:ff", "active": True, "ssid": "Guest", "ap": "WifiMaster0/AccessPoint0", "mode": "11n"}, "WiFi 2.4 GHz - Guest", "2.4 GHz", "mdi:wifi"),
        ({"mac": "aa:bb:cc:dd:ee:ff", "active": True, "interface": {"name": "GigabitEthernet0/Vlan1"}}, "Ethernet", None, "mdi:ethernet"),
        ({"mac": "aa:bb:cc:dd:ee:ff", "active": True, "txrate": "866"}, "WiFi 5 GHz (likely)", "5 GHz", "mdi:wifi-strength-4"),
    ],
)
def test_client_connection_sensors_pin_type_band_and_icon(client, expected_type, expected_band, expected_icon) -> None:
    coordinator = _coordinator({"clients_by_mac": {"aa:bb:cc:dd:ee:ff": client}})
    entry = _entry()

    connection = KeeneticClientConnectionTypeSensor(coordinator, entry, "aa:bb:cc:dd:ee:ff", "Laptop")
    assert connection.native_value == expected_type
    assert connection.icon == expected_icon
    band = KeeneticClientWifiBandSensor(coordinator, entry, "aa:bb:cc:dd:ee:ff", "Laptop")
    assert band.native_value == expected_band
    assert band.icon in {"mdi:wifi", "mdi:wifi-strength-4", "mdi:wifi-off"}


def test_client_detail_sensors_pin_online_offline_and_counter_edges() -> None:
    mac = "aa:bb:cc:dd:ee:ff"
    online = {
        "mac": mac,
        "active": True,
        "ip": "192.0.2.50",
        "uptime": "60",
        "last-seen": "5",
        "rxbytes": 1024**3,
        "txbytes": 2 * 1024**3,
        "rssi": "-55",
        "txrate": "300",
        "mode": "11ac",
    }
    entry = _entry()
    coordinator = _coordinator({"clients_by_mac": {mac: online}})

    assert KeeneticClientIpSensor(coordinator, entry, mac, "Laptop").native_value == "192.0.2.50"
    assert KeeneticClientUptimeSensor(coordinator, entry, mac, "Laptop").native_value == 60
    assert KeeneticClientLastSeenSensor(coordinator, entry, mac, "Laptop").available is False
    assert KeeneticClientRxSensor(coordinator, entry, mac, "Laptop").native_value == pytest.approx(1.0)
    assert KeeneticClientTxSensor(coordinator, entry, mac, "Laptop").native_value == pytest.approx(2.0)
    assert KeeneticClientRssiSensor(coordinator, entry, mac, "Laptop").native_value == -55
    assert KeeneticClientTxRateSensor(coordinator, entry, mac, "Laptop").native_value == 300
    mode = KeeneticClientWifiModeSensor(coordinator, entry, mac, "Laptop")
    assert mode.native_value == "11AC"
    assert mode.icon == "mdi:wifi-strength-3"

    offline = dict(online, active=False, link="down", rxbytes="0", txbytes="")
    coordinator.data = {"clients_by_mac": {mac: offline}}
    assert KeeneticClientRxSensor(coordinator, entry, mac, "Laptop").available is False
    assert KeeneticClientRxSensor(coordinator, entry, mac, "Laptop").native_value is None
    assert KeeneticClientTxSensor(coordinator, entry, mac, "Laptop").native_value is None
    assert KeeneticClientLastSeenSensor(coordinator, entry, mac, "Laptop").available is True
    assert KeeneticClientLastSeenSensor(coordinator, entry, mac, "Laptop").native_value is not None

    missing = _coordinator({})
    assert KeeneticClientUptimeSensor(missing, entry, mac, "Laptop").native_value == 0
    assert KeeneticClientRssiSensor(missing, entry, mac, "Laptop").native_value is None
    assert KeeneticClientConnectionTypeSensor(missing, entry, mac, "Laptop").native_value == "unknown"
    assert KeeneticClientWifiModeSensor(missing, entry, mac, "Laptop").native_value is None


def test_doh_uri_redaction_handles_private_and_invalid_shapes() -> None:
    assert _redact_doh_uri("https://user:pass@example.test/private/id?token=secret") == "https://example.test/"
    assert _redact_doh_uri("https://example.test:8443/path") == "https://example.test:8443/"
    assert _redact_doh_uri("") == ""
    assert _redact_doh_uri("https://[bad") == ""


def test_device_info_helpers_pin_configuration_urls_and_fallbacks() -> None:
    assert usable_ip("0.0.0.0") is None
    assert usable_ip("192.0.2.5") == "192.0.2.5"
    assert sanitize_mesh_id("aa:bb-cc dd") == "aa_bb_cc_dd"
    assert mesh_unique_id("entry", "aa:bb", "uptime") == "entry_mesh_aa_bb_uptime"

    assert get_main_device_info("Router", "entry", "4.2", "KN", TEST_HOST, True)[
        "configuration_url"
    ] == "https://192.0.2.1"
    assert get_main_device_info("Router", "entry", None, None, TEST_HOST, False, "https://router.example/path")[
        "configuration_url"
    ] == "http://router.example"

    mesh = get_mesh_device_info(
        "Router",
        "entry",
        {"name": "Node", "ip": "192.0.2.2", "model": "Ext", "firmware": "4.2"},
        "aa:bb",
        ssl=True,
        fqdn="node.example",
    )
    assert mesh["configuration_url"] == "https://node.example"
    assert get_mesh_device_info("Router", "entry", None, None, TEST_HOST)[
        "identifiers"
    ] == {("keenetic_router_pro", "entry")}

    assert get_wan_device_info("Router", "entry", "PPPoE0", "ISP", "PPPoE", "primary")[
        "name"
    ] == "Router — ISP (primary)"
    assert get_vpn_interface_device_info("Router", "entry", "Wireguard0", "WG", "wireguard")[
        "model"
    ] == "WIREGUARD interface"
    assert get_crypto_map_device_info("Router", "entry", "Office", "198.51.100.1")[
        "name"
    ] == "Router — IPsec Office → 198.51.100.1"

    client = get_client_device_info(
        "entry",
        "Router",
        "aa:bb:cc:dd:ee:ff",
        "Laptop",
        {"hostname": "laptop.local", "ip": TEST_HOST_ALT, "ssdp": {"manufacturer": "Acme", "model": "Book"}},
    )
    assert client["name"] == "laptop.local (Router)"
    assert client["manufacturer"] == "Acme"
    assert client["configuration_url"] == TEST_BASE_URL_ALT


def test_crypto_map_sensors_pin_state_counters_and_missing_tunnel() -> None:
    coordinator = _coordinator(
        {
            "crypto_maps": {
                "Office": {
                    "state": "PHASE2_ESTABLISHED",
                    "ike_state": "ESTABLISHED",
                    "local_endpoint": TEST_HOST,
                    "remote_endpoint": "198.51.100.1",
                    "remote_peer": "198.51.100.1",
                    "rx_bytes": "100",
                    "tx_bytes": "200",
                    "rx_throughput": "3.5",
                    "tx_throughput": "4",
                }
            }
        }
    )
    entry = _entry()

    state = KeeneticCryptoMapStateSensor(coordinator, entry, "Office")
    assert state.unique_id == "entry_123_cmap_Office_state"
    assert state.native_value == "PHASE2_ESTABLISHED"
    assert state.extra_state_attributes == {
        "local_endpoint": TEST_HOST,
        "remote_endpoint": "198.51.100.1",
    }
    assert KeeneticCryptoMapIkeStateSensor(coordinator, entry, "Office").native_value == "ESTABLISHED"
    assert KeeneticCryptoMapRxBytesSensor(coordinator, entry, "Office").native_value == 100
    assert KeeneticCryptoMapTxBytesSensor(coordinator, entry, "Office").native_value == 200
    assert KeeneticCryptoMapRxThroughputSensor(coordinator, entry, "Office").native_value == pytest.approx(28.0)
    assert KeeneticCryptoMapTxThroughputSensor(coordinator, entry, "Office").native_value == pytest.approx(32.0)

    missing = _coordinator({"crypto_maps": {}})
    assert KeeneticCryptoMapStateSensor(missing, entry, "Office").native_value is None
    assert KeeneticCryptoMapStateSensor(missing, entry, "Office").extra_state_attributes is None
    assert KeeneticCryptoMapRxBytesSensor(missing, entry, "Office").native_value is None
    assert KeeneticCryptoMapRxThroughputSensor(missing, entry, "Office").native_value is None


def test_entity_base_classes_pin_device_info_lookup_and_availability() -> None:
    data = {
        "system": {"ndw4": {"version": "4.2"}, "description": "Hero"},
        "ndns": {"name": "router", "domain": "keenetic.pro"},
        "mesh_nodes": [{"cid": "node-1", "name": "Node", "ip": "192.0.2.2", "fqdn": "node.example"}],
        "wan_interfaces": [{"id": "PPPoE0", "description": "ISP", "type": "PPPoE", "role_label": "primary"}],
        "crypto_maps": {"Office": {"remote_peer": "198.51.100.1"}},
        "clients": [{"mac": "aa:bb:cc:dd:ee:ff", "name": "Laptop", "ip": "0.0.0.0", "active": True}],
    }
    coordinator = _coordinator(data)

    controller = ControllerEntity(coordinator, "entry_123", "Router")
    assert controller._firmware_version == "4.2"
    assert controller._model_name == "Hero"
    assert controller.device_info["configuration_url"] == "http://router.keenetic.pro"

    mesh = MeshEntity(coordinator, "entry_123", "Router", "node-1")
    assert mesh._mesh_unique_id("uptime") == "entry_123_mesh_node_1_uptime"
    assert mesh.available is True
    assert mesh.device_info["configuration_url"] == "http://node.example"

    missing_mesh = MeshEntity(coordinator, "entry_123", "Router", "missing")
    assert missing_mesh.available is False
    assert missing_mesh.device_info["identifiers"] == {
        ("keenetic_router_pro", "entry_123")
    }

    wan = WanEntity(coordinator, "entry_123", "Router", "PPPoE0")
    assert wan.available is True
    assert wan.device_info["name"] == "Router — ISP (primary)"

    iface_as_wan = InterfaceEntity(coordinator, "entry_123", "Router", "PPPoE0")
    assert iface_as_wan.device_info["model"] == "WAN (PPPoE)"
    iface_vpn = InterfaceEntity(coordinator, "entry_123", "Router", "Wireguard0", "WG", "wireguard")
    assert iface_vpn.device_info["model"] == "WIREGUARD interface"

    cmap = CryptoMapEntity(coordinator, "entry_123", "Router", "Office")
    assert cmap.available is True
    assert cmap.device_info["name"] == "Router — IPsec Office → 198.51.100.1"
    coordinator.data["crypto_maps"] = []
    assert cmap.available is False

    client = ClientEntity(coordinator, "entry_123", "Router", "aa-bb-cc-dd-ee-ff", "Fallback", "192.0.2.50")
    assert client.available is True
    assert client.ip_address == "192.0.2.50"
    assert client.hostname == "Laptop"
    assert client.is_connected is True
    assert client.device_info["name"] == "Laptop (Router)"
    assert client._client_fingerprint({"last-seen": 1, "uptime": 2, "name": "Laptop"}) == {"name": "Laptop"}

    coordinator.data["clients"] = [{"mac": "aa:bb:cc:dd:ee:ff", "hostname": "host.local"}]
    assert client.hostname == "host.local"
    coordinator.data["clients"] = []
    assert client.hostname == "Fallback"


def test_client_sensor_metadata_and_remaining_connection_branches() -> None:
    mac = "aa:bb:cc:dd:ee:ff"
    entry = _entry()
    coordinator = _coordinator(
        {
            "clients_by_mac": {
                mac: {
                    "mac": mac,
                    "active": True,
                    "ssid": "Office",
                    "txrate": "866",
                    "mode": "11ax",
                    "duplex": False,
                    "port": None,
                }
            }
        }
    )

    sensors = [
        KeeneticClientIpSensor(coordinator, entry, mac, "Laptop"),
        KeeneticClientUptimeSensor(coordinator, entry, mac, "Laptop"),
        KeeneticClientLastSeenSensor(coordinator, entry, mac, "Laptop"),
        KeeneticClientRxSensor(coordinator, entry, mac, "Laptop"),
        KeeneticClientTxSensor(coordinator, entry, mac, "Laptop"),
        KeeneticClientRssiSensor(coordinator, entry, mac, "Laptop"),
        KeeneticClientTxRateSensor(coordinator, entry, mac, "Laptop"),
        KeeneticClientConnectionTypeSensor(coordinator, entry, mac, "Laptop"),
        KeeneticClientWifiBandSensor(coordinator, entry, mac, "Laptop"),
        KeeneticClientWifiModeSensor(coordinator, entry, mac, "Laptop"),
    ]
    assert [sensor.name for sensor in sensors] == [
        "IP",
        "Wi-Fi Session",
        "Last Seen",
        "RX",
        "TX",
        "RSSI",
        "Link Speed",
        "Connection Type",
        "WiFi Band",
        "WiFi Mode",
    ]
    assert sensors[1].native_unit_of_measurement == "seconds"
    assert sensors[3].native_unit_of_measurement == "gigabytes"
    assert sensors[5].native_unit_of_measurement == "dBm"
    assert sensors[6].native_unit_of_measurement == "Mbps"
    assert sensors[7].native_value == "WiFi 5 GHz - Office"
    assert sensors[7].extra_state_attributes == {"ssid": "Office", "mode": "11ax", "duplex": "Half"}
    assert sensors[9].icon == "mdi:wifi-strength-4"

    coordinator.data = {"clients_by_mac": {mac: {"mac": mac, "active": True, "mws": {"ap": "MeshAp", "mode": "11n"}}}}
    mesh_connection = KeeneticClientConnectionTypeSensor(coordinator, entry, mac, "Laptop")
    assert mesh_connection.native_value == "WiFi (Mesh) - MeshAp"
    assert mesh_connection.icon == "mdi:wifi-question"
    assert mesh_connection.extra_state_attributes["mode"] == "11n"
    assert KeeneticClientWifiModeSensor(coordinator, entry, mac, "Laptop").icon == "mdi:wifi-strength-2"

    coordinator.data = {"clients_by_mac": {mac: {"mac": mac, "active": True, "interface": "WifiMaster0/AccessPoint0"}}}
    assert KeeneticClientConnectionTypeSensor(coordinator, entry, mac, "Laptop").native_value == "WiFi 2.4 GHz"
    assert KeeneticClientConnectionTypeSensor(coordinator, entry, mac, "Laptop").icon == "mdi:wifi"

    coordinator.data = {"clients_by_mac": {mac: {"mac": mac, "active": True, "mode": "11g"}}}
    assert KeeneticClientWifiModeSensor(coordinator, entry, mac, "Laptop").icon == "mdi:wifi-strength-1"


async def test_clients_domain_pins_endpoint_fallbacks_and_policy_commands() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(
        side_effect=[
            KeeneticApiError("404 missing"),
            {"host": [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.0.2.50"}]},
            {"Policy0": {"description": "Default"}, "Policy1": "ignored"},
            {"host": [{"mac": "AA-BB-CC-DD-EE-FF", "policy": "Policy0", "access": "permit"}, "bad"]},
        ]
    )

    assert await client.async_get_clients() == [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.0.2.50"}]
    assert client._hotspot_subpath_skip
    assert await client.async_get_policies() == {"Policy0": "Default"}
    assert await client.async_get_host_policies() == {
        "aa:bb:cc:dd:ee:ff": {"policy": "Policy0", "access": "permit"}
    }

    client._rci_parse = AsyncMock(return_value={"neighbour": [{"mac": "AA-BB-CC-DD-EE-FF", "address": "192.0.2.50"}, {"mac": "bad"}]})
    client._rci_get = AsyncMock(return_value={})
    assert await client.async_get_ip_neighbours() == [
        {"mac": "AA-BB-CC-DD-EE-FF", "address": "192.0.2.50"}
    ]

    client._rci_parse = AsyncMock()
    await client.async_set_client_policy("AA-BB-CC-DD-EE-FF", "deny")
    assert [call.args[0] for call in client._rci_parse.await_args_list] == [
        "ip hotspot host aa:bb:cc:dd:ee:ff deny",
        "system configuration save",
    ]

    client._rci_parse = AsyncMock()
    await client.async_set_client_policy("AA-BB-CC-DD-EE-FF", "default")
    assert [call.args[0] for call in client._rci_parse.await_args_list] == [
        "no ip hotspot host aa:bb:cc:dd:ee:ff policy",
        "ip hotspot host aa:bb:cc:dd:ee:ff permit",
        "system configuration save",
    ]


async def test_wan_network_vpn_wifi_domain_branches_pin_router_shapes() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    iface_list = [
        {
            "id": "PPPoE0",
            "type": "PPPoE",
            "state": "up",
            "address": "198.51.100.2/32",
            "global": True,
            "priority": 100,
            "role": ["inet"],
            "summary": {"layer": {"conf": "running", "ipv4": "running"}},
            "rx-speed": 8 * 1024 * 1024,
            "tx-speed": 16 * 1024 * 1024,
            "rx-bytes": 10,
            "tx-bytes": 20,
            "wireguard": {"peer": {"remote-endpoint-address": "203.0.113.1", "rxbytes": 7, "txbytes": 8}},
        },
        {
            "id": "Wireguard0",
            "type": "Wireguard",
            "state": "up",
            "description": "WG",
            "global": True,
            "priority": 90,
            "summary": {"layer": {"conf": "running", "ipv4": "pending"}},
            "wireguard": {"peer": [{"remote-endpoint-address": "203.0.113.2", "rxbytes": 9, "txbytes": 10}]},
        },
        {"id": "Bridge0", "type": "Bridge", "interface-name": "Home"},
        {"id": "WifiMaster0/AccessPoint0", "type": "AccessPoint", "state": "up", "group": "Bridge0", "ssid": "", "channel": "6"},
        {"id": "WifiMaster1/AccessPoint0", "type": "AccessPoint", "state": "down", "group": "Bridge0", "ssid": "Home", "channel": "36", "tx-power": 50},
        {"id": "0", "type": "Port", "label": "0", "link": "up", "speed": 1000, "duplex": True},
    ]

    wan_status = await client.async_get_wan_status(iface_list=iface_list)
    assert wan_status["status"] == "connected"
    assert wan_status["ip"] == "198.51.100.2"

    wans = await client.async_get_wan_interfaces(iface_list=iface_list)
    assert {wan["id"] for wan in wans} == {"PPPoE0", "Wireguard0"}
    # Up + global but ipv4 pending (no usable address) = no internet (False),
    # not None/unavailable.
    assert next(wan for wan in wans if wan["id"] == "Wireguard0")["internet_access"] is False

    traffic = await client.async_get_traffic_stats(iface_list=iface_list)
    assert traffic == {
        "download_speed": 1.0,
        "upload_speed": 2.0,
        "total_rx": 10,
        "total_tx": 20,
    }

    ports = await client.async_get_port_info({"0": iface_list[-1]})
    assert ports == [{"label": "0", "appearance": "Port", "link": "up", "speed": 1000, "duplex": True}]

    wg = await client.async_get_wireguard_status(iface_list=iface_list)
    assert wg["profiles"]["Wireguard0"]["remote"] == "203.0.113.2"
    tunnels = await client.async_get_vpn_tunnels(iface_list=iface_list)
    assert tunnels["profiles"]["Wireguard0"]["enabled"] is True

    wifi = await client.async_get_wifi_networks(iface_list=iface_list)
    assert [(item["name"], item["band"], item["enabled"]) for item in wifi] == [
        ("Home 2.4 GHz", "2.4 GHz", True),
        ("Home 5 GHz", "5 GHz", False),
    ]

    client._rci_parse = AsyncMock()
    await client.async_set_wifi_enabled("WifiMaster0/AccessPoint0", False)
    await client.async_set_wireguard_enabled("Wireguard0", True)
    assert [call.args[0] for call in client._rci_parse.await_args_list] == [
        "interface WifiMaster0/AccessPoint0 down",
        "interface Wireguard0 up",
    ]


async def test_dns_proxy_status_handles_collapsed_doh_and_latches_missing_endpoint() -> None:
    client = KeeneticClient(TEST_HOST, TEST_USERNAME, TEST_PASSWORD)
    client._rci_get = AsyncMock(
        return_value={
            "proxy-status": [
                {
                    "proxy-name": "Proxy",
                    "proxy-config": "https://dns.example/doh",
                    "proxy-stat": "8.8.8.8 53 100 80 10 20ms 30ms 1",
                    "proxy-https": {
                        "server-https": {"uri": "https://nextdns.io/private-id?x=1"}
                    },
                },
                "ignored",
            ]
        }
    )

    result = await client.async_get_dns_proxy_status()

    assert result["status"] == "degraded"
    assert result["doh_server_count"] == 1
    assert result["failed_requests"] == 10
    assert result["proxies"][0]["configured_doh_uris"] == ["https://nextdns.io/"]

    client._rci_get = AsyncMock(side_effect=aiohttp.ClientError("404 missing"))
    assert await client.async_get_dns_proxy_status() == {}
    assert client._dns_proxy_supported is False
    assert await client.async_get_dns_proxy_status() == {}
