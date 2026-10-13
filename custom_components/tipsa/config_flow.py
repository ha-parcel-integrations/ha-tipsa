"""Config flow for the TIPSA parcel tracker integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_TRACKING_CODE_RE = re.compile(r"^\d{22}$")
# Spain is five digits and the only form probed. Portugal (NNNN, NNNN-NNN) and
# Andorra (ADNNN) have not been seen through the carrier's postcode gate, so
# they are accepted by shape and left for the carrier to judge.
_POSTCODE_RE = re.compile(r"^(?:\d{5}|\d{4}(?:-\d{3})?|AD\d{3})$")
_OVERRIDE_SEPARATOR = "@"


def normalize_tracking_code(value: str) -> str:
    """Return the tracking_code with spaces, dots and dashes stripped."""
    return re.sub(r"[\s.\-]+", "", value or "")


def valid_tracking_code(value: str) -> bool:
    """Whether ``value`` is a full 22-digit tracking_code.

    A bare 10-digit waybill is answered by the carrier like an unknown
    shipment, so a request would buy nothing.
    """
    return bool(_TRACKING_CODE_RE.match(value))


def normalize_postcode(value: str) -> str:
    """Strip all whitespace and upper-case the postcode.

    A surviving trailing space is what makes the carrier answer ``Error en el
    CP``, so this must run before every request.
    """
    return re.sub(r"\s+", "", value or "").upper()


def valid_postcode(value: str) -> bool:
    """Whether ``value`` has the shape of a Spanish, Portuguese or Andorran postcode."""
    return bool(_POSTCODE_RE.match(value))


def parse_parcel_entry(value: str) -> tuple[str, str | None]:
    """Split ``tracking_code`` or ``tracking_code@postcode`` into its normalised parts."""
    tracking_code, _, postcode = (value or "").partition(_OVERRIDE_SEPARATOR)
    postcode = normalize_postcode(postcode)
    return normalize_tracking_code(tracking_code), postcode or None


def _current_parcels(entry: ConfigEntry) -> list[dict[str, str]]:
    """Return a mutable copy of the tracked parcels list."""
    return [dict(item) for item in entry.options.get(CONF_PARCELS, [])]


class TIPSAConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI-driven configuration flow for the TIPSA integration."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> TIPSAOptionsFlowHandler:
        """Return the options flow handler."""
        return TIPSAOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create a TIPSA hub, one per delivery postcode.

        The postcode is checked by the carrier on every lookup and is stored on
        the hub, so adding a parcel afterwards needs only its tracking_code.
        Several hubs are allowed (home and work); the same postcode only once.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            postal_code = normalize_postcode(user_input[CONF_POSTAL_CODE])
            if not valid_postcode(postal_code):
                errors[CONF_POSTAL_CODE] = "invalid_postcode"
            else:
                await self.async_set_unique_id(postal_code)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"TIPSA ({postal_code})",
                    data={},
                    options={
                        CONF_PARCELS: [],
                        CONF_POSTAL_CODE: postal_code,
                        CONF_DELIVERED_FILTER_TYPE: DEFAULT_DELIVERED_FILTER_TYPE,
                        CONF_DELIVERED_FILTER_AMOUNT: DEFAULT_DELIVERED_FILTER_AMOUNT,
                        CONF_INCLUDE_HISTORY: DEFAULT_INCLUDE_HISTORY,
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_POSTAL_CODE): str}),
            errors=errors,
        )


class TIPSAOptionsFlowHandler(OptionsFlow):
    """Manage tracked parcels separately from integration settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer parcel management separately from integration settings."""
        return self.async_show_menu(
            step_id="init", menu_options=["parcels", "settings"]
        )

    async def async_step_parcels(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the complete tracked-code list."""
        errors: dict[str, str] = {}
        hub_postcode = self.config_entry.options.get(CONF_POSTAL_CODE)
        if user_input is not None:
            parcels: list[dict[str, str]] = []
            seen: set[str] = set()
            for value in user_input.get("tracking_codes") or []:
                code, postcode = parse_parcel_entry(value)
                if not code:
                    continue
                if not valid_tracking_code(code):
                    errors["base"] = "invalid_tracking_code"
                elif postcode is not None and not valid_postcode(postcode):
                    errors["base"] = "invalid_postcode"
                elif code not in seen:
                    seen.add(code)
                    parcel = {CONF_TRACKING_CODE: code}
                    if postcode is not None and postcode != hub_postcode:
                        parcel[CONF_POSTAL_CODE] = postcode
                    parcels.append(parcel)
            if not errors:
                return self.async_create_entry(
                    title="",
                    data={**self.config_entry.options, CONF_PARCELS: parcels},
                )
        current_codes = [
            p[CONF_TRACKING_CODE]
            + (f"{_OVERRIDE_SEPARATOR}{p[CONF_POSTAL_CODE]}" if p.get(CONF_POSTAL_CODE) else "")
            for p in _current_parcels(self.config_entry)
        ]
        schema = vol.Schema(
            {
                vol.Optional("tracking_codes"): selector.TextSelector(
                    selector.TextSelectorConfig(multiple=True)
                )
            }
        )
        return self.async_show_form(
            step_id="parcels",
            data_schema=self.add_suggested_values_to_schema(
                schema, {"tracking_codes": current_codes}
            ),
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the non-parcel integration settings."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    **self.config_entry.options,
                    CONF_DELIVERED_FILTER_TYPE: user_input[CONF_DELIVERED_FILTER_TYPE],
                    CONF_DELIVERED_FILTER_AMOUNT: int(
                        user_input[CONF_DELIVERED_FILTER_AMOUNT]
                    ),
                    CONF_INCLUDE_HISTORY: bool(user_input[CONF_INCLUDE_HISTORY]),
                },
            )
        current = self.config_entry.options
        schema: dict[Any, Any] = {
            vol.Required(
                CONF_DELIVERED_FILTER_TYPE,
                default=current.get(
                    CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
                ),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["days", "parcels"],
                    translation_key=CONF_DELIVERED_FILTER_TYPE,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Required(
                CONF_DELIVERED_FILTER_AMOUNT,
                default=current.get(
                    CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=365, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_INCLUDE_HISTORY,
                default=current.get(CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY),
            ): selector.BooleanSelector(),
        }
        return self.async_show_form(step_id="settings", data_schema=vol.Schema(schema))
