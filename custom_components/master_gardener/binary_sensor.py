"""Binary sensors: needs water or fertilizer (soil sensors), frost or heat risk."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CARE_FEED,
    CARE_FROST,
    CARE_HEAT,
    CARE_WATER,
    CONF_CONDUCTIVITY_SENSORS,
    CONF_MOISTURE_SENSORS,
)
from .engine.care import Advice
from .entity import GardenEntity
from .manager import GardenConfigEntry, GardenManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GardenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    for area in manager.area_subentries:
        entities = []
        if area.data.get(CONF_MOISTURE_SENSORS):
            entities.append(CareBinarySensor(manager, area, CARE_WATER))
        if area.data.get(CONF_CONDUCTIVITY_SENSORS) and manager.feed_below(area) > 0:
            entities.append(CareBinarySensor(manager, area, CARE_FEED))
        if manager.weather_entity:
            entities.append(WeatherBinarySensor(manager, area, CARE_FROST))
            entities.append(WeatherBinarySensor(manager, area, CARE_HEAT))
        async_add_entities(entities, config_subentry_id=area.subentry_id)


class CareBinarySensor(GardenEntity, BinarySensorEntity):
    """On while the soil says the area needs attention; unknown without a reading."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, manager: GardenManager, area: ConfigSubentry, kind: str) -> None:
        key = {CARE_WATER: "needs_water", CARE_FEED: "needs_fertilizer"}[kind]
        super().__init__(manager, area, key)
        self._attr_translation_key = key
        self._area = area
        self._kind = kind

    def _advice(self) -> Advice | None:
        return self.manager.care_advice(self._area)[self._kind]

    @property
    def is_on(self) -> bool | None:
        advice = self._advice()
        return advice.needed if advice else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        advice = self._advice()
        if not advice:
            return {}
        return {
            "reason": advice.reason,
            "reading": advice.reading,
            "threshold": advice.threshold,
        }


class WeatherBinarySensor(GardenEntity, BinarySensorEntity):
    """On while the forecast threatens plantings here; unknown with no forecast."""

    def __init__(self, manager: GardenManager, area: ConfigSubentry, kind: str) -> None:
        key = {CARE_FROST: "frost_risk", CARE_HEAT: "heat_risk"}[kind]
        super().__init__(manager, area, key)
        self._attr_translation_key = key
        self._attr_device_class = {
            CARE_FROST: BinarySensorDeviceClass.COLD,
            CARE_HEAT: BinarySensorDeviceClass.HEAT,
        }[kind]
        self._area = area
        self._kind = kind

    @property
    def is_on(self) -> bool | None:
        if not self.manager.forecast:
            return None
        return self.manager.weather_alerts(self._area)[self._kind] is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        alert = self.manager.weather_alerts(self._area)[self._kind]
        if not alert:
            return {}
        return {
            "reason": alert.reason,
            "date": alert.day.isoformat(),
            "plants": list(alert.plants),
        }
