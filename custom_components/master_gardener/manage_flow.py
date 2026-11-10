"""Manage plantings: record a death or removal, a purchase, or fix the details.

Like Add plantings, this subentry flow never creates a subentry: it changes
the planting and ends with a summary.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
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

from .engine.models import ACTIVE, DEAD, PLANNED, PROPOSED, REMOVED, Planting

if TYPE_CHECKING:
    from .manager import GardenManager

CONF_AREA = "area"
CONF_PLANTING = "planting"
CONF_NOTES = "notes"
CONF_COST = "cost"
CONF_NAME = "name"
CONF_QUANTITY = "quantity"
CONF_PLANT = "plant"
CONF_PROFILE = "care_profile"
CONF_WHEN = "when"
CONF_PLANTED_ON = "planted_on"
CONF_ESTIMATED_COST = "estimated_cost"
WHEN_ESTABLISHED = "established"
WHEN_DATE = "on_date"
WHEN_NOT_YET = "not_yet"

# What can happen to a planting, by its status. Each is a step below.
ACTIONS = {
    ACTIVE: ["died", "removed", "edit"],
    PLANNED: ["bought", "dropped", "edit"],
    PROPOSED: ["accept_planned", "accept_active", "declined", "edit"],
}
STATUS_ORDER = {ACTIVE: 0, PLANNED: 1, PROPOSED: 2}

NOTES = TextSelector(TextSelectorConfig(multiline=True))
MONEY = NumberSelector(
    NumberSelectorConfig(min=0, max=1_000_000, step=0.01, mode=NumberSelectorMode.BOX)
)


def planting_label(planting: Planting) -> str:
    """How a planting is listed: name, how many, and anything unusual."""
    label = planting.name
    if planting.quantity > 1:
        label += f" ({planting.quantity})"
    if planting.status != ACTIVE:
        label += f", {planting.status}"
    if planting.planted_on:
        label += f", planted {planting.planted_on.isoformat()}"
    return label


class ManagePlantingsFlow(ConfigSubentryFlow):
    def __init__(self) -> None:
        self._area = ""
        self._planting_id = ""

    def _manager(self) -> GardenManager | None:
        return getattr(self._get_entry(), "runtime_data", None)

    def _managed(self, manager: GardenManager) -> list[Planting]:
        """Plantings that can still change: not dead or removed."""
        return [p for p in manager.plantings.values() if p.status in ACTIONS]

    @property
    def _planting(self) -> Planting:
        return self._manager().plantings[self._planting_id]  # type: ignore[union-attr]

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the area, unless only one has plantings."""
        manager = self._manager()
        if manager is None:
            return self.async_abort(reason="not_loaded")
        names = {a.subentry_id: a.title for a in manager.area_subentries}
        areas = sorted(
            {p.area_id for p in self._managed(manager) if p.area_id in names},
            key=lambda a: names[a].lower(),
        )
        if not areas:
            return self.async_abort(reason="no_plantings")
        if user_input is not None or len(areas) == 1:
            self._area = user_input[CONF_AREA] if user_input else areas[0]
            return await self.async_step_pick()
        options = [SelectOptionDict(value=a, label=names[a]) for a in areas]
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_AREA): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        manager = self._manager()
        if manager is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            self._planting_id = user_input[CONF_PLANTING]
            return await self.async_step_action()
        plantings = sorted(
            (p for p in self._managed(manager) if p.area_id == self._area),
            key=lambda p: (STATUS_ORDER[p.status], p.name.lower()),
        )
        area = next(
            a.title for a in manager.area_subentries if a.subentry_id == self._area
        )
        options = [
            SelectOptionDict(value=p.id, label=planting_label(p)) for p in plantings
        ]
        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PLANTING): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
            description_placeholders={"area": area},
        )

    async def async_step_action(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """What happened: the choices depend on the planting's status."""
        planting = self._planting
        return self.async_show_menu(
            step_id="action",
            menu_options=ACTIONS[planting.status],
            description_placeholders={"name": planting.name},
        )

    # --- what happened ------------------------------------------------------

    def _done(self, reason: str) -> SubentryFlowResult:
        return self.async_abort(
            reason=reason, description_placeholders={"name": self._planting.name}
        )

    def _set(self, status: str, reason: str, **kwargs: Any) -> SubentryFlowResult:
        self._manager().set_planting_status(  # type: ignore[union-attr]
            self._planting_id, status, **kwargs
        )
        return self._done(reason)

    def _notes_form(self, step_id: str) -> SubentryFlowResult:
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({vol.Optional(CONF_NOTES): NOTES}),
            description_placeholders={"name": self._planting.name},
        )

    async def async_step_died(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is None:
            return self._notes_form("died")
        return self._set(DEAD, "recorded_dead", notes=user_input.get(CONF_NOTES))

    async def async_step_removed(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is None:
            return self._notes_form("removed")
        return self._set(REMOVED, "recorded_removed", notes=user_input.get(CONF_NOTES))

    async def async_step_dropped(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is None:
            return self._notes_form("dropped")
        return self._set(REMOVED, "recorded_dropped", notes=user_input.get(CONF_NOTES))

    async def async_step_declined(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is None:
            return self._notes_form("declined")
        return self._set(REMOVED, "recorded_declined", notes=user_input.get(CONF_NOTES))

    async def async_step_accept_planned(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return self._set(PLANNED, "recorded_planned")

    async def _bought(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        if user_input is None:
            schema = vol.Schema({vol.Optional(CONF_COST): MONEY})
            return self.async_show_form(
                step_id=step_id,
                data_schema=self.add_suggested_values_to_schema(
                    schema, {CONF_COST: self._planting.estimated_cost}
                ),
                description_placeholders={"name": self._planting.name},
            )
        return self._set(ACTIVE, "recorded_bought", cost=user_input.get(CONF_COST))

    async def async_step_bought(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._bought("bought", user_input)

    async def async_step_accept_active(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._bought("accept_active", user_input)

    # --- editing -------------------------------------------------------------

    async def async_step_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        manager = self._manager()
        if manager is None:
            return self.async_abort(reason="not_loaded")
        planting = self._planting
        errors: dict[str, str] = {}
        placeholders = {"name": planting.name, "suggestions": ""}
        if user_input is not None:
            record = planting.plant_data
            wanted = (user_input.get(CONF_PLANT) or "").strip()
            if wanted != (planting.plant or ""):
                record = None
                if wanted:
                    record, candidates = await manager.plants.async_resolve(wanted)
                    if record is None:
                        errors[CONF_PLANT] = (
                            "plant_ambiguous" if candidates else "plant_unknown"
                        )
                        placeholders["suggestions"] = ", ".join(
                            f"{c['slug']} ({c['common_name']})" for c in candidates[:8]
                        )
            # Only a growing plant has a planting date to edit.
            when = user_input.get(CONF_WHEN, WHEN_NOT_YET)
            if when == WHEN_DATE and not user_input.get(CONF_PLANTED_ON):
                errors[CONF_PLANTED_ON] = "date_needed"
            if not errors:
                manager.edit_planting(
                    planting.id,
                    record=record,
                    name=user_input[CONF_NAME].strip() or planting.name,
                    quantity=int(user_input[CONF_QUANTITY]),
                    planted_on=date.fromisoformat(user_input[CONF_PLANTED_ON])
                    if when == WHEN_DATE
                    else None,
                    established=when == WHEN_ESTABLISHED,
                    notes=user_input.get(CONF_NOTES, ""),
                    profile=user_input.get(CONF_PROFILE),
                    estimated_cost=planting.estimated_cost
                    if planting.status == ACTIVE
                    else user_input.get(CONF_ESTIMATED_COST),
                )
                return self._done("recorded_edit")

        if planting.planted_on:
            when = WHEN_DATE
        elif planting.established:
            when = WHEN_ESTABLISHED
        else:
            when = WHEN_NOT_YET
        profiles = [
            SelectOptionDict(value=key, label=profile["name"])
            for key, profile in sorted(
                manager.catalog.profiles.items(), key=lambda kv: kv[1]["name"]
            )
        ]
        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME): TextSelector(),
            vol.Required(CONF_QUANTITY): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=10000, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Optional(CONF_PLANT): TextSelector(),
            vol.Optional(CONF_PROFILE): SelectSelector(
                SelectSelectorConfig(options=profiles, mode=SelectSelectorMode.DROPDOWN)
            ),
        }
        if planting.status == ACTIVE:
            fields[vol.Required(CONF_WHEN)] = SelectSelector(
                SelectSelectorConfig(
                    options=[WHEN_ESTABLISHED, WHEN_DATE, WHEN_NOT_YET],
                    translation_key="edit_planted_when",
                    mode=SelectSelectorMode.LIST,
                )
            )
            fields[vol.Optional(CONF_PLANTED_ON)] = DateSelector()
        else:
            fields[vol.Optional(CONF_ESTIMATED_COST)] = MONEY
        fields[vol.Optional(CONF_NOTES)] = NOTES
        current = {
            CONF_NAME: planting.name,
            CONF_QUANTITY: planting.quantity,
            CONF_PLANT: planting.plant,
            CONF_PROFILE: planting.profile,
            CONF_WHEN: when,
            CONF_PLANTED_ON: planting.planted_on.isoformat()
            if planting.planted_on
            else None,
            CONF_ESTIMATED_COST: planting.estimated_cost,
            CONF_NOTES: planting.notes or None,
        }
        schema = vol.Schema(fields)
        return self.async_show_form(
            step_id="edit",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or current
            ),
            errors=errors,
            description_placeholders=placeholders,
        )
