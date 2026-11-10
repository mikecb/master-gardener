"""Holds garden state, persists it, and asks the engine for tasks."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import date, datetime, timedelta
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import DistanceConverter, TemperatureConverter
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from .const import (
    CARE_FEED,
    CARE_FROST,
    CARE_HEAT,
    CARE_WATER,
    CONF_ANNUAL_BUDGET,
    CONF_CONDUCTIVITY_SENSORS,
    CONF_DRY_BELOW,
    CONF_FEED_BELOW,
    CONF_FIRST_FROST,
    CONF_LAST_FROST,
    CONF_MOISTURE_SENSORS,
    CONF_STYLE,
    CONF_WEATHER_ENTITY,
    CONF_ZONE,
    DOMAIN,
    LOOKAHEAD_DAYS,
    LOOKBACK_DAYS,
    SUBENTRY_AREA,
)
from .engine.care import (
    Advice,
    default_dry_below,
    default_feed_below,
    feed_advice,
    water_advice,
)
from .engine.catalog import Catalog
from .engine.models import (
    ACTIVE,
    DONE_PROJECT,
    PLANNED,
    PLANTING_STATUSES,
    PROJECT_STATUSES,
    PROPOSED,
    REMOVED,
    Area,
    CustomTask,
    Planting,
    Project,
    Site,
    Task,
)
from .engine.plan import (
    KIND_PLANTING,
    KIND_PROJECT,
    PERENNIALS,
    PlanItem,
    PlanYear,
    build_plan,
    plan_task,
    planting_tier,
)
from .engine.planting_rules import care_profile
from .engine.schedule import IN_GROUND_KINDS, build_tasks
from .engine.weather import (
    Alert,
    DayForecast,
    daily,
    frost_alert,
    heat_alert,
    rain_note,
)
from .plant_store import PlantStore

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# After a care task is ticked off it stays away this long, giving the water
# or fertilizer time to show up in the readings, or the cold or heat to pass.
CARE_SNOOZE = {
    CARE_WATER: timedelta(hours=12),
    CARE_FEED: timedelta(days=14),
    CARE_FROST: timedelta(days=1),
    CARE_HEAT: timedelta(days=3),
}
CARE_TITLES = {
    CARE_WATER: "Water {name}",
    CARE_FEED: "Fertilize {name}",
    CARE_FROST: "Protect {name} from frost",
    CARE_HEAT: "Shield {name} from heat",
}
# The forecasts a weather entity may offer, by its feature flag, best first.
FORECAST_TYPES = ((1, "daily"), (4, "twice_daily"), (2, "hourly"))
# A forecast that could not be refreshed for this long is no longer trusted.
FORECAST_MAX_AGE = timedelta(hours=6)
# Conductivity is judged in µS/cm.
UNIT_FACTORS = {"mS/cm": 1000, "S/cm": 1_000_000}

type GardenConfigEntry = ConfigEntry[GardenManager]


def _month_day(value: str) -> tuple[int, int]:
    month, day = value.split("-")
    return int(month), int(day)


class GardenManager:
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        catalog: Catalog,
        plants: PlantStore,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.catalog = catalog
        self.plants = plants
        self.garden_device_id: str | None = None  # set during entry setup
        self._store: Store[dict] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.plantings: dict[str, Planting] = {}
        self.custom_tasks: dict[str, CustomTask] = {}
        self.projects: dict[str, Project] = {}
        self._completions: dict[str, date] = {}
        self._last_done: dict[str, date] = {}
        self._area_since: dict[str, date] = {}
        self._snoozed: dict[str, datetime] = {}
        self._care_on: dict[tuple[str, str], bool] = {}
        self.forecast: list[DayForecast] = []
        self._forecast_at: datetime | None = None
        self._listeners: list[CALLBACK_TYPE] = []
        self._cache: tuple[date, list[Task]] | None = None

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.plantings = {
            p["id"]: Planting.from_dict(p) for p in data.get("plantings", [])
        }
        self.custom_tasks = {
            t["id"]: CustomTask.from_dict(t) for t in data.get("custom_tasks", [])
        }
        self.projects = {
            p["id"]: Project.from_dict(p) for p in data.get("projects", [])
        }
        self._completions = _dates(data.get("completions", {}))
        self._last_done = _dates(data.get("last_done", {}))
        self._area_since = _dates(data.get("area_since", {}))
        self._snoozed = {
            key: datetime.fromisoformat(value)
            for key, value in data.get("snoozed", {}).items()
        }
        # Plantings from before the plant database named a care profile directly.
        for planting in self.plantings.values():
            if not planting.plant_data and planting.plant in self.catalog.profiles:
                planting.profile = planting.profile or planting.plant
        # Remember when each area was first seen so we never invent a backlog.
        new = [a for a in self.area_subentries if a.subentry_id not in self._area_since]
        for subentry in new:
            self._area_since[subentry.subentry_id] = self.today
        if new:
            self._save()

    # --- reading -----------------------------------------------------------

    @property
    def today(self) -> date:
        return dt_util.now().date()

    @property
    def zone(self) -> int | None:
        """The property's USDA hardiness zone number, if setup looked it up."""
        digits = "".join(c for c in self.entry.data.get(CONF_ZONE, "") if c.isdigit())
        return int(digits) if digits else None

    @property
    def site(self) -> Site:
        conf = {**self.entry.data, **self.entry.options}
        return Site(
            last_frost=_month_day(conf[CONF_LAST_FROST]),
            first_frost=_month_day(conf[CONF_FIRST_FROST]),
        )

    @property
    def area_subentries(self) -> list[ConfigSubentry]:
        return [
            s
            for s in self.entry.subentries.values()
            if s.subentry_type == SUBENTRY_AREA
        ]

    def tasks_between(self, start: date, end: date) -> list[Task]:
        areas = [
            Area(
                id=s.subentry_id,
                name=s.title,
                style=s.data[CONF_STYLE],
                since=self._area_since.get(s.subentry_id),
            )
            for s in self.area_subentries
        ]
        tasks = build_tasks(
            site=self.site,
            areas=areas,
            plantings=list(self.plantings.values()),
            custom_tasks=list(self.custom_tasks.values()),
            completions=self._completions,
            last_done=self._last_done,
            catalog=self.catalog,
            start=start,
            end=end,
            today=self.today,
        )
        tasks += [t for t in self._plan_tasks() if t.end >= start and t.start <= end]
        tasks.sort(key=lambda t: (t.start, t.end, t.title))
        return tasks

    def tasks(self, area_id: str | None = None) -> list[Task]:
        """Tasks around today (cached), optionally for one area."""
        today = self.today
        if self._cache is None or self._cache[0] != today:
            self._cache = (
                today,
                self.tasks_between(
                    today - timedelta(days=LOOKBACK_DAYS),
                    today + timedelta(days=LOOKAHEAD_DAYS),
                ),
            )
        tasks = [*self._care_tasks(), *self._cache[1]]
        if area_id is None:
            return tasks
        return [t for t in tasks if t.area_id == area_id]

    # --- soil care ---------------------------------------------------------

    def care_sensor_ids(self) -> list[str]:
        """Every soil sensor any area uses."""
        ids: set[str] = set()
        for area in self.area_subentries:
            ids.update(area.data.get(CONF_MOISTURE_SENSORS, []))
            ids.update(area.data.get(CONF_CONDUCTIVITY_SENSORS, []))
        return sorted(ids)

    def dry_below(self, area: ConfigSubentry) -> float:
        return area.data.get(CONF_DRY_BELOW, default_dry_below(area.data[CONF_STYLE]))

    def feed_below(self, area: ConfigSubentry) -> float:
        return area.data.get(CONF_FEED_BELOW, default_feed_below(area.data[CONF_STYLE]))

    def care_advice(self, area: ConfigSubentry) -> dict[str, Advice | None]:
        """Water and feed advice for an area from its sensors' current states."""
        moisture = self._readings(area.data.get(CONF_MOISTURE_SENSORS, []))
        conductivity = self._readings(area.data.get(CONF_CONDUCTIVITY_SENSORS, []))
        key = area.subentry_id
        advice = {
            CARE_WATER: water_advice(
                moisture,
                self.dry_below(area),
                self._care_on.get((key, CARE_WATER), False),
                rain_note(self.forecast, self.today, imperial=self.imperial),
            ),
            CARE_FEED: feed_advice(
                conductivity,
                moisture,
                feed_below=self.feed_below(area),
                dry_below=self.dry_below(area),
                site=self.site,
                today=self.today,
                was_needed=self._care_on.get((key, CARE_FEED), False),
            ),
        }
        for kind, item in advice.items():
            self._care_on[(key, kind)] = bool(item and item.needed)
        return advice

    def _readings(self, entity_ids: list[str]) -> list[float]:
        """Numeric states of the sensors; unavailable ones are left out."""
        values = []
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            try:
                value = float(state.state)
            except (AttributeError, ValueError):
                continue
            if math.isfinite(value):
                unit = state.attributes.get("unit_of_measurement")
                values.append(value * UNIT_FACTORS.get(unit, 1))
        return values

    def _care_tasks(self) -> list[Task]:
        """A task for each area that needs water, feeding or protecting now."""
        now = dt_util.utcnow()
        today = self.today
        tasks = []
        for area in self.area_subentries:
            # Soil care is due today; protection is due by the day it turns.
            wanted = [
                (kind, today, advice.reason)
                for kind, advice in self.care_advice(area).items()
                if advice and advice.needed
            ]
            wanted += [
                (kind, alert.day, alert.reason)
                for kind, alert in self.weather_alerts(area).items()
                if alert
            ]
            for kind, due, reason in wanted:
                task_id = f"care:{kind}:{area.subentry_id}"
                snoozed_until = self._snoozed.get(task_id)
                if snoozed_until and snoozed_until > now:
                    continue
                tasks.append(
                    Task(
                        id=task_id,
                        area_id=area.subentry_id,
                        title=CARE_TITLES[kind].format(name=area.title),
                        kind=kind,
                        start=today,
                        end=due,
                        detail=reason,
                    )
                )
        return tasks

    # --- build plan --------------------------------------------------------

    @property
    def annual_budget(self) -> float | None:
        """What may be spent on the garden each year, if a budget is set."""
        return self.entry.options.get(CONF_ANNUAL_BUDGET) or None

    def spent(self, year: int) -> float:
        """What plantings bought and projects finished in a year cost."""
        return sum(
            p.cost or 0
            for p in self.plantings.values()
            if p.bought_on and p.bought_on.year == year
        ) + sum(
            p.cost or 0
            for p in self.projects.values()
            if p.done_on and p.done_on.year == year
        )

    def plan_items(self) -> list[PlanItem]:
        """Everything planned and not yet bought or done."""
        items = [
            PlanItem(
                id=p.id,
                area_id=p.area_id,
                name=p.name,
                tier=p.tier or planting_tier(p.plant_data, p.profile),
                priority=p.priority,
                cost=p.estimated_cost,
                not_before=p.not_before,
                added_on=p.added_on,
            )
            for p in self.plantings.values()
            if p.status == PLANNED
        ]
        items += [
            PlanItem(
                id=p.id,
                area_id=p.area_id,
                name=p.name,
                tier=p.tier,
                kind=KIND_PROJECT,
                priority=p.priority,
                cost=p.estimated_cost,
                not_before=p.not_before,
                added_on=p.added_on,
            )
            for p in self.projects.values()
            if p.status == PLANNED
        ]
        areas = {a.subentry_id for a in self.area_subentries}
        return [i for i in items if i.area_id in areas]

    def plan(self) -> list[PlanYear]:
        year = self.today.year
        return build_plan(
            self.plan_items(),
            this_year=year,
            budget=self.annual_budget,
            spent=self.spent(year),
        )

    def _plan_tasks(self) -> list[Task]:
        """This year's purchases: open ones from the plan, and those made."""
        year = self.today.year
        site = self.site
        (current, *_) = self.plan()
        # Open purchases, then those already made this year (kept ticked, so
        # unticking one can undo it).
        wanted: list[tuple[PlanItem, date | None]] = [
            (p.item, None) for p in current.placements
        ]
        for planting in self.plantings.values():
            if planting.bought_on and planting.bought_on.year == year:
                wanted.append((self._as_item(planting), planting.bought_on))
        for project in self.projects.values():
            if project.done_on and project.done_on.year == year:
                wanted.append((self._as_item(project), project.done_on))
        areas = {a.subentry_id for a in self.area_subentries}
        tasks = []
        for item, completed_on in wanted:
            if item.area_id not in areas:
                continue
            planting = self.plantings.get(item.id)
            tasks.append(
                plan_task(
                    item,
                    record=planting.plant_data if planting else None,
                    quantity=planting.quantity if planting else 1,
                    site=site,
                    year=year,
                    completed_on=completed_on,
                )
            )
        return tasks

    @staticmethod
    def _as_item(source: Planting | Project) -> PlanItem:
        """A bought planting or finished project, shaped for its to-do item."""
        is_project = isinstance(source, Project)
        return PlanItem(
            id=source.id,
            area_id=source.area_id,
            name=source.name,
            tier=source.tier or PERENNIALS,
            kind=KIND_PROJECT if is_project else KIND_PLANTING,
            cost=source.cost,
        )

    def _plan_source(self, item_id: str) -> Planting | Project:
        source = self.plantings.get(item_id) or self.projects.get(item_id)
        if source is None:
            raise ServiceValidationError(f"No planting or project with id {item_id}")
        return source

    def add_project(
        self,
        area_id: str,
        name: str,
        *,
        tier: str = "hardscape",
        estimated_cost: float | None = None,
        priority: str = "normal",
        not_before: int | None = None,
        notes: str = "",
    ) -> Project:
        project = Project(
            id=uuid4().hex[:8],
            area_id=area_id,
            name=name,
            tier=tier,
            estimated_cost=estimated_cost,
            priority=priority,
            not_before=not_before,
            notes=notes,
            added_on=self.today,
        )
        self.projects[project.id] = project
        self._changed()
        return project

    def set_project_status(
        self,
        project_id: str,
        status: str,
        *,
        cost: float | None = None,
        notes: str | None = None,
    ) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise ServiceValidationError(f"No project with id {project_id}")
        if status not in PROJECT_STATUSES:
            raise ServiceValidationError(f"Unknown status {status}")
        if status == DONE_PROJECT and project.status != DONE_PROJECT:
            project.done_on = self.today
            project.cost = cost if cost is not None else project.estimated_cost
        elif status != DONE_PROJECT:
            project.done_on = project.cost = None
        elif cost is not None:
            project.cost = cost
        project.status = status
        if notes:
            project.notes = f"{project.notes}\n{notes}".strip()
        self._changed()
        return project

    def update_plan_item(
        self,
        item_id: str,
        *,
        priority: str | None = None,
        tier: str | None = None,
        not_before: int | None = None,
        estimated_cost: float | None = None,
    ) -> Planting | Project:
        """Change how a planting or project is placed in the build plan."""
        source = self._plan_source(item_id)
        if priority:
            source.priority = priority
        if tier:
            source.tier = tier
        if not_before is not None:
            source.not_before = not_before or None  # 0 clears it
        if estimated_cost is not None:
            source.estimated_cost = estimated_cost
        self._changed()
        return source

    # --- weather -----------------------------------------------------------

    @property
    def weather_entity(self) -> str | None:
        """The weather entity whose forecast is followed, if one was chosen."""
        return self.entry.options.get(CONF_WEATHER_ENTITY)

    @property
    def imperial(self) -> bool:
        return self.hass.config.units is US_CUSTOMARY_SYSTEM

    def weather_alerts(self, area: ConfigSubentry) -> dict[str, Alert | None]:
        """Frost and heat alerts for the plantings in an area."""
        today = self.today
        plantings = [
            p for p in self.plantings.values() if p.area_id == area.subentry_id
        ]
        return {
            CARE_FROST: frost_alert(
                self.forecast,
                plantings,
                site=self.site,
                today=today,
                imperial=self.imperial,
            ),
            CARE_HEAT: heat_alert(
                self.forecast, plantings, today=today, imperial=self.imperial
            ),
        }

    async def async_update_forecast(self, *_: object) -> None:
        """Read the forecast from the weather entity and tell the entities."""
        entries = await self._async_fetch_forecast()
        now = dt_util.utcnow()
        if entries is not None:
            self.forecast = daily(entries)
            self._forecast_at = now
        elif self._forecast_at and now - self._forecast_at > FORECAST_MAX_AGE:
            self.forecast = []
            self._forecast_at = None
        else:
            return
        self.async_notify()

    @callback
    def async_weather_changed(self, *_: object) -> None:
        """The weather entity came up or changed: fetch if there is no forecast."""
        if self._forecast_at is None:
            self.entry.async_create_background_task(
                self.hass, self.async_update_forecast(), "master_gardener forecast"
            )

    async def _async_fetch_forecast(self) -> list[dict] | None:
        """Forecast entries by local day, in °C and mm; None if not available."""
        entity_id = self.weather_entity
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        features = state.attributes.get("supported_features") or 0
        kind = next((name for flag, name in FORECAST_TYPES if features & flag), None)
        if kind is None:
            return None
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": entity_id, "type": kind},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("No forecast from %s: %s", entity_id, err)
            return None
        temperature_unit = state.attributes.get(
            "temperature_unit", UnitOfTemperature.CELSIUS
        )
        rain_unit = state.attributes.get("precipitation_unit", "mm")
        entries = []
        for item in (response or {}).get(entity_id, {}).get("forecast") or []:
            when = dt_util.parse_datetime(str(item.get("datetime")))
            if when is None:
                continue
            entry: dict = {
                "day": dt_util.as_local(when).date(),
                "is_daytime": item.get("is_daytime"),
                "precipitation_probability": item.get("precipitation_probability"),
            }
            for key in ("temperature", "templow"):
                if item.get(key) is not None:
                    entry[key] = TemperatureConverter.convert(
                        item[key], temperature_unit, UnitOfTemperature.CELSIUS
                    )
            if item.get("precipitation") is not None:
                entry["precipitation"] = DistanceConverter.convert(
                    item["precipitation"], rain_unit, "mm"
                )
            entries.append(entry)
        return entries

    # --- writing -----------------------------------------------------------

    async def async_add_planting(
        self,
        area_id: str,
        plant: str,
        *,
        name: str | None = None,
        quantity: int = 1,
        planted_on: date | None = None,
        notes: str = "",
        profile: str | None = None,
        status: str = ACTIVE,
        estimated_cost: float | None = None,
        priority: str = "normal",
        tier: str | None = None,
        not_before: int | None = None,
    ) -> Planting:
        """Record a planting, looking the plant up in the catalogue."""
        if not self.plants.ready:
            raise ServiceValidationError(
                "The plant catalogue has not been downloaded yet. Try again shortly."
            )
        if profile and profile not in self.catalog.profiles:
            raise ServiceValidationError(f"Unknown care profile {profile}")
        record, candidates = await self.plants.async_resolve(plant)
        if record is None and candidates:
            options = ", ".join(
                f"{c['slug']} ({c['common_name']})" for c in candidates[:8]
            )
            raise ServiceValidationError(
                f"Several plants match '{plant}'. Use one of these ids: {options}"
            )
        planting = Planting(
            id=uuid4().hex[:8],
            area_id=area_id,
            name=name or (record["common_name"] if record else plant),
            plant=record["slug"] if record else None,
            plant_data=record,
            profile=profile or (care_profile(record) if record else None),
            quantity=quantity,
            planted_on=planted_on,
            added_on=self.today,
            status=status,
            notes=notes,
            estimated_cost=estimated_cost,
            priority=priority,
            tier=tier,
            not_before=not_before,
        )
        self.plantings[planting.id] = planting
        self._changed()
        return planting

    async def async_update_catalogue(self, force: bool = False) -> bool:
        """Fetch the plant catalogue if due, then refresh every planting's record."""
        changed = await self.plants.async_refresh(force)
        if changed:
            for planting in self.plantings.values():
                if not planting.plant:
                    continue
                if record := await self.plants.async_get(planting.plant):
                    planting.plant_data = record
                    planting.profile = planting.profile or care_profile(record)
            self._changed()
        else:
            self.async_notify()
        return changed

    def set_planting_status(
        self,
        planting_id: str,
        status: str,
        notes: str | None = None,
        *,
        cost: float | None = None,
    ) -> Planting:
        planting = self.plantings.get(planting_id)
        if planting is None:
            raise ServiceValidationError(f"No planting with id {planting_id}")
        if status not in PLANTING_STATUSES:
            raise ServiceValidationError(f"Unknown status {status}")
        if planting.status in (PROPOSED, PLANNED) and status == ACTIVE:
            # Bought: its calendar starts now and the cost counts this year.
            planting.added_on = self.today
            planting.bought_on = self.today
            planting.cost = cost if cost is not None else planting.estimated_cost
        elif status in (PROPOSED, PLANNED):
            planting.bought_on = planting.cost = None
        elif cost is not None:
            planting.cost = cost
        planting.status = status
        if notes:
            planting.notes = f"{planting.notes}\n{notes}".strip()
        self._changed()
        return planting

    def add_task(
        self, area_id: str, title: str, due: date | None = None, notes: str = ""
    ) -> CustomTask:
        task = CustomTask(
            id=uuid4().hex[:8],
            area_id=area_id,
            title=title,
            due=due or self.today,
            notes=notes,
        )
        self.custom_tasks[task.id] = task
        self._changed()
        return task

    def update_custom_task(
        self, task_id: str, *, title: str | None, due: date | None, notes: str | None
    ) -> None:
        custom = self.custom_tasks.get(task_id.removeprefix("custom:"))
        if custom is None:
            return
        if title:
            custom.title = title
        if due:
            custom.due = due
        if notes is not None:
            custom.notes = notes
        self._changed()

    def complete(self, task_id: str) -> None:
        if task_id.startswith("care:"):
            kind = task_id.split(":")[1]
            if kind not in CARE_SNOOZE:
                raise ServiceValidationError(f"No current task with id {task_id}")
            self._snoozed[task_id] = dt_util.utcnow() + CARE_SNOOZE[kind]
            self._changed()
            return
        if task_id.startswith("plan:"):
            self._buy(task_id)
            return
        task = self._find(task_id)
        planting = self.plantings.get(task.planting_id or "")
        if planting and not planting.planted_on and task.kind in IN_GROUND_KINDS:
            planting.planted_on = self.today  # ticking "plant" records the planting
        if task.series:
            self._last_done[task.series] = self.today
        else:
            self._completions[task_id] = self.today
        self._changed()

    def reopen(self, task_id: str) -> None:
        if task_id.startswith("plan:"):
            source = self._plan_source(task_id.split(":")[1])
            if source.status in (ACTIVE, DONE_PROJECT):
                if isinstance(source, Project):
                    self.set_project_status(source.id, PLANNED)
                else:
                    self.set_planting_status(source.id, PLANNED)
            return
        if self._completions.pop(task_id, None):
            self._changed()

    def _buy(self, task_id: str) -> None:
        """A purchase from the plan was made, or a project done."""
        source = self._plan_source(task_id.split(":")[1])
        if source.status != PLANNED:
            return  # already bought
        if isinstance(source, Project):
            self.set_project_status(source.id, DONE_PROJECT)
        else:
            self.set_planting_status(source.id, ACTIVE)

    def delete(self, task_id: str) -> None:
        """Remove a hand-added task; dismiss a generated one."""
        if task_id.startswith("plan:"):
            # Deleting a purchase means not buying it: drop it from the plan.
            source = self._plan_source(task_id.split(":")[1])
            if source.status == PLANNED:
                note = "Taken off the build plan from the to-do list."
                if isinstance(source, Project):
                    self.set_project_status(source.id, REMOVED, notes=note)
                else:
                    self.set_planting_status(source.id, REMOVED, note)
            return
        if self.custom_tasks.pop(task_id.removeprefix("custom:"), None):
            self._completions.pop(task_id, None)
            self._changed()
        else:
            self.complete(task_id)

    def _find(self, task_id: str) -> Task:
        for task in self.tasks():
            if task.id == task_id:
                return task
        raise ServiceValidationError(f"No current task with id {task_id}")

    # --- plumbing ----------------------------------------------------------

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def async_refresh(self, *_: object) -> None:
        """Recompute and tell entities (also called at midnight)."""
        self._cache = None
        self.async_notify()

    @callback
    def async_notify(self, *_: object) -> None:
        """Tell entities something they show changed, e.g. a soil reading."""
        for listener in list(self._listeners):
            listener()

    def _changed(self) -> None:
        self._save()
        self.async_refresh()

    def _save(self) -> None:
        self._store.async_delay_save(self._data, 1)

    async def async_save(self) -> None:
        await self._store.async_save(self._data())

    def _data(self) -> dict:
        return {
            "plantings": [p.to_dict() for p in self.plantings.values()],
            "custom_tasks": [t.to_dict() for t in self.custom_tasks.values()],
            "projects": [p.to_dict() for p in self.projects.values()],
            "completions": _isos(self._completions),
            "last_done": _isos(self._last_done),
            "area_since": _isos(self._area_since),
            "snoozed": {k: v.isoformat() for k, v in self._snoozed.items()},
        }


def _dates(raw: dict[str, str]) -> dict[str, date]:
    return {key: date.fromisoformat(value) for key, value in raw.items()}


def _isos(values: dict[str, date]) -> dict[str, str]:
    return {key: value.isoformat() for key, value in values.items()}
