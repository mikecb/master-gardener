"""To-do lists: what needs doing now in each garden area.

Ticking an item records the work. Adding an item is how you give feedback
("grass too long"): it becomes a task due today in that area.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DONE_VISIBLE_DAYS
from .engine.models import DONE, UPCOMING
from .entity import GardenEntity
from .manager import GardenConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GardenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    for area in manager.area_subentries:
        async_add_entities(
            [AreaTodoList(manager, area, "todo")], config_subentry_id=area.subentry_id
        )


def _as_date(value: date | datetime | None) -> date | None:
    return value.date() if isinstance(value, datetime) else value


class AreaTodoList(GardenEntity, TodoListEntity):
    _attr_name = None  # named after the device
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    @property
    def todo_items(self) -> list[TodoItem]:
        today = self.manager.today
        recent = today - timedelta(days=DONE_VISIBLE_DAYS)
        items = []
        for task in self.tasks():
            state = task.state(today)
            if task.projected or (state == UPCOMING and not task.custom):
                continue
            if state == DONE and task.completed_on < recent:
                continue
            items.append(
                TodoItem(
                    uid=task.id,
                    summary=task.title,
                    status=TodoItemStatus.COMPLETED
                    if state == DONE
                    else TodoItemStatus.NEEDS_ACTION,
                    due=task.end,
                    description=task.detail or None,
                )
            )
        return items

    async def async_create_todo_item(self, item: TodoItem) -> None:
        self.manager.add_task(
            self.area_id,
            item.summary or "Task",
            _as_date(item.due),
            item.description or "",
        )

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if item.uid.startswith("custom:"):
            self.manager.update_custom_task(
                item.uid,
                title=item.summary,
                due=_as_date(item.due),
                notes=item.description,
            )
        if item.status == TodoItemStatus.COMPLETED:
            self.manager.complete(item.uid)
        elif item.status == TodoItemStatus.NEEDS_ACTION:
            self.manager.reopen(item.uid)

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            self.manager.delete(uid)
