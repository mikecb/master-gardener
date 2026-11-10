"""Config flow: frost dates for the property, one subentry per garden area."""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import TYPE_CHECKING, Any

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
    BooleanSelector,
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
    CONF_ANNUAL_BUDGET,
    CONF_CHARACTER,
    CONF_CONDUCTIVITY_SENSORS,
    CONF_DRY_BELOW,
    CONF_FEED_BELOW,
    CONF_FIRST_FROST,
    CONF_FROST_STATION,
    CONF_LAST_FROST,
    CONF_LATITUDE,
    CONF_LIGHT,
    CONF_LONGITUDE,
    CONF_MOISTURE_SENSORS,
    CONF_NAME,
    CONF_SEED_INVENTORY,
    CONF_SIZE_SQFT,
    CONF_STYLE,
    CONF_USE_STATION,
    CONF_WEATHER_ENTITY,
    CONF_ZIP_CODE,
    CONF_ZONE,
    DOMAIN,
    LIGHTS,
    SEED_INVENTORY_DOMAIN,
    STYLES,
    SUBENTRY_AREA,
    SUBENTRY_MANAGE,
    SUBENTRY_PLANTING,
    SUBENTRY_REVIEW,
)
from .engine import frost_normals
from .engine.climate import frost_dates_for_zone
from .engine.matching import names_match, rank_candidates
from .engine.plantdb import name_forms
from .manage_flow import ManagePlantingsFlow, ReviewProposalsFlow

_LOGGER = logging.getLogger(__name__)

# Public USDA Plant Hardiness Zone Map data, keyed by ZIP code.
ZONE_URL = "https://phzmapi.org/{zip_code}.json"

if TYPE_CHECKING:
    from .manager import GardenManager

WEATHER_SELECTOR = EntitySelector(EntitySelectorConfig(domain="weather"))

CONF_AREA = "area"
CONF_PLANTS = "plants"
CONF_WHEN = "when"
CONF_PLANTED_ON = "planted_on"
WHEN_THIS_SEASON = "this_season"
WHEN_ESTABLISHED = "established"
WHEN_DATE = "on_date"
# Answers on the matching step besides a catalogue plant.
KEEP = "_keep"
SKIP = "_skip"
MAX_LINES = 50
MAX_CHOICES = 8
# How many search results are ranked to find those choices.
SEARCH_POOL = 50
# "3 hosta", "3x hosta", "3 x hosta"
QUANTITY = re.compile(r"^(\d+)\s*[x\u00d7]?\s+(.+)$", re.IGNORECASE)


async def _lookup_zone(hass: HomeAssistant, zip_code: str) -> dict:
    """The hardiness zone ("7a") of a US ZIP code, and where it is.

    Raises LookupError for a ZIP code the map does not cover.
    """
    session = async_get_clientsession(hass)
    async with session.get(
        ZONE_URL.format(zip_code=zip_code), timeout=aiohttp.ClientTimeout(total=15)
    ) as response:
        if response.status == 404:
            raise LookupError(zip_code)
        response.raise_for_status()
        found = await response.json(content_type=None)
    place: dict[str, Any] = {CONF_ZONE: found["zone"]}
    try:
        place[CONF_LATITUDE] = float(found["coordinates"]["lat"])
        place[CONF_LONGITUDE] = float(found["coordinates"]["lon"])
    except (KeyError, TypeError, ValueError):
        pass  # the zone is enough; frost dates then come from it
    return place


async def _lookup_frost_normals(
    hass: HomeAssistant, latitude: float, longitude: float
) -> frost_normals.FrostNormals | None:
    """The nearest weather station's frost dates, or None if none can be had."""
    session = async_get_clientsession(hass)
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        for radius in frost_normals.SEARCH_RADII:
            url = frost_normals.search_url(latitude, longitude, radius)
            async with session.get(url, timeout=timeout) as response:
                response.raise_for_status()
                found = frost_normals.stations(await response.json(content_type=None))
            ranked = frost_normals.nearest(found, latitude, longitude)
            if not ranked:
                continue
            url = frost_normals.data_url([s.id for s, _ in ranked])
            async with session.get(url, timeout=timeout) as response:
                response.raise_for_status()
                rows = await response.json(content_type=None)
            if normals := frost_normals.pick(
                rows if isinstance(rows, list) else [], ranked
            ):
                return normals
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.debug("No station frost normals: %s", err)
    return None


