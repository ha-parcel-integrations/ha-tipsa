"""Tests for the shipment-page parser."""

import json

import pytest

from custom_components.tipsa.parser import TIPSAParseError, clean_text, parse_page

from .payloads import (
    DELIVERED_CODE,
    DELIVERED_ROWS,
    FORBIDDEN_VALUES,
    detail_html,
)


def test_parses_the_permitted_cells():
    parsed = parse_page(detail_html(), DELIVERED_CODE)

    assert parsed["tracking_code"] == DELIVERED_CODE
    assert parsed["status"] == "ENTREGADO"
    assert parsed["current_status"] == "ENTREGADO"
    assert parsed["sender"] == "Example Shop"
    assert parsed["shop_reference"] == "SHOP-REF-1"
    assert parsed["pieces"] == 1
    assert parsed["ship_date"] == "2026-04-15"
    assert parsed["weight_kg"] == 0.86  # decimal comma
    assert parsed["volume_m3"] == 0.0
    assert parsed["delivered_date"] == "2026-04-17"
    assert parsed["delivered_time"] == "20:19"


def test_the_six_forbidden_cells_never_reach_the_result():
    """The page masks some of them and prints the rest unmasked; none may be read."""
    parsed = parse_page(detail_html(), DELIVERED_CODE)
    dumped = repr(parsed)

    for value in FORBIDDEN_VALUES:
        assert value not in dumped
    # the masked spellings and the depot block are equally absent
    for fragment in ("***", "Calle de la Agencia", "88888"):
        assert fragment not in dumped
    assert "Receptor" not in dumped
    assert "Observaciones" not in dumped


def test_the_depot_town_from_the_history_table_is_the_only_location():
    parsed = parse_page(detail_html(), DELIVERED_CODE)
    towns = {e["location"] for e in parsed["events"]}
    assert towns == {None, "TOWN ONE", "TOWN TWO"}


def test_history_is_oldest_to_newest_with_madrid_time_in_utc():
    events = parse_page(detail_html(), DELIVERED_CODE)["events"]

    assert len(events) == len(DELIVERED_ROWS)
    stamps = [e["timestamp"] for e in events]
    assert stamps == sorted(stamps)
    assert events[0]["status"] == "PENDIENTE DE ENTREGAR A TIPSA"
    assert events[0]["timestamp"] == "2026-04-10T00:53:00+00:00"  # CEST, UTC+2
    assert events[-1]["status"] == "ENTREGADO"


def test_equal_timestamps_keep_the_pages_chronology():
    events = parse_page(detail_html(), DELIVERED_CODE)["events"]
    wordings = [e["status"] for e in events]
    # the page lists TRANSITO above LECTURA EN HUB MADRID; the lower row came first
    assert wordings.index("LECTURA EN HUB MADRID") < wordings.index("TRANSITO")
    assert wordings.index("LECTURA EN AGENCIA DESTINO AGENCIA 04") < wordings.index(
        "REPARTO"
    )


def test_each_history_cell_is_read_once_despite_the_tooltip_duplicate():
    events = parse_page(detail_html(), DELIVERED_CODE)["events"]
    assert events[-1]["status"] == "ENTREGADO"  # not ENTREGADOENTREGADO
    assert all("ENTREGADOENTREGADO" not in (e["status"] or "") for e in events)


def test_a_blank_town_cell_is_none():
    events = parse_page(detail_html(), DELIVERED_CODE)["events"]
    assert events[-1]["location"] is None


def test_a_masked_sender_is_skipped():
    parsed = parse_page(detail_html(sender="J***** M*****"), DELIVERED_CODE)
    assert parsed["sender"] is None


def test_a_page_without_a_delivery_block_still_parses():
    parsed = parse_page(detail_html(delivered=None), DELIVERED_CODE)
    assert parsed["delivered_date"] is None
    assert parsed["delivered_time"] is None


def test_a_page_with_no_history_rows_is_valid():
    parsed = parse_page(detail_html(rows=[]), DELIVERED_CODE)
    assert parsed["events"] == []


