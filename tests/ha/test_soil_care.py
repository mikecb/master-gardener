"""Soil sensors driving water and fertilizer advice, end to end."""

from datetime import timedelta

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.master_gardener.const import DOMAIN

MOISTURE = "sensor.bed_moisture"
MOISTURE_2 = "sensor.bed_moisture_2"
FERTILITY = "sensor.bed_fertility"
JUNE_10 = "2027-06-10 12:00:00+00:00"


def _entry(hass, **area):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Master Gardener",
        data={"last_frost": "04-15", "first_frost": "10-25"},
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
    # The plant catalogue downloads in a background task.
    await hass.async_block_till_done(wait_background_tasks=True)


async def _set(hass, entity_id, value, unit="%"):
    hass.states.async_set(entity_id, value, {"unit_of_measurement": unit})
    await hass.async_block_till_done()


def _entity_id(hass, entry, platform, key):
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_veg_{key}"
    )


async def _todo(hass, entity_id):
    result = await hass.services.async_call(
        "todo",
        "get_items",
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )
    return result[entity_id]["items"]


async def test_no_sensors_no_care_entities(hass):
    entry = _entry(hass)
    await _setup(hass, entry)
    assert _entity_id(hass, entry, "binary_sensor", "needs_water") is None
    assert _entity_id(hass, entry, "binary_sensor", "needs_fertilizer") is None


async def test_water_advice_follows_the_driest_sensor(hass, freezer):
    freezer.move_to(JUNE_10)
    await _set(hass, MOISTURE, "45")
    await _set(hass, MOISTURE_2, "unavailable")
    entry = _entry(hass, moisture_sensors=[MOISTURE, MOISTURE_2])
    await _setup(hass, entry)
    water = _entity_id(hass, entry, "binary_sensor", "needs_water")
    todo = _entity_id(hass, entry, "todo", "todo")
    due = _entity_id(hass, entry, "sensor", "tasks_due")
    assert hass.states.get(water).state == "off"

    # The second sensor comes back dry: default threshold for vegetables is 30%.
    await _set(hass, MOISTURE_2, "18")
    state = hass.states.get(water)
    assert state.state == "on"
    assert state.attributes["reason"] == "Soil moisture 18% is below 30%"
    assert state.attributes["reading"] == 18
    item = next(i for i in await _todo(hass, todo) if "Water" in i["summary"])
    assert item["summary"] == "Water Vegetable garden"
    assert item["description"] == "Soil moisture 18% is below 30%"
    due_before = int(hass.states.get(due).state)

    # Ticking it off hides the task for 12 hours while the soil catches up...
    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": todo, "item": item["uid"], "status": "completed"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert not [i for i in await _todo(hass, todo) if "Water" in i["summary"]]
    assert int(hass.states.get(due).state) == due_before - 1
    assert hass.states.get(water).state == "on"  # the soil is still dry

    # ...and it comes back if the soil is still dry after that.
    freezer.move_to(dt_util.utcnow() + timedelta(hours=13))
    await _set(hass, MOISTURE_2, "19")
    assert [i for i in await _todo(hass, todo) if "Water" in i["summary"]]

    # It must recover past the threshold, not just touch it.
    await _set(hass, MOISTURE_2, "32")
    assert hass.states.get(water).state == "on"
    await _set(hass, MOISTURE_2, "40")
    assert hass.states.get(water).state == "off"
    assert not [i for i in await _todo(hass, todo) if "Water" in i["summary"]]


async def test_unknown_without_a_reading(hass):
    entry = _entry(hass, moisture_sensors=[MOISTURE])
    await _setup(hass, entry)
    water = _entity_id(hass, entry, "binary_sensor", "needs_water")
    assert hass.states.get(water).state == "unknown"


async def test_fertilizer_advice(hass, freezer):
    freezer.move_to(JUNE_10)
    await _set(hass, MOISTURE, "40")
    await _set(hass, FERTILITY, "0.2", "mS/cm")  # 200 µS/cm
    entry = _entry(
        hass,
        moisture_sensors=[MOISTURE],
        conductivity_sensors=[FERTILITY],
        dry_below=25,
        feed_below=300,
    )
    await _setup(hass, entry)
    feed = _entity_id(hass, entry, "binary_sensor", "needs_fertilizer")
    todo = _entity_id(hass, entry, "todo", "todo")
    state = hass.states.get(feed)
    assert state.state == "on"
    assert state.attributes["reading"] == 200
    assert state.attributes["threshold"] == 300
    assert [i for i in await _todo(hass, todo) if "Fertilize" in i["summary"]]

    # Dry soil reads low on conductivity: water first, judge fertility later.
    await _set(hass, MOISTURE, "20")
    state = hass.states.get(feed)
    assert state.state == "off"
    assert "water first" in state.attributes["reason"]
    summaries = [i["summary"] for i in await _todo(hass, todo)]
    assert "Water Vegetable garden" in summaries
    assert "Fertilize Vegetable garden" not in summaries

    # Late in the year nothing is fed, whatever the reading.
    await _set(hass, MOISTURE, "40")
    assert hass.states.get(feed).state == "on"
    freezer.move_to("2027-10-01 12:00:00+00:00")
    entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(feed)
    assert state.state == "off"
    assert state.attributes["reason"] == "Outside the feeding season"


async def test_never_fed_area_gets_no_fertilizer_entity(hass):
    entry = _entry(hass, conductivity_sensors=[FERTILITY], feed_below=0)
    await _setup(hass, entry)
    assert _entity_id(hass, entry, "binary_sensor", "needs_fertilizer") is None


async def test_snooze_survives_reload(hass, freezer):
    freezer.move_to(JUNE_10)
    await _set(hass, MOISTURE, "10")
    entry = _entry(hass, moisture_sensors=[MOISTURE])
    await _setup(hass, entry)
    todo = _entity_id(hass, entry, "todo", "todo")
    item = next(i for i in await _todo(hass, todo) if "Water" in i["summary"])
    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": todo, "item": item["uid"], "status": "completed"},
        blocking=True,
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert not [i for i in await _todo(hass, todo) if "Water" in i["summary"]]


async def test_area_form_accepts_sensors_and_thresholds(hass):
    entry = _entry(hass)
    await _setup(hass, entry)
    result = await entry.start_subentry_reconfigure_flow(hass, "veg")
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Vegetable garden",
            "style": "vegetable",
            "light": "full_sun",
            "moisture_sensors": [MOISTURE],
            "dry_below": 35,
            "conductivity_sensors": [FERTILITY],
            "feed_below": 400,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    data = entry.subentries["veg"].data
    assert data["moisture_sensors"] == [MOISTURE]
    assert data["dry_below"] == 35
    # The entry reloaded and the care entities now exist.
    assert _entity_id(hass, entry, "binary_sensor", "needs_water")
    assert _entity_id(hass, entry, "binary_sensor", "needs_fertilizer")
