"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That keeps the carrier-specific
mapping apart from the coordinator and makes it unit-testable without HA.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    NEW_STATUS_ISSUE_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
_RESPONSE_TIMEZONE = ZoneInfo("Europe/Madrid")

# Keys are normalised wordings (see :func:`normalize_wording`). TIPSA mixes
# upper and sentence case between rows, and the stepper spells three stages
# differently from the history rows, so both spellings are mapped.
#
# ``Mal clasificado`` (a missort) is ``in_transit`` by decision, not evidence:
# TIPSA draws it as an ordinary stepper stage and the one parcel seen with it
# went on to be delivered, so ``problem`` would make a healthy parcel look
# broken.
#
_STATUS_MAP: dict[str, ParcelStatus] = {
    "PENDIENTE DE ENTREGAR A TIPSA": ParcelStatus.REGISTERED,
    "DOCUMENTADO": ParcelStatus.REGISTERED,
    "TRANSITO": ParcelStatus.IN_TRANSIT,
    "EN CAMINO": ParcelStatus.IN_TRANSIT,
    "PENDIENTE DE LLEGADA": ParcelStatus.IN_TRANSIT,
    "MAL CLASIFICADO": ParcelStatus.IN_TRANSIT,
    "LEIDO EN DESTINO": ParcelStatus.IN_TRANSIT,
    "AUSENTE": ParcelStatus.PROBLEM,
    "REPARTO": ParcelStatus.OUT_FOR_DELIVERY,
    "EN REPARTO": ParcelStatus.OUT_FOR_DELIVERY,
    "ENTREGADO": ParcelStatus.DELIVERED,
}

# An agency or hub name is appended to these, so they match on prefix.
# An agency, hub, collection point or incident reason is appended to these, so
# they match on prefix. ``DISPONIBLE`` and ``INCIDENCIA`` were read off this
# same shipment page for a parcel whose shipper-side event codes were checked
# against it one by one, so the wording is the page's, not a shipper label
# guessed at. They are the only route to ``at_pickup_point`` and the second to
# ``problem``.
_PREFIX_MAP: tuple[tuple[str, ParcelStatus], ...] = (
    ("LECTURA EN AGENCIA DESTINO", ParcelStatus.IN_TRANSIT),
    ("LECTURA EN HUB", ParcelStatus.IN_TRANSIT),
    ("DISPONIBLE", ParcelStatus.AT_PICKUP_POINT),
    ("INCIDENCIA", ParcelStatus.PROBLEM),
)

# Mapped without a parcel of our own behind them, so each asks once for
# confirmation. Empty the set as real parcels settle the wordings.
_UNCONFIRMED_WORDINGS = frozenset({"LEIDO EN DESTINO", "AUSENTE"})

# Wordings we have already warned about, so each is logged only once per HA
# session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()
_unconfirmed_wordings_logged: set[str] = set()


def normalize_wording(text: str | None) -> str:
    """Accent-free, upper-case, whitespace-collapsed form of a status wording."""
    decomposed = unicodedata.normalize("NFD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", stripped.replace("\xa0", " ")).strip().upper()


def _warn_unmapped_status(kind: str, wording: str) -> None:
    """Log an unmapped carrier wording once, with a copy-paste issue link."""
    wording = normalize_wording(wording)[:80]
    if wording in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(wording)
    _LOGGER.warning(
        "Unrecognised TIPSA %s status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as 'unknown'",
        kind,
        NEW_STATUS_ISSUE_URL,
        wording,
    )


def _warn_unconfirmed_wording(wording: str, mapped: ParcelStatus) -> None:
    """Ask once for confirmation of a mapping no tracked parcel has shown."""
    if wording in _unconfirmed_wordings_logged:
        return
    _unconfirmed_wordings_logged.add(wording)
    _LOGGER.warning(
        "TIPSA reported '%s', which this integration reads as '%s'. No parcel "
        "tracked here has shown that wording yet — please confirm it looks "
        "right: %s",
        wording,
        mapped,
        NEW_STATUS_ISSUE_URL,
    )