def test_unparseable_numbers_and_dates_become_none():
    html = (
        detail_html()
        .replace("0,86", "heavy")
        .replace("15/04/26 </div>", "soon </div>")
        .replace("<div class='mdl-cell mdl-cell--3-col'>1 </div>", "<div class='mdl-cell mdl-cell--3-col'>x </div>")
        .replace(">20:19 <", ">late <")
    )
    parsed = parse_page(html, DELIVERED_CODE)
    assert parsed["weight_kg"] is None
    assert parsed["ship_date"] is None
    assert parsed["pieces"] is None
    assert parsed["delivered_time"] is None


def test_a_label_without_a_value_does_not_leak_onto_the_next_cell():
    html = detail_html().replace(
        "<div class='mdl-cell mdl-cell--6-col'>Example Shop </div>", ""
    )
    assert parse_page(html, DELIVERED_CODE)["sender"] is None


def test_a_value_cell_of_an_unexpected_width_does_not_arm_the_next_cell():
    """An allowed label must not survive onto a later, unlabelled cell.

    A page change that widens a value cell would otherwise hand the label to
    whatever cell comes next — the one route by which a forbidden value could
    reach an allowed key.
    """
    html = detail_html().replace(
        "<div class='mdl-cell mdl-cell--6-col'>Example Shop </div>",
        "<div class='mdl-cell mdl-cell--12-col'>Example Shop </div>"
        "<div class='mdl-cell mdl-cell--6-col'>Calle Inventada 1, 99999</div>",
    )
    parsed = parse_page(html, DELIVERED_CODE)
    assert parsed["sender"] is None
    assert "Inventada" not in json.dumps(parsed)


def test_a_missing_history_table_fails_closed():
    with pytest.raises(TIPSAParseError, match="missing"):
        parse_page(detail_html(table=False), DELIVERED_CODE)


def test_a_changed_history_header_fails_closed():
    html = detail_html().replace("<th>ESTADO</th>", "<th>SITUACION</th>")
    with pytest.raises(TIPSAParseError, match="missing"):
        parse_page(html, DELIVERED_CODE)


def test_a_missing_identity_input_fails_closed():
    html = detail_html().replace("id='Albaran'", "id='Other'")
    with pytest.raises(TIPSAParseError, match="missing"):
        parse_page(html, DELIVERED_CODE)


def test_a_different_tracking_code_in_the_page_is_rejected():
    with pytest.raises(TIPSAParseError, match="does not match"):
        parse_page(detail_html(), "0000900000900000000009")


def test_the_rebuilt_tracking_code_must_agree_independently():
    """envio-localizado matches but the hidden split does not."""
    with pytest.raises(TIPSAParseError, match="does not match"):
        parse_page(detail_html(hidden_albaran="9999999999"), DELIVERED_CODE)


def test_a_history_row_with_the_wrong_cell_count_fails_closed():
    broken = detail_html(rows=[("17/04/26 20:19", "ENTREGADO", " ")]).replace(
        "</td></tr></tbody>", "</td><td>extra</td></tr></tbody>"
    )
    with pytest.raises(TIPSAParseError, match="cells"):
        parse_page(broken, DELIVERED_CODE)


def test_a_history_row_with_a_bad_timestamp_fails_closed():
    with pytest.raises(TIPSAParseError, match="timestamp"):
        parse_page(
            detail_html(rows=[("yesterday", "ENTREGADO", " ")]), DELIVERED_CODE
        )


def test_clean_text_collapses_whitespace_and_nbsp():
    assert clean_text(" a\xa0\n b  ") == "a b"


def test_a_stray_value_cell_without_a_label_is_ignored():
    html = detail_html().replace(
        "<div id='datos-generales-resto'",
        "<div class='mdl-cell mdl-cell--6-col'>stray</div><div id='datos-generales-resto'",
        1,
    )
    parsed = parse_page(html, DELIVERED_CODE)
    assert "stray" not in repr(parsed)


def test_an_empty_weight_cell_is_none():
    html = detail_html().replace("0,86 </div>", " </div>")
    assert parse_page(html, DELIVERED_CODE)["weight_kg"] is None
