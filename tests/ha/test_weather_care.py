"""A weather forecast holding off watering and warning of frost and heat."""

from homeassistant.core import SupportsResponse
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.master_gardener.const import DOMAIN

WEATHER = "weather.home"
MOISTURE = "sensor.bed_moisture"
# Noon UTC is early morning in the test time zone, so the local date matches.
APRIL_20 = "2027-04-20 12:00:00+00:00"
JUNE_10 = "2027-06-10 12:00:00+00:00"
DAILY, HOURLY, TWICE_DAILY = 1, 2, 4


class FakeWeather:
    """A weather entity: a state, and the action that returns its forecast."""

    def __init__(self, hass, features=DAILY, temperature_unit="°C"):
        self.hass = hass
        self.forecast = []
        self.asked = []
        self.attributes = {
            "supported_features": features,
            "temperature_unit": temperature_unit,
            "precipitation_unit": "mm",
        }

        async def get_forecasts(call):
            self.asked.append(call.data["type"])
            return {WEATHER: {"forecast": self.forecast}}

        hass.services.async_register(
            "weather",
            "get_forecasts",
            get_forecasts,
            supports_response=SupportsResponse.ONLY,
        )
        self.set_state("sunny")

    def set_state(self, state):
        self.hass.states.async_set(WEATHER, state, self.attributes)


def _at(day, **values):
    """A forecast entry for a day of the month under test."""
    return {"datetime": f"{day}T12:00:00+00:00", **values}


def _entry(hass, weather=WEATHER, **area):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Master Gardener",
        data={"last_frost": "04-15", "first_frost": "10-25"},
        options={"weather_entity": weather} if weather else {},
        subentries_data=[
            {
                "subentry_id": "veg",
                "subentry_type": "area",
                "title": "Vegetable garden",
                "unique_id": None,
                "data": {
                    "name": "Vegetable garden",
                    "style": "vegetable",
                    "light": "full_sun",
                    **area,
                },
            }
        ],
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    # The forecast and the plant catalogue are fetched in background tasks.
    await hass.async_block_till_done(wait_background_tasks=True)


async def _refetch(hass, entry):
    await entry.runtime_data.async_update_forecast()
    await hass.async_block_till_done()


def _entity_id(hass, entry, platform, key):
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_veg_{key}"
    )


async def _call(hass, action, data=None):
    return await hass.services.async_call(
        DOMAIN, action, data or {}, blocking=True, return_response=True
    )


async def _plant(hass, plant, planted_on):
    await _call(
        hass,
        "add_planting",
        {"area": "Vegetable garden", "plant": plant, "planted_on": planted_on},
    )


async def _todo(hass, entity_id):
    result = await hass.services.async_call(
        "todo",
        "get_items",
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )
    return {item["summary"]: item for item in result[entity_id]["items"]}


async def test_no_weather_entity_no_weather_sensors(hass):
    entry = _entry(hass, weather=None)
    await _setup(hass, entry)
    assert _entity_id(hass, entry, "binary_sensor", "frost_risk") is None
    assert _entity_id(hass, entry, "binary_sensor", "heat_risk") is None
    assert "alerts" not in (await _call(hass, "get_garden"))["areas"][0]


