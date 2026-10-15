# Working in this repository

Home Assistant custom integration for **TIPSA** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

API mechanics — endpoints, parameters, status vocabularies — live in the
private `carrier-research/tipsa/api/` and are **never** copied here.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| change which optional field this carrier populates vs. always returns `None` | Update `const.py`'s `CAPABILITIES` in the same commit — it feeds the comparison table on the docs site, so a field that starts (or stops) coming back non-null and isn't reflected there is a wrong claim on the website, not just a stale comment. If this carrier has more than one backend (a country-specific transport, not just a config option) with genuinely different field support, `CAPABILITIES` should be a `CAPABILITIES_BY_VARIANT` dict instead — one frozenset per backend, so a field only some backends populate doesn't get silently intersected away or overclaimed for the rest. A field you could not verify (no real parcel yet) goes in `PENDING_CAPABILITIES` instead of being left out — the site then says "awaiting data" rather than claiming the API never exposes it; move it into `CAPABILITIES` once confirmed |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).
- **The optional pickup-point summaries are a pair.** A carrier that can tell
  a parcel is destined for a pickup point exposes
  `en_route_to_pickup_point` for `pickup is true` before it arrives; one that
  can reach `ParcelStatus.AT_PICKUP_POINT` also exposes `awaiting_pickup`.
  See *Parcel contract* in `CONVENTIONS.md`. Say "pickup point", not
  "ServicePoint"/"parcel shop"/"locker", for the generic concept; the
  example carrier demonstrates both canonical sensors.

## Carrier-specific notes

Code + postcode model, GLS shape: the hub owns one delivery postcode
(`entry.options[postal_code]`, `unique_id` = the postcode), a parcel needs only
its 22-digit tracking code, and a parcel addressed elsewhere carries its own
`postal_code` override (`code@postcode` in the options list, the
`postal_code` service field). `single_config_entry` is deliberately absent:
one hub per postcode.

- **The postcode-gated lookup is the only route.** Never fall back to the
  postcode-free sibling door when the gate rejects a postcode, and never retry
  a parcel against other postcodes to find a match. The gate is what keeps a
  bare tracking code from exposing the recipient.
- **Not-found is one answer for three causes** (wrong postcode, unknown
  tracking code, expired history). The coordinator keeps the last good data, shows
  a placeholder and logs one warning per tracking code that names all
  three. `No ha proporcionado el CP` / `Error en el CP` are our bugs, not user
  errors: the client raises, and the flow validation (`valid_postcode`, all
  whitespace stripped) is what keeps them from users.
- **The reader takes an allow-list of fields and nothing else.** TIPSA
  asterisk-masks the recipient name, receiver and street address, then states
  the full address, town and postcode unmasked elsewhere in the reply, so
  "skip masked values" is not a privacy rule. The allow-list is
  `(section, label)` keyed and lives in `parser.py`; everything outside it is
  dropped as the reply is read.
  That covers `Destinatario`, `Receptor`, `Dirección Destino`, `Población
  Destino`, `Código postal`, `Observaciones` (in **both** the general and the
  delivery block) and `Persona de contacto`, plus the whole `AGENCIA DESTINO`
  block — never into `raw`, never into `history`, never into a fixture. Do not
  read `Código postal` back to verify the user's postcode.
  **A label only ever applies to the field immediately after it.** Anything
  else disarms it; a widened value would otherwise pass the label on to the
  next unlabelled field, which is how a forbidden value reached `sender`
  before `test_a_value_cell_of_an_unexpected_width_does_not_arm_the_next_cell`
  existed.
  `tests/test_parser.py` and `tests/test_diagnostics.py` assert their absence.
  The invented values in `tests/payloads.py` exist only to prove that.
- **History is ordered by reversing the reply first.** TIPSA states the
  newest row first; reverse, then stable-sort by minute, so rows sharing a
  minute keep the reply's own order (a hub read before `TRANSITO`, an agency
  read before `REPARTO`). Plain source order on ties would put the older row
  last and make the newest-mappable-row fallback report the wrong status.
