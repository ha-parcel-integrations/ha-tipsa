"""Tests for the TIPSA config and options flow."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tipsa.config_flow import (
    normalize_postcode,
    normalize_tracking_code,
    parse_parcel_entry,
    valid_postcode,
    valid_tracking_code,
)
from custom_components.tipsa.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DOMAIN,
)

from .payloads import ACTIVE_CODE, DELIVERED_CODE, POSTCODE


def test_normalize_tracking_code_strips_spaces_dots_and_dashes():
    assert normalize_tracking_code("0000 1000-0010.0000000000") == "0000100000100000000000"
    assert normalize_tracking_code("") == ""
    assert normalize_tracking_code(None) == ""


def test_valid_tracking_code_needs_all_22_digits():
    assert valid_tracking_code(DELIVERED_CODE)
    assert not valid_tracking_code("1234567890")  # bare 10-digit waybill
    assert not valid_tracking_code(DELIVERED_CODE + "0")
    assert not valid_tracking_code("000010000010000000000A")
    assert not valid_tracking_code("")


def test_normalize_postcode_strips_all_whitespace():
    assert normalize_postcode(" 28 001\t") == "28001"
    assert normalize_postcode("ad 500") == "AD500"
    assert normalize_postcode(None) == ""


@pytest.mark.parametrize("value", ["28001", "1000", "1000-001", "AD500"])
def test_valid_postcode_accepts_spanish_portuguese_andorran_shapes(value):
    assert valid_postcode(value)


@pytest.mark.parametrize("value", ["", "280", "280011", "ABCDE", "1000-0", "AD50"])
def test_valid_postcode_rejects_other_shapes(value):
    assert not valid_postcode(value)


def test_parse_parcel_entry_splits_an_optional_postcode_override():
    assert parse_parcel_entry(f"{DELIVERED_CODE}") == (DELIVERED_CODE, None)
    assert parse_parcel_entry(f" {DELIVERED_CODE} @ 08 001 ") == (DELIVERED_CODE, "08001")
    assert parse_parcel_entry(f"{DELIVERED_CODE}@") == (DELIVERED_CODE, None)


async def test_user_flow_creates_a_postcode_scoped_hub(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTAL_CODE: " 28 001 "}
    )

    assert result["type"] == "create_entry"
    assert result["title"] == "TIPSA (28001)"
    assert result["options"][CONF_POSTAL_CODE] == POSTCODE
    assert result["options"][CONF_PARCELS] == []
    assert result["result"].unique_id == POSTCODE


async def test_user_flow_rejects_an_invalid_postcode(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTAL_CODE: "280"}
    )
    assert result["type"] == "form"
    assert result["errors"] == {CONF_POSTAL_CODE: "invalid_postcode"}


async def test_the_same_postcode_cannot_be_added_twice(hass):
    MockConfigEntry(domain=DOMAIN, unique_id=POSTCODE).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTAL_CODE: POSTCODE}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_a_second_postcode_gets_its_own_hub(hass):
    MockConfigEntry(domain=DOMAIN, unique_id=POSTCODE).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTAL_CODE: "08001"}
    )
    assert result["type"] == "create_entry"


def _hub(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=POSTCODE,
        options={CONF_POSTAL_CODE: POSTCODE, CONF_PARCELS: parcels},
    )


def _settings_input(
    *,
    history=False,
    filter_type="days",
    amount=7,
) -> dict:
    """Build the settings-form submission."""
    return {
        CONF_DELIVERED_FILTER_TYPE: filter_type,
        CONF_DELIVERED_FILTER_AMOUNT: amount,
        CONF_INCLUDE_HISTORY: history,
    }


async def _open_options_step(hass, entry, step_id: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["parcels", "settings"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


async def _submit_codes(hass, entry, codes):
    result = await _open_options_step(hass, entry, "parcels")
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": codes}
    )


async def test_options_add_parcel(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _submit_codes(hass, entry, [DELIVERED_CODE])
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: DELIVERED_CODE}]
    assert result["data"][CONF_POSTAL_CODE] == POSTCODE  # untouched


async def test_options_add_code_with_separators(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _submit_codes(hass, entry, ["0000 100000-100000000000"])
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: DELIVERED_CODE}]


async def test_options_rejects_a_bare_waybill(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _submit_codes(hass, entry, ["1234567890"])
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_tracking_code"}


async def test_options_postcode_override_is_stored_only_when_it_differs(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _submit_codes(
        hass,
        entry,
        [f"{DELIVERED_CODE}@08001", f"{ACTIVE_CODE}@{POSTCODE}"],
    )
    assert result["data"][CONF_PARCELS] == [
        {CONF_TRACKING_CODE: DELIVERED_CODE, CONF_POSTAL_CODE: "08001"},
        {CONF_TRACKING_CODE: ACTIVE_CODE},
    ]


async def test_options_rejects_an_invalid_override_postcode(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _submit_codes(hass, entry, [f"{DELIVERED_CODE}@12"])
    assert result["errors"] == {"base": "invalid_postcode"}


async def test_options_shows_existing_overrides_for_editing(hass):
    entry = _hub([{CONF_TRACKING_CODE: DELIVERED_CODE, CONF_POSTAL_CODE: "08001"}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    suggested = [
        key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key == "tracking_codes"
    ]
    assert suggested == [[f"{DELIVERED_CODE}@08001"]]


async def test_options_ignores_blank_entries_and_de_duplicates(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _submit_codes(hass, entry, ["  ", DELIVERED_CODE, DELIVERED_CODE])
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: DELIVERED_CODE}]


async def test_options_remove_parcel(hass):
    entry = _hub([{CONF_TRACKING_CODE: DELIVERED_CODE}, {CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    result = await _submit_codes(hass, entry, [ACTIVE_CODE])
    assert [p[CONF_TRACKING_CODE] for p in result["data"][CONF_PARCELS]] == [ACTIVE_CODE]


async def test_options_can_clear_the_tracked_code_list(hass):
    entry = _hub([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": []}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == []


async def test_options_changes_history_and_delivered(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _settings_input(
            history=True,
            filter_type="parcels",
            amount=5,
        ),
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_INCLUDE_HISTORY] is True
    assert result["data"][CONF_DELIVERED_FILTER_TYPE] == "parcels"
    assert result["data"][CONF_DELIVERED_FILTER_AMOUNT] == 5
