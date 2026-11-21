<p align="center"><img src="https://brands.home-assistant.io/keenetic_router_pro/icon.png" width="80" height="80" alt=""></p>

<h1 align="center">Keenetic Router Pro</h1>

<p align="center">Know when your internet switches providers, when a device comes home,<br>and when a mesh extender goes offline. All from Home Assistant.</p>

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ABovsh&repository=Keenetic-Router-Pro&category=integration)
![Version](https://img.shields.io/badge/version-1.20.0-blue?style=for-the-badge)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.5%2B-41BDF5?style=for-the-badge&logo=home-assistant)

[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_Keenetic-Router-Pro&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=ABovsh_Keenetic-Router-Pro)
[![Reliability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_Keenetic-Router-Pro&metric=reliability_rating)](https://sonarcloud.io/component_measures?id=ABovsh_Keenetic-Router-Pro&metric=reliability_rating)
[![Security](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_Keenetic-Router-Pro&metric=security_rating)](https://sonarcloud.io/component_measures?id=ABovsh_Keenetic-Router-Pro&metric=security_rating)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_Keenetic-Router-Pro&metric=sqale_rating)](https://sonarcloud.io/component_measures?id=ABovsh_Keenetic-Router-Pro&metric=sqale_rating)
[![Coverage](https://img.shields.io/sonar/coverage/ABovsh_Keenetic-Router-Pro?server=https%3A%2F%2Fsonarcloud.io&style=for-the-badge&logo=sonarcloud&label=coverage)](https://sonarcloud.io/component_measures?id=ABovsh_Keenetic-Router-Pro&metric=coverage)

Home Assistant integration for **Keenetic routers** and their **mesh extenders**.
It shows each internet uplink, the devices on your network, VPN tunnels and the
router's own health, and lets you control Wi-Fi, uplinks, tunnels and client
access. Home Assistant talks to the router's web interface on your network;
there is no cloud dependency and no extra Python package to install.

**Contents:** [Features](#features) · [Requirements](#requirements) ·
[Install](#installation) · [Setup](#setup) · [First steps](#first-steps) ·
[Options](#options) · [Automations](#automations) · [Devices and entities](#devices-and-entities) ·
[Update cadence](#how-often-readings-update) · [Help](#troubleshooting) · [Changelog](CHANGELOG.md)

## Features

### 🌐 Internet uplinks and failover

- **Every uplink as its own device:** provider, role (main or backup), public IP, uptime, traffic and live throughput.
- **Real internet checks:** **Connected** follows the router's ping check where one is set up, otherwise link and IP state, so a line that is up without internet can show as disconnected.
- **Failover tracking:** **WAN Status**, **WAN Failover Count** and a device trigger when the router switches to another uplink.
- **Downtime per provider:** each uplink's **Downtime** and the router's **Internet Downtime** (no uplink had internet) add up over time, so you can compare providers by month.
- **Turn an uplink on or off** with its **Enabled** switch, for example to test a backup line.

### 📱 Devices on your network

- **Presence for the devices you pick:** a device tracker per tracked client, using the router's own view of who is connected.
- **Counts for the whole network:** total, connected and disconnected clients, and clients on the main router versus the extenders.
- **Per-client details:** IP, Wi-Fi session, last seen and connection type; with full detail also traffic, signal (RSSI), link rate, Wi-Fi band and mode.
- **Connect and disconnect triggers** for automations, even with per-client sensors off.

### 🔒 Client access control

- **Connection policy per client:** route a device through a VPN policy, back to **Default**, or **Deny (Blocked)** to cut its internet access.
- **Bandwidth limit per client** in kbit/s (disabled by default; enable the entity to use it).

### 📡 Mesh Wi-Fi system

- **Each extender as its own device:** online state, uptime, CPU, memory, clients served, IP address and Ethernet ports.
- **Mesh System State** on the main router.
- **Firmware updates and reboot** for every extender from Home Assistant.

### 🛡️ VPN tunnels

- **WireGuard, OpenVPN and other tunnels** are found automatically, with an **Enabled** switch; WireGuard adds uptime and traffic, and a tunnel used as an uplink gets the full uplink device.
- **IPsec site-to-site tunnels:** tunnel state, IKE state, connected, traffic and throughput, with an on/off switch.

### 🖥️ Router health and Wi-Fi

- **CPU, memory and uptime;** the installed firmware version is on the **Firmware Update** entity.
- **Wi-Fi radios:** temperature and traffic for 2.4 GHz and 5 GHz; a switch for each Wi-Fi network.
- **Ethernet ports:** link state, with speed and duplex while the link is up.
- **LAN and WAN traffic** totals for the router.

### 🔄 Firmware and maintenance

- **Firmware updates** for the router and extenders through Home Assistant's update entities, with the model's release notes and, on the router, install progress.
- **Reboot** the router or any extender with a button.
- **Daily update check:** the integration asks the router once a day whether new firmware is out.

### 🩺 Problems explained

- **Repairs** notices when the router refuses the API address after KeeneticOS 5.1.9, when the connection uses plaintext HTTP, and when the router lacks components for some features.
- **Reauthentication and reconfiguration** without removing the integration.
- **Diagnostics download** with passwords, addresses, MAC addresses and network names redacted.

Also supported: **several routers** (one entry each), **automatic discovery** of Keenetic routers on your network, and a light recorder footprint: long-term statistics only for measured quantities, and readings that change too little to matter are not written again.

Available entities depend on your router model and the KeeneticOS components installed on it.

## Requirements

| Requirement | Details |
| --- | --- |
| Home Assistant | 2024.5 or newer |
| Router | A Keenetic router with the web interface reachable from Home Assistant (LAN or VPN) |
| Router account | An account with **Web interface** permission; a dedicated account is recommended |
| KeeneticOS 5.1.9 or newer | Connect through the web interface address with **Use challenge authentication** on |
| Extenders | Keenetic extenders in the router's mesh Wi-Fi system appear automatically |

## Installation

### HACS (recommended)

The integration is a HACS **custom repository**.

[![Open Keenetic Router Pro in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ABovsh&repository=Keenetic-Router-Pro&category=integration)

1. Open the link above, or in HACS open **⋮ → Custom repositories**, paste the URL below and choose **Integration**.
2. Download **Keenetic Router Pro**.
3. Restart Home Assistant.

```text
https://github.com/ABovsh/Keenetic-Router-Pro
```

HACS downloads the release asset named `keenetic_router_pro.zip` from each published version.

### Manual

1. Download `keenetic_router_pro.zip` from the [latest release](https://github.com/ABovsh/Keenetic-Router-Pro/releases/latest).
2. Unpack it into `custom_components/keenetic_router_pro/` in your Home Assistant configuration directory.
3. Restart Home Assistant.

## Setup

[![Add Keenetic Router Pro to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=keenetic_router_pro)

Home Assistant may also offer a discovered Keenetic router under **Settings → Devices & services**.
Otherwise open **Add integration** and search for **Keenetic Router Pro**.

1. Choose **Direct / local** and enter the address you use for the router's web interface. If you changed its web port, use that port here.
   - **HTTPS (recommended):** enter the router's KeenDNS name, set port `443` and turn on **Use SSL**. On your LAN, that name must resolve to the router's LAN address. An IP address does not work with SSL, because the router's certificate is issued for its KeenDNS name.
   - **HTTP on your LAN or VPN:** enter the router's LAN address, such as `192.168.1.1`, set port `80` and leave **Use SSL** off. Home Assistant shows a Repair notice for plaintext HTTP.
2. Sign in with a router account that has **Web interface** permission and turn on **Use challenge authentication**.
3. Select the clients to track. You can change the list later.

To change settings later, open the integration entry:

- **Configure:** tracked clients and how many sensors each one gets.
- **Reconfigure:** address, port, SSL, credentials and connection mode.

<details>
<summary>Connecting from outside your home</summary>

Over a VPN, use the same steps as on your LAN. For direct internet access, use the router's own KeenDNS name and HTTPS. On the router, allow internet access to the web interface over **HTTPS only**, use a dedicated account with a strong password, and restrict access to Home Assistant's source address where possible.

</details>

<details>
<summary>Using a KeenDNS protected web app on KeeneticOS 5.1.8 or older</summary>

On the router, publish **This Keenetic device** as a password-protected web app. Choose **HTTP** and the internal port that serves the API. Give a dedicated account **HTTP Proxy** permission. This account can issue management commands.

In Home Assistant, choose **KeenDNS protected web app** and enter the app hostname and credentials. This mode uses HTTPS on port 443 with Basic Auth. For KeeneticOS 5.1.9 or newer, use **Direct / local**.

</details>

## First steps

1. Open the router under **Settings → Devices & services → Keenetic Router Pro**. Check that **CPU Load**, **Uptime** and **WAN Status** show values.
2. Open an uplink device and check that **Connected** is on for the line you use.
3. Open a tracked client and check its device tracker shows **Home**.

**Get a notification when backup internet takes over**

1. Create an automation and add a **Device** trigger.
2. Select your router and choose **switches to a different WAN**.
3. Add a notification action for your phone and save.

**See which provider had the most downtime**

Add each uplink's **Downtime** sensor to a statistics card and compare the change over the same month. **Internet Downtime** measures how long *all* uplinks lacked internet. Time when Home Assistant couldn't read the router is not counted.

## Options

Open the integration entry and choose **Configure**.

| Option | What it does |
| --- | --- |
| **Clients to track** | Devices that get a device tracker, a connection policy selector and a bandwidth limit |
| **Per-client sensors** | How many sensors each tracked client gets (see below) |

| Per-client sensors | Sensors per tracked client |
| --- | --- |
| `full` (default) | IP, Wi-Fi session, last seen, connection type, RX, TX, RSSI, TX rate, Wi-Fi band and Wi-Fi mode |
| `basic` | IP, Wi-Fi session, last seen and connection type |
| `off` | None; presence, connection policy, bandwidth limit and client triggers keep working |


## Automations

The router device offers these **device triggers**:

| Trigger | Fires when |
| --- | --- |
| **A client connects to …** | A device joins the network |
| **A client disconnects from …** | A device leaves the network |
| **… switches to a different WAN** | The router moves internet traffic to another uplink |

Other useful automation inputs:

- **Uplink Connected** (`binary_sensor`) for "no internet on the main line" alerts.
- **Extender Connected** (`binary_sensor`) for an extender that went offline.
- **Update available** (`binary_sensor`) or the **Firmware Update** entity for new firmware.
- **Connection Policy** (`select`) and **Enabled** switches as actions, for example to cut a child's device off at night.

## Devices and entities

The integration creates one router device and child devices for what it finds.
Entity IDs start with the device name, for example `sensor.keenetic_giga_cpu_load`.

| Device | Main entities |
| --- | --- |
| **Router** | CPU Load, Memory Usage, Uptime, WAN Status, PPPoE Uptime, WAN Failover Count, Internet Downtime, Total Connected Clients, Connected Clients, Clients, Mesh System State, Port 0…n, LAN/WAN RX/TX, Wi-Fi 2.4/5 GHz Temperature and RX/TX, Wi-Fi network switches, WireGuard tunnel uptime and RX/TX, Update available, Firmware Update, Reboot Router |
| **Uplink (WAN)**, including a VPN tunnel used as an uplink | Connected, Provider, Role, Interface, Public IP, Uptime, Downtime, RX/TX Bytes, RX/TX Throughput, Enabled switch |
| **Other VPN tunnel** | Enabled switch; WireGuard uptime and RX/TX appear on the router device |
| **IPsec tunnel** | Connected, Tunnel state, IKE state, RX/TX Bytes, RX/TX Throughput, on/off switch |
| **Mesh extender** | Connected (with the extender's IP), Uptime, CPU Load, Memory Usage, Clients, Port 0…n, Update available, Firmware Update, Reboot |
| **Tracked client** | Device tracker, Connection Policy, Bandwidth Limit, and the per-client sensors chosen in [Options](#options) |

Some entities start disabled because most users don't need them: WAN RX/TX, PPPoE Uptime, an uplink's Provider, Role and Interface, a tunnel's Downtime, per-client RX/TX and TX rate, and the bandwidth limit. Enable them on the entity's settings page.

## How often readings update

| Every | Readings |
| --- | --- |
| 1 minute | Link and uplink state, internet checks, failover, client presence |
| 2 minutes | Traffic and throughput, CPU, memory, connections, Wi-Fi, VPN tunnels, ports |
| 3 minutes | Mesh extenders, IPsec tunnels, client connection policies |
| 15 minutes | Firmware version, KeenDNS, the list of connection policies |
| 1 day | Firmware update check |

A change you make from Home Assistant, such as a new policy or an extender update, is read back right away.

## Troubleshooting

<details>
<summary>The router refuses the configured API address</summary>

If this Repair appears after upgrading to KeeneticOS 5.1.9, open **Reconfigure**. Replace the old port-79 forward or protected app with the router's web address, choose **Direct / local**, and turn on challenge authentication.

</details>

<details>
<summary>HTTPS or sign-in fails</summary>

Check that Home Assistant can resolve and reach the hostname on the router certificate. Try signing in to the router's web interface with the same credentials. Direct connections require **Web interface** permission.

</details>

<details>
<summary>Some entities are missing</summary>

The router creates entities only for features it has. A Repair notice lists features the router doesn't provide; install the matching component under **System → Components** in the router's web interface, then reload the integration. Extenders, uplinks and tunnels appear once the router reports them.

</details>

<details>
<summary>HACS can't download the integration</summary>

Check the custom repository URL and choose a published version. HACS downloads the release asset named `keenetic_router_pro.zip`.

</details>

<details>
<summary>I have too many client sensors</summary>

In **Configure**, set **Per-client sensors** to `basic` or `off`, or track fewer clients. See [Options](#options).

</details>

For a support request, download diagnostics from the integration entry and review the file before sharing. See the [security policy](SECURITY.md) for private vulnerability reports.

## Privacy

Home Assistant connects only to your router and its extenders, plus Keenetic's support site to find the release notes for your model. The daily firmware check is made by the router itself. Router credentials stay in Home Assistant's configuration; they are never written to logs or diagnostics.

[Release notes](CHANGELOG.md) · [Apache License 2.0](LICENSE) · [Report an issue](https://github.com/ABovsh/Keenetic-Router-Pro/issues)
