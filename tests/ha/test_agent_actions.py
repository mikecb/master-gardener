"""The actions an AI agent uses to read the garden and act on it."""

import re
from pathlib import Path

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.master_gardener.const import DOMAIN

FEB_20 = "2027-02-20 12:00:00+00:00"
ROOT = Path(__file__).parents[2]


@pytest.fixture
def garden(hass):
    """Two areas that know about each other; one has a soil sensor."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Master Gardener",
        data={
            "zip_code": "22901",
            "zone": "7b",
            "last_frost": "04-15",
            "first_frost": "10-25",
        },
        subentries_data=[
            {
                "subentry_id": "veg",
                "subentry_type": "area",
                "title": "Vegetable Rows",
                "unique_id": None,
                "data": {
                    "name": "Vegetable Rows",
                    "style": "vegetable",
                    "light": "full_sun",
                    "size_sqft": 800,
                    "adjacent": ["walk"],
                    "moisture_sensors": ["sensor.rows_moisture"],
                },
            },
            {
                "subentry_id": "walk",
                "subentry_type": "area",
                "title": "Front Walk",
                "unique_id": None,
                "data": {
                    "name": "Front Walk",
                    "style": "cottage",
                    "light": "part_shade",
                    "character": "Loose and welcoming",
                    "adjacent": ["veg", "gone"],
                },
            },
        ],
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _call(hass, action, data=None):
    return await hass.services.async_call(
        DOMAIN, action, data or {}, blocking=True, return_response=True
    )


async def test_get_garden(hass, garden, freezer):
    freezer.move_to(FEB_20)
    hass.states.async_set("sensor.rows_moisture", "12", {"unit_of_measurement": "%"})
    await _setup(hass, garden)
    await _call(hass, "add_planting", {"area": "Vegetable Rows", "plant": "tomato"})
    result = await _call(hass, "get_garden")

    assert result["today"] == "2027-02-20"
    assert result["site"] == {
        "zip_code": "22901",
        "hardiness_zone": "7b",
        "last_spring_frost": "04-15",
        "first_autumn_frost": "10-25",
    }
    assert result["plant_catalogue"]["ready"] is True
    assert result["care_profiles"]["rose"] == "Rose"

    rows, walk = result["areas"]
    assert (rows["id"], rows["name"], rows["style"]) == (
        "veg",
        "Vegetable Rows",
        "vegetable",
    )
    assert rows["size_sqft"] == 800
    assert rows["next_to"] == ["Front Walk"]
    assert rows["plantings"] == {"active": 1, "proposed": 0, "dead": 0, "removed": 0}
    assert rows["tasks_due"] == 2  # start tomatoes, and water
    assert rows["soil"]["water"]["needed"] is True
    assert rows["soil"]["water"]["reason"] == "Soil moisture 12% is below 30%"
    assert "fertilizer" not in rows["soil"]  # no conductivity sensor

    assert walk["character"] == "Loose and welcoming"
    assert walk["next_to"] == ["Vegetable Rows"]  # a deleted neighbour is dropped
    assert "soil" not in walk


async def test_areas_are_addressed_by_name(hass, garden):
    await _setup(hass, garden)
    for spelling in ("Front Walk", "front walk", "  FRONT   WALK ", "walk"):
        task = await _call(hass, "add_task", {"area": spelling, "title": "Sweep"})
        assert task["area_id"] == "walk"
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "add_task", {"area": "Rose garden", "title": "x"})
    assert "Vegetable Rows, Front Walk" in str(err.value)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "list_tasks", {"area": "Rose garden"})


async def test_list_and_complete_tasks(hass, garden, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, garden)
    tomato = await _call(
        hass, "add_planting", {"area": "Vegetable Rows", "plant": "tomato"}
    )
    custom = await _call(
        hass, "add_task", {"area": "Front Walk", "title": "Grass too long"}
    )

    due = (await _call(hass, "list_tasks"))["tasks"]
    assert {t["title"] for t in due} == {"Start Tomato indoors", "Grass too long"}
    start = next(t for t in due if t["kind"] == "sow_indoors")
    assert start["area"] == "Vegetable Rows"
    assert start["state"] == "due"
    assert (start["start"], start["end"]) == ("2027-02-18", "2027-03-03")
    assert start["planting_id"] == tomato["id"]
    assert "6-8 weeks" in start["detail"]

    only_walk = (await _call(hass, "list_tasks", {"area": "Front Walk"}))["tasks"]
    assert [t["id"] for t in only_walk] == [custom["task_id"]]

    upcoming = await _call(hass, "list_tasks", {"show": "upcoming", "days": 90})
    titles = [t["title"] for t in upcoming["tasks"]]
    assert "Plant Tomato outdoors" in titles
    assert "Start Tomato indoors" not in titles
    assert "Harvest Tomato" not in titles  # June is beyond 90 days
    assert all(t["state"] == "upcoming" for t in upcoming["tasks"])
    everything = await _call(hass, "list_tasks", {"show": "all", "days": 200})
    assert {"Start Tomato indoors", "Harvest Tomato"} <= {
        t["title"] for t in everything["tasks"]
    }

    assert await _call(hass, "complete_task", {"task_id": start["id"]}) == {
        "completed": start["id"]
    }
    await _call(hass, "complete_task", {"task_id": custom["task_id"]})
    assert (await _call(hass, "list_tasks"))["tasks"] == []
    with pytest.raises(ServiceValidationError, match="No current task"):
        await _call(hass, "complete_task", {"task_id": "planting:nope:start:2027"})

    # Completing the planting-out task records when it went in the ground.
    freezer.move_to("2027-04-25 12:00:00+00:00")
    garden.runtime_data.async_refresh()
    plant_out = next(
        t
        for t in (await _call(hass, "list_tasks"))["tasks"]
        if t["kind"] == "transplant"
    )
    await _call(hass, "complete_task", {"task_id": plant_out["id"]})
    listed = (await _call(hass, "list_plantings", {"area": "Vegetable Rows"}))[
        "plantings"
    ]
    assert listed[0]["planted_on"] == "2027-04-25"
    assert listed[0]["area"] == "Vegetable Rows"


async def test_proposals_wait_for_a_decision(hass, garden, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, garden)
    proposal = await _call(
        hass,
        "add_planting",
        {
            "area": "Front Walk",
            "plant": "bloodgood-japanese-maple",
            "status": "proposed",
            "quantity": 2,
            "estimated_cost": 180,
            "notes": "Red foliage against the pale steps; stays under 20 ft.",
        },
    )
    assert proposal["status"] == "proposed"
    assert proposal["estimated_cost"] == 180
    assert proposal["profile"] == "japanese_maple"

    # Recorded, but nothing is scheduled for it yet.
    everything = {"area": "Front Walk", "show": "all", "days": 400}
    assert not [
        t
        for t in (await _call(hass, "list_tasks", everything))["tasks"]
        if t["planting_id"] == proposal["id"]
    ]
    proposed = await _call(hass, "list_plantings", {"status": "proposed"})
    assert [p["id"] for p in proposed["plantings"]] == [proposal["id"]]
    assert (await _call(hass, "list_plantings", {"status": "active"}))[
        "plantings"
    ] == []
    walk = (await _call(hass, "get_garden"))["areas"][1]
    assert walk["plantings"]["proposed"] == 1

    # Accepted a month later: its calendar starts from the day it was accepted.
    freezer.move_to("2027-03-20 12:00:00+00:00")
    garden.runtime_data.async_refresh()
    accepted = await _call(
        hass, "set_planting_status", {"planting_id": proposal["id"], "status": "active"}
    )
    assert (accepted["status"], accepted["added_on"]) == ("active", "2027-03-20")
    titles = {
        t["title"]
        for t in (await _call(hass, "list_tasks", everything))["tasks"]
        if t["planting_id"] == proposal["id"]
    }
    assert "Plant Bloodgood Japanese Maple" in titles
    assert dt_util.now().date().isoformat() == "2027-03-20"

    # Turned down instead: kept as history, with the reason.
    other = await _call(
        hass,
        "add_planting",
        {"area": "Front Walk", "plant": "knock-out-rose", "status": "proposed"},
    )
    rejected = await _call(
        hass,
        "set_planting_status",
        {
            "planting_id": other["id"],
            "status": "removed",
            "notes": "Too thorny by the steps",
        },
    )
    assert rejected["status"] == "removed"
    assert "Too thorny" in rejected["notes"]


async def test_search_by_what_a_spot_needs(hass, garden):
    await _setup(hass, garden)

    async def ids(**criteria):
        found = await _call(hass, "search_plants", {"limit": 50, **criteria})
        return {p["id"] for p in found["plants"]}

    assert await ids(sun="shade") == {
        "hosta",
        "enemion-biternatum",
        "false-rue-anemone-2",
    }
    assert await ids(plant_type="tree", max_height_in=300) == {
        "bloodgood-japanese-maple",
        "coral-bark-japanese-maple",
        "montmorency-cherry",
    }
    assert await ids(plant_type="deciduous tree") == {"coral-bark-japanese-maple"}
    assert await ids(category="vegetable", water="moderate", max_height_in=24) == {
        "carrot",
        "garlic",
    }
    assert await ids(query="maple", min_height_in=180, max_height_in=250) == {
        "bloodgood-japanese-maple"
    }
    # Filters still respect the property's zone (7).
    assert await ids(water="low") == set()
    assert await ids(water="low", all_zones=True) == {"desert-rose"}

    maple = (await _call(hass, "search_plants", {"query": "bloodgood"}))["plants"][0]
    assert maple["spread_in"] == [180, 240]
    assert maple["season"] == "perennial"
    assert maple["planting"] == "Plant in fall while the soil is still warm."

    with pytest.raises(ServiceValidationError, match="at least one filter"):
        await _call(hass, "search_plants", {"query": "  "})


async def test_skill_only_names_actions_that_exist(hass, garden):
    await _setup(hass, garden)
    skill = (ROOT / "skills" / "master-gardener" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert skill.startswith("---\nname: master-gardener\ndescription: ")
    registered = set(hass.services.async_services()[DOMAIN])
    table = set(re.findall(r"^\| `(\w+)` \|", skill, flags=re.M))
    assert table and table <= registered
    # Every action is documented for the UI too.
    import yaml

    described = yaml.safe_load(
        (ROOT / "custom_components" / "master_gardener" / "services.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert set(described) == registered
