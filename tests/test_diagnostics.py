"""Tests for TIPSA diagnostics."""

from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.tipsa.diagnostics import async_get_config_entry_diagnostics
from custom_components.tipsa.parcels import normalize_parcel
from custom_components.tipsa.parser import parse_page

from .payloads import (
    DELIVERED_CODE,
    DETAIL_URL,
    FORBIDDEN_VALUES,
    POSTCODE,
    SERVICIO,
    detail_html,
)


async def test_diagnostics_redacts_identifiers_and_never_holds_the_forbidden_cells(
    hass,
):
    """Diagnostics get pasted into public issues - nothing identifying may survive.

    The parcel is built from the real page rather than a hand-made dict, so the
    forbidden values actually travel through the parser. Asserting their absence
    from a dict that never held them would prove nothing.
    """
    parcel = normalize_parcel(
        parse_page(detail_html(), DELIVERED_CODE), include_history=True
    )
    entry = MagicMock()
    entry.options = {
        "postal_code": POSTCODE,
        "parcels": [{"tracking_code": DELIVERED_CODE, "postal_code": "08001"}],
    }
    entry.runtime_data.coordinator.current_tier_minutes = 15
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=15)
    entry.runtime_data.coordinator.data = [parcel]
    entry.runtime_data.coordinator.delivered = [parcel]
    entry.runtime_data.coordinator.delivered_codes = set()

    result = await async_get_config_entry_diagnostics(hass, entry)
    dumped = repr(result)

    assert result["counts"] == {
        "incoming_active": 1,
        "delivered": 1,
        "skipped_from_fetch": 0,
    }
    assert result["entry_options"]["postal_code"] == "**REDACTED**"
    assert result["entry_options"]["parcels"][0]["postal_code"] == "**REDACTED**"
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    for key in ("barcode", "sender"):
        assert result["incoming"][0][key] == "**REDACTED**"
    raw = result["incoming"][0]["raw"]
    for key in ("tracking_code", "shop_reference", "sender"):
        assert raw[key] == "**REDACTED**"
    located = [event for event in raw["events"] if event["location"] is not None]
    assert located and all(e["location"] == "**REDACTED**" for e in located)
    # none of the identifiers, request values or the six forbidden cells survive
    for value in (
        DELIVERED_CODE,
        POSTCODE,
        "08001",
        SERVICIO,
        DETAIL_URL,
        "SHOP-REF-1",
        "Example Shop",
        *FORBIDDEN_VALUES,
    ):
        assert value not in dumped
    # status wordings stay readable, they are what an unrecognised-status report needs
    assert result["incoming"][0]["status"] == "delivered"
    assert raw["status"] == "ENTREGADO"
    assert raw["events"][-1]["status"] == "ENTREGADO"


async def test_diagnostics_reports_suspended_polling(hass):
    """update_interval None (Section 2.1's full stop) must be visible, not just absent."""
    entry = MagicMock()
    entry.options = {"parcels": []}
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"] == {
        "tier_minutes": None,
        "update_interval_seconds": None,
        "suspended": True,
    }
