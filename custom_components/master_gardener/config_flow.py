"""Config flow: frost dates for the property, one subentry per garden area."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    DateSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    CONF_ADJACENT,
    CONF_CHARACTER,
    CONF_CONDUCTIVITY_SENSORS,
    CONF_DRY_BELOW,
    CONF_FEED_BELOW,
    CONF_FIRST_FROST,
    CONF_LAST_FROST,
    CONF_LIGHT,
    CONF_MOISTURE_SENSORS,
    CONF_NAME,
    CONF_SIZE_SQFT,
    CONF_STYLE,
    CONF_WEATHER_ENTITY,
    CONF_ZIP_CODE,
    CONF_ZONE,
    DOMAIN,
    LIGHTS,
    STYLES,
    SUBENTRY_AREA,
)
from .engine.climate import frost_dates_for_zone

# Public USDA Plant Hardiness Zone Map data, keyed by ZIP code.
ZONE_URL = "https://phzmapi.org/{zip_code}.json"

WEATHER_SELECTOR = EntitySelector(EntitySelectorConfig(domain="weather"))


async def _lookup_zone(hass: HomeAssistant, zip_code: str) -> str:
    """Return the hardiness zone ("7a") for a US ZIP code.

    Raises LookupError for a ZIP code the map does not cover.
    """
    session = async_get_clientsession(hass)
    async with session.get(
        ZONE_URL.format(zip_code=zip_code), timeout=aiohttp.ClientTimeout(total=15)
    ) as response:
        if response.status == 404:
            raise LookupError(zip_code)
        response.raise_for_status()
        return (await response.json(content_type=None))["zone"]


def _site_schema(last_frost: str, first_frost: str) -> vol.Schema:
    year = date.today().year
    return vol.Schema(
        {
            vol.Required(
                CONF_LAST_FROST, default=f"{year}-{last_frost}"
            ): DateSelector(),
            vol.Required(
                CONF_FIRST_FROST, default=f"{year}-{first_frost}"
            ): DateSelector(),
        }
    )


def _site_data(user_input: dict[str, Any]) -> dict[str, str]:
    """Keep month and day only: "2026-04-15" -> "04-15"."""
    return {
        CONF_LAST_FROST: user_input[CONF_LAST_FROST][5:],
        CONF_FIRST_FROST: user_input[CONF_FIRST_FROST][5:],
    }


def _site_errors(data: dict[str, str]) -> dict[str, str]:
    if data[CONF_LAST_FROST] >= data[CONF_FIRST_FROST]:
        return {"base": "frost_order"}
    return {}


class MasterGardenerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._zip_code = ""
        self._zone = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the ZIP code and look up its hardiness zone."""
        errors: dict[str, str] = {}
        if user_input is not None:
            zip_code = user_input[CONF_ZIP_CODE].strip()[:5]
            if not re.fullmatch(r"\d{5}", zip_code):
                errors[CONF_ZIP_CODE] = "invalid_zip"
            else:
                try:
                    self._zone = await _lookup_zone(self.hass, zip_code)
                    frost_dates_for_zone(self._zone)
                except LookupError:
                    errors[CONF_ZIP_CODE] = "unknown_zip"
                except (aiohttp.ClientError, TimeoutError, ValueError):
                    errors["base"] = "cannot_connect"
                else:
                    self._zip_code = zip_code
                    return await self.async_step_frost()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_ZIP_CODE): TextSelector()}), user_input
            ),
            errors=errors,
        )

    async def async_step_frost(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the frost dates estimated from the zone; the user may correct them."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _site_data(user_input)
            errors = _site_errors(data)
            if not errors:
                return self.async_create_entry(
                    title="Master Gardener",
                    data={
                        CONF_ZIP_CODE: self._zip_code,
                        CONF_ZONE: self._zone,
                        **data,
                    },
                )
        return self.async_show_form(
            step_id="frost",
            data_schema=_site_schema(*frost_dates_for_zone(self._zone)),
            description_placeholders={"zone": self._zone, "zip_code": self._zip_code},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SiteOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_AREA: AreaSubentryFlow}


class SiteOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _site_data(user_input)
            errors = _site_errors(data)
            if not errors:
                # Left empty, the weather entity is dropped again.
                if weather := user_input.get(CONF_WEATHER_ENTITY):
                    data[CONF_WEATHER_ENTITY] = weather
                return self.async_create_entry(data=data)
        current = {**self.config_entry.data, **self.config_entry.options}
        schema = _site_schema(current[CONF_LAST_FROST], current[CONF_FIRST_FROST])
        schema = schema.extend({vol.Optional(CONF_WEATHER_ENTITY): WEATHER_SELECTOR})
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_WEATHER_ENTITY: current.get(CONF_WEATHER_ENTITY)}
            ),
            errors=errors,
        )


def _soil_sensors(device_class: str) -> EntitySelector:
    return EntitySelector(
        EntitySelectorConfig(domain="sensor", device_class=device_class, multiple=True)
    )


def _threshold(low: float, high: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=low,
            max=high,
            step=step,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement=unit,
        )
    )


class AreaSubentryFlow(ConfigSubentryFlow):
    """Add or edit one garden area."""

    def _schema(self, exclude: str | None = None) -> vol.Schema:
        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME): TextSelector(),
            vol.Required(CONF_STYLE): SelectSelector(
                SelectSelectorConfig(
                    options=STYLES,
                    translation_key="style",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_LIGHT, default="mixed"): SelectSelector(
                SelectSelectorConfig(
                    options=LIGHTS,
                    translation_key="light",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_SIZE_SQFT): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=250000,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="ft²",
                )
            ),
        }
        others = [
            SelectOptionDict(value=s.subentry_id, label=s.title)
            for s in self._get_entry().subentries.values()
            if s.subentry_type == SUBENTRY_AREA and s.subentry_id != exclude
        ]
        if others:
            fields[vol.Optional(CONF_ADJACENT)] = SelectSelector(
                SelectSelectorConfig(
                    options=others, multiple=True, mode=SelectSelectorMode.DROPDOWN
                )
            )
        fields[vol.Optional(CONF_CHARACTER)] = TextSelector(
            TextSelectorConfig(multiline=True)
        )
        fields[vol.Optional(CONF_MOISTURE_SENSORS)] = _soil_sensors("moisture")
        fields[vol.Optional(CONF_DRY_BELOW)] = _threshold(1, 100, 1, "%")
        fields[vol.Optional(CONF_CONDUCTIVITY_SENSORS)] = _soil_sensors("conductivity")
        fields[vol.Optional(CONF_FEED_BELOW)] = _threshold(0, 5000, 10, "µS/cm")
        return vol.Schema(fields)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="user", data_schema=self._schema())

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title=user_input[CONF_NAME],
                data=user_input,
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                self._schema(exclude=subentry.subentry_id), subentry.data
            ),
        )
