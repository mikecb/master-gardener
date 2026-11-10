"""The build plan: planned purchases spread over years under a budget."""

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.master_gardener.const import DOMAIN

FEB_20 = "2027-02-20 12:00:00+00:00"
MAR_10 = "2027-03-10 12:00:00+00:00"


@pytest.fixture
def garden(hass):
    """A vegetable garden and a border, with a 500 a year budget."""
    hass.config.currency = "USD"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Master Gardener",
        data={"last_frost": "04-15", "first_frost": "10-25"},
        options={"annual_budget": 500},
        subentries_data=[
            {
                "subentry_id": area,
                "subentry_type": "area",
                "title": title,
                "unique_id": None,
                "data": {"name": title, "style": style, "light": "full_sun"},
            }
            for area, title, style in (
                ("veg", "Vegetable garden", "vegetable"),
                ("border", "Front border", "cottage"),
            )
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


def _entity_id(hass, entry, platform, key, area=None):
    unique = f"{entry.entry_id}_{area}_{key}" if area else f"{entry.entry_id}_{key}"
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique)
    assert entity_id, f"missing {platform} {unique}"
    return entity_id


async def _todo(hass, entity_id):
    result = await hass.services.async_call(
        "todo",
        "get_items",
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )
    return {i["summary"]: i for i in result[entity_id]["items"]}


async def _wishlist(hass):
    """An oak, a path, hostas and zinnias: more than one year's budget."""
    planned = {"status": "planned"}
    oak = await _call(
        hass,
        "add_planting",
        {
            "area": "Front border",
            "plant": "white-oak",
            "estimated_cost": 400,
            **planned,
        },
    )
    path = await _call(
        hass,
        "add_project",
        {
            "area": "Front border",
            "name": "Lay the flagstone path",
            "estimated_cost": 300,
        },
    )
    hosta = await _call(
        hass,
        "add_planting",
        {
            "area": "Front border",
            "plant": "hosta",
            "quantity": 3,
            "estimated_cost": 60,
            **planned,
        },
    )
    zinnia = await _call(
        hass,
        "add_planting",
        {
            "area": "Vegetable garden",
            "plant": "zinnia",
            "estimated_cost": 20,
            **planned,
        },
    )
    return {"oak": oak, "path": path, "hosta": hosta, "zinnia": zinnia}


def _years(plan):
    return {
        i["name"]: (y["year"], i["waits"]) for y in plan["years"] for i in y["items"]
    }


async def test_plan_spreads_the_wishlist_over_years(hass, garden, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, garden)
    items = await _wishlist(hass)
    assert items["oak"]["status"] == "planned"
    assert items["path"]["tier"] == "hardscape"

    plan = await _call(hass, "get_plan")
    assert plan["currency"] == "USD"
    assert plan["annual_budget"] == 500
    assert _years(plan) == {
        "White Oak": (2027, None),
        "Zinnia": (2027, None),
        "Lay the flagstone path": (2028, "No room in the budget before 2028"),
        "Hosta": (2028, "After Lay the flagstone path"),
    }
    this_year, next_year = plan["years"]
    assert (this_year["planned"], this_year["remaining"]) == (420, 80)
    assert next_year["remaining"] == 140
    tiers = {i["name"]: i["tier"] for y in plan["years"] for i in y["items"]}
    assert tiers["White Oak"] == "trees_shrubs"
    assert tiers["Zinnia"] == "small_plants"

    budget = hass.states.get(_entity_id(hass, garden, "sensor", "budget_remaining"))
    assert float(budget.state) == 80
    assert budget.attributes["unit_of_measurement"] == "USD"
    assert budget.attributes["planned"] == 420
    planned = hass.states.get(_entity_id(hass, garden, "sensor", "planned_this_year"))
    assert planned.state == "2"
    assert [i["name"] for i in planned.attributes["items"]] == ["White Oak", "Zinnia"]

    garden_view = await _call(hass, "get_garden")
    assert garden_view["budget"]["remaining_this_year"] == 80
    assert garden_view["budget"]["planned_items"] == 4
    assert garden_view["areas"][1]["plantings"]["planned"] == 2

    # Planned plantings get no gardening tasks, only this year's purchase.
    tasks = await _call(hass, "list_tasks", {"show": "all", "days": 365})
    titles = {t["title"]: t for t in tasks["tasks"]}
    assert "Plant White Oak" not in titles
    assert "Buy Hosta" not in titles  # next year
    oak = titles["Buy White Oak"]
    assert (oak["kind"], oak["start"], oak["end"]) == (
        "buy",
        "2027-08-02",
        "2027-09-12",
    )
    assert oak["detail"] == "In this year's build plan: White Oak, about 400."


