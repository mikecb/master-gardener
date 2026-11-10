"""Service actions: read the garden, record plantings and losses, manage tasks.

Every action returns a response, so the same set serves automations, the UI
and an AI agent working through Home Assistant's action API.
"""

from __future__ import annotations

from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentry
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

from .const import (
    CARE_FEED,
    CARE_WATER,
    CONF_ADJACENT,
    CONF_CHARACTER,
    CONF_CONDUCTIVITY_SENSORS,
    CONF_FIRST_FROST,
    CONF_LAST_FROST,
    CONF_LIGHT,
    CONF_MOISTURE_SENSORS,
    CONF_SIZE_SQFT,
    CONF_STYLE,
    CONF_ZIP_CODE,
    CONF_ZONE,
    DOMAIN,
)
from .engine.models import (
    ACTIVE,
    DONE,
    DUE,
    OVERDUE,
    PLANTING_STATUSES,
    PROPOSED,
    UPCOMING,
    Task,
)
from .engine.planting_rules import care_profile
from .manager import GardenManager
from .plant_store import REFRESH_ERRORS

ATTR_AREA = "area"
SHOW_DUE = "due"
SHOW_UPCOMING = "upcoming"
SHOW_ALL = "all"
SEARCH_FILTERS = (
    "sun",
    "water",
    "category",
    "plant_type",
    "min_height_in",
    "max_height_in",
)

ADD_PLANTING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_AREA): cv.string,
        vol.Required("plant"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("quantity", default=1): cv.positive_int,
        vol.Optional("planted_on"): cv.date,
        vol.Optional("notes", default=""): cv.string,
        vol.Optional("care_profile"): cv.string,
        vol.Optional("status", default=ACTIVE): vol.In((ACTIVE, PROPOSED)),
        vol.Optional("estimated_cost"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)
SEARCH_PLANTS_SCHEMA = vol.Schema(
    {
        vol.Optional("query", default=""): cv.string,
        vol.Optional("sun"): vol.In(("full", "partial", "shade")),
        vol.Optional("water"): vol.In(("low", "moderate", "high")),
        vol.Optional("category"): vol.In(
            ("vegetable", "herb", "fruit", "berry", "flower", "cover-crop")
        ),
        vol.Optional("plant_type"): cv.string,
        vol.Optional("min_height_in"): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional("max_height_in"): vol.All(vol.Coerce(float), vol.Range(min=0)),
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
LIST_PLANTINGS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_AREA): cv.string,
        vol.Optional("status"): vol.In(PLANTING_STATUSES),
    }
)
LIST_TASKS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_AREA): cv.string,
        vol.Optional("show", default=SHOW_DUE): vol.In(
            (SHOW_DUE, SHOW_UPCOMING, SHOW_ALL)
        ),
        vol.Optional("days", default=30): vol.All(cv.positive_int, vol.Range(max=400)),
    }
)
COMPLETE_TASK_SCHEMA = vol.Schema({vol.Required("task_id"): cv.string})


def _manager(hass: HomeAssistant) -> GardenManager:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Master Gardener is not set up")
    return entries[0].runtime_data


def _area_id(hass: HomeAssistant, manager: GardenManager, area: str) -> str:
    """Find a garden area by its name, its id, or the device picked in the UI."""
    areas = manager.area_subentries
    wanted = " ".join(area.lower().split())
    for subentry in areas:
        if wanted in (subentry.subentry_id.lower(), subentry.title.lower()):
            return subentry.subentry_id
    device = dr.async_get(hass).async_get(area)
    prefix = f"{manager.entry.entry_id}_"
    valid = {s.subentry_id for s in areas}
    for domain, identifier in device.identifiers if device else ():
        if domain == DOMAIN and identifier.removeprefix(prefix) in valid:
            return identifier.removeprefix(prefix)
    names = ", ".join(s.title for s in areas) or "none yet"
    raise ServiceValidationError(f"No garden area called '{area}'. Your areas: {names}")


def _optional_area(hass: HomeAssistant, manager: GardenManager, call: ServiceCall):
    if ATTR_AREA not in call.data:
        return None
    return _area_id(hass, manager, call.data[ATTR_AREA])


def _names(manager: GardenManager) -> dict[str, str]:
    return {s.subentry_id: s.title for s in manager.area_subentries}


def _task(task: Task, manager: GardenManager, names: dict[str, str]) -> dict:
    return {
        "id": task.id,
        "area": names.get(task.area_id),
        "title": task.title,
        "kind": task.kind,
        "state": task.state(manager.today),
        "start": task.start.isoformat(),
        "end": task.end.isoformat(),
        "detail": task.detail,
        "planting_id": task.planting_id,
    }


