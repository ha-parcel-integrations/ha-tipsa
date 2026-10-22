"""Constants for the TIPSA parcel tracker integration."""

from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "tipsa"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"  # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"  # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"  # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"  # Ready to collect at a pickup location
    DELIVERED = "delivered"  # Handed over
    RETURNING = "returning"  # Failed delivery, going back to sender
    PROBLEM = "problem"  # Carrier reports an exception/issue
    UNKNOWN = "unknown"  # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Every optional key the parcel contract defines. CAPABILITIES below must be a
# subset of this — it exists so a typo in CAPABILITIES fails a test instead of
# silently dropping a carrier off a table on the docs site.
KNOWN_CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# The page carries no ETA, no dimensions (only a volume) and no collection
# point, so those stay ``None``. ``url`` is ``None`` too: the only per-parcel
# link bypasses the postcode gate, and a form the user still has to fill in is
# not a link to their parcel.
CAPABILITIES = frozenset({"weight", "history"})

# Fields whose support is not confirmed yet — typically a carrier built without
# a real parcel to check against. Leave this empty once the open questions are
# answered. A field is in exactly one of three states: in CAPABILITIES (seen
# populated), in PENDING_CAPABILITIES (docs site shows "awaiting data"), or in
# neither (the API never exposes it). Pending fields must still come back as a
# literal ``None`` from normalize_parcel() until they are confirmed and moved
# into CAPABILITIES. A multi-backend carrier declares
# PENDING_CAPABILITIES_BY_VARIANT with the same keys as its
# CAPABILITIES_BY_VARIANT instead (omit backends with nothing pending).
PENDING_CAPABILITIES: frozenset[str] = frozenset({"pickup_point"})

# If this carrier ever grows a second backend with a genuinely different
# payload shape (a country-specific API, not just a config option), replace
# the single CAPABILITIES above with a CAPABILITIES_BY_VARIANT dict instead:
#
#   CAPABILITIES_BY_VARIANT = {
#       "Germany": frozenset({"pickup_point", "url", "history"}),
#       "Other": frozenset({"weight", "dimensions", "delivery_window",
#                            "pickup_point", "url", "history"}),
#   }
#
# Key order is display order on the docs site's comparison table; label each
# key exactly as the carrier's own country/backend selector does. The docs
# site's generator accepts either shape — don't declare both. Do not add this
# preemptively: a single-backend carrier (the common case) keeps the flat
# CAPABILITIES above.

# Two keyless GETs per refresh. The lookup door requires the recipient's
# postcode as ``cp`` and answers 200 with the detail URL in a body meta-refresh
# (there is no Location header). The postcode-free sibling door is deliberately
# never used, not even as a fallback: the postcode gate is what stops a bare
# tracking_code from exposing the recipient. An empty ``servicio`` in the refresh
# means wrong postcode, unknown tracking_code or expired history, all
# indistinguishable. The detail page declares no charset and is ISO-8859-1.
LOOKUP_URL = "https://aplicaciones.tip-sa.com/cliente/datos_prestashop.php"
DETAIL_ORIGIN = "https://dinapaqweb.tipsa-dinapaq.com"
DETAIL_PATH = "/dinapaqweb/detalle_envio.php"
# Links to a pre-filled issue form. The ``?template=`` parameter matters:
# without it the link opens a blank form, and the report comes back missing the
# version and the log line we need.
_ISSUES = "https://github.com/ha-parcel-integrations/ha-tipsa/issues/new"
NEW_STATUS_ISSUE_URL = f"{_ISSUES}?template=unrecognised_status.yml"
NEW_BUG_ISSUE_URL = f"{_ISSUES}?template=bug_report.yml"

DETAIL_ENCODING = "iso-8859-1"
REQUEST_TIMEOUT_SECONDS = 30

# Tracked parcels live in the config entry options as a list of
# ``{tracking_code}`` dicts — this carrier has no account or parcel feed, so the
# user enters the codes themselves. The hub owns the delivery postcode
# (``CONF_POSTAL_CODE`` in the options); a parcel addressed elsewhere carries
# its own ``postal_code`` override.
CONF_PARCELS = "parcels"
CONF_TRACKING_CODE = "tracking_code"
CONF_POSTAL_CODE = "postal_code"

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Dynamic, status-driven polling — unconditional across the suite, no
# user-facing interval option (see CLAUDE.md's "Dynamic polling"
# section for the full algorithm and the reasoning behind it).
#
# Quiet window: no polling between these local hours except the two anchors
# below, for overnight / end-of-day catch-up.
QUIET_WINDOW_START_HOUR = 0
QUIET_WINDOW_END_HOUR = 6

# Cadence while polling is active (minutes). Hot = at least one tracked,
# not-yet-delivered parcel is out_for_delivery within HOT_LOOKAHEAD_HOURS of
# its planned_from (or has no planned_from at all); mid = anything else still
# in flight (registered, in_transit, at_pickup_point, unknown, problem,
# returning).
HOT_INTERVAL_MINUTES = 15
MID_INTERVAL_MINUTES = 45
HOT_LOOKAHEAD_HOURS = 1

# Small, stable per-install offset added to every computed interval so
# different installs don't all hit an anchor or tier boundary at the same
# second. Deterministic (hash of the config entry id), not random.
STAGGER_MINUTES = 7

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
