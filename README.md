<p align="center">
  <img src="images/icon.png" width="128" alt="SURU WaterMonitor logo">
</p>

<h1 align="center">SURU WaterMonitor for Home Assistant</h1>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="hacs"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT"></a>
</p>

Unofficial Home Assistant integration for the **SURU WaterMonitor** smart water
meter platform (SenseGuard GmbH). It signs in to the SURU cloud with your app
account and brings your water consumption, meter reading and leak alarm into
Home Assistant, including the Energy dashboard.

> [!WARNING]
> This is an **unofficial**, community-built integration. It is **not affiliated
> with, endorsed by, or supported by SenseGuard GmbH / SURU**. It talks to the
> same private cloud API as the official "WaterMonitor" app. The vendor can
> change or restrict the API at any time. Use at your own risk.

## Features

- **Hourly water consumption in the Energy dashboard** - most meters upload
  their data only once a day. The integration writes the hourly values from the
  SURU cloud into a long-term statistic with their real timestamps, so the
  Energy dashboard shows *when* the water was used, not when the data arrived.
  The last 30 days are backfilled on setup.
  [Details](#energy-dashboard)
- **Meter reading and today's consumption** as sensors
- **Leak alarm** - the SURU pipe monitor (leak, high flow, frost/temperature,
  low flow) as a problem binary sensor
- **Water quality** - hardness and pH of your water supply
- One device per water meter, multiple meters per account supported
- Setup via the UI, re-authentication on password change, adjustable polling
  interval
- No extra Python dependencies

### Entities

| Entity | Unit | Notes |
|---|---|---|
| Meter reading | m3 | `device_class: water`, `total_increasing` |
| Consumption today | L | sum of today's hourly values, resets at midnight |
| Alarm | on/off | `device_class: problem`; active incident types in the attribute `active_incidents` |
| Water hardness | dH | diagnostic |
| pH value | - | diagnostic |
| Last measured | timestamp | diagnostic: time of the meter's last upload |

Plus the statistic **SURU WaterMonitor &lt;unit&gt; water consumption** (L) for
the Energy dashboard, see below.

The data comes from your utility's smart meter via the SURU cloud. Most meters
upload once a day, so values in Home Assistant are only as fresh as the last
upload (see *Last measured*).

## Requirements

- Home Assistant **2025.11** or newer
- A working **SURU WaterMonitor** app account (email + password) with an
  activated meter

## Installation

### HACS (custom repository)

1. HACS -> top-right menu -> **Custom repositories**
2. Add `https://github.com/teuffel/homeassistant-suru-watermonitor`,
   category **Integration**
3. Install **SURU WaterMonitor**, then **restart** Home Assistant

### Manual

Copy `custom_components/suru_water_monitor/` into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

Settings -> Devices & Services -> **Add Integration** -> "SURU WaterMonitor" ->
enter your app **email** and **password**.

The polling interval (default 60 min, range 5-1440) can be changed any time via
the integration's **Configure** button (options).

## Energy dashboard

Settings -> Dashboards -> Energy -> **Water consumption** -> add the statistic
**SURU WaterMonitor &lt;unit&gt; water consumption** (L).

How it works:

- After each upload of the meter, the hourly values up to the time of the last
  measurement are written with their real timestamps. Hours after that follow
  with the next upload.
- On first setup the last 30 days are backfilled. If the meter was offline for
  a while, the missing hours are filled in once its data arrives.

The statistic needs access to the hourly consumption data. Some accounts don't
have it (the API answers HTTP 403, see
[#3](https://github.com/teuffel/homeassistant-suru-watermonitor/issues/3)); then
no statistic is created and all other entities work normally.

Alternatively, you can add the **Meter reading** sensor (m3). It works with every
account, but the consumption of a whole upload then shows up in the hour in
which Home Assistant fetched it.

Use only one of the two, otherwise the consumption is counted twice. When you
switch from the meter reading to the statistic, the Energy dashboard shows the
last 30 days (backfill) from the statistic; older history stays recorded under
the meter reading sensor.

## How it works

Authentication is AWS Cognito (`USER_SRP_AUTH`, no client secret) - exactly what
the official app does. The resulting access token is sent as a Bearer token to
the SURU API Gateway. The SRP login is implemented self-contained, so the
integration needs **no extra Python dependencies** (no `boto3`).

## Privacy & legal

- Your credentials are stored by Home Assistant in its config entry and are only
  sent to AWS Cognito (`cognito-idp.eu-central-1.amazonaws.com`) and the SURU API
  over HTTPS.
- This repository contains **no vendor code or app binaries**. It only provides
  an interoperable client, which is permitted for interoperability purposes
  (EU Directive 2009/24/EC Art. 6, German UrhG section 69e). "SURU" and
  "WaterMonitor" are trademarks of their respective owners and are used here only
  descriptively to indicate compatibility.

## License

[MIT](LICENSE)