- **`status` is TIPSA's own status field**, else the newest mappable
  history row, else `unknown`, with one warning for a wording the map does not
  know — even when the fallback resolves it, so a new wording still gets
  reported. `delivered_at` is the delivered history row (Madrid time for every
  agency); the delivery block's date and time are local to the depot, an hour
  off in Portugal, so they are only the fallback.
- **`Mal clasificado` is `in_transit` by decision, not evidence.** TIPSA draws
  it as an ordinary stepper stage and the one parcel seen with it was
  delivered. Mapping it to `problem` would make a healthy parcel look broken.
  It is the mapping a reviewer will want to re-litigate.
- **Fields that are `None` on purpose:** `receiver` (never read),
  `planned_from`/`planned_to` (there is no ETA at all, so the hot polling tier
  starts as soon as a parcel is out for delivery and
  `*_parcel_delivery_time_changed` can never fire), `dimensions` (only a
  volume is shown) and `pickup_point`. `pickup_point` is in
  `PENDING_CAPABILITIES`: the delivering-depot block is not a collection
  point, but a pickup wording has not been seen yet. `CAPABILITIES` is
  `weight` and `history`. `url` is `None`: the only per-parcel link is the
  postcode-free sibling door, and offering it would hand every dashboard a
  link that bypasses the gate this integration deliberately goes through.
  The locator form was there briefly instead and is not a link to a parcel.
- **Keep `en_route_to_pickup_point` and `awaiting_pickup`, even though both
  are always empty today.** TIPSA runs a collection network — its own home page
  carries a "Localizador de Puntos de Recogida" and the `puntospickup`
  application, and it is a GEOPOST network member, sharing the Pickup network
  with SEUR — and its shipper status catalogue has a `Disponible para recoger`
  code. So the wording is expected to turn up, not speculative, which is
  exactly what `PENDING_CAPABILITIES` means. Removing the pair would have to be
  undone the first time a real parcel is routed to a shop. Decided 2026-10-13
  after a review proposed dropping them.
- **Unseen and therefore `unknown` plus a warning:** a return (`Devuelto`),
  a partial delivery (`Entrega parcial`), a reroute (`Recanalizado`) and the
  rest of the shipper catalogue; any Portuguese or Andorran parcel; and what
  the gate wants for a Portuguese or Andorran postcode (the flow accepts the
  shapes permissively).
- **`DISPONIBLE` and `INCIDENCIA` are prefix matches in TIPSA's own
  wording**, not shipper labels: `DISPONIBLE` (available for collection) and
  `incidencia` (with the reason appended) were confirmed against a real
  parcel, code by code — the research notes carry that cross-check.
  `DISPONIBLE` is the only route to `at_pickup_point`, so it is what makes
  `awaiting_pickup` reachable. The same check is why `Documentado` is known to
  appear as a history row.
- **Do not pre-map the rest of that catalogue.** Its labels are the shipper's,
  and TIPSA demonstrably reports different text (`En tránsito` → `TRANSITO`,
  `En reparto` → `REPARTO`), while six wordings seen live here appear nowhere
  in it. It is also incomplete — the gaps are in the research notes. Let the
  warning do its job.
- **`LEIDO EN DESTINO` and `Ausente` are mapped without a parcel of our own.**
  Both come from what TIPSA reports for other people's parcels. `Ausente`
  (a failed delivery attempt) is the only route to
  `ParcelStatus.PROBLEM` this carrier currently has. Both are in
  `_UNCONFIRMED_WORDINGS`, so each asks once per HA run for confirmation
  rather than reporting nothing — the pre-1.0 rule. Take them out of that set
  once a tracked parcel has shown the wording.
- **Never route on the tracking code's shape.** The 6+6+10 layout is shared with
  another Spanish carrier; the user picks the carrier.
- **Nothing is cached between polls**, and no cookie, key or referer is sent.
  The reply's character encoding is pinned in `api.py` rather than trusted —
  the mechanics are in the research notes.

## Options and reloads

