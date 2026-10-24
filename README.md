# TIPSA Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-tipsa.svg)](https://github.com/ha-parcel-integrations/ha-tipsa/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-tipsa/total.svg)](https://github.com/ha-parcel-integrations/ha-tipsa/releases)
[![HACS](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 📦 Part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/), a family of package-tracking integrations for Home Assistant that all publish the same parcel format, statuses and events, so they plug into cross-carrier automations and the optional [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator). Browse the other carriers on the website.
>
> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [TIPSA](https://www.tip-sa.com) parcels (Spain, Portugal and Andorra). No account is needed — you enter your delivery postcode once and then the 22-digit tracking code of each parcel, just like on the TIPSA website.

> **Pre-1.0.** Built from one delivered Spanish parcel. Statuses for a return or a partial delivery, and parcels delivered in Portugal or Andorra, have not been seen yet: they show `unknown` and log one warning each, so please [open an issue](https://github.com/ha-parcel-integrations/ha-tipsa/issues/new?template=unrecognised_status.yml) when you see it. A warning names the status wording and nothing about you or your parcel.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of TIPSA parcels by their 22-digit tracking code — no account needed, and the postcode is asked once
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), the carrier's own status text, the sender, the weight and the status history
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- `tipsa.track_parcel` / `tipsa.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2025.12 or newer
- A parcel delivered by TIPSA in Spain, Portugal or Andorra, and its
  22-digit tracking code (from the shipping confirmation or the missed-delivery
  card) — no account needed. The shorter waybill number on its own is not
  enough.
- The postcode the parcel is delivered to. TIPSA checks it on every lookup, so
  it has to be the recipient's postcode. Spanish postcodes are five digits;
  the Portuguese and Andorran forms are accepted but have not been tested.

## Installation

### HACS (recommended)

1. Open HACS and search for **TIPSA**.
2. Install it.
3. Restart Home Assistant.

Or click the button below to open it directly in HACS:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ha-parcel-integrations&repository=ha-tipsa&category=integration)

### Manual

Copy `custom_components/tipsa` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=tipsa)

Or set it up by hand: add the integration via **Settings → Devices & Services → Add Integration → TIPSA** and enter the postcode your parcels are delivered to. You can add a second hub for a second address (home and work, say); the same postcode can only be added once.

Then add parcels via the integration's **Configure** dialog, the [`tipsa.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). Each parcel needs only its 22-digit tracking code. A parcel addressed to a different postcode than its hub can be entered in the **Configure** dialog as `code@postcode`, or with the `postal_code` field of `tipsa.track_parcel`.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked codes (`code` or `code@postcode`). Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure.

## Dynamic polling

Polling isn't a setting here — the integration adjusts its own cadence to
what your tracked parcels are actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today. TIPSA publishes no delivery window, so the hot tier starts as soon as
  a parcel is out for delivery.
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Fully paused** — once every tracked parcel has been delivered, or nothing
  is tracked at all, polling stops until you add a parcel back (adding one
  always triggers an immediate check, regardless of the pause).
- A small, fixed per-hub offset is added on top, so not every TIPSA
  hub out there polls at exactly the same second.

## Removal

Standard HA removal applies: **Settings → Devices & Services → TIPSA → ⋮ → Delete**. Nothing is stored on TIPSA's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.tipsa_<postcode>_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.tipsa_<postcode>_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.tipsa_<postcode>_next_delivery` | The next delivery. TIPSA publishes no ETA, so this stays empty |
| `sensor.tipsa_<postcode>_en_route_to_pickup_point` | Parcels heading for a pickup point. TIPSA does not announce this in advance, so it stays empty |
| `sensor.tipsa_<postcode>_awaiting_pickup` | Parcels waiting at a TIPSA collection point |
| `sensor.tipsa_<postcode>_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.tipsa_<postcode>_last_successful_update` | Diagnostic: when TIPSA was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Handed over to TIPSA, or documented |
| `in_transit` | In the sorting network, including a hub or agency scan |
| `out_for_delivery` | With the courier today |
| `delivered` | Delivered |
| `at_pickup_point` | Waiting for you at a TIPSA collection point (`DISPONIBLE`) |
| `problem` | A delivery attempt found nobody in (`Ausente`), or TIPSA reported an incident (`INCIDENCIA`) |
| `unknown` | The parcel is not found, or a status we have not mapped yet |

TIPSA also reports `Mal clasificado` (missorted); it is reported as `in_transit`, because the parcel it was seen on recovered and was delivered normally.

Two wordings are mapped from what TIPSA reports for other people's parcels rather than from a parcel tracked here, so each asks once in the log whether it reads correctly: `LEIDO EN DESTINO` (read at the destination) as `in_transit`, and `Ausente` (nobody in) as `problem`. No return wording has been seen yet, so `returning` is never reported.

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the TIPSA device):

| Event | When |
|---|---|
| `tipsa_parcel_registered` | A new parcel appears in the active list |
| `tipsa_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `tipsa_parcel_delivered` | A parcel is delivered |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `tipsa.track_parcel` | `tracking_code`, `postal_code` (optional) | Start tracking a parcel. `postal_code` picks the hub when several are set up. |
| `tipsa.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.tipsa: debug
```

## Troubleshooting

- **A parcel shows `unknown` and the log says TIPSA has no record of it** — TIPSA gives one and the same answer when the postcode is wrong for that parcel, when the tracking code is unknown, and when its history has expired, so the integration cannot tell you which it is. The postcode is the usual cause: it must be the one the parcel is addressed to, not the delivering depot's. Check it on the hub, or add the parcel as `code@postcode`. A parcel that was only just handed to TIPSA can also show this until its first scan.
- **A status logs "Unrecognised TIPSA status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-tipsa/issues/new?template=unrecognised_status.yml) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by TIPSA, Home Assistant, or any other third party referenced in this project. Please don't contact TIPSA for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to TIPSA's terms. Data is sent only to TIPSA's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration reads the same public shipment lookup that TIPSA offers for each parcel, and sends it your postcode and the parcel's tracking code. It never uses TIPSA's registered-client area.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
