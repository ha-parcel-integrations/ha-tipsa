"""Services for the TIPSA parcel tracker integration.

`tipsa.track_parcel` / `tipsa.untrack_parcel` let you add or remove a
tracked parcel without opening the integration options — so a Lovelace button
can start tracking a parcel straight from a dashboard.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .config_flow import (
    normalize_postcode,
    normalize_tracking_code,
    valid_postcode,
    valid_tracking_code,
)
from .const import CONF_PARCELS, CONF_POSTAL_CODE, CONF_TRACKING_CODE, DOMAIN

SERVICE_TRACK_PARCEL = "track_parcel"
SERVICE_UNTRACK_PARCEL = "untrack_parcel"

_TRACK_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TRACKING_CODE): cv.string,
        vol.Optional(CONF_POSTAL_CODE): cv.string,
    }
)
_UNTRACK_SCHEMA = vol.Schema({vol.Required(CONF_TRACKING_CODE): cv.string})


def _resolve_entry(hass: HomeAssistant, postal_code: str | None):
    """Pick the TIPSA hub to act on, and whether the postcode is an override.

    With one hub, that hub. With several, a ``postal_code`` naming one of them
    selects it; omitted or unmatched, the call is ambiguous and raises.
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("TIPSA is not set up")
    if postal_code:
        for entry in entries:
            if entry.options.get(CONF_POSTAL_CODE) == postal_code:
                return entry, False
        if len(entries) == 1:
            return entries[0], True
        raise ServiceValidationError(
            "No TIPSA hub for that postal code - add one or pass the postal code of an existing hub"
        )
    if len(entries) == 1:
        return entries[0], False
    raise ServiceValidationError(
        "Multiple TIPSA hubs are set up - pass postal_code to choose one"
    )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the TIPSA services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_TRACK_PARCEL):
        return

    async def _track(call: ServiceCall) -> None:
        tracking_code = normalize_tracking_code(call.data[CONF_TRACKING_CODE])
        if not valid_tracking_code(tracking_code):
            raise ServiceValidationError(
                "A TIPSA tracking code is 22 digits (the 10-digit waybill alone is not enough)"
            )
        postal_code = normalize_postcode(call.data.get(CONF_POSTAL_CODE) or "")
        if postal_code and not valid_postcode(postal_code):
            raise ServiceValidationError("That is not a valid postal code")
        entry, override = _resolve_entry(hass, postal_code)

        parcels = [dict(p) for p in entry.options.get(CONF_PARCELS, [])]
        if any(p[CONF_TRACKING_CODE] == tracking_code for p in parcels):
            return  # already tracked — no-op
        parcel = {CONF_TRACKING_CODE: tracking_code}
        if override:
            parcel[CONF_POSTAL_CODE] = postal_code
        parcels.append(parcel)
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_PARCELS: parcels}
        )

    async def _untrack(call: ServiceCall) -> None:
        tracking_code = normalize_tracking_code(call.data[CONF_TRACKING_CODE])
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError("TIPSA is not set up")
        # Remove the parcel from whichever hub(s) track it.
        for entry in entries:
            current = entry.options.get(CONF_PARCELS, [])
            kept = [p for p in current if p[CONF_TRACKING_CODE] != tracking_code]
            if len(kept) != len(current):
                hass.config_entries.async_update_entry(
                    entry, options={**entry.options, CONF_PARCELS: kept}
                )

    hass.services.async_register(
        DOMAIN, SERVICE_TRACK_PARCEL, _track, schema=_TRACK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UNTRACK_PARCEL, _untrack, schema=_UNTRACK_SCHEMA
    )


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the TIPSA services (once the last hub unloads)."""
    for service in (SERVICE_TRACK_PARCEL, SERVICE_UNTRACK_PARCEL):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
