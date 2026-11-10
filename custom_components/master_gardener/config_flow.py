"""Config flow: frost dates for the property, one subentry per garden area."""

from __future__ import annotations

from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    DateSelector,
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
    CONF_FIRST_FROST,
    CONF_LAST_FROST,
    CONF_LIGHT,
    CONF_NAME,
    CONF_SIZE_SQFT,
    CONF_STYLE,
    DOMAIN,
    LIGHTS,
    STYLES,
    SUBENTRY_AREA,
)

DEFAULT_LAST_FROST = "04-15"
DEFAULT_FIRST_FROST = "10-25"


def _site_schema(last_frost: str, first_frost: str) -> vol.Schema:
    year = date.today().year
    return vol.Schema(
        {
            vol.Required(CONF_LAST_FROST, default=f"{year}-{last_frost}"): DateSelector(),
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

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _site_data(user_input)
            errors = _site_errors(data)
            if not errors:
                return self.async_create_entry(title="Master Gardener", data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=_site_schema(DEFAULT_LAST_FROST, DEFAULT_FIRST_FROST),
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
                return self.async_create_entry(data=data)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_site_schema(current[CONF_LAST_FROST], current[CONF_FIRST_FROST]),
            errors=errors,
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
