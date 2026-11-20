# Keenetic Router Pro

Bring your Keenetic network into Home Assistant. See which internet connection is active, how long each provider was offline, which mesh extenders are reachable, and when a device joins or leaves. Home Assistant polls the router directly on your LAN with no cloud dependency.

![Version](https://img.shields.io/badge/version-1.20.0-blue?style=for-the-badge)
![Home Assistant 2024.5 or newer](https://img.shields.io/badge/Home%20Assistant-2024.5%2B-41BDF5?style=for-the-badge&logo=home-assistant)

[Install](#install-with-hacs) · [Connect your router](#connect-your-router) · [Explore your devices](#explore-your-devices) · [Troubleshooting](#troubleshooting)

## Install with HACS

1. In **HACS**, open **Custom repositories** from the menu. Enter `https://github.com/ABovsh/Keenetic-Router-Pro` and choose **Integration** as the repository type.
2. Find **Keenetic Router Pro** in HACS, download it, then restart Home Assistant.
3. Open **Settings → Devices & services → Add integration** and select **Keenetic Router Pro**.

## Connect your router

For a router on your LAN or reachable over a VPN, choose **Direct / local**. Enter the address and port of the router’s **web interface**. Use an account with **Web interface** permission and enable **Use challenge authentication**. KeeneticOS 5.1.9 and newer require challenge authentication on the web port.

| How you reach the router | Host | Port¹ | Use SSL |
| --- | --- | --- | --- |
| HTTPS on your LAN or over VPN | Router’s KeenDNS name, resolving to its LAN address | 443 | On |
| HTTP on your LAN or over VPN | Router’s LAN address, for example `192.168.1.1` | 80 | Off |
| HTTPS over the internet | Router’s own KeenDNS name | 443 | On |

¹ Enter your configured web port if you changed it. For HTTPS, the hostname must match the router certificate. For internet access, use a dedicated account with a strong password, allow **HTTPS only**, and restrict access to Home Assistant’s source address where possible.

Select the clients you want to track and finish setup. Open the integration’s **Devices** list and select the router to see its firmware, health, and connected-client count.

<details>
<summary>Connecting through a KeenDNS protected web app on KeeneticOS 5.1.8 or older</summary>

Choose **KeenDNS protected web app** if you already publish the API through a password-protected KeenDNS application. On the router, set its target to **This Keenetic device**, select **HTTP** for the internal application and use the internal port serving the API. Give a dedicated account **HTTP Proxy** permission. That account can issue management commands through the app.

In Home Assistant, enter the **app hostname** and its credentials. This mode uses HTTPS on external port 443 and Basic Auth. On KeeneticOS 5.1.9 and newer, use the direct connection settings above.

</details>

## Explore your devices

The devices and controls you see depend on your model, installed KeeneticOS components, and tracked clients.

| Device | What to look for |
| --- | --- |
| Router | CPU and memory usage, active connections, Wi-Fi radio temperature, DNS proxy health, firmware updates, and reboot. **Internet Downtime** counts periods when no uplink has internet. |
| WAN uplink | Connection state, public IP, throughput, traffic totals, and an enable switch. Each provider’s **Downtime** counts loss of link or internet on that uplink. |
| Mesh extender | Reachability, connected clients, ports, traffic, firmware updates, and reboot. New nodes appear as the controller discovers them. |
| VPN | WireGuard and IPsec state, traffic, and controls for supported profiles. A VPN that serves as an uplink shares its WAN device. |
| Tracked client | Presence, last seen, connection policy, access blocking, and bandwidth limit where supported. |

**Compare providers.** Add each uplink’s **Downtime** sensor to a Home Assistant statistics card and compare the change over the same week or month. Use **Internet Downtime** for the whole site. Time when Home Assistant cannot read the router is excluded.

**Get a failover notification.** In the automation editor, add a **Device** trigger, select the router, and choose **switches to a different WAN**. Add a notification action. The same router offers triggers for clients connecting and disconnecting.

**Choose how much client history to keep.** In the integration options, set **Per-client sensors** to `basic` for IP address, session, last seen, and connection type, or `full` for all client sensors. Choose `off` to skip this sensor family while keeping presence tracking and client controls.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Repair says **Keenetic router refuses the configured API address** | After a KeeneticOS 5.1.9 upgrade, a forward to port 79 or a protected app targeting it stops working. Open **Reconfigure**, choose **Direct / local**, enter the router web address, and enable challenge authentication. |
| HTTPS works in a browser but setup fails | Check that Home Assistant resolves the KeenDNS hostname to the intended address and can reach its HTTPS port. Enter the hostname covered by the certificate. |
| Credentials are rejected | Sign in to the router web interface with the same account. Direct mode requires **Web interface** permission. **HTTP Proxy** permission applies to the older protected-app setup. |
| HACS cannot download the integration | Check the custom repository URL and select a published version. HACS downloads the release asset named `keenetic_router_pro.zip`. |
| A sensor appears slow to update | Link state and router-reported presence are polled every minute. Traffic and system gauges refresh less often, and firmware update checks run daily. |

For a support request, download diagnostics from the integration entry and review the file before sharing it. For credentials, redaction, and private vulnerability reports, see the [security policy](SECURITY.md).

## Project

[Release notes](CHANGELOG.md) · [Apache License 2.0](LICENSE) · [Report an issue](https://github.com/ABovsh/Keenetic-Router-Pro/issues)

<details>
<summary>Code quality and test coverage</summary>

[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_Keenetic-Router-Pro&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=ABovsh_Keenetic-Router-Pro)
[![Reliability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_Keenetic-Router-Pro&metric=reliability_rating)](https://sonarcloud.io/component_measures?id=ABovsh_Keenetic-Router-Pro&metric=reliability_rating)
[![Security](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_Keenetic-Router-Pro&metric=security_rating)](https://sonarcloud.io/component_measures?id=ABovsh_Keenetic-Router-Pro&metric=security_rating)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_Keenetic-Router-Pro&metric=sqale_rating)](https://sonarcloud.io/component_measures?id=ABovsh_Keenetic-Router-Pro&metric=sqale_rating)
[![Coverage](https://img.shields.io/sonar/coverage/ABovsh_Keenetic-Router-Pro?server=https%3A%2F%2Fsonarcloud.io&style=for-the-badge&logo=sonarcloud&label=coverage)](https://sonarcloud.io/component_measures?id=ABovsh_Keenetic-Router-Pro&metric=coverage)

</details>