def map_event_status(text: str | None) -> ParcelStatus | None:
    """Map one status wording to a canonical status, or ``None``.

    Never warns about a gap — whether one matters depends on the caller, which
    owns that one-shot warning. A mapping in `_UNCONFIRMED_WORDINGS` does ask
    once for confirmation, because nothing here has seen it on a parcel.
    """
    wording = normalize_wording(text)
    if not wording:
        return None
    mapped = _STATUS_MAP.get(wording)
    if mapped is not None:
        if wording in _UNCONFIRMED_WORDINGS:
            _warn_unconfirmed_wording(wording, mapped)
        return mapped
    for prefix, status in _PREFIX_MAP:
        if wording.startswith(prefix):
            return status
    return None


def map_parcel_status(
    current: str | None, history_texts: list[str | None] | None = None
) -> ParcelStatus:
    """Map the page's own status field, else the newest mappable history row.

    An absent field falls back to the history the same way an unmappable one
    does — a page that drops the field still knows its parcel was delivered.
    Only when both fail is it ``unknown``. A wording we could not map is
    reported either way, so a fallback that happens to work does not hide a
    status the suite has never seen; an absent field is nothing to report.
    """
    wording = normalize_wording(current)
    if wording:
        mapped = map_event_status(current)
        if mapped is not None:
            return mapped
        _warn_unmapped_status("current", current or "")
    for earlier in reversed(history_texts or []):
        fallback = map_event_status(earlier)
        if fallback is not None:
            return fallback
    return ParcelStatus.UNKNOWN


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the parsed page rows.

    Each entry is ``{timestamp, status, raw_status}``, top-level (not under
    ``raw``) so it survives the aggregator's ``strip_raw()``. The parser has
    already ordered the rows oldest to newest, keeping the page's own order
    for rows that share a minute; this keeps that order and caps to the most
    recent ``max_events``.
    """
    entries: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict) or not event.get("timestamp"):
            continue
        text = event.get("status")
        status = map_event_status(text)
        if status is None and normalize_wording(text):
            _warn_unmapped_status("history", text)
        entries.append(
            {"timestamp": event["timestamp"], "status": status, "raw_status": text}
        )
    return entries[-max_events:]


def _delivered_at(raw: dict, events: list[dict]) -> str | None:
    """When the parcel was delivered, as an ISO UTC timestamp.

    The delivered history row is the same clock as every other row (Madrid
    for every agency). The ``DATOS DE LA ENTREGA`` date and time are local
    to the delivering depot, an hour off for Portuguese agencies, so they are
    only the fallback.
    """
    for event in reversed(events):
        if map_event_status(event.get("status")) is ParcelStatus.DELIVERED:
            return event.get("timestamp")
    day, clock = raw.get("delivered_date"), raw.get("delivered_time")
    if not day or not clock:
        return None
    try:
        local = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=_RESPONSE_TIMEZONE)
    except ValueError:
        return None
    return local.astimezone(timezone.utc).isoformat()


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order. A key is ``None`` when the
    page does not expose it. ``receiver`` is ``None`` on purpose: the
    recipient cells are never read.
    """
    events = [e for e in raw.get("events") or [] if isinstance(e, dict)]
    current = raw.get("status")
    status = map_parcel_status(current, [e.get("status") for e in events])
    delivered = status is ParcelStatus.DELIVERED

    return {
        "carrier": "TIPSA",
        "barcode": raw.get("tracking_code"),
        "sender": raw.get("sender") or None,
        "receiver": None,
        "status": status,
        "raw_status": current or None,
        "delivered": delivered,
        "delivered_at": _delivered_at(raw, events) if delivered else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": None,
        "url": None,
        "weight": raw.get("weight_kg"),
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
