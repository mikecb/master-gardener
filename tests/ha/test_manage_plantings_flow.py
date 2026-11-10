"""The Manage plantings form: deaths, removals, purchases and edits."""

from datetime import date

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.master_gardener.const import DOMAIN
from custom_components.master_gardener.engine.models import Planting
from custom_components.master_gardener.manage_flow import planting_label

FEB_20 = "2027-02-20 12:00:00+00:00"


async def _setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _start(hass, entry):
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, "manage_plantings"), context={"source": SOURCE_USER}
    )


async def _next(hass, result, data=None):
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], data or {}
    )


async def _add(hass, area, plant, **extra):
    return await hass.services.async_call(
        DOMAIN,
        "add_planting",
        {"area": area, "plant": plant, **extra},
        blocking=True,
        return_response=True,
    )


async def _choose(hass, entry, planting_id, action, area=None):
    """Open the form and get to the step for one action."""
    result = await _start(hass, entry)
    if result["step_id"] == "user":
        result = await _next(hass, result, {"area": area})
    assert result["step_id"] == "pick"
    result = await _next(hass, result, {"planting": planting_id})
    assert result["type"] is FlowResultType.MENU
    return await _next(hass, result, {"next_step_id": action})


def test_planting_label():
    plant = Planting(id="a", area_id="x", name="Hosta", quantity=3)
    assert planting_label(plant) == "Hosta (3)"
    plant = Planting(
        id="b", area_id="x", name="Fig", status="planned", planted_on=date(2026, 5, 1)
    )
    assert planting_label(plant) == "Fig, planned, planted 2026-05-01"


