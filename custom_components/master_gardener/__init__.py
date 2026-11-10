"""Master Gardener: plan and maintain the gardens of a property."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .engine.catalog import Catalog
from .manager import GardenConfigEntry, GardenManager
from .services import async_setup_services

PLATFORMS = [Platform.CALENDAR, Platform.SENSOR, Platform.TODO]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GardenConfigEntry) -> bool:
    catalog = await hass.async_add_executor_job(Catalog.load)
    manager = GardenManager(hass, entry, catalog)
    await manager.async_load()
    entry.runtime_data = manager

    # The property device must exist before area devices point at it.
    manager.garden_device_id = (
        dr.async_get(hass)
        .async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, entry.entry_id)},
            name="Garden",
            manufacturer="Master Gardener",
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        .id
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Due and overdue change with the date, not only with user actions.
    entry.async_on_unload(
        async_track_time_change(
            hass, manager.async_refresh, hour=0, minute=0, second=10
        )
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: GardenConfigEntry) -> None:
    """Areas or frost dates changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GardenConfigEntry) -> bool:
    await entry.runtime_data.async_save()  # a reload re-reads the file
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
