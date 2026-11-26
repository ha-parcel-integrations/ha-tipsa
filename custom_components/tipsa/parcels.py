"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That keeps the carrier-specific
mapping apart from the coordinator and makes it unit-testable without HA.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
import re
from typing import Any
import unicodedata
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
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
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
        if status is None and text is not None and normalize_wording(text):
            _warn_unmapped_status("history", text)
        entries.append(
            {"timestamp": event["timestamp"], "status": status, "raw_status": text}
        )
    return entries[-max_events:]


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


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
        local = datetime.fromisoformat(f"{day}T{clock}").replace(
            tzinfo=_RESPONSE_TIMEZONE
        )
    except ValueError:
        return None
    return local.astimezone(UTC).isoformat()


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
    planned_from, planned_to = planned_for_status(status, None, None)

    return {
        "carrier": "TIPSA",
        "barcode": raw.get("tracking_code"),
        "sender": raw.get("sender") or None,
        "receiver": None,
        "contents": None,
        "status": status,
        "raw_status": current or None,
        "delivered": delivered,
        "delivered_at": _delivered_at(raw, events) if delivered else None,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": is_pickup(
            None, status, (map_event_status(e.get("status")) for e in events)
        ),
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
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