For code-based carriers, the options flow starts with exactly `Parcels` and
`Settings` — or, where the carrier supports outgoing parcels, `Incoming
parcels` / `Outgoing parcels` / `Settings` (see the next section).
`Parcels` is one editable multi-code list; `Settings` is
a flat form — some carriers use one sectioned form
(`data_entry_flow.section`) instead; both are generator variants, not carrier
decisions. Changes apply without a restart. Two models, **do not mix them**:
- **Account-less carriers** (the default, and the `--auth byo-key` build) apply
  changes live: an update listener calls `async_request_refresh()`, so
  added/removed parcel sensors appear immediately (this is also the resume
  path after polling has fully suspended — see "Dynamic polling" below).
- **Account-based carriers** call `async_schedule_reload` on submit and register
  **no** update listener. Combining a listener with a reload-on-update flow is
  deprecated, an error in HA 2026.12+.

## Auth models

Three `--auth` builds, one axis: **what the config flow asks for and
validates**, not how tracking works. `none` and `byo-key` both key tracking on
codes the user types in (`Parcels`/`Settings` options, `track_parcel` /
`untrack_parcel` services, the account-less coordinator and its full-stop /
delivered-skip behaviour below) — `byo-key` only adds a required key field
validated against the carrier's **official** API at setup, `CONF_API_KEY` in
`entry.data`, and a reauth flow for when the key is rotated or revoked outside
Home Assistant (`TIPSAAuthError` from any per-parcel fetch raises
`ConfigEntryAuthFailed` for the whole poll — one credential covers every
tracked code, so a rejected key is never treated as one parcel's problem).
`credentials` is the only one that changes the *tracking* model too (an
account feed, not user-entered codes) — see the split above.

Reach for `byo-key` only when carrier-research has already established that
the carrier's *unauthenticated/consumer* surface is unusable (bot-walled,
requires a session a script can't hold) and that the *official* developer key
is reachable by a private individual — `key_access: consumer` in the research
doc's front matter, not `business`. A `business`-gated key is a wall, not a
BYO key, whatever the portal's own copy claims (see
`carrier-research/CLAUDE.md`'s "Standing rulings").

## Incoming and outgoing parcels

**Add outgoing support whenever the carrier lets a consumer send a parcel** —
a C2C shipment, a locker drop-off, a marketplace or returns label. It is not
an optional extra to bolt on later: without it a parcel the user sent counts
towards `incoming_active` and sits on their dashboard next to the ones they
are waiting for. A carrier that genuinely has no consumer-sending surface is
exempt — say so in *Carrier-specific notes* so the gap reads as a decision.
**TIPSA is exempt:** sending needs a credentialed shipper account that a
household cannot obtain, so there is no consumer sending surface and every
tracked parcel is incoming.

Where the direction comes from has exactly two answers, and which one applies
follows from the carrier, not from taste:

- **Account-based** — the account feed distinguishes them, so derive it and
  never ask the user. A shipment matching neither side logs a one-shot
  warning and defaults to incoming rather than disappearing from every list.
  References: `ha-ppl-cz` (one call, split on a field), `ha-dhl-nl` (a
  separate "sent" endpoint).
- **Account-less** — the payload cannot reveal it (the user's own parcel and
  a stranger's look alike, and there is no account identity to compare a
  party against), so the **user declares it per parcel**: two menu entries in
  the options flow, each the same multi-code list, plus a `direction` field on
  `track_parcel`. Store it as `CONF_DIRECTION` on the `CONF_PARCELS` dicts,
  defaulting to incoming, so entries written before the option existed need no
  migration. Re-filing a code under the other direction moves it instead of
  erroring — that is the correction path. Reference: `ha-packeta`.
  **Never infer direction from free-text event wording**: a handover sentence
  that happens to name the drop-off point is not a structured field, says
  nothing before handover, and silently ties the split to one locale.

Above that split the shape is identical either way, and is suite-wide:
`coordinator.outgoing` / `coordinator.delivered_outgoing` alongside `data` /
`delivered`; `outgoing_parcels` + `outgoing_delivered_parcels` summary
sensors (their unique_ids belong in `non_parcel_unique_ids`); per-parcel
sensors spawned for **both** directions; the
`<domain>_outgoing_parcel_status_changed` / `_outgoing_parcel_delivered`
event pair, with **no** `registered` and no delivery-time event for outgoing;
`awaiting_pickup`, `next_delivery` and the calendar staying incoming-only;
and both new lists in `diagnostics.py`. The aggregator needs no change — it
buckets on the sensor suffix and the event prefix. Also set
`directions: incoming+outgoing` for the carrier in the docs site's
`data/carriers.yml`.

## Tracking-code validation

The suite default is that `valid_tracking_code` accepts every non-empty code.
**TIPSA is the deliberate exception**: a tracking code is exactly 22 digits
(`^\d{22}$` after stripping spaces, dots and dashes), and a bare 10-digit
waybill is answered by the carrier like an unknown shipment, so the flow and
the `track_parcel` service reject it before any request is made. The postcode
is validated by shape (`valid_postcode`) the same way.

## Dynamic polling

There is no user-facing polling interval — this is a deliberate suite-wide
choice, not a gap. `coordinator.py`'s `_hottest_tier_minutes` /
`_next_update_interval` recompute `update_interval` at the end of every
refresh. The reference carrier in `ha-carrier-template` (its `coordinator.py`)
is the canonical implementation every carrier mirrors; the design rationale (quiet window, tiers, stagger,
backoff, delivered-skip) is spelled out below.

- **Quiet window:** no polling 00:00–06:00 local time, except two daily
  anchors (~00:00 and ~06:00) for overnight / end-of-day catch-up.
- **Tiers while polling:** *hot* (15 min) when a tracked, not-yet-delivered
  parcel is `out_for_delivery` within an hour of its `planned_from` (or has no
  `planned_from` at all); *mid* (45 min) for anything else still in flight —
  `problem`/`returning` included, deliberately not hot. Account-based carriers
  never fully stop even with nothing hot or in transit: the mid-tier poll is
  also how a new shipment gets discovered.
- **Full stop (account-less carriers only):** `update_interval = None` when
  nothing is tracked or every tracked parcel is delivered. Resumes the moment
  a parcel is added back, via the options-flow refresh above.
- **Stagger:** a small, stable per-install offset (hash of the config entry
  id) is added to every computed interval so installs don't all hit an anchor
  or tier boundary at the same second.
- **429 backoff:** a 429 anywhere in a poll raises `UpdateFailed` with
  `retry_after` — the carrier's own `Retry-After` header if present, otherwise
  an exponential backoff tracked per-coordinator. `api.py`'s
  `…ApiError.status_code` / `.retry_after` carry this from the HTTP layer.
- **Delivered codes are skipped from the fetch (account-less carriers only):**
  once a tracking code's payload comes back `delivered`, `coordinator.py`
  excludes it from the next cycle's fetch — its payload can never change
  again. `self._delivered_codes` (keyed on the tracking code, not the barcode)
  is rebuilt from each cycle's results and intersected with the tracked set on
  untrack. The code stays in the options list, keeps its sensor and its
  cached payload, and still shows under the retention window — it just costs
  no more requests. `coordinator.delivered_codes` surfaces the count in
  diagnostics. Account-based carriers have nothing to skip here — one account
  call already returns everything, so they either have no `delivered_codes` or
  it is always empty.

A carrier that genuinely throttles or soft-bans traffic harder than the 429
backoff handles is a documented, local divergence from this in that one
repo's own `CLAUDE.md` — not a generator flag.

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (HTTP client, error types; `account/` and `tracking/` packages instead when a repo has two sources) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure, no I/O) | partly (the status map, `normalize_parcel`) |
| `coordinator.py` (fetch, cache, event firing) | mostly not |
| `config_flow.py` | partly (code validation; key/credential validation on `--auth byo-key`/`credentials`) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `device.py` (shared device-info helper) | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`, account-less only) | no |

`parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. `aiohttp.ClientError` is caught **per parcel** in the gather
loop (one bad parcel doesn't fail the poll) but **not** around the whole update
(the coordinator wraps that). Entities: `has_entity_name` + `translation_key`,
`icons.json`, translated units, `_attr_attribution`, `_unrecorded_attributes` on
anything with a parcel list or `raw`. Over-redact diagnostics — they get pasted
into public issues.

## Running tests

```
python -m pytest tests/ --cov=custom_components.tipsa
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in your own private research notes, never in
this repo.
