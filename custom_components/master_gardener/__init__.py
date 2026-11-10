"""Master Gardener: plan and maintain the gardens of a property."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .engine.catalog import Catalog
from .manager import GardenConfigEntry, GardenManager
from .plant_store import REFRESH_ERRORS, PlantStore
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.SENSOR,
    Platform.TODO,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GardenConfigEntry) -> bool:
    catalog = await hass.async_add_executor_job(Catalog.load)
    integration = await async_get_integration(hass, DOMAIN)
    plants = PlantStore(hass, str(integration.version))
    await plants.async_load()
    manager = GardenManager(hass, entry, catalog, plants)
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
    # Soil readings drive the water and fertilizer advice.
    if sensors := manager.care_sensor_ids():
        entry.async_on_unload(
            async_track_state_change_event(hass, sensors, manager.async_notify)
        )

    # The forecast holds off watering and warns of frost and heat. It is read
    # in the background: the weather entity may not be up yet.
    if weather := manager.weather_entity:
        entry.async_create_background_task(
            hass, manager.async_update_forecast(), "master_gardener forecast"
        )
        entry.async_on_unload(
            async_track_time_interval(
                hass, manager.async_update_forecast, timedelta(hours=1)
            )
        )
        entry.async_on_unload(
            async_track_state_change_event(
                hass, [weather], manager.async_weather_changed
            )
        )

    # Seed on hand comes from a Simple Inventory inventory, read again
    # whenever its sensor changes (an item added, used up or edited).
    if seed_inventory := manager.seed_inventory:
        manager.async_seed_inventory_changed()
        entry.async_on_unload(
            async_track_state_change_event(
                hass, [seed_inventory], manager.async_seed_inventory_changed
            )
        )

    # The plant catalogue downloads in the background: setup never waits on it,
    # and existing plantings keep their own copy of their plant's record.
    async def update_catalogue(_now: datetime | None = None) -> None:
        try:
            await manager.async_update_catalogue()
        except REFRESH_ERRORS as err:
            _LOGGER.warning("Could not update the plant catalogue: %s", err)

    entry.async_create_background_task(
        hass, update_catalogue(), "master_gardener plant catalogue"
    )
    entry.async_on_unload(
        async_track_time_interval(hass, update_catalogue, timedelta(days=1))
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: GardenConfigEntry) -> None:
    """Areas or frost dates changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GardenConfigEntry) -> bool:
    await entry.runtime_data.async_save()  # a reload re-reads the file
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
