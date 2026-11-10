"""The Add plantings form: record what is already growing, several at once."""

from datetime import date

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.master_gardener.config_flow import (
    names_match,
    parse_plant_lines,
    rank_candidates,
)
from custom_components.master_gardener.const import DOMAIN
from custom_components.master_gardener.plant_store import SOURCE_URL

FEB_20 = "2027-02-20 12:00:00+00:00"


async def _setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _start(hass, entry):
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, "planting"), context={"source": SOURCE_USER}
    )


async def _submit(hass, result, data):
    return await hass.config_entries.subentries.async_configure(result["flow_id"], data)


def _plantings(entry):
    return {p.name: p for p in entry.runtime_data.plantings.values()}


def test_parse_plant_lines():
    text = (
        "3 hosta\n\n  - japanese   maple \n2x Lavender\n4 \u00d7 fern\n• garlic\n0 rue"
    )
    assert parse_plant_lines(text) == [
        (3, "hosta"),
        (1, "japanese maple"),
        (2, "Lavender"),
        (4, "fern"),
        (1, "garlic"),
        (1, "rue"),
    ]


def test_names_match():
    assert names_match("Tomatoes", "Tomato")
    assert names_match("Peonies", "Peony")
    assert names_match("  white   OAK", "White Oak")
    assert not names_match("lavender", "Spanish Lavender")
    assert not names_match("knock-out-rose", "Knock Out Rose")


def test_rank_candidates():
    def plant(name, scientific):
        return {"common_name": name, "scientific_name": scientific}

    ranked = rank_candidates(
        "boxwood",
        [
            plant("Boxwood Basil (Dwarf Basil)", "Ocimum basilicum 'Boxwood'"),
            plant("African Boxwood", "Myrsine africana"),
            plant("Welleri Boxwood", "Buxus sempervirens 'Welleri'"),
            plant("Buddy Boxwood", "Buxus sempervirens 'Buddy'"),
            plant("Tide Hill Boxwood", "Buxus microphylla 'Tide Hill'"),
        ],
    )
    assert [p["common_name"] for p in ranked] == [
        "Buddy Boxwood",
        "Welleri Boxwood",
        "Tide Hill Boxwood",
        "African Boxwood",
        "Boxwood Basil (Dwarf Basil)",
    ]


async def test_a_match_by_id_is_confirmed(hass, entry):
    await _setup(hass, entry)
    result = await _submit(
        hass,
        await _start(hass, entry),
        {"area": "veg", "plants": "knock-out-rose", "when": "established"},
    )
    assert result["step_id"] == "match"
    (field,) = result["data_schema"].schema
    assert str(field) == "1. knock-out-rose"
    assert field.default() == "knock-out-rose"
    result = await _submit(hass, result, {"1. knock-out-rose": "knock-out-rose"})
    assert result["reason"] == "plantings_added"
    assert set(_plantings(entry)) == {"Knock Out Rose"}


async def test_add_established_plants_with_choices(hass, entry, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, entry)
    result = await _start(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await _submit(
        hass,
        result,
        {
            "area": "veg",
            "plants": "3 hosta\njapanese maple\napple\nTomatoes\n"
            "Grandma's fig\nfalse rue anemone",
            "when": "established",
        },
    )
    # Only the lines that did not name exactly one plant are asked about.
    assert result["step_id"] == "match"
    fields = [str(key) for key in result["data_schema"].schema]
    assert fields == ["2. japanese maple", "5. Grandma's fig", "6. false rue anemone"]

    result = await _submit(
        hass,
        result,
        {
            "2. japanese maple": "coral-bark-japanese-maple",
            "5. Grandma's fig": "_keep",
            "6. false rue anemone": "_skip",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "plantings_added"
    assert result["description_placeholders"] == {
        "area": "Vegetable garden",
        "plants": "3 Hosta, Coral Bark Japanese Maple, Apple, Tomato, Grandma's fig",
    }

    plantings = _plantings(entry)
    assert set(plantings) == {
        "Hosta",
        "Coral Bark Japanese Maple",
        "Apple",
        "Tomato",
        "Grandma's fig",
    }
    hosta = plantings["Hosta"]
    assert (hosta.quantity, hosta.established, hosta.planted_on) == (3, True, None)
    assert plantings["Apple"].profile == "apple"
    # An annual is never "planted years ago": it went in this season.
    tomato = plantings["Tomato"]
    assert (tomato.established, tomato.planted_on) == (False, date(2027, 2, 20))
    fig = plantings["Grandma's fig"]
    assert (fig.plant, fig.established) == (None, True)
    # Nothing is stored as a subentry: only the two areas are there.
    assert len(entry.subentries) == 2

    # Established plants get upkeep but no "Plant ..." task each year.
    tasks = entry.runtime_data.tasks_between(date(2027, 1, 1), date(2028, 12, 31))
    titles = {t.title for t in tasks}
    assert not {"Plant Hosta", "Plant Apple", "Plant Coral Bark Japanese Maple"} & (
        titles
    )
    assert any(t.planting_id == plantings["Apple"].id for t in tasks)


async def test_planted_this_season_or_on_a_date(hass, entry, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, entry)

    result = await _submit(
        hass,
        await _start(hass, entry),
        {"area": "lawn", "plants": "hosta", "when": "this_season"},
    )
    assert result["reason"] == "plantings_added"  # no choices needed
    hosta = _plantings(entry)["Hosta"]
    assert (hosta.established, hosta.planted_on) == (False, date(2027, 2, 20))

    result = await _start(hass, entry)
    result = await _submit(
        hass, result, {"area": "lawn", "plants": "apple", "when": "on_date"}
    )
    assert result["errors"] == {"planted_on": "date_needed"}
    result = await _submit(
        hass,
        result,
        {
            "area": "lawn",
            "plants": "apple",
            "when": "on_date",
            "planted_on": "2019-04-01",
        },
    )
    assert result["reason"] == "plantings_added"
    apple = _plantings(entry)["Apple"]
    assert (apple.established, apple.planted_on) == (False, date(2019, 4, 1))


async def test_form_errors(hass, entry):
    await _setup(hass, entry)
    result = await _start(hass, entry)
    result = await _submit(
        hass, result, {"area": "veg", "plants": " \n \n", "when": "established"}
    )
    assert result["errors"] == {"plants": "no_plants"}
    result = await _submit(
        hass,
        result,
        {"area": "veg", "plants": "fern\n" * 51, "when": "established"},
    )
    assert result["errors"] == {"plants": "too_many_plants"}


async def test_skipping_everything_adds_nothing(hass, entry):
    await _setup(hass, entry)
    result = await _submit(
        hass,
        await _start(hass, entry),
        {"area": "veg", "plants": "japanese maple", "when": "established"},
    )
    result = await _submit(hass, result, {"1. japanese maple": "_skip"})
    assert result["reason"] == "nothing_added"
    assert entry.runtime_data.plantings == {}


async def test_catalogue_not_ready(hass, entry, aioclient_mock):
    aioclient_mock.clear_requests()
    aioclient_mock.get(SOURCE_URL, status=500)
    await _setup(hass, entry)
    assert not entry.runtime_data.plants.ready
    result = await _start(hass, entry)
    assert result["reason"] == "catalogue_not_ready"


async def test_no_areas_yet(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Master Gardener",
        data={"last_frost": "04-15", "first_frost": "10-25"},
    )
    entry.add_to_hass(hass)
    await _setup(hass, entry)
    result = await _start(hass, entry)
    assert result["reason"] == "no_areas"


async def test_not_loaded(hass, entry):
    result = await _start(hass, entry)
    assert result["reason"] == "not_loaded"
