"""Shared entity base: one device per garden area, plus one for the property."""

from __future__ import annotations

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_STYLE, DOMAIN
from .engine.models import DUE, OVERDUE, Task
from .manager import GardenManager


def area_device_id(entry_id: str, area_id: str) -> str:
    return f"{entry_id}_{area_id}"


class GardenEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, manager: GardenManager, area: ConfigSubentry | None, key: str
    ) -> None:
        self.manager = manager
        self.area_id = area.subentry_id if area else None
        entry_id = manager.entry.entry_id
        if area:
            self._attr_unique_id = f"{entry_id}_{area.subentry_id}_{key}"
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, area_device_id(entry_id, area.subentry_id))},
                name=area.title,
                manufacturer="Master Gardener",
                model=area.data[CONF_STYLE].replace("_", " ").capitalize(),
                via_device_id=manager.garden_device_id,
            )
        else:
            self._attr_unique_id = f"{entry_id}_{key}"
            self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)})

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.manager.async_add_listener(self.async_write_ha_state))

    def tasks(self) -> list[Task]:
        """This area's tasks, or the whole property's."""
        return self.manager.tasks(self.area_id)

    def actionable(self, *states: str) -> list[Task]:
        """Real (not projected) tasks that are due and/or overdue today."""
        today = self.manager.today
        wanted = states or (DUE, OVERDUE)
        return [t for t in self.tasks() if not t.projected and t.state(today) in wanted]
