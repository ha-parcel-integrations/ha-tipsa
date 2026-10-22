"""Reader for TIPSA's shipment tracking response.

The response carries no structured alternative, so this module fails closed:
anything it cannot positively identify raises :class:`TIPSAParseError`
instead of yielding a partial parcel.

The set of fields read *is* the record. TIPSA asterisk-masks the recipient's
name and street address and then states the same address, town and postcode
unmasked elsewhere in the reply, so a mask is not a privacy boundary. Every
field is therefore matched against an allow-list of ``(section, label)``
pairs and anything else is dropped on the spot: the recipient, contact,
address, town, postcode and notes fields never reach the result. Do not widen
the allow-list without re-reading that boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html.parser import HTMLParser
import re
import unicodedata
from zoneinfo import ZoneInfo

RESPONSE_TIMEZONE = ZoneInfo("Europe/Madrid")

_SECTION_GENERAL = "DATOS GENERALES"
_SECTION_DELIVERY = "DATOS DE LA ENTREGA"

# (section, label) pairs that are read. Everything else is discarded.
_READ_GENERAL = {
    "remitente": "sender",
    "estado actual": "current_status",
    "referencia": "shop_reference",
    "bultos": "pieces",
    "fecha": "ship_date",
    "kilos": "weight_kg",
    "volumen (m3)": "volume_m3",
}
_READ_DELIVERY = {"fecha": "delivered_date", "hora": "delivered_time"}

_IDENTITY_INPUTS = frozenset(
    {"envio-localizado", "estado-localizado", "CodAgeCargo", "CodAgeOri", "Albaran"}
)
_VALUE_CELL_RE = re.compile(r"^mdl-cell--[3468]-col$")
_HISTORY_HEADER = ("FECHA/HORA", "ESTADO", "POBLACION")
_EVENT_TIME_FORMAT = "%d/%m/%y %H:%M"
_IGNORED_TEXT_TAGS = frozenset({"script", "style"})


class TIPSAParseError(Exception):
    """Raised when the page does not have the expected shipment shape."""


def clean_text(value: str) -> str:
    """Collapse whitespace (including non-breaking spaces) to single spaces."""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _fold(value: str) -> str:
    """Upper-case, accent-free, whitespace-collapsed form for comparisons."""
    decomposed = unicodedata.normalize("NFD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return clean_text(stripped).upper()


class _Div:
    __slots__ = ("classes", "has_child_div", "parts")

    def __init__(self, classes: set[str]) -> None:
        self.classes = classes
        self.parts: list[str] = []
        self.has_child_div = False


class _PageParser(HTMLParser):
    """Collect identity inputs, labelled leaf cells and the history table."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inputs: dict[str, str] = {}
        self.fields: dict[str, str] = {}
        self.history_rows: list[list[str]] = []
        self.history_found = False
        self._section = ""
        self._pending: str | None = None
        self._divs: list[_Div] = []
        self._ignored = 0
        self._table_depth = 0
        self._table_header: list[str] = []
        self._table_rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None
        self._cell_is_header = False
        self._span_is_tooltip: list[bool] = []

    # -- tag handling -------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag in _IGNORED_TEXT_TAGS:
            self._ignored += 1
        elif tag == "input":
            element_id = attr.get("id")
            if element_id in _IDENTITY_INPUTS and element_id not in self.inputs:
                self.inputs[element_id] = attr.get("value") or ""
        elif tag == "div":
            if self._divs:
                self._divs[-1].has_child_div = True
            self._divs.append(_Div(set((attr.get("class") or "").split())))
        elif tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._table_header = []
                self._table_rows = []
        elif tag == "tr" and self._table_depth:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell_parts = []
            self._cell_is_header = tag == "th"
            self._span_is_tooltip = []
        elif tag == "span" and self._cell_parts is not None:
            self._span_is_tooltip.append("mdl-tooltip" in (attr.get("class") or ""))

    def handle_endtag(self, tag):
        if tag in _IGNORED_TEXT_TAGS:
            self._ignored = max(0, self._ignored - 1)
        elif tag == "span" and self._span_is_tooltip:
            self._span_is_tooltip.pop()
        elif tag in ("td", "th") and self._cell_parts is not None:
            text = clean_text("".join(self._cell_parts))
            if self._cell_is_header:
                self._table_header.append(text)
            elif self._row is not None:
                self._row.append(text)
            self._cell_parts = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._table_rows.append(self._row)
            self._row = None
        elif tag == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0:
                self._close_table()
        elif tag == "div" and self._divs:
            div = self._divs.pop()
            if not div.has_child_div:
                self._leaf(div.classes, clean_text("".join(div.parts)))

    def handle_data(self, data):
        if self._ignored:
            return
        if self._cell_parts is not None:
            # Every history cell repeats its text in a tooltip span; only the
            # visible text counts, or each value would be read twice.
            if not any(self._span_is_tooltip):
                self._cell_parts.append(data)
        elif self._divs and not self._table_depth:
            self._divs[-1].parts.append(data)

    # -- structure ----------------------------------------------------------

    def _close_table(self) -> None:
        header = tuple(_fold(cell) for cell in self._table_header)
        if header == _HISTORY_HEADER and not self.history_found:
            self.history_found = True
            self.history_rows = self._table_rows

    def _leaf(self, classes: set[str], text: str) -> None:
        if "panel-datos" in classes:
            self._section = _fold(text)
            self._pending = None
        elif "color-accent" in classes:
            self._pending = text.lower()
        else:
            # A value belongs to the label immediately before it. Any other
            # leaf disarms that label, or a cell of an unexpected width would
            # pass the label on to the next unlabelled cell — which is how a
            # forbidden value could reach an allowed key.
            label, self._pending = self._pending, None
            if label is None or not any(_VALUE_CELL_RE.match(c) for c in classes):
                return
            if self._section == _SECTION_GENERAL:
                key = _READ_GENERAL.get(label)
            elif self._section == _SECTION_DELIVERY:
                key = _READ_DELIVERY.get(label)
            else:
                key = None
            if key is not None and key not in self.fields:
                self.fields[key] = text