async def test_rain_holds_off_watering(hass, freezer):
    freezer.move_to(JUNE_10)
    weather = FakeWeather(hass)
    weather.forecast = [
        _at("2027-06-10", temperature=24, templow=14, precipitation=2),
        _at("2027-06-11", temperature=22, templow=13, precipitation=6),
    ]
    hass.states.async_set(MOISTURE, "22", {"unit_of_measurement": "%"})
    entry = _entry(hass, moisture_sensors=[MOISTURE])
    await _setup(hass, entry)
    assert weather.asked == ["daily"]
    water = _entity_id(hass, entry, "binary_sensor", "needs_water")
    todo = _entity_id(hass, entry, "todo", "todo")

    state = hass.states.get(water)
    assert state.state == "off"
    assert state.attributes["reason"] == (
        "Soil moisture 22% is below 30%, but 8 mm of rain is forecast by tomorrow"
    )
    assert "Water Vegetable garden" not in await _todo(hass, todo)

    # Too dry to gamble on a forecast.
    hass.states.async_set(MOISTURE, "12", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    assert hass.states.get(water).state == "on"
    assert "Water Vegetable garden" in await _todo(hass, todo)

    # The rain is no longer forecast: back to what the soil says.
    hass.states.async_set(MOISTURE, "22", {"unit_of_measurement": "%"})
    weather.forecast = [_at("2027-06-10", temperature=24, templow=14)]
    await _refetch(hass, entry)
    state = hass.states.get(water)
    assert state.state == "on"
    assert state.attributes["reason"] == "Soil moisture 22% is below 30%"


async def test_frost_warning_names_the_tender_plantings(hass, freezer):
    freezer.move_to(APRIL_20)
    weather = FakeWeather(hass)
    weather.forecast = [
        _at("2027-04-20", temperature=14, templow=6),
        _at("2027-04-21", temperature=11, templow=1),
    ]
    entry = _entry(hass)
    await _setup(hass, entry)
    frost = _entity_id(hass, entry, "binary_sensor", "frost_risk")
    todo = _entity_id(hass, entry, "todo", "todo")
    assert hass.states.get(frost).state == "off"  # nothing planted yet

    await _plant(hass, "tomato", "2027-04-18")
    await _plant(hass, "carrot", "2027-04-01")
    await hass.async_block_till_done()
    reason = "Lows down to 1°C forecast, starting tomorrow. Cover or bring in: Tomato"
    state = hass.states.get(frost)
    assert state.state == "on"
    assert state.attributes["reason"] == reason
    assert state.attributes["date"] == "2027-04-21"
    assert state.attributes["plants"] == ["Tomato"]
    item = (await _todo(hass, todo))["Protect Vegetable garden from frost"]
    assert item["description"] == reason
    assert item["due"] == "2027-04-21"

    area = (await _call(hass, "get_garden"))["areas"][0]
    assert area["alerts"] == [
        {"kind": "frost", "date": "2027-04-21", "reason": reason, "plants": ["Tomato"]}
    ]

    # Done: the task goes, the sensor keeps reporting the forecast.
    await _call(hass, "complete_task", {"task_id": item["uid"]})
    await hass.async_block_till_done()
    assert "Protect Vegetable garden from frost" not in await _todo(hass, todo)
    assert hass.states.get(frost).state == "on"

    # The cold snap leaves the forecast.
    weather.forecast = [_at("2027-04-20", temperature=14, templow=6)]
    await _refetch(hass, entry)
    state = hass.states.get(frost)
    assert state.state == "off"
    assert "reason" not in state.attributes


async def test_heat_warning_for_cool_season_crops(hass, freezer):
    freezer.move_to(JUNE_10)
    weather = FakeWeather(hass)
    weather.forecast = [_at("2027-06-10", temperature=33, templow=19)]
    entry = _entry(hass)
    await _setup(hass, entry)
    await _plant(hass, "pea", "2027-03-20")
    await hass.async_block_till_done()
    state = hass.states.get(_entity_id(hass, entry, "binary_sensor", "heat_risk"))
    assert state.state == "on"
    assert state.attributes["reason"] == (
        "Highs up to 33°C forecast, starting today. Water early and shade: Pea"
    )
    todo = _entity_id(hass, entry, "todo", "todo")
    assert "Shield Vegetable garden from heat" in await _todo(hass, todo)


async def test_day_and_night_forecast_in_fahrenheit(hass, freezer):
    """The shape of a US National Weather Service forecast."""
    freezer.move_to(APRIL_20)
    hass.config.units = US_CUSTOMARY_SYSTEM
    weather = FakeWeather(hass, features=TWICE_DAILY | HOURLY, temperature_unit="°F")
    weather.forecast = [
        _at("2027-04-20", temperature=58, is_daytime=True),
        _at("2027-04-20", temperature=30, is_daytime=False),
        {"temperature": 20},  # no time: ignored
    ]
    entry = _entry(hass)
    await _setup(hass, entry)
    assert weather.asked == ["twice_daily"]
    await _plant(hass, "tomato", "2027-04-18")
    await hass.async_block_till_done()
    state = hass.states.get(_entity_id(hass, entry, "binary_sensor", "frost_risk"))
    assert state.state == "on"
    assert state.attributes["reason"] == (
        "Lows down to 30°F forecast, starting today. Cover or bring in: Tomato"
    )


async def test_forecast_arrives_late_and_goes_stale(hass, freezer):
    freezer.move_to(APRIL_20)
    weather = FakeWeather(hass)
    weather.set_state("unavailable")
    weather.forecast = [_at("2027-04-20", temperature=14, templow=6)]
    entry = _entry(hass)
    await _setup(hass, entry)
    frost = _entity_id(hass, entry, "binary_sensor", "frost_risk")
    assert hass.states.get(frost).state == "unknown"
    assert weather.asked == []

    # The weather integration finishes starting: no need to wait an hour.
    weather.set_state("cloudy")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(frost).state == "off"
    assert weather.asked == ["daily"]

    # A short outage keeps the last forecast; a long one does not.
    weather.set_state("unavailable")
    await _refetch(hass, entry)
    assert hass.states.get(frost).state == "off"
    freezer.move_to("2027-04-20 19:00:00+00:00")
    await _refetch(hass, entry)
    assert hass.states.get(frost).state == "unknown"


async def test_weather_entity_without_a_usable_forecast(hass, freezer):
    freezer.move_to(APRIL_20)
    FakeWeather(hass, features=0)
    entry = _entry(hass)
    await _setup(hass, entry)
    frost = _entity_id(hass, entry, "binary_sensor", "frost_risk")
    assert hass.states.get(frost).state == "unknown"

    # The forecast action itself failing is no different.
    hass.services.async_remove("weather", "get_forecasts")
    hass.states.async_set(WEATHER, "sunny", {"supported_features": DAILY})
    await _refetch(hass, entry)
    assert hass.states.get(frost).state == "unknown"


async def test_options_flow_sets_and_clears_the_weather_entity(hass):
    FakeWeather(hass)
    entry = _entry(hass, weather=None)
    await _setup(hass, entry)
    dates = {"last_frost": "2026-04-15", "first_frost": "2026-10-25"}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**dates, "weather_entity": WEATHER}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.weather_entity == WEATHER
    assert _entity_id(hass, entry, "binary_sensor", "frost_risk")

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], dates)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.weather_entity is None