async def test_buying_from_the_to_do_list(hass, garden, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, garden)
    await _wishlist(hass)
    freezer.move_to(MAR_10)
    todo = _entity_id(hass, garden, "todo", "todo", "veg")

    items = await _todo(hass, todo)
    buy = items["Buy Zinnia"]
    assert buy["status"] == "needs_action"
    assert buy["due"] == "2027-04-14"

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": todo, "item": buy["uid"], "status": "completed"},
        blocking=True,
    )
    zinnia = (await _call(hass, "list_plantings", {"area": "Vegetable garden"}))[
        "plantings"
    ][0]
    assert zinnia["status"] == "active"
    assert (zinnia["bought_on"], zinnia["cost"]) == ("2027-03-10", 20)
    assert (await _todo(hass, todo))["Buy Zinnia"]["status"] == "completed"
    upcoming = await _call(hass, "list_tasks", {"show": "upcoming", "days": 60})
    assert "Sow Zinnia outdoors" in [t["title"] for t in upcoming["tasks"]]

    plan = await _call(hass, "get_plan")
    assert (plan["spent_this_year"], plan["planned_this_year"]) == (20, 400)
    assert plan["remaining_this_year"] == 80

    # Unticking puts it back in the plan.
    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": todo, "item": buy["uid"], "status": "needs_action"},
        blocking=True,
    )
    zinnia = garden.runtime_data.plantings[zinnia["id"]]
    assert (zinnia.status, zinnia.bought_on, zinnia.cost) == ("planned", None, None)
    assert (await _call(hass, "get_plan"))["spent_this_year"] == 0


async def test_projects_and_purchases_count_against_the_budget(hass, garden, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, garden)
    items = await _wishlist(hass)

    # The oak cost less than estimated, and then died: the money is still spent.
    oak = await _call(
        hass,
        "set_planting_status",
        {"planting_id": items["oak"]["id"], "status": "active", "cost": 350},
    )
    assert (oak["bought_on"], oak["cost"]) == ("2027-02-20", 350)
    await _call(
        hass,
        "set_planting_status",
        {"planting_id": items["oak"]["id"], "status": "dead", "notes": "Drought"},
    )
    plan = await _call(hass, "get_plan")
    assert plan["spent_this_year"] == 350

    # A project done for more than planned, then its cost corrected.
    path = items["path"]["id"]
    done = await _call(
        hass, "set_project_status", {"project_id": path, "status": "done"}
    )
    assert (done["done_on"], done["cost"]) == ("2027-02-20", 300)
    done = await _call(
        hass, "set_project_status", {"project_id": path, "status": "done", "cost": 320}
    )
    assert done["cost"] == 320
    plan = await _call(hass, "get_plan")
    assert plan["spent_this_year"] == 670
    # Over budget already: what is left waits for next year.
    assert _years(plan) == {
        "Zinnia": (2028, "No room in the budget before 2028"),
        "Hosta": (2028, "No room in the budget before 2028"),
    }
    assert plan["remaining_this_year"] == -170
    today = garden.runtime_data.today
    finished = {
        t.title: t.state(today)
        for t in garden.runtime_data.tasks("border")
        if t.kind == "project"
    }
    assert finished == {"Lay the flagstone path": "done"}

    removed = await _call(
        hass, "set_project_status", {"project_id": path, "status": "removed"}
    )
    assert (removed["done_on"], removed["cost"]) == (None, None)
    with pytest.raises(ServiceValidationError):
        await _call(
            hass, "set_project_status", {"project_id": "nope", "status": "done"}
        )