def _area(manager: GardenManager, area: ConfigSubentry, names: dict[str, str]) -> dict:
    data = area.data
    today = manager.today
    plantings = [p for p in manager.plantings.values() if p.area_id == area.subentry_id]
    states = [
        t.state(today) for t in manager.tasks(area.subentry_id) if not t.projected
    ]
    out = {
        "id": area.subentry_id,
        "name": area.title,
        "style": data[CONF_STYLE],
        "light": data.get(CONF_LIGHT),
        "size_sqft": data.get(CONF_SIZE_SQFT),
        "character": data.get(CONF_CHARACTER),
        "next_to": [names[a] for a in data.get(CONF_ADJACENT, []) if a in names],
        "plantings": {
            status: sum(1 for p in plantings if p.status == status)
            for status in PLANTING_STATUSES
        },
        "tasks_due": states.count(DUE) + states.count(OVERDUE),
        "tasks_overdue": states.count(OVERDUE),
    }
    if data.get(CONF_MOISTURE_SENSORS) or data.get(CONF_CONDUCTIVITY_SENSORS):
        advice = manager.care_advice(area)
        out["soil"] = {
            key: {
                "needed": item.needed,
                "reason": item.reason,
                "reading": item.reading,
                "threshold": item.threshold,
            }
            for key, item in (
                ("water", advice[CARE_WATER]),
                ("fertilizer", advice[CARE_FEED]),
            )
            if item
        }
    if manager.weather_entity:
        out["alerts"] = [
            {
                "kind": kind,
                "date": alert.day.isoformat(),
                "reason": alert.reason,
                "plants": list(alert.plants),
            }
            for kind, alert in manager.weather_alerts(area).items()
            if alert
        ]
    return out


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    async def get_garden(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        conf = {**manager.entry.data, **manager.entry.options}
        names = _names(manager)
        return {
            "today": manager.today.isoformat(),
            "site": {
                "zip_code": conf.get(CONF_ZIP_CODE),
                "hardiness_zone": conf.get(CONF_ZONE),
                "last_spring_frost": conf[CONF_LAST_FROST],
                "first_autumn_frost": conf[CONF_FIRST_FROST],
            },
            "plant_catalogue": {
                "ready": manager.plants.ready,
                "plants": manager.plants.meta.get("plants"),
                "fetched_at": manager.plants.meta.get("fetched_at"),
            },
            "care_profiles": {
                key: profile["name"]
                for key, profile in manager.catalog.profiles.items()
            },
            "areas": [_area(manager, s, names) for s in manager.area_subentries],
        }

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
            status=call.data["status"],
            estimated_cost=call.data.get("estimated_cost"),
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
        filters = {key: call.data[key] for key in SEARCH_FILTERS if key in call.data}
        if not call.data["query"].strip() and not filters:
            raise ServiceValidationError("Give a search text or at least one filter")
        zone = None if call.data["all_zones"] else manager.zone
        found = await manager.plants.async_search(
            call.data["query"], zone=zone, limit=call.data["limit"], **filters
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
                    "spread_in": [p["spread_in_min"], p["spread_in_max"]],
                    "season": p["season"],
                    "planting": p["plant_note"],
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
        return {**task.to_dict(), "task_id": f"custom:{task.id}"}

    async def list_plantings(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        names = _names(manager)
        area_id = _optional_area(hass, manager, call)
        status = call.data.get("status")
        return {
            "plantings": [
                {**p.to_dict(with_record=False), "area": names.get(p.area_id)}
                for p in manager.plantings.values()
                if (area_id is None or p.area_id == area_id)
                and (status is None or p.status == status)
            ]
        }

    async def list_tasks(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        names = _names(manager)
        show = call.data["show"]
        today = manager.today
        horizon = today + timedelta(days=call.data["days"])
        wanted = {
            SHOW_DUE: (DUE, OVERDUE),
            SHOW_UPCOMING: (UPCOMING,),
            SHOW_ALL: (DUE, OVERDUE, UPCOMING),
        }[show]
        tasks = []
        for task in manager.tasks(_optional_area(hass, manager, call)):
            state = task.state(today)
            if state == DONE or state not in wanted:
                continue
            if state == UPCOMING and task.start > horizon:
                continue
            tasks.append(_task(task, manager, names))
        return {"today": today.isoformat(), "tasks": tasks}

    async def complete_task(call: ServiceCall) -> ServiceResponse:
        _manager(hass).complete(call.data["task_id"])
        return {"completed": call.data["task_id"]}

    for name, handler, schema, responds in (
        ("get_garden", get_garden, vol.Schema({}), SupportsResponse.ONLY),
        ("add_planting", add_planting, ADD_PLANTING_SCHEMA, SupportsResponse.OPTIONAL),
        (
            "set_planting_status",
            set_planting_status,
            SET_PLANTING_STATUS_SCHEMA,
            SupportsResponse.OPTIONAL,
        ),
        ("add_task", add_task, ADD_TASK_SCHEMA, SupportsResponse.OPTIONAL),
        ("list_tasks", list_tasks, LIST_TASKS_SCHEMA, SupportsResponse.ONLY),
        (
            "complete_task",
            complete_task,
            COMPLETE_TASK_SCHEMA,
            SupportsResponse.OPTIONAL,
        ),
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
