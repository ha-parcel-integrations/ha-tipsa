"""Tests for the TIPSA services (track_parcel / untrack_parcel)."""

from unittest.mock import AsyncMock, patch

from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tipsa.const import (
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DOMAIN,
)
from custom_components.tipsa.services import async_setup_services

from .payloads import ACTIVE_CODE, DELIVERED_CODE, POSTCODE, active_sample

_SAMPLE = active_sample()


async def _setup(hass, parcels: list[dict] | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=POSTCODE,
        options={CONF_POSTAL_CODE: POSTCODE, CONF_PARCELS: parcels or []},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.tipsa.api.TIPSAApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_track_parcel_adds_to_options(hass):
    entry = await _setup(hass)
    with patch(
        "custom_components.tipsa.api.TIPSAApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE},
            blocking=True,
        )
        await hass.async_block_till_done()

    parcels = entry.options[CONF_PARCELS]
    assert parcels == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_track_parcel_normalizes_code(hass):
    entry = await _setup(hass)
    with patch(
        "custom_components.tipsa.api.TIPSAApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: "0000 20000020-0000000001"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_track_parcel_rejects_a_bare_waybill(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError, match="22 digits"):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: "1234567890"}, blocking=True
        )


async def test_track_parcel_rejects_empty_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: ""}, blocking=True
        )


async def test_track_parcel_rejects_an_invalid_postcode(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError, match="postal code"):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_POSTAL_CODE: "12"},
            blocking=True,
        )


async def test_track_parcel_with_the_hub_postcode_stores_no_override(hass):
    entry = await _setup(hass)
    with patch(
        "custom_components.tipsa.api.TIPSAApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_POSTAL_CODE: " 28 001"},
            blocking=True,
        )
        await hass.async_block_till_done()
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_track_parcel_with_a_foreign_postcode_is_a_per_parcel_override(hass):
    entry = await _setup(hass)
    with patch(
        "custom_components.tipsa.api.TIPSAApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_POSTAL_CODE: "08001"},
            blocking=True,
        )
        await hass.async_block_till_done()
    assert entry.options[CONF_PARCELS] == [
        {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_POSTAL_CODE: "08001"}
    ]


async def _two_hubs(hass):
    first = await _setup(hass)
    second = MockConfigEntry(
        domain=DOMAIN,
        unique_id="08001",
        options={CONF_POSTAL_CODE: "08001", CONF_PARCELS: []},
    )
    second.add_to_hass(hass)
    with patch(
        "custom_components.tipsa.api.TIPSAApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        assert await hass.config_entries.async_setup(second.entry_id)
        await hass.async_block_till_done()
    return first, second


async def test_several_hubs_need_a_postcode_to_choose(hass):
    first, second = await _two_hubs(hass)
    with pytest.raises(ServiceValidationError, match="Multiple"):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE}, blocking=True
        )
    with pytest.raises(ServiceValidationError, match="No TIPSA hub"):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_POSTAL_CODE: "46001"},
            blocking=True,
        )

    with patch(
        "custom_components.tipsa.api.TIPSAApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, CONF_POSTAL_CODE: "08001"},
            blocking=True,
        )
        await hass.async_block_till_done()
    assert second.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]
    assert first.options[CONF_PARCELS] == []


async def test_untrack_removes_from_every_hub_and_services_outlive_one_hub(hass):
    first, second = await _two_hubs(hass)
    for entry in (first, second):
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_PARCELS: [{CONF_TRACKING_CODE: ACTIVE_CODE}],
            },
        )
    with patch(
        "custom_components.tipsa.api.TIPSAApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.async_block_till_done()
        await hass.services.async_call(
            DOMAIN, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE}, blocking=True
        )
        await hass.async_block_till_done()
        assert first.options[CONF_PARCELS] == [] == second.options[CONF_PARCELS]

        assert await hass.config_entries.async_unload(first.entry_id)
        assert hass.services.has_service(DOMAIN, "track_parcel")
        assert await hass.config_entries.async_unload(second.entry_id)
        assert not hass.services.has_service(DOMAIN, "track_parcel")


async def test_services_raise_when_nothing_is_set_up(hass):
    async_setup_services(hass)
    for service, data in (
        ("track_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE}),
        ("untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE}),
    ):
        with pytest.raises(ServiceValidationError, match="not set up"):
            await hass.services.async_call(DOMAIN, service, data, blocking=True)


async def test_track_parcel_duplicate_is_noop(hass):
    entry = await _setup(hass)
    with patch(
        "custom_components.tipsa.api.TIPSAApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        for _ in range(2):
            await hass.services.async_call(
                DOMAIN,
                "track_parcel",
                {CONF_TRACKING_CODE: ACTIVE_CODE},
                blocking=True,
            )
            await hass.async_block_till_done()

    assert len(entry.options[CONF_PARCELS]) == 1


async def test_untrack_parcel_removes_from_options(hass):
    entry = await _setup(hass, parcels=[{CONF_TRACKING_CODE: ACTIVE_CODE}])
    with patch(
        "custom_components.tipsa.api.TIPSAApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_PARCELS] == []


async def test_untrack_unknown_code_is_noop(hass):
    entry = await _setup(hass, parcels=[{CONF_TRACKING_CODE: ACTIVE_CODE}])
    with patch(
        "custom_components.tipsa.api.TIPSAApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: DELIVERED_CODE},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert len(entry.options[CONF_PARCELS]) == 1
