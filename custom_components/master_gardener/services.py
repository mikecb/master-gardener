"""Service actions: record plantings, report losses, add tasks."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .engine.models import PLANTING_STATUSES
from .engine.planting_rules import care_profile
from .manager import GardenManager
from .plant_store import REFRESH_ERRORS

ATTR_AREA = "area"

ADD_PLANTING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_AREA): cv.string,
        vol.Required("plant"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("quantity", default=1): cv.positive_int,
        vol.Optional("planted_on"): cv.date,
        vol.Optional("notes", default=""): cv.string,
        vol.Optional("care_profile"): cv.string,
    }
)
SEARCH_PLANTS_SCHEMA = vol.Schema(
    {
        vol.Required("query"): cv.string,
        vol.Optional("limit", default=10): vol.All(cv.positive_int, vol.Range(max=50)),
        vol.Optional("all_zones", default=False): cv.boolean,
    }
)
SET_PLANTING_STATUS_SCHEMA = vol.Schema(
    {
        vol.Required("planting_id"): cv.string,
        vol.Required("status"): vol.In(PLANTING_STATUSES),
        vol.Optional("notes"): cv.string,
    }
)
ADD_TASK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_AREA): cv.string,
        vol.Required("title"): cv.string,
        vol.Optional("due"): cv.date,
        vol.Optional("notes", default=""): cv.string,
    }
)
LIST_PLANTINGS_SCHEMA = vol.Schema({vol.Optional(ATTR_AREA): cv.string})


def _manager(hass: HomeAssistant) -> GardenManager:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Master Gardener is not set up")
    return entries[0].runtime_data


def _area_id(hass: HomeAssistant, manager: GardenManager, device_id: str) -> str:
    """Map the garden-area device picked in the UI to its area id."""
    device = dr.async_get(hass).async_get(device_id)
    prefix = f"{manager.entry.entry_id}_"
    valid = {s.subentry_id for s in manager.area_subentries}
    for domain, identifier in device.identifiers if device else ():
        if domain == DOMAIN and identifier.removeprefix(prefix) in valid:
            return identifier.removeprefix(prefix)
    raise ServiceValidationError("Pick one of your garden areas")


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    async def add_planting(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        planting = await manager.async_add_planting(
            _area_id(hass, manager, call.data[ATTR_AREA]),
            call.data["plant"],
            name=call.data.get("name"),
            quantity=call.data["quantity"],
            planted_on=call.data.get("planted_on"),
            notes=call.data["notes"],
            profile=call.data.get("care_profile"),
        )
        return {
            **planting.to_dict(with_record=False),
            "in_catalog": planting.plant is not None,
        }

    async def search_plants(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        if not manager.plants.ready:
            raise ServiceValidationError(
                "The plant catalogue has not been downloaded yet. Try again shortly."
            )
        zone = None if call.data["all_zones"] else manager.zone
        found = await manager.plants.async_search(
            call.data["query"], zone, call.data["limit"]
        )
        return {
            "zone": zone,
            "plants": [
                {
                    "id": p["slug"],
                    "name": p["common_name"],
                    "scientific_name": p["scientific_name"],
                    "type": f"{p['category']}: {p['subcategory']}",
                    "zones": f"{p['usda_zone_min']}-{p['usda_zone_max']}",
                    "sun": p["sun"],
                    "water": p["water"],
                    "height_in": [p["height_in_min"], p["height_in_max"]],
                    "care_profile": care_profile(p),
                }
                for p in found
            ],
        }

    async def refresh_catalogue(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        try:
            await manager.async_update_catalogue(force=True)
        except REFRESH_ERRORS as err:
            raise HomeAssistantError(
                f"Could not download the plant catalogue: {err}"
            ) from err
        return dict(manager.plants.meta)

    async def set_planting_status(call: ServiceCall) -> ServiceResponse:
        planting = _manager(hass).set_planting_status(
            call.data["planting_id"], call.data["status"], call.data.get("notes")
        )
        return planting.to_dict(with_record=False)

    async def add_task(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        task = manager.add_task(
            _area_id(hass, manager, call.data[ATTR_AREA]),
            call.data["title"],
            call.data.get("due"),
            call.data["notes"],
        )
        return task.to_dict()

    async def list_plantings(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        area_id = (
            _area_id(hass, manager, call.data[ATTR_AREA])
            if ATTR_AREA in call.data
            else None
        )
        return {
            "plantings": [
                p.to_dict(with_record=False)
                for p in manager.plantings.values()
                if area_id is None or p.area_id == area_id
            ]
        }

    for name, handler, schema, responds in (
        ("add_planting", add_planting, ADD_PLANTING_SCHEMA, SupportsResponse.OPTIONAL),
        (
            "set_planting_status",
            set_planting_status,
            SET_PLANTING_STATUS_SCHEMA,
            SupportsResponse.OPTIONAL,
        ),
        ("add_task", add_task, ADD_TASK_SCHEMA, SupportsResponse.OPTIONAL),
        ("search_plants", search_plants, SEARCH_PLANTS_SCHEMA, SupportsResponse.ONLY),
        (
            "refresh_catalogue",
            refresh_catalogue,
            vol.Schema({}),
            SupportsResponse.OPTIONAL,
        ),
        (
            "list_plantings",
            list_plantings,
            LIST_PLANTINGS_SCHEMA,
            SupportsResponse.ONLY,
        ),
    ):
        hass.services.async_register(
            DOMAIN, name, handler, schema=schema, supports_response=responds
        )