async def test_record_a_death(hass, entry, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, entry)
    apple = await _add(hass, "veg", "apple", established=True)
    await _add(hass, "lawn", "hosta")

    result = await _start(hass, entry)
    assert result["step_id"] == "user"  # two areas have plantings
    result = await _next(hass, result, {"area": "veg"})
    assert result["step_id"] == "pick"
    assert result["description_placeholders"] == {"area": "Vegetable garden"}
    (field,) = result["data_schema"].schema
    labels = [o["label"] for o in result["data_schema"].schema[field].config["options"]]
    assert labels == ["Apple"]  # only this area's

    result = await _next(hass, result, {"planting": apple["id"]})
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["died", "removed", "edit"]
    result = await _next(hass, result, {"next_step_id": "died"})
    assert result["step_id"] == "died"
    result = await _next(hass, result, {"notes": "Fire blight"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "recorded_dead"
    assert result["description_placeholders"] == {"name": "Apple"}

    planting = entry.runtime_data.plantings[apple["id"]]
    assert planting.status == "dead"
    assert planting.notes == "Fire blight"
    assert len(entry.subentries) == 2  # nothing stored as a subentry

    # The dead apple is no longer offered; one area left, so no area step.
    result = await _start(hass, entry)
    assert result["step_id"] == "pick"


async def test_removed_dropped_and_declined(hass, entry):
    await _setup(hass, entry)
    hosta = await _add(hass, "veg", "hosta")
    oak = await _add(hass, "veg", "white-oak", status="planned")
    rose = await _add(hass, "veg", "knock-out-rose", status="proposed")

    for planting, action, reason in (
        (hosta, "removed", "recorded_removed"),
        (oak, "dropped", "recorded_dropped"),
        (rose, "declined", "recorded_declined"),
    ):
        result = await _choose(hass, entry, planting["id"], action)
        result = await _next(hass, result, {})
        assert result["reason"] == reason
        assert entry.runtime_data.plantings[planting["id"]].status == "removed"

    assert (await _start(hass, entry))["reason"] == "no_plantings"


async def test_buying_and_accepting(hass, entry, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, entry)
    oak = await _add(hass, "veg", "white-oak", status="planned", estimated_cost=400)
    rose = await _add(
        hass, "veg", "knock-out-rose", status="proposed", estimated_cost=45
    )
    fern = await _add(hass, "veg", "hosta", status="proposed")

    result = await _start(hass, entry)
    result = await _next(hass, result, {"planting": oak["id"]})
    assert result["menu_options"] == ["bought", "dropped", "edit"]
    result = await _next(hass, result, {"next_step_id": "bought"})
    assert result["step_id"] == "bought"
    result = await _next(hass, result, {"cost": 350})
    assert result["reason"] == "recorded_bought"
    planting = entry.runtime_data.plantings[oak["id"]]
    assert (planting.status, planting.cost, planting.bought_on) == (
        "active",
        350,
        date(2027, 2, 20),
    )

    result = await _choose(hass, entry, rose["id"], "accept_active")
    result = await _next(hass, result, {})  # keeps the estimate
    assert entry.runtime_data.plantings[rose["id"]].cost == 45

    result = await _start(hass, entry)
    result = await _next(hass, result, {"planting": fern["id"]})
    assert result["menu_options"] == [
        "accept_planned",
        "accept_active",
        "declined",
        "edit",
    ]
    result = await _next(hass, result, {"next_step_id": "accept_planned"})
    assert result["reason"] == "recorded_planned"
    assert entry.runtime_data.plantings[fern["id"]].status == "planned"


async def test_edit_details(hass, entry, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, entry)
    maple = await _add(hass, "veg", "bloodgood-japanese-maple", quantity=2)

    result = await _choose(hass, entry, maple["id"], "edit")
    assert result["step_id"] == "edit"
    fields = {str(k): k for k in result["data_schema"].schema}
    assert "estimated_cost" not in fields  # growing: it has been bought
    suggested = {
        name: (key.description or {}).get("suggested_value")
        for name, key in fields.items()
    }
    assert suggested["name"] == "Bloodgood Japanese Maple"
    assert suggested["quantity"] == 2
    assert suggested["plant"] == "bloodgood-japanese-maple"
    assert suggested["care_profile"] == "japanese_maple"
    assert suggested["when"] == "not_yet"

    form = {
        "name": "Coral bark maple by the gate",
        "quantity": 1,
        "plant": "coral-bark-japanese-maple",
        "when": "on_date",
        "planted_on": "2021-10-01",
        "notes": "From the nursery sale",
    }
    # A date is needed for "on a date", and the plant id must be one plant.
    no_date = {k: v for k, v in form.items() if k != "planted_on"}
    result = await _next(hass, result, no_date)
    assert result["errors"] == {"planted_on": "date_needed"}
    result = await _next(hass, result, {**form, "plant": "false rue anemone"})
    assert result["errors"] == {"plant": "plant_ambiguous"}
    assert "enemion-biternatum" in result["description_placeholders"]["suggestions"]
    result = await _next(hass, result, {**form, "plant": "unobtainium"})
    assert result["errors"] == {"plant": "plant_unknown"}

    result = await _next(hass, result, form)
    assert result["reason"] == "recorded_edit"
    assert result["description_placeholders"] == {
        "name": "Coral bark maple by the gate"
    }
    planting = entry.runtime_data.plantings[maple["id"]]
    assert planting.plant == "coral-bark-japanese-maple"
    assert planting.plant_data["common_name"] == "Coral Bark Japanese Maple"
    assert planting.profile == "japanese_maple"  # chosen again from the plant
    assert (planting.quantity, planting.planted_on, planting.established) == (
        1,
        date(2021, 10, 1),
        False,
    )
    assert planting.notes == "From the nursery sale"
    titles = {
        t.title
        for t in entry.runtime_data.tasks_between(date(2027, 1, 1), date(2027, 12, 31))
    }
    assert "Plant Coral bark maple by the gate" not in titles

    # Established, a care profile by hand, and off the catalogue.
    result = await _choose(hass, entry, maple["id"], "edit")
    result = await _next(
        hass,
        result,
        {
            "name": "Maple",
            "quantity": 1,
            "care_profile": "deciduous_tree",
            "when": "established",
        },
    )
    assert result["reason"] == "recorded_edit"
    assert (planting.plant, planting.plant_data, planting.profile) == (
        None,
        None,
        "deciduous_tree",
    )
    assert (planting.planted_on, planting.established, planting.notes) == (
        None,
        True,
        "",
    )


async def test_edit_a_planned_planting(hass, entry):
    await _setup(hass, entry)
    oak = await _add(hass, "veg", "white-oak", status="planned", estimated_cost=400)
    result = await _choose(hass, entry, oak["id"], "edit")
    fields = {str(k) for k in result["data_schema"].schema}
    assert "when" not in fields
    assert "estimated_cost" in fields
    result = await _next(
        hass,
        result,
        {
            "name": "White Oak",
            "quantity": 1,
            "plant": "white-oak",
            "estimated_cost": 250,
        },
    )
    planting = entry.runtime_data.plantings[oak["id"]]
    assert (planting.estimated_cost, planting.status) == (250, "planned")
    assert planting.planted_on is None and not planting.established


async def test_not_loaded(hass, entry):
    assert (await _start(hass, entry))["reason"] == "not_loaded"


async def test_no_plantings(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Master Gardener",
        data={"last_frost": "04-15", "first_frost": "10-25"},
    )
    entry.add_to_hass(hass)
    await _setup(hass, entry)
    assert (await _start(hass, entry))["reason"] == "no_plantings"
