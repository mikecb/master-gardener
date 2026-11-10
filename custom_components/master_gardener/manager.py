"""Holds garden state, persists it, and asks the engine for tasks."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FIRST_FROST,
    CONF_LAST_FROST,
    CONF_STYLE,
    DOMAIN,
    LOOKAHEAD_DAYS,
    LOOKBACK_DAYS,
    SUBENTRY_AREA,
)
from .engine.catalog import Catalog
from .engine.models import PLANTING_STATUSES, Area, CustomTask, Planting, Site, Task
from .engine.schedule import build_tasks

STORAGE_VERSION = 1

type GardenConfigEntry = ConfigEntry[GardenManager]


def _month_day(value: str) -> tuple[int, int]:
    month, day = value.split("-")
    return int(month), int(day)


class GardenManager:
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, catalog: Catalog
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.catalog = catalog
        self.garden_device_id: str | None = None  # set during entry setup
        self._store: Store[dict] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.plantings: dict[str, Planting] = {}
        self.custom_tasks: dict[str, CustomTask] = {}
        self._completions: dict[str, date] = {}
        self._last_done: dict[str, date] = {}
        self._area_since: dict[str, date] = {}
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
        self._completions = _dates(data.get("completions", {}))
        self._last_done = _dates(data.get("last_done", {}))
        self._area_since = _dates(data.get("area_since", {}))
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
        return build_tasks(
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
        tasks = self._cache[1]
        if area_id is None:
            return tasks
        return [t for t in tasks if t.area_id == area_id]

    # --- writing -----------------------------------------------------------

    def add_planting(
        self,
        area_id: str,
        plant: str,
        *,
        name: str | None = None,
        quantity: int = 1,
        planted_on: date | None = None,
        notes: str = "",
    ) -> Planting:
        key = self.catalog.match(plant)
        planting = Planting(
            id=uuid4().hex[:8],
            area_id=area_id,
            name=name or (self.catalog.plants[key]["name"] if key else plant),
            plant=key,
            quantity=quantity,
            planted_on=planted_on,
            added_on=self.today,
            notes=notes,
        )
        self.plantings[planting.id] = planting
        self._changed()
        return planting

    def set_planting_status(
        self, planting_id: str, status: str, notes: str | None = None
    ) -> Planting:
        planting = self.plantings.get(planting_id)
        if planting is None:
            raise ServiceValidationError(f"No planting with id {planting_id}")
        if status not in PLANTING_STATUSES:
            raise ServiceValidationError(f"Unknown status {status}")
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
        task = self._find(task_id)
        if task.series:
            self._last_done[task.series] = self.today
        else:
            self._completions[task_id] = self.today
        self._changed()

    def reopen(self, task_id: str) -> None:
        if self._completions.pop(task_id, None):
            self._changed()

    def delete(self, task_id: str) -> None:
        """Remove a hand-added task; dismiss a generated one."""
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
            "completions": _isos(self._completions),
            "last_done": _isos(self._last_done),
            "area_since": _isos(self._area_since),
        }


def _dates(raw: dict[str, str]) -> dict[str, date]:
    return {key: date.fromisoformat(value) for key, value in raw.items()}


def _isos(values: dict[str, date]) -> dict[str, str]:
    return {key: value.isoformat() for key, value in values.items()}
