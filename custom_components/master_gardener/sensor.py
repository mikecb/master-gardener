"""Sensors: task counts, the next task, and what is planted where."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .engine.models import ACTIVE, OVERDUE, UPCOMING
from .entity import GardenEntity
from .manager import GardenConfigEntry

MAX_LISTED = 25


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GardenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    async_add_entities(
        [
            TasksDueSensor(manager, None),
            TasksOverdueSensor(manager, None),
            PlantCatalogueSensor(manager),
            BudgetRemainingSensor(manager),
            PlannedThisYearSensor(manager),
        ]
    )
    for area in manager.area_subentries:
        async_add_entities(
            [
                TasksDueSensor(manager, area),
                TasksOverdueSensor(manager, area),
                NextTaskSensor(manager, area),
                PlantingsSensor(manager, area),
            ],
            config_subentry_id=area.subentry_id,
        )


class _TaskCountSensor(GardenEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _states: tuple[str, ...] = ()

    def __init__(self, manager, area) -> None:
        super().__init__(manager, area, self._attr_translation_key)

    @property
    def native_value(self) -> int:
        return len(self.actionable(*self._states))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tasks = self.actionable(*self._states)[:MAX_LISTED]
        return {
            "tasks": [
                {"id": t.id, "title": t.title, "due": t.end.isoformat()} for t in tasks
            ]
        }


class TasksDueSensor(_TaskCountSensor):
    """Everything that needs doing now, including what is overdue."""

    _attr_translation_key = "tasks_due"


class TasksOverdueSensor(_TaskCountSensor):
    _attr_translation_key = "tasks_overdue"
    _states = (OVERDUE,)


class NextTaskSensor(GardenEntity, SensorEntity):
    _attr_translation_key = "next_task"

    def __init__(self, manager, area) -> None:
        super().__init__(manager, area, "next_task")

    def _next(self):
        today = self.manager.today
        for task in self.tasks():
            if task.state(today) == UPCOMING:
                return task
        return None

    @property
    def native_value(self) -> str | None:
        task = self._next()
        return task.title[:255] if task else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        task = self._next()
        if not task:
            return {}
        return {"start": task.start.isoformat(), "end": task.end.isoformat()}


class PlantingsSensor(GardenEntity, SensorEntity):
    """Count of living plantings; the attribute lists all, with their ids."""

    _attr_translation_key = "plantings"

    def __init__(self, manager, area) -> None:
        super().__init__(manager, area, "plantings")

    def _plantings(self):
        return [p for p in self.manager.plantings.values() if p.area_id == self.area_id]

    @property
    def native_value(self) -> int:
        return sum(1 for p in self._plantings() if p.status == ACTIVE)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"plantings": [p.to_dict(with_record=False) for p in self._plantings()]}


class PlantCatalogueSensor(GardenEntity, SensorEntity):
    """How many plants the downloaded catalogue holds, and how fresh it is."""

    _attr_translation_key = "plant_catalogue"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager) -> None:
        super().__init__(manager, None, "plant_catalogue")

    @property
    def native_value(self) -> int | None:
        return self.manager.plants.meta.get("plants")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        meta = self.manager.plants.meta
        return {
            key: meta[key]
            for key in ("fetched_at", "checked_at", "source")
            if key in meta
        }


class BudgetRemainingSensor(GardenEntity, SensorEntity):
    """This year's budget less what was bought and what is still planned."""

    _attr_translation_key = "budget_remaining"
    _attr_device_class = SensorDeviceClass.MONETARY

    def __init__(self, manager) -> None:
        super().__init__(manager, None, "budget_remaining")
        self._attr_native_unit_of_measurement = manager.hass.config.currency

    @property
    def native_value(self) -> float | None:
        return self.manager.plan()[0].remaining

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        year = self.manager.plan()[0]
        return {
            "year": year.year,
            "budget": year.budget,
            "spent": year.spent,
            "planned": year.planned,
        }


class PlannedThisYearSensor(GardenEntity, SensorEntity):
    """How many plan items fall in this year; the attribute lists them."""

    _attr_translation_key = "planned_this_year"

    def __init__(self, manager) -> None:
        super().__init__(manager, None, "planned_this_year")

    @property
    def native_value(self) -> int:
        return len(self.manager.plan()[0].placements)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        names = {a.subentry_id: a.title for a in self.manager.area_subentries}
        return {
            "items": [
                {
                    "id": p.item.id,
                    "name": p.item.name,
                    "area": names.get(p.item.area_id),
                    "estimated_cost": p.item.cost,
                }
                for p in self.manager.plan()[0].placements[:MAX_LISTED]
            ]
        }
