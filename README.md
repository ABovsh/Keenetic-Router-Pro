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

## What you can do

- **Compare internet providers.** See which WAN is active, how fast it is, and how much downtime each provider had this month.
- **Track devices at home.** See who’s connected and when they were last seen. Set a bandwidth limit, block access, or assign a connection policy.
- **Check your mesh.** Find offline extenders, see how many clients each serves, and check port traffic.
- **Manage VPNs.** Monitor WireGuard and IPsec traffic and switch supported tunnels on or off.
- **Check router health.** Watch CPU, memory, and Wi-Fi temperature. Toggle Wi-Fi, install firmware updates, or reboot a router or extender.

Home Assistant polls the router on your LAN with no cloud dependency. Available controls depend on your model and KeeneticOS components.

## Quick start

### 1. Download in HACS

[![Open Keenetic Router Pro in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ABovsh&repository=Keenetic-Router-Pro&category=integration)

Download **Keenetic Router Pro**, then restart Home Assistant.

<details>
<summary>Add the repository manually</summary>

In HACS, open **⋮ → Custom repositories**. Paste the URL below, select **Integration**, and choose **Add**. Open the new repository and download it.

```text
https://github.com/ABovsh/Keenetic-Router-Pro
```

</details>

### 2. Connect your router

[![Add Keenetic Router Pro to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=keenetic_router_pro)

You can also open **Settings → Devices & services → Add integration** and search for **Keenetic Router Pro**.

1. Choose **Direct / local** and enter the address you use for the router’s web interface. If you changed its web port, use that port here.
   - **HTTPS:** Enter the router’s KeenDNS name, set port `443`, and turn on **Use SSL**. On your LAN, the name must resolve to the router’s LAN address.
   - **HTTP on your LAN or VPN:** Enter the router’s LAN address, such as `192.168.1.1`, set port `80`, and leave **Use SSL** off.
2. Sign in with a router account that has **Web interface** permission. Turn on **Use challenge authentication**.
3. Select the clients you want to track. Open the router under **Devices** to see its first readings.

<details>
<summary>Connecting from outside your home</summary>

Over a VPN, use the same steps as on your LAN. For direct internet access, use the router’s own KeenDNS name and HTTPS. On the router, allow internet access to the web interface over **HTTPS only**, use a dedicated account with a strong password, and restrict access to Home Assistant’s source address where possible.

</details>

<details>
<summary>Using a KeenDNS protected web app on KeeneticOS 5.1.8 or older</summary>

On the router, publish **This Keenetic device** as a password-protected web app. Choose **HTTP** and the internal port that serves the API. Give a dedicated account **HTTP Proxy** permission. This account can issue management commands.

In Home Assistant, choose **KeenDNS protected web app** and enter the app hostname and credentials. This mode uses HTTPS on port 443 with Basic Auth. For KeeneticOS 5.1.9 or newer, use **Direct / local**.

</details>

## Try it

**Get a notification when backup internet takes over**

1. Create an automation and add a **Device** trigger.
2. Select your router and choose **switches to a different WAN**.
3. Add a notification action for your phone and save.

**See which provider had the most downtime**

Add each WAN’s **Downtime** sensor to a statistics card and compare the change over the same month. **Internet Downtime** measures how long *all* uplinks lacked internet. Time when Home Assistant couldn’t read the router is excluded.

## Help

<details>
<summary>The router refuses the configured API address</summary>

If this Repair appears after upgrading to KeeneticOS 5.1.9, open **Reconfigure**. Replace the old port-79 forward or protected app with the router’s web address, choose **Direct / local**, and enable challenge authentication.

</details>

<details>
<summary>HTTPS or sign-in fails</summary>

Check that Home Assistant can resolve and reach the hostname on the router certificate. Try signing in to the router’s web interface with the same credentials. Direct connections require **Web interface** permission.

</details>

<details>
<summary>HACS can’t download the integration</summary>

Check the custom repository URL and choose a published version. HACS downloads the release asset named `keenetic_router_pro.zip`.

</details>

<details>
<summary>I have too many client sensors, or a reading updates slowly</summary>

In the integration options, set **Per-client sensors** to `basic` for IP, session, last seen, and connection type. Choose `off` to keep only presence and controls, or `full` for all client sensors.

Link state and router-reported presence refresh every minute. Traffic and system gauges refresh less often. Firmware update checks run daily.

</details>

For a support request, download diagnostics from the integration entry and review the file before sharing. See the [security policy](SECURITY.md) for private vulnerability reports.


[Release notes](CHANGELOG.md) · [Apache License 2.0](LICENSE) · [Report an issue](https://github.com/ABovsh/Keenetic-Router-Pro/issues)
