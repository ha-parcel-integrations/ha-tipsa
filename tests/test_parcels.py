"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance: ``parcels.py`` is free of I/O so the
carrier-specific mapping can be tested as plain functions.
"""

from datetime import UTC, datetime, timedelta
import logging

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tipsa.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    PENDING_CAPABILITIES,
    ParcelStatus,
)
from custom_components.tipsa.parcels import (
    apply_delivered_filter,
    build_history,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    normalize_wording,
    parse_iso,
    sort_parcels_by_ts,
)

from .payloads import DELIVERED_ROWS, FORBIDDEN_VALUES, active_sample, delivered_sample

# ---------------------------------------------------------------------------
# status mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wording", "expected"),
    [
        ("ENTREGADO", ParcelStatus.DELIVERED),
        ("Entregado", ParcelStatus.DELIVERED),
        ("REPARTO", ParcelStatus.OUT_FOR_DELIVERY),
        ("En reparto", ParcelStatus.OUT_FOR_DELIVERY),
        ("TRANSITO", ParcelStatus.IN_TRANSIT),
        ("TRÁNSITO", ParcelStatus.IN_TRANSIT),
        ("En camino", ParcelStatus.IN_TRANSIT),
        ("Pendiente de llegada", ParcelStatus.IN_TRANSIT),
        ("Mal clasificado", ParcelStatus.IN_TRANSIT),
        ("LEIDO EN DESTINO", ParcelStatus.IN_TRANSIT),
        ("LEÍDO EN DESTINO", ParcelStatus.IN_TRANSIT),
        ("Ausente", ParcelStatus.PROBLEM),
        ("AUSENTE", ParcelStatus.PROBLEM),
        ("PENDIENTE DE ENTREGAR A TIPSA", ParcelStatus.REGISTERED),
        ("Documentado", ParcelStatus.REGISTERED),
        ("LECTURA EN AGENCIA DESTINO AGENCIA  04", ParcelStatus.IN_TRANSIT),
        ("Lectura en hub Barcelona", ParcelStatus.IN_TRANSIT),
        ("DISPONIBLE", ParcelStatus.AT_PICKUP_POINT),
        ("DISPONIBLE EN PUNTO EJEMPLO", ParcelStatus.AT_PICKUP_POINT),
        ("incidencia", ParcelStatus.PROBLEM),
        ("INCIDENCIA (solicitada en otra direccion)", ParcelStatus.PROBLEM),
        ("  entregado  ", ParcelStatus.DELIVERED),
    ],
)
def test_map_event_status_known(wording, expected):
    assert map_event_status(wording) is expected


def test_prefix_wordings_need_the_whole_prefix():
    """Only the two documented prefixes are open-ended, nothing else."""
    assert map_event_status("LECTURA EN") is None
    assert map_event_status("ENTREGADO A VECINO") is None


def test_map_event_status_missing_and_unmapped_are_none():
    assert map_event_status(None) is None
    assert map_event_status("   ") is None
    assert map_event_status("Entrega parcial") is None


def test_normalize_wording_folds_accents_case_and_whitespace():
    assert normalize_wording(" Tránsito\xa0  rápido ") == "TRANSITO RAPIDO"
    assert normalize_wording(None) == ""


def test_map_parcel_status_uses_the_status_field_first():
    # the field says delivered even though the newest mappable row says transit
    assert map_parcel_status("ENTREGADO", ["TRANSITO"]) is ParcelStatus.DELIVERED


def test_a_missing_status_field_still_falls_back_to_the_history(caplog):
    """A page that drops the field still knows its parcel was in transit."""
    with caplog.at_level(logging.WARNING):
        assert map_parcel_status(None, ["TRANSITO"]) is ParcelStatus.IN_TRANSIT
    assert caplog.records == []


def test_a_missing_status_field_with_no_usable_history_is_unknown_and_silent(caplog):
    """Nothing to map and nothing to report: no wording failed to map."""
    with caplog.at_level(logging.WARNING):
        assert map_parcel_status(None, ["QUIEN SABE"]) is ParcelStatus.UNKNOWN
        assert map_parcel_status(None, []) is ParcelStatus.UNKNOWN
    assert caplog.records == []


def test_an_unconfirmed_wording_asks_for_confirmation_once(caplog):
    with caplog.at_level(logging.WARNING):
        assert map_parcel_status("Ausente") is ParcelStatus.PROBLEM
        assert map_parcel_status("ausente") is ParcelStatus.PROBLEM
        assert map_event_status("Leido en destino") is ParcelStatus.IN_TRANSIT
    asks = [r for r in caplog.records if "please confirm" in r.getMessage()]
    assert len(asks) == 2
    assert {"AUSENTE", "LEIDO EN DESTINO"} == {
        w
        for w in ("AUSENTE", "LEIDO EN DESTINO")
        if any(w in r.getMessage() for r in asks)
    }


def test_a_confirmed_wording_does_not_ask(caplog):
    with caplog.at_level(logging.WARNING):
        assert map_parcel_status("Entregado") is ParcelStatus.DELIVERED
    assert "please confirm" not in caplog.text


def test_unmapped_status_falls_back_to_newest_mappable_history_row(caplog):
    """Oldest to newest: the newest mappable row wins, an unmapped newer one is skipped."""
    history = ["PENDIENTE DE ENTREGAR A TIPSA", "TRANSITO", "Entrega parcial"]
    with caplog.at_level(logging.WARNING):
        assert map_parcel_status("Devuelto", history) is ParcelStatus.IN_TRANSIT
    # the fallback resolves the status, but the unknown wording is still reported
    warnings = [r for r in caplog.records if "Unrecognised TIPSA" in r.getMessage()]
    assert len(warnings) == 1
    assert "DEVUELTO" in warnings[0].getMessage()


def test_unmapped_status_with_no_mappable_history_warns_once(caplog):
    with caplog.at_level(logging.WARNING):
        assert (
            map_parcel_status("Devuelto", ["Entrega parcial"]) is ParcelStatus.UNKNOWN
        )
        assert map_parcel_status("Devuelto", None) is ParcelStatus.UNKNOWN
    warnings = [r for r in caplog.records if "Unrecognised TIPSA" in r.getMessage()]
    assert len(warnings) == 1
    assert "DEVUELTO" in warnings[0].getMessage()
    assert "unrecognised_status.yml" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z") == datetime(
        2026, 4, 29, 13, 12, 42, tzinfo=UTC
    )
    assert parse_iso("2026-04-29T13:12:42").tzinfo is UTC
    assert parse_iso("nope") is None
    assert parse_iso(None) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_keeps_order_and_maps_each_row():
    history = build_history(delivered_sample()["events"])
    assert [h["raw_status"] for h in history][-1] == "ENTREGADO"
    assert history[-1]["status"] is ParcelStatus.DELIVERED
    assert history[0]["status"] is ParcelStatus.REGISTERED
    assert set(history[0]) == {"timestamp", "status", "raw_status"}


def test_build_history_caps_to_max_events():
    events = [
        {"timestamp": f"2026-04-{day:02d}T10:00:00+00:00", "status": "TRANSITO"}
        for day in range(1, 11)
    ]
    history = build_history(events, max_events=3)
    assert [h["timestamp"][8:10] for h in history] == ["08", "09", "10"]


def test_build_history_skips_malformed_rows():
    assert build_history(None) == []
    assert build_history(["x", {"status": "TRANSITO"}]) == []


def test_build_history_warns_once_for_an_unmapped_wording(caplog):
    events = [{"timestamp": "2026-04-01T10:00:00+00:00", "status": "Entrega parcial"}]
    with caplog.at_level(logging.WARNING):
        history = build_history(events)
        build_history(events)
    assert history[0]["status"] is None
    assert history[0]["raw_status"] == "Entrega parcial"
    assert len([r for r in caplog.records if "history status" in r.getMessage()]) == 1


# ---------------------------------------------------------------------------
# normalize_parcel
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    assert CAPABILITIES <= KNOWN_CAPABILITIES
    assert PENDING_CAPABILITIES <= KNOWN_CAPABILITIES


def test_a_capability_is_never_both_populated_and_pending():
    assert not CAPABILITIES & PENDING_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    delivered = normalize_parcel(delivered_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)

    assert "weight" in CAPABILITIES and delivered["weight"] is not None
    assert "history" in CAPABILITIES and with_history["history"] is not None
    assert "url" not in CAPABILITIES and delivered["url"] is None


def test_fields_the_page_cannot_supply_are_none():
    """Pending and unsupported fields stay literal None until a parcel shows them."""
    for parcel in (
        normalize_parcel(delivered_sample()),
        normalize_parcel(active_sample()),
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None
        assert parcel["pickup_point"] is None
        assert parcel["dimensions"] is None
        assert parcel["receiver"] is None
    assert {"pickup_point"} == PENDING_CAPABILITIES


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "TIPSA"
    assert parcel["barcode"] == delivered_sample()["tracking_code"]
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "ENTREGADO"
    assert parcel["delivered"] is True
    # 17/04/26 20:19 Madrid summer time is 18:19 UTC
    assert parcel["delivered_at"] == "2026-04-17T18:19:00+00:00"
    assert parcel["sender"] == "Example Shop"
    assert parcel["weight"] == 0.86
    assert parcel["url"] is None
    assert parcel["pickup"] is False
    assert parcel["history"] is None


def test_there_is_no_per_parcel_url():
    """The only per-parcel link bypasses the postcode gate, so none is offered."""
    assert normalize_parcel(delivered_sample())["url"] is None


def test_delivered_at_falls_back_to_the_delivery_block():
    raw = delivered_sample()
    raw["events"] = []
    parcel = normalize_parcel(raw)
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered_at"] == "2026-04-17T18:19:00+00:00"


@pytest.mark.parametrize(
    ("date", "clock"), [(None, None), ("2026-04-17", None), ("2026-04-17", "99:99")]
)
def test_delivered_at_is_none_without_a_usable_delivery_block(date, clock):
    raw = delivered_sample()
    raw.update({"events": [], "delivered_date": date, "delivered_time": clock})
    assert normalize_parcel(raw)["delivered_at"] is None


def test_active_parcel_is_not_delivered():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] is ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_normalize_history_is_opt_in_and_chronological():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    stamps = [h["timestamp"] for h in parcel["history"]]
    assert stamps == sorted(stamps)
    assert parcel["history"][-1]["status"] is ParcelStatus.DELIVERED


def test_a_tie_keeps_the_page_chronology_so_the_newest_row_is_last():
    """Rows sharing a minute: the page lists the later event first.

    LECTURA EN AGENCIA DESTINO and REPARTO share 12:46; REPARTO is the later
    one, so it must come after, or the "newest mappable row" fallback would
    report in_transit for a parcel that is out for delivery.
    """
    raw = delivered_sample()
    raw["status"] = "Devuelto"  # unmapped, forces the history fallback
    raw["events"] = [
        e for e in raw["events"] if not e["status"].startswith("ENTREGADO")
    ]
    assert normalize_parcel(raw)["status"] is ParcelStatus.OUT_FOR_DELIVERY


def test_pending_placeholder_for_unknown_shipment():
    parcel = normalize_parcel({"tracking_code": "0000100000100000000000"})
    assert parcel["status"] is ParcelStatus.UNKNOWN
    assert parcel["raw_status"] is None
    assert parcel["delivered"] is False
    assert parcel["history"] is None


def test_normalize_keeps_raw_payload_and_nothing_forbidden():
    raw = delivered_sample()
    parcel = normalize_parcel(raw, include_history=True)
    assert parcel["raw"] is raw
    assert len(DELIVERED_ROWS) == len(raw["events"])
    assert not any(value in repr(parcel) for value in FORBIDDEN_VALUES)


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(UTC)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
