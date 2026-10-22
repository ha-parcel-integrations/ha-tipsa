"""TIPSA parcel tracker custom component for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TIPSAApiClient
from .const import DOMAIN, PLATFORMS
from .coordinator import TIPSACoordinator
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)


@dataclass
class TIPSAData:
    """Runtime data attached to the TIPSA config entry."""

    client: TIPSAApiClient
    coordinator: TIPSACoordinator


type TIPSAConfigEntry = ConfigEntry[TIPSAData]


async def async_setup_entry(hass: HomeAssistant, entry: TIPSAConfigEntry) -> bool:
    """Set up TIPSA from a config entry."""
    # No auth: the HA-managed session is fine.
    client = TIPSAApiClient(async_get_clientsession(hass))
    coordinator = TIPSACoordinator(hass, client, entry)

    # Fetch initial data here, before forwarding to platforms. Raising
    # ConfigEntryNotReady from a forwarded platform is too late for HA to catch
    # cleanly (it logs a warning and half-sets-up the entry); doing the first
    # refresh here lets a transient failure fail the whole entry so HA retries
    # it with backoff.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = TIPSAData(client=client, coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Apply option changes (added/removed parcels, history) live via a
    # coordinator refresh — no reload — so per-parcel sensors appear and
    # disappear immediately. The update listener does NOT reload, so it does
    # not trip the config-entry-listener deprecation. This is also the resume
    # path after polling fully suspended (Section 2.1): adding a parcel back
    # triggers this refresh, which recomputes the tier and re-arms scheduling.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    async_setup_services(hass)

    return True


async def _async_options_updated(hass: HomeAssistant, entry: TIPSAConfigEntry) -> None:
    """Apply changed options by refreshing the coordinator."""
    await entry.runtime_data.coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: TIPSAConfigEntry) -> bool:
    """Unload the TIPSA config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    # The services are shared across hubs, so only remove them once the last
    # hub is gone.
    others_loaded = any(
        other.entry_id != entry.entry_id and other.state is ConfigEntryState.LOADED
        for other in hass.config_entries.async_entries(DOMAIN)
    )
    if not others_loaded:
        async_unload_services(hass)
    return True