async def test_update_plan_item(hass, garden, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, garden)
    items = await _wishlist(hass)
    path = items["path"]["id"]

    await _call(hass, "update_plan_item", {"item_id": path, "not_before": 2030})
    years = _years(await _call(hass, "get_plan"))
    assert years["Lay the flagstone path"] == (2030, "Not before 2030")
    assert years["Hosta"] == (2030, "After Lay the flagstone path")

    # A project re-tiered after the perennials no longer holds them up.
    await _call(hass, "update_plan_item", {"item_id": path, "tier": "small_plants"})
    assert _years(await _call(hass, "get_plan"))["Hosta"] == (2027, None)

    await _call(
        hass,
        "update_plan_item",
        {"item_id": path, "not_before": 0, "estimated_cost": 10, "priority": "high"},
    )
    project = garden.runtime_data.projects[path]
    assert (project.not_before, project.estimated_cost, project.priority) == (
        None,
        10,
        "high",
    )
    hosta = await _call(
        hass, "update_plan_item", {"item_id": items["hosta"]["id"], "priority": "low"}
    )
    assert hosta["priority"] == "low"
    with pytest.raises(ServiceValidationError):
        await _call(hass, "update_plan_item", {"item_id": "nope", "priority": "low"})


async def test_proposal_accepted_into_the_plan(hass, garden, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, garden)
    proposal = await _call(
        hass,
        "add_planting",
        {
            "area": "Front border",
            "plant": "knock-out-rose",
            "status": "proposed",
            "estimated_cost": 45,
        },
    )
    plan = await _call(hass, "get_plan")
    assert plan["undecided_proposals"] == {"count": 1, "estimated_cost": 45}
    assert plan["years"][0]["items"] == []

    await _call(
        hass,
        "set_planting_status",
        {"planting_id": proposal["id"], "status": "planned"},
    )
    plan = await _call(hass, "get_plan")
    assert plan["undecided_proposals"]["count"] == 0
    assert _years(plan) == {"Knock Out Rose": (2027, None)}


async def test_projects_survive_a_reload(hass, garden, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, garden)
    await _wishlist(hass)
    assert await hass.config_entries.async_reload(garden.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    plan = await _call(hass, "get_plan")
    assert "Lay the flagstone path" in _years(plan)
    assert len(garden.runtime_data.plan_items()) == 4


async def test_options_flow_sets_and_clears_the_budget(hass, garden, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, garden)
    await _wishlist(hass)
    dates = {"last_frost": "2027-04-15", "first_frost": "2027-10-25"}

    result = await hass.config_entries.options.async_init(garden.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**dates, "annual_budget": 2000}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert garden.runtime_data.annual_budget == 2000

    result = await hass.config_entries.options.async_init(garden.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], dates)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert garden.runtime_data.annual_budget is None

    # No budget: the whole plan is this year.
    plan = await _call(hass, "get_plan")
    assert len(plan["years"]) == 1
    assert plan["remaining_this_year"] is None
    budget = hass.states.get(_entity_id(hass, garden, "sensor", "budget_remaining"))
    assert budget.state == "unknown"


async def test_deleting_a_purchase_drops_it_from_the_plan(hass, garden, freezer):
    freezer.move_to("2027-03-20 12:00:00+00:00")
    await _setup(hass, garden)
    items = await _wishlist(hass)
    await _call(
        hass,
        "update_plan_item",
        {"item_id": items["path"]["id"], "tier": "small_plants", "estimated_cost": 10},
    )
    border = _entity_id(hass, garden, "todo", "todo", "border")
    todo = await _todo(hass, border)
    await hass.services.async_call(
        "todo",
        "remove_item",
        {"entity_id": border, "item": todo["Lay the flagstone path"]["uid"]},
        blocking=True,
    )
    project = garden.runtime_data.projects[items["path"]["id"]]
    assert project.status == "removed"
    assert "Taken off the build plan" in project.notes
    assert "Lay the flagstone path" not in await _todo(hass, border)
    assert (await _call(hass, "get_plan"))["spent_this_year"] == 0

    zinnia = garden.runtime_data.plantings[items["zinnia"]["id"]]
    garden.runtime_data.delete(f"plan:{zinnia.id}:2027")
    assert zinnia.status == "removed"
    garden.runtime_data.delete(f"plan:{zinnia.id}:2027")  # already gone: no-op
    assert zinnia.status == "removed"
