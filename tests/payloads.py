"""Sample TIPSA payloads shared by the test modules.

Two layers: ``*_sample`` build the parsed per-parcel dict the client hands to
the coordinator, and :func:`detail_html` / :func:`lookup_html` build the wire
bodies the client and parser consume. The HTML mirrors the live page's
structure, scrubbed to placeholder values. Its recipient, contact, address,
town, postcode and notes cells are filled with invented text purely so tests
can prove they are never read; see :data:`FORBIDDEN_VALUES`.
"""

from __future__ import annotations

from custom_components.tipsa.parser import _events as parse_events

ACTIVE_CODE = "0000200000200000000001"
DELIVERED_CODE = "0000100000100000000000"
POSTCODE = "28001"
SERVICIO = "11111111-2222-3333-4444-555555555555"
DETAIL_URL = (
    "https://dinapaqweb.tipsa-dinapaq.com/dinapaqweb/detalle_envio.php"
    f"?servicio={SERVICIO}&fecha=15/04/26"
)

# Invented values for the cells the parser must never read. A test asserts
# none of them appears anywhere in a parsed result or in diagnostics.
FORBIDDEN_VALUES = (
    "Ana Ejemplo Prueba",
    "Calle Inventada 1",
    "Villa Ejemplo",
    "99999",
    "Nota con Calle Inventada 1 Villa Ejemplo 99999",
    "Receptor Ejemplo",
    "Persona Contacto",
)

# History rows, newest first, as the page lists them: (stamp, wording, town).
DELIVERED_ROWS = [
    ("17/04/26 20:19", "ENTREGADO", " "),
    ("17/04/26 12:46", "REPARTO", " "),
    ("17/04/26 12:46", "LECTURA EN AGENCIA DESTINO AGENCIA  04", "TOWN ONE"),
    ("16/04/26 11:00", "Mal clasificado", " "),
    ("15/04/26 17:09", "TRANSITO", " "),
    ("15/04/26 17:09", "LECTURA EN HUB MADRID", "TOWN TWO "),
    ("10/04/26 02:53", "PENDIENTE DE ENTREGAR A TIPSA", " "),
]


def split_tracking_code(tracking_code: str) -> tuple[str, str, str]:
    """Split a 22-digit tracking_code into charge agency, origin agency, waybill."""
    return tracking_code[:6], tracking_code[6:12], tracking_code[12:]


