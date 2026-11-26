"""The parcel contract's checks, run over every normalised sample."""

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from custom_components.tipsa.calendar import TIPSADeliveriesCalendar
from custom_components.tipsa.const import ParcelStatus
from custom_components.tipsa.parcels import (
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
)
from custom_components.tipsa.sensor import (
    TIPSAEnRouteToPickupPointSensor,
    TIPSANextDeliverySensor,
)

from .payloads import active_sample, delivered_sample

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
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


def _with_status(wording: str) -> dict:
    sample = active_sample()
    sample["status"] = wording
    return sample


def _collected() -> dict:
    """A parcel that reached a collection point and was then handed over."""
    sample = delivered_sample()
    sample["events"] = [
        {"timestamp": "2026-04-16T09:00:00+00:00", "status": "DISPONIBLE EN PUNTO"},
        {"timestamp": "2026-04-17T18:19:00+00:00", "status": "ENTREGADO"},
    ]
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "at_pickup_point": lambda: _with_status("DISPONIBLE EN PUNTO"),
    "collected": _collected,
    "problem": lambda: _with_status("INCIDENCIA ejemplo"),
    "bare": lambda: {"tracking_code": "0000200000200000000009"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True


def test_a_home_delivery_is_not_pickup():
    assert normalize_parcel(delivered_sample())["pickup"] is False


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2099-04-29T13:00:00+00:00", None, zone=UTC)
    assert end == "2099-04-29T23:59:59+00:00"
    assert start == "2099-04-29T13:00:00+00:00"


def test_a_date_covers_the_whole_day():
    assert planned_window("2099-04-29", None, zone=UTC) == (
        "2099-04-29T00:00:00+00:00",
        "2099-04-29T23:59:59+00:00",
    )


def test_the_window_is_emptied_for_a_finished_or_waiting_parcel():
    window = ("2099-04-29T00:00:00+00:00", "2099-04-29T23:59:59+00:00")
    assert planned_for_status(ParcelStatus.IN_TRANSIT, *window) == window
    for status in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert planned_for_status(status, *window) == (None, None)


def _coordinator(data: list[dict]) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def _entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "e1"
    return entry


def _listed(barcode: str, **fields) -> dict:
    return {
        "barcode": barcode,
        "sender": "Example Shop",
        "status": ParcelStatus.IN_TRANSIT,
        "pickup": False,
        "pickup_point": None,
        "url": None,
        "planned_from": None,
        "planned_to": None,
    } | fields


def test_next_delivery_skips_a_window_that_has_ended():
    sensor = TIPSANextDeliverySensor(
        _coordinator(
            [
                _listed(
                    "OLD",
                    planned_from="2020-01-01T10:00:00+00:00",
                    planned_to="2020-01-01T23:59:59+00:00",
                ),
                _listed("NEW", planned_from="2099-01-01T10:00:00+00:00"),
            ]
        ),
        _entry(),
    )
    assert sensor.native_value == datetime(2099, 1, 1, 10, tzinfo=UTC)
    assert sensor.extra_state_attributes["barcode"] == "NEW"


def test_en_route_excludes_returning_and_arrived():
    sensor = TIPSAEnRouteToPickupPointSensor(
        _coordinator(
            [
                _listed("GO", pickup=True),
                _listed("BACK", pickup=True, status=ParcelStatus.RETURNING),
                _listed("HERE", pickup=True, status=ParcelStatus.AT_PICKUP_POINT),
            ]
        ),
        _entry(),
    )
    assert [p["barcode"] for p in sensor.extra_state_attributes["parcels"]] == ["GO"]


def test_a_whole_day_window_is_an_all_day_event():
    calendar = TIPSADeliveriesCalendar(
        _coordinator(
            [
                _listed(
                    "DAY",
                    planned_from="2099-01-01T00:00:00+00:00",
                    planned_to="2099-01-01T23:59:59+00:00",
                )
            ]
        ),
        _entry(),
    )
    (event,) = calendar._events()
    assert event.start == date(2099, 1, 1)
    assert event.end == date(2099, 1, 2)
