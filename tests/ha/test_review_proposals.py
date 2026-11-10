"""Proposals: a reminder on the to-do list, and deciding them in one go."""

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.master_gardener.const import DOMAIN

JAN_10 = "2027-01-10 12:00:00+00:00"


async def _setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _call(hass, action, data=None):
    return await hass.services.async_call(
        DOMAIN, action, data or {}, blocking=True, return_response=True
    )


async def _propose(hass, area, plant, **extra):
    return await _call(
        hass,
        "add_planting",
        {"area": area, "plant": plant, "status": "proposed", **extra},
    )


async def _start(hass, entry):
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, "review_proposals"), context={"source": SOURCE_USER}
    )


async def _todo(hass, entity_id):
    result = await hass.services.async_call(
        "todo",
        "get_items",
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )
    return {i["summary"]: i for i in result[entity_id]["items"]}


async def test_a_reminder_until_decided(hass, entry, freezer):
    freezer.move_to(JAN_10)
    await _setup(hass, entry)
    await _propose(hass, "veg", "tomato", notes="For the sunny bed.")
    await _propose(hass, "veg", "carrot")

    todo = await _todo(hass, "todo.vegetable_garden")
    item = todo["Decide on 2 proposed plantings"]
    assert item["due"] == "2027-02-18"  # when the tomato's window opens
    assert item["description"] == (
        "Review proposals, on the Master Gardener integration page: "
        "Carrot (sow from Mar 25); Tomato (start indoors from Feb 18)."
    )
    assert "Decide on 1 proposed planting" not in await _todo(hass, "todo.front_lawn")

    # Ticking it puts it away for a week.
    await hass.services.async_call(
        "todo",
        "update_item",
        {
            "entity_id": "todo.vegetable_garden",
            "item": item["uid"],
            "status": "completed",
        },
        blocking=True,
    )
    assert "Decide on 2 proposed plantings" not in await _todo(
        hass, "todo.vegetable_garden"
    )
    freezer.move_to("2027-01-18 12:00:00+00:00")
    assert "Decide on 2 proposed plantings" in await _todo(
        hass, "todo.vegetable_garden"
    )


async def test_a_proposal_with_no_window_is_due_in_two_weeks(hass, entry, freezer):
    freezer.move_to(JAN_10)
    await _setup(hass, entry)
    await _propose(hass, "lawn", "Grandma's fig")
    item = (await _todo(hass, "todo.front_lawn"))["Decide on 1 proposed planting"]
    assert item["due"] == "2027-01-24"


async def test_review_in_one_go(hass, entry, freezer):
    freezer.move_to(JAN_10)
    await _setup(hass, entry)
    tomato = await _propose(hass, "veg", "tomato", notes="For the sunny bed.")
    carrot = await _propose(hass, "veg", "carrot")
    oak = await _propose(hass, "veg", "white-oak", estimated_cost=300)
    pea = await _propose(hass, "veg", "pea")
    await _propose(hass, "lawn", "hosta")

    result = await _start(hass, entry)
    assert result["step_id"] == "user"
    labels = [
        o["label"] for o in result["data_schema"].schema["area"].config["options"]
    ]
    assert labels == ["Front lawn (1)", "Vegetable garden (4)"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"area": "veg"}
    )
    assert result["step_id"] == "review"
    proposals = result["description_placeholders"]["proposals"]
    assert proposals.splitlines()[0] == "- **1. Carrot**: sow from Mar 25."
    assert "- **2. Pea**" in proposals
    assert "- **3. Tomato**: start indoors from Feb 18. For the sunny bed." in proposals
    fields = [str(k) for k in result["data_schema"].schema]
    assert fields == ["1. Carrot", "2. Pea", "3. Tomato", "4. White Oak", "notes"]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "1. Carrot": "active",
            "2. Pea": "later",
            "3. Tomato": "removed",
            "4. White Oak": "planned",
            "notes": "Not this year",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reviewed"
    assert result["description_placeholders"] == {
        "area": "Vegetable garden",
        "summary": (
            "1 to grow, 1 added to the build plan, 1 turned down, 1 left for later"
        ),
    }
    plantings = entry.runtime_data.plantings
    assert plantings[carrot["id"]].status == "active"
    assert plantings[pea["id"]].status == "proposed"
    assert plantings[tomato["id"]].status == "removed"
    assert plantings[tomato["id"]].notes.endswith("Not this year")
    assert plantings[oak["id"]].status == "planned"
    assert not plantings[carrot["id"]].notes  # the why is for turned-down ones

    todo = await _todo(hass, "todo.vegetable_garden")
    assert "Decide on 1 proposed planting" in todo


async def test_seed_proposals_have_no_build_plan_choice(hass, entry):
    await _setup(hass, entry)
    seedling = await _propose(hass, "veg", "tomato")
    entry.runtime_data.plantings[seedling["id"]].from_seed = "Sungold tomato"
    result = await _start(hass, entry)  # one area: straight to the review
    (field, _notes) = result["data_schema"].schema
    options = result["data_schema"].schema[field].config["options"]
    assert options == ["later", "active", "removed"]


async def test_nothing_decided_and_nothing_to_review(hass, entry):
    await _setup(hass, entry)
    assert (await _start(hass, entry))["reason"] == "no_proposals"
    await _propose(hass, "veg", "tomato")
    result = await _start(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"1. Tomato": "later"}
    )
    assert result["reason"] == "nothing_decided"


async def test_decided_elsewhere_meanwhile(hass, entry):
    await _setup(hass, entry)
    tomato = await _propose(hass, "veg", "tomato")
    result = await _start(hass, entry)
    await _call(
        hass, "set_planting_status", {"planting_id": tomato["id"], "status": "active"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"1. Tomato": "removed"}
    )
    assert result["reason"] == "nothing_decided"
    assert entry.runtime_data.plantings[tomato["id"]].status == "active"


async def test_not_loaded(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Master Gardener",
        data={"last_frost": "04-15", "first_frost": "10-25"},
    )
    entry.add_to_hass(hass)
    assert (await _start(hass, entry))["reason"] == "not_loaded"