def _history_table(rows: list[tuple[str, str, str]]) -> str:
    body = "".join(
        "<tr>"
        f'<td class="mdl-data-table__cell--non-numeric">{stamp}</td>'
        '<td class="mdl-data-table__cell--non-numeric ellipsis">'
        f'<span class="mdl-tooltip" for="d{i}">{wording}</span>'
        f'<span id="d{i}">{wording}</span></td>'
        '<td class="mdl-data-table__cell--non-numeric ellipsis">'
        f'<span class="mdl-tooltip" for="p{i}">{town}</span>'
        f'<span id="p{i}">{town}</span></td>'
        "</tr>"
        for i, (stamp, wording, town) in enumerate(rows, start=1)
    )
    return (
        '<table id="datos-generales-resto" class="mdl-data-table">'
        "<thead><tr>"
        "<th>FECHA/HORA</th><th>ESTADO</th><th>POBLACIÓN</th>"
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def _pair(label: str, value: str, cols: int = 6) -> str:
    return (
        f"<div class='mdl-cell mdl-cell--{cols}-col color-accent'>{label}</div>"
        f"<div class='mdl-cell mdl-cell--{cols}-col'>{value} </div>"
    )


def detail_html(
    tracking_code: str = DELIVERED_CODE,
    *,
    status: str = "ENTREGADO",
    sender: str = "Example Shop",
    rows: list[tuple[str, str, str]] | None = None,
    delivered: tuple[str, str] | None = ("17/04/26", "20:19"),
    table: bool = True,
    hidden_albaran: str | None = None,
) -> str:
    """Build a shipment page in the live structure, scrubbed."""
    cargo, origin, albaran = split_tracking_code(tracking_code)
    rows = DELIVERED_ROWS if rows is None else rows
    delivery_block = ""
    if delivered is not None:
        delivery_block = (
            "<div id='datos-generales' class='mdl-cell mdl-cell--12-col panel-datos'>"
            "<span>DATOS DE LA ENTREGA</span></div>"
            "<div id='datos-generales-resto' class='mdl-cell mdl-cell--12-col "
            "panel-datos-resto mdl-grid'>"
            "<div class='mdl-grid'>"
            f"{_pair('Receptor', 'Receptor Ejemplo', 4)}"
            f"{_pair('Observaciones', FORBIDDEN_VALUES[4], 4)}"
            "</div><div class='mdl-grid'>"
            f"{_pair('Fecha', delivered[0], 4)}{_pair('Hora', delivered[1], 4)}"
            "</div></div>"
        )
    return f"""<!DOCTYPE html>
<html><head><title>Detalle</title><script>var x = "<div>ignored</div>";</script></head>
<body>
<input type='hidden' id='CodAgeCargo' value='{cargo}'>\
<input type='hidden' id='CodAgeOri' value='{origin}'>\
<input type='hidden' id='Albaran' value='{hidden_albaran or albaran}'>
<input class="mdl-textfield__input" id="envio-localizado" type="text" value="{tracking_code}" readonly="true">
<input class="mdl-textfield__input" id="estado-localizado" type="text" value= "{status}" readonly="true">
<div id="stepper"><div id="descL_0">Documentado</div><div id="descL_4">En reparto</div></div>
<div id='datos-generales' class='mdl-cell mdl-cell--12-col panel-datos'>
<span>DATOS GENERALES</span></div>
<div id='datos-generales-resto' class='mdl-cell mdl-cell--12-col panel-datos-resto mdl-grid'>
{_pair("Remitente", sender)}
{_pair("Destinatario", "A*** E***** P*****")}
{_pair("Persona de contacto", FORBIDDEN_VALUES[6])}
{_pair("Dirección Destino", "C***** I******** *")}
{_pair("Población Destino", FORBIDDEN_VALUES[2])}
{_pair("Código postal", FORBIDDEN_VALUES[3])}
{_pair("Estado Actual", status)}
{_pair("Referencia", "SHOP-REF-1")}
{_pair("Bultos", "1", 3)}
{_pair("Fecha", "15/04/26", 3)}
{_pair("Kilos", "0,86", 3)}
{_pair("Volumen (m3)", "0.00", 3)}
{_pair("Observaciones", FORBIDDEN_VALUES[4])}
</div>
<div id='datos-generales' class='mdl-cell mdl-cell--12-col panel-datos'>
<span>AGENCIA DESTINO</span></div>
<div id='datos-generales-resto' class='mdl-cell mdl-cell--12-col panel-datos-resto mdl-grid'>
{_pair("Nombre", "AGENCIA  04")}
{_pair("Dirección", "Calle de la Agencia 9")}
{_pair("Población", "TOWN ONE")}
{_pair("Código postal", "88888")}
</div>
<div id="datos" class="datos mdl-grid mdl-cell mdl-cell--12-col">
<div id='datos-generales' class="mdl-cell mdl-cell--12-col panel-datos">
<span>DETALLES DEL ENVÍO</span></div>
<div id='datos-generales-resto' class="mdl-cell mdl-cell--12-col panel-datos-resto mdl-grid">
{_history_table(rows) if table else "<p>sin historial</p>"}
</div>
{delivery_block}
</div>
</body></html>"""


def detail_bytes(*args, **kwargs) -> bytes:
    """The page as the wire carries it: ISO-8859-1, no charset declared."""
    return detail_html(*args, **kwargs).encode("iso-8859-1")


def lookup_html(target: str | None = DETAIL_URL) -> str:
    """The postcode-gated door's body: a meta-refresh to the detail page."""
    if target is None:
        return "<html><head><title></title></head><body></body></html>"
    return (
        "\n\n\n<html>\n<head>\n"
        f"<meta http-equiv='refresh' content='0;URL={target}'>\n"
        "<title></title>\n<body></body>\n</head>\n</html>"
    )


EMPTY_TARGET = (
    "https://dinapaqweb.tipsa-dinapaq.com/dinapaqweb/detalle_envio.php?servicio=&fecha="
)


def _events(rows: list[tuple[str, str, str]]) -> list[dict]:
    """Parsed events, oldest to newest, for sample dicts."""
    return parse_events(
        [[stamp, wording.strip(), town.strip()] for stamp, wording, town in rows]
    )


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """The parsed dict for a delivered parcel."""
    return {
        "tracking_code": code,
        "status": "ENTREGADO",
        "current_status": "ENTREGADO",
        "sender": "Example Shop",
        "shop_reference": "SHOP-REF-1",
        "pieces": 1,
        "ship_date": "2026-04-15",
        "weight_kg": 0.86,
        "volume_m3": 0.0,
        "delivered_date": "2026-04-17",
        "delivered_time": "20:19",
        "events": _events(DELIVERED_ROWS),
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """The parsed dict for a parcel out for delivery."""
    sample = delivered_sample(code)
    sample.update(
        {
            "status": "REPARTO",
            "current_status": "REPARTO",
            "delivered_date": None,
            "delivered_time": None,
            "events": _events(DELIVERED_ROWS[1:]),
        }
    )
    return sample