def _date(value: str | None) -> str | None:
    """Return a ``dd/MM/yy`` date as ISO, or ``None``."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d/%m/%y").date().isoformat()
    except ValueError:
        return None


def _number(value: str | None) -> float | None:
    """Return a decimal written with comma or point, or ``None``."""
    if not value:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def _events(rows: list[list[str]]) -> list[dict]:
    """Turn history rows into events, oldest to newest.

    The page lists newest first, so the document is reversed before a stable
    sort: rows that share a minute then stay in the page's own chronology
    (the later-listed row happened first).
    """
    events: list[tuple[datetime, dict]] = []
    for row in rows:
        if len(row) != 3:
            raise TIPSAParseError("history row has an unexpected number of cells")
        try:
            moment = datetime.strptime(row[0], _EVENT_TIME_FORMAT).replace(
                tzinfo=RESPONSE_TIMEZONE
            )
        except ValueError as err:
            raise TIPSAParseError("history row has an unparseable timestamp") from err
        events.append(
            (
                moment,
                {
                    "timestamp": moment.astimezone(UTC).isoformat(),
                    "status": row[1] or None,
                    "location": row[2] or None,
                },
            )
        )
    events.reverse()
    events.sort(key=lambda item: item[0])
    return [event for _, event in events]


def parse_page(html: str, tracking_code: str) -> dict:
    """Parse a shipment page into the raw parcel dict.

    Raises :class:`TIPSAParseError` when the page is not the expected shape or
    describes a different shipment than ``tracking_code``.
    """
    parser = _PageParser()
    parser.feed(html)
    parser.close()

    missing = _IDENTITY_INPUTS - parser.inputs.keys()
    if missing or not parser.history_found:
        raise TIPSAParseError("shipment page is missing expected elements")

    inputs = parser.inputs
    rebuilt = f"{inputs['CodAgeCargo']}{inputs['CodAgeOri']}{inputs['Albaran']}"
    if inputs["envio-localizado"] != tracking_code or rebuilt != tracking_code:
        raise TIPSAParseError(
            "shipment page does not match the requested tracking_code"
        )

    fields = parser.fields
    sender = fields.get("sender")
    pieces = fields.get("pieces")
    delivered_time = fields.get("delivered_time")
    return {
        "tracking_code": tracking_code,
        "status": clean_text(inputs["estado-localizado"]) or None,
        "current_status": fields.get("current_status") or None,
        # The page masks private names with asterisks; a masked value is noise.
        "sender": None if not sender or "*" in sender else sender,
        "shop_reference": fields.get("shop_reference") or None,
        "pieces": int(pieces) if pieces and pieces.isdigit() else None,
        "ship_date": _date(fields.get("ship_date")),
        "weight_kg": _number(fields.get("weight_kg")),
        "volume_m3": _number(fields.get("volume_m3")),
        "delivered_date": _date(fields.get("delivered_date")),
        "delivered_time": (
            delivered_time
            if delivered_time and re.fullmatch(r"\d{2}:\d{2}", delivered_time)
            else None
        ),
        "events": _events(parser.history_rows),
    }