def _station_text(normals: frost_normals.FrostNormals) -> str:
    """One sentence naming the station, for the forms."""
    height = f", {normals.elevation_m} m up" if normals.elevation_m is not None else ""
    last, first = (
        f"{day:%B} {day.day}"
        for day in (date(2001, *normals.last_frost), date(2001, *normals.first_frost))
    )
    return (
        f"The nearest weather station with frost records, {normals.name} "
        f"({normals.distance_km:.0f} km away{height}), has an average last spring "
        f"frost of {last} and first autumn frost of {first} "
        f"({frost_normals.SOURCE})."
    )


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
        self._place: dict[str, Any] = {}
        self._normals: frost_normals.FrostNormals | None = None

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
                    self._place = await _lookup_zone(self.hass, zip_code)
                    self._zone = self._place[CONF_ZONE]
                    frost_dates_for_zone(self._zone)
                except LookupError:
                    errors[CONF_ZIP_CODE] = "unknown_zip"
                except (aiohttp.ClientError, TimeoutError, ValueError):
                    errors["base"] = "cannot_connect"
                else:
                    self._zip_code = zip_code
                    if CONF_LATITUDE in self._place:
                        self._normals = await _lookup_frost_normals(
                            self.hass,
                            self._place[CONF_LATITUDE],
                            self._place[CONF_LONGITUDE],
                        )
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
        """Show the station's frost dates, else the zone's; the user may correct."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _site_data(user_input)
            errors = _site_errors(data)
            if not errors:
                station = (
                    {CONF_FROST_STATION: self._normals.as_data()}
                    if self._normals
                    else {}
                )
                return self.async_create_entry(
                    title="Master Gardener",
                    data={
                        CONF_ZIP_CODE: self._zip_code,
                        **self._place,
                        **data,
                        **station,
                    },
                )
        intro = f"ZIP code {self._zip_code} is in hardiness zone {self._zone}."
        if self._normals:
            normals = self._normals.as_data()
            defaults = (normals[CONF_LAST_FROST], normals[CONF_FIRST_FROST])
            source = f"{intro} {_station_text(self._normals)}"
        else:
            defaults = frost_dates_for_zone(self._zone)
            source = (
                f"{intro} No nearby weather station frost records could be found, "
                "so these are typical frost dates for that zone."
            )
        return self.async_show_form(
            step_id="frost",
            data_schema=_site_schema(*defaults),
            description_placeholders={"source": source},
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
        return {
            SUBENTRY_AREA: AreaSubentryFlow,
            SUBENTRY_PLANTING: PlantingFlow,
            SUBENTRY_MANAGE: ManagePlantingsFlow,
            SUBENTRY_REVIEW: ReviewProposalsFlow,
        }


class SiteOptionsFlow(OptionsFlow):
    def __init__(self) -> None:
        self._normals: frost_normals.FrostNormals | None = None
        self._looked_up = False

    async def _station(self) -> frost_normals.FrostNormals | None:
        """The nearest station's frost dates, looked up once per form."""
        if self._looked_up:
            return self._normals
        self._looked_up = True
        conf = {**self.config_entry.data, **self.config_entry.options}
        place = {k: conf[k] for k in (CONF_LATITUDE, CONF_LONGITUDE) if k in conf}
        if len(place) < 2 and conf.get(CONF_ZIP_CODE):
            # Set up before locations were kept: find the ZIP code again.
            try:
                place = await _lookup_zone(self.hass, conf[CONF_ZIP_CODE])
            except (LookupError, aiohttp.ClientError, TimeoutError, ValueError):
                place = {}
        if CONF_LATITUDE not in place and (
            self.hass.config.latitude or self.hass.config.longitude
        ):
            # No ZIP code to go by: the garden is presumably at home.
            place = {
                CONF_LATITUDE: self.hass.config.latitude,
                CONF_LONGITUDE: self.hass.config.longitude,
            }
        if CONF_LATITUDE in place:
            self._normals = await _lookup_frost_normals(
                self.hass, place[CONF_LATITUDE], place[CONF_LONGITUDE]
            )
        return self._normals

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        normals = await self._station()
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            if normals and user_input.get(CONF_USE_STATION):
                station = normals.as_data()
                data = {
                    CONF_LAST_FROST: station[CONF_LAST_FROST],
                    CONF_FIRST_FROST: station[CONF_FIRST_FROST],
                }
            else:
                data = _site_data(user_input)
            errors = _site_errors(data)
            if not errors:
                # Left empty, the weather entity is dropped again.
                if weather := user_input.get(CONF_WEATHER_ENTITY):
                    data[CONF_WEATHER_ENTITY] = weather
                # Empty or 0: no budget, the whole plan is this year.
                if budget := user_input.get(CONF_ANNUAL_BUDGET):
                    data[CONF_ANNUAL_BUDGET] = budget
                if seeds := user_input.get(CONF_SEED_INVENTORY):
                    data[CONF_SEED_INVENTORY] = seeds
                if normals:
                    data[CONF_FROST_STATION] = normals.as_data()
                elif CONF_FROST_STATION in current:
                    data[CONF_FROST_STATION] = current[CONF_FROST_STATION]
                return self.async_create_entry(data=data)
        schema = _site_schema(current[CONF_LAST_FROST], current[CONF_FIRST_FROST])
        if normals:
            schema = schema.extend(
                {vol.Optional(CONF_USE_STATION, default=False): BooleanSelector()}
            )
        schema = schema.extend(
            {
                vol.Optional(CONF_WEATHER_ENTITY): WEATHER_SELECTOR,
                vol.Optional(CONF_ANNUAL_BUDGET): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=10_000_000,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement=self.hass.config.currency,
                    )
                ),
                vol.Optional(CONF_SEED_INVENTORY): EntitySelector(
                    EntitySelectorConfig(
                        domain="sensor", integration=SEED_INVENTORY_DOMAIN
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {
                    key: current.get(key)
                    for key in (
                        CONF_WEATHER_ENTITY,
                        CONF_ANNUAL_BUDGET,
                        CONF_SEED_INVENTORY,
                    )
                },
            ),
            description_placeholders={
                "station": _station_text(normals) if normals else ""
            },
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


def parse_plant_lines(text: str) -> list[tuple[int, str]]:
    """One plant per line, optionally led by a count: [(quantity, name)]."""
    lines = []
    for raw in text.splitlines():
        line = " ".join(raw.strip(" -*•\t").split())
        if not line:
            continue
        quantity = 1
        if match := QUANTITY.match(line):
            quantity, line = max(1, int(match[1])), match[2]
        lines.append((quantity, line))
    return lines


def _choice_label(record: dict) -> str:
    kind = record.get("subcategory") or record.get("category") or ""
    return f"{record['common_name']} - {record['scientific_name']}, {kind}"


class PlantingFlow(ConfigSubentryFlow):
    """Record plants already growing in an area, several at a time.

    It never creates a subentry: the plantings are recorded with the garden
    and the flow ends with a summary.
    """

    def __init__(self) -> None:
        self._area = ""
        self._when: dict[str, Any] = {}
        self._lines: list[tuple[int, str]] = []
        self._found: dict[int, dict] = {}  # line -> the one plant it names
        self._choices: dict[int, list[dict]] = {}  # line -> candidates

    def _manager(self) -> GardenManager | None:
        entry = self._get_entry()
        return getattr(entry, "runtime_data", None)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        manager = self._manager()
        if manager is None:
            return self.async_abort(reason="not_loaded")
        areas = manager.area_subentries
        if not areas:
            return self.async_abort(reason="no_areas")
        if not manager.plants.ready:
            return self.async_abort(reason="catalogue_not_ready")
        errors: dict[str, str] = {}
        if user_input is not None:
            lines = parse_plant_lines(user_input[CONF_PLANTS])
            if not lines:
                errors[CONF_PLANTS] = "no_plants"
            elif len(lines) > MAX_LINES:
                errors[CONF_PLANTS] = "too_many_plants"
            elif user_input[CONF_WHEN] == WHEN_DATE and not user_input.get(
                CONF_PLANTED_ON
            ):
                errors[CONF_PLANTED_ON] = "date_needed"
            else:
                self._area = user_input[CONF_AREA]
                self._when = user_input
                self._lines = lines
                for index, (_, text) in enumerate(lines):
                    record, candidates = await manager.plants.async_resolve(text)
                    if record and names_match(text, record["common_name"]):
                        self._found[index] = record
                        continue
                    if record:
                        # Found by its id ("lavender" is Spanish lavender):
                        # confirm it, alongside its relatives and namesakes.
                        genus = record["scientific_name"].split()[0]
                        others = [
                            *await manager.plants.async_search(
                                genus, limit=MAX_CHOICES
                            ),
                            *await manager.plants.async_search(text, limit=MAX_CHOICES),
                        ]
                        unique = {record["slug"]: record}
                        for other in rank_candidates(text, others):
                            unique.setdefault(other["slug"], other)
                        candidates = list(unique.values())
                    else:
                        pool = list(candidates)
                        for form in name_forms(text):
                            pool += await manager.plants.async_search(
                                form, limit=SEARCH_POOL
                            )
                        unique: dict[str, dict] = {}
                        for found in pool:
                            unique.setdefault(found["slug"], found)
                        candidates = rank_candidates(text, list(unique.values()))
                    self._choices[index] = candidates[:MAX_CHOICES]
                if self._choices:
                    return await self.async_step_match()
                return self._finish(manager, {})
        schema = vol.Schema(
            {
                vol.Required(CONF_AREA): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=a.subentry_id, label=a.title)
                            for a in areas
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_PLANTS): TextSelector(
                    TextSelectorConfig(multiline=True)
                ),
                vol.Required(CONF_WHEN, default=WHEN_ESTABLISHED): SelectSelector(
                    SelectSelectorConfig(
                        options=[WHEN_ESTABLISHED, WHEN_THIS_SEASON, WHEN_DATE],
                        translation_key="planted_when",
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional(CONF_PLANTED_ON): DateSelector(),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    def _key(self, index: int) -> str:
        """The field for one line, labelled with what was typed."""
        quantity, text = self._lines[index]
        return (
            f"{index + 1}. {quantity} {text}"
            if quantity > 1
            else f"{index + 1}. {text}"
        )

    async def async_step_match(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the plant meant where a line matched several, or none."""
        manager = self._manager()
        if manager is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            return self._finish(
                manager,
                {index: user_input[self._key(index)] for index in self._choices},
            )
        fields: dict[Any, Any] = {}
        for index, candidates in self._choices.items():
            text = self._lines[index][1]
            options = [
                SelectOptionDict(value=c["slug"], label=_choice_label(c))
                for c in candidates
            ]
            options += [
                SelectOptionDict(
                    value=KEEP, label=f'None of these: keep "{text}" as typed'
                ),
                SelectOptionDict(value=SKIP, label="Skip this one"),
            ]
            default = candidates[0]["slug"] if candidates else KEEP
            fields[vol.Required(self._key(index), default=default)] = SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
            )
        return self.async_show_form(step_id="match", data_schema=vol.Schema(fields))

    def _finish(
        self, manager: GardenManager, picked: dict[int, str]
    ) -> SubentryFlowResult:
        when = self._when[CONF_WHEN]
        today = manager.today
        planted_on = (
            date.fromisoformat(self._when[CONF_PLANTED_ON])
            if when == WHEN_DATE
            else None
        )
        candidates = {
            c["slug"]: c for options in self._choices.values() for c in options
        }
        added = []
        for index, (quantity, text) in enumerate(self._lines):
            choice = picked.get(index)
            if choice == SKIP:
                continue
            record = self._found.get(index) or candidates.get(choice or "")
            annual = record is not None and record["season"] != "perennial"
            planting = manager.add_planting_record(
                self._area,
                record,
                name=None if record else text,
                quantity=quantity,
                planted_on=planted_on
                or (today if when == WHEN_THIS_SEASON or annual else None),
                established=when == WHEN_ESTABLISHED and not annual,
                save=False,
            )
            added.append(planting)
        if not added:
            return self.async_abort(reason="nothing_added")
        manager.plantings_added()
        area = next(
            a.title for a in manager.area_subentries if a.subentry_id == self._area
        )
        names = ", ".join(
            f"{p.quantity} {p.name}" if p.quantity > 1 else p.name for p in added
        )
        return self.async_abort(
            reason="plantings_added",
            description_placeholders={"area": area, "plants": names},
        )
