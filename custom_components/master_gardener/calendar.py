"""Calendars: one per garden area and one for the whole property."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .engine.models import DONE, UPCOMING, Task
from .entity import GardenEntity
from .manager import GardenConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GardenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    async_add_entities([GardenCalendar(manager, None, "calendar")])
    for area in manager.area_subentries:
        async_add_entities(
            [GardenCalendar(manager, area, "calendar")],
            config_subentry_id=area.subentry_id,
        )


class GardenCalendar(GardenEntity, CalendarEntity):
    _attr_name = None  # named after the device

    @property
    def event(self) -> CalendarEvent | None:
        """The task in progress, or else the next one coming up."""
        today = self.manager.today
        open_tasks = [t for t in self.tasks() if t.state(today) != DONE]
        current = [t for t in open_tasks if t.state(today) != UPCOMING]
        if current:
            return self._event(min(current, key=lambda t: t.end))
        return self._event(open_tasks[0]) if open_tasks else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        tasks = self.manager.tasks_between(start_date.date(), end_date.date())
        return [
            self._event(t)
            for t in tasks
            if self.area_id is None or t.area_id == self.area_id
        ]

    def _event(self, task: Task) -> CalendarEvent:
        summary = task.title
        if self.area_id is None:
            names = {a.subentry_id: a.title for a in self.manager.area_subentries}
            if names.get(task.area_id, "") not in summary:
                summary = f"{summary} ({names.get(task.area_id, '?')})"
        if task.completed_on:
            summary = f"✓ {summary}"
        return CalendarEvent(
            start=task.start,
            end=task.end + timedelta(days=1),  # calendar end dates are exclusive
            summary=summary,
            description=task.detail or None,
            uid=task.id,
        )
