"""Diagnostics support for the TIPSA parcel tracker integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import TIPSAConfigEntry

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
#
# The page prints the recipient's address in cells the parser never reads, so
# none of that can reach a parcel dict; what is redacted here is what the
# integration does hold: the tracking_code, the postcode, the shipper's own
# tracking_code and name, and the depot towns. The two request URLs (the first
# carries the postcode, the second a shipment id) are never stored, and their
# parameter names are listed anyway in case one ever rides along. Status
# wordings stay readable on purpose: they are what an unrecognised-status
# report needs.
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "tracking_codes",
    "barcode",
    "sender",
    "receiver",
    "url",
    # parsed page fields
    "shop_reference",
    "location",
    # entry options
    "postal_code",
    # request parameters
    "id",
    "cp",
    "servicio",
    "fecha",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TIPSAConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the TIPSA config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
            "skipped_from_fetch": len(coordinator.delivered_codes),
        },
        "polling": {
            "tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "suspended": coordinator.update_interval is None,
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }
