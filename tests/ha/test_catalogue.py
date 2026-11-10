"""The OpenPlantDB catalogue: download, refresh, search, and use in plantings."""

from datetime import timedelta
from pathlib import Path

import aiohttp
import pytest
import yaml
from catalogue_helpers import FIXTURE_CSV, mock_catalogue
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.master_gardener.const import DOMAIN
from custom_components.master_gardener.plant_store import SOURCE_URL

FEB_20 = "2027-02-20 12:00:00+00:00"


async def _setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


def _area(hass, entry, area="veg"):
    return (
        dr.async_get(hass)
        .async_get_device_by_identifier(
            (DOMAIN, f"{entry.entry_id}_{area}"), entry.entry_id
        )
        .id
    )


async def _call(hass, action, data=None):
    return await hass.services.async_call(
        DOMAIN, action, data or {}, blocking=True, return_response=True
    )


async def _add(hass, entry, plant, **extra):
    return await _call(
        hass, "add_planting", {"area": _area(hass, entry), "plant": plant, **extra}
    )


def _catalogue_sensor(hass, entry):
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_plant_catalogue"
    )
    return hass.states.get(entity_id)


async def test_first_setup_downloads_the_catalogue(hass, entry, catalogue):
    await _setup(hass, entry)
    store = entry.runtime_data.plants
    assert store.ready
    assert store.meta["plants"] == 17
    assert store.meta["etag"] == '"v1"'
    assert (catalogue / "openplantdb.sqlite").exists()
    assert not (catalogue / "plants.csv.download").exists()
    state = _catalogue_sensor(hass, entry)
    assert state.state == "17"
    assert state.attributes["source"] == SOURCE_URL


async def test_setup_succeeds_while_the_catalogue_is_unreachable(
    hass, entry, aioclient_mock
):
    aioclient_mock.clear_requests()
    aioclient_mock.get(SOURCE_URL, exc=aiohttp.ClientError("offline"))
    await _setup(hass, entry)
    assert not entry.runtime_data.plants.ready
    assert _catalogue_sensor(hass, entry).state == "unknown"
    with pytest.raises(ServiceValidationError, match="not been downloaded"):
        await _add(hass, entry, "tomato")
    with pytest.raises(ServiceValidationError, match="not been downloaded"):
        await _call(hass, "search_plants", {"query": "tomato"})

    # Once it is reachable, a manual refresh brings it in.
    aioclient_mock.clear_requests()
    mock_catalogue(aioclient_mock)
    assert (await _call(hass, "refresh_catalogue"))["plants"] == 17
    assert (await _add(hass, entry, "tomato"))["in_catalog"] is True


async def test_search(hass, entry):
    hass.config_entries.async_update_entry(entry, data={**entry.data, "zone": "7b"})
    await _setup(hass, entry)
    result = await _call(hass, "search_plants", {"query": "japanese maple"})
    assert result["zone"] == 7
    assert [p["id"] for p in result["plants"]] == [
        "bloodgood-japanese-maple",
        "coral-bark-japanese-maple",
    ]
    first = result["plants"][0]
    assert first["scientific_name"] == "Acer palmatum 'Bloodgood'"
    assert first["zones"] == "5-8"
    assert first["care_profile"] == "japanese_maple"

    # Only plants hardy in the property's zone, unless asked otherwise.
    assert (await _call(hass, "search_plants", {"query": "desert rose"}))[
        "plants"
    ] == []
    everywhere = await _call(
        hass, "search_plants", {"query": "desert rose", "all_zones": True}
    )
    assert [p["id"] for p in everywhere["plants"]] == ["desert-rose"]
    limited = await _call(hass, "search_plants", {"query": "a", "limit": 2})
    assert len(limited["plants"]) == 2


async def test_add_planting_lookups(hass, entry):
    await _setup(hass, entry)
    apple = await _add(hass, entry, "Apple")
    assert (apple["plant"], apple["name"], apple["profile"]) == (
        "apple",
        "Apple",
        "apple",
    )
    assert "plant_data" not in apple

    with pytest.raises(ServiceValidationError) as err:
        await _add(hass, entry, "japanese maple")
    assert "bloodgood-japanese-maple" in str(err.value)
    assert "coral-bark-japanese-maple" in str(err.value)

    unknown = await _add(hass, entry, "Grandma's mystery vine")
    assert unknown["in_catalog"] is False
    assert unknown["name"] == "Grandma's mystery vine"
    assert unknown["profile"] is None

    # A care profile can be given for a plant the catalogue does not know...
    hedge = await _add(hass, entry, "Old privet hedge", care_profile="boxwood")
    assert (hedge["in_catalog"], hedge["profile"]) == (False, "boxwood")
    # ...or to overrule the automatic match.
    rose = await _add(hass, entry, "knock-out-rose", care_profile="perennial")
    assert rose["profile"] == "perennial"
    with pytest.raises(ServiceValidationError, match="Unknown care profile"):
        await _add(hass, entry, "tomato", care_profile="bonsai")


async def test_ticking_plant_task_records_the_planting(hass, entry, freezer):
    freezer.move_to("2027-03-20 12:00:00+00:00")
    await _setup(hass, entry)
    apple = await _add(hass, entry, "apple")
    todo = er.async_get(hass).async_get_entity_id(
        "todo", DOMAIN, f"{entry.entry_id}_veg_todo"
    )

    async def items():
        result = await hass.services.async_call(
            "todo",
            "get_items",
            {"entity_id": todo},
            blocking=True,
            return_response=True,
        )
        return {i["summary"]: i for i in result[todo]["items"]}

    listed = await items()
    assert "Plant Apple" in listed  # window is Mar 18 - Apr 14
    assert "bare-root" in listed["Plant Apple"]["description"]
    await hass.services.async_call(
        "todo",
        "update_item",
        {
            "entity_id": todo,
            "item": listed["Plant Apple"]["uid"],
            "status": "completed",
        },
        blocking=True,
    )
    manager = entry.runtime_data
    assert manager.plantings[apple["id"]].planted_on == dt_util.now().date()
    # It is in the ground: never asked to plant it again, upkeep carries on.
    next_year = manager.tasks_between(
        dt_util.now().date() + timedelta(days=300),
        dt_util.now().date() + timedelta(days=500),
    )
    titles = {t.title for t in next_year if t.planting_id == apple["id"]}
    assert "Plant Apple" not in titles
    assert "Dormant-prune Apple" in titles


async def test_weekly_check_downloads_only_when_changed(
    hass, entry, aioclient_mock, freezer
):
    freezer.move_to(FEB_20)
    await _setup(hass, entry)
    manager = entry.runtime_data
    store = manager.plants
    assert store.refresh_reason() is None
    assert await manager.async_update_catalogue() is False

    freezer.move_to(dt_util.utcnow() + timedelta(days=8))
    assert store.refresh_reason() == "weekly check"
    aioclient_mock.clear_requests()
    aioclient_mock.get(SOURCE_URL, status=304)
    assert await manager.async_update_catalogue() is False
    assert aioclient_mock.mock_calls[-1][3]["If-None-Match"] == '"v1"'
    assert store.refresh_reason() is None  # checked, so not due again
    assert store.meta["plants"] == 17


async def test_changed_catalogue_updates_existing_plantings(
    hass, entry, aioclient_mock, freezer
):
    freezer.move_to(FEB_20)
    await _setup(hass, entry)
    tomato = await _add(hass, entry, "tomato")
    manager = entry.runtime_data
    assert manager.plantings[tomato["id"]].plant_data["maturity_days_min"] == 60

    updated = FIXTURE_CSV.read_bytes().replace(
        b",60,85,transplant,indoor-start,", b",50,85,transplant,indoor-start,"
    )
    aioclient_mock.clear_requests()
    mock_catalogue(aioclient_mock, updated, etag='"v2"')
    freezer.move_to(dt_util.utcnow() + timedelta(days=8))
    assert await manager.async_update_catalogue() is True
    assert manager.plants.meta["etag"] == '"v2"'
    assert manager.plantings[tomato["id"]].plant_data["maturity_days_min"] == 50


async def test_integration_update_refetches(hass, entry, aioclient_mock):
    await _setup(hass, entry)
    store = entry.runtime_data.plants
    store.meta["version"] = "0.0.1"  # as if downloaded by an older release
    assert store.refresh_reason() == "integration updated"
    before = aioclient_mock.call_count
    assert await entry.runtime_data.async_update_catalogue() is True
    assert aioclient_mock.call_count == before + 1
    assert "If-None-Match" not in (aioclient_mock.mock_calls[-1][3] or {})
    assert store.refresh_reason() is None


@pytest.mark.parametrize(
    "broken",
    [{"status": 500}, {"content": b"slug,common_name\ntomato,Tomato\n"}],
    ids=["server error", "not the expected file"],
)
async def test_bad_download_keeps_the_working_catalogue(
    hass, entry, aioclient_mock, broken
):
    await _setup(hass, entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(SOURCE_URL, **broken)
    with pytest.raises(HomeAssistantError, match="Could not download"):
        await _call(hass, "refresh_catalogue")
    assert entry.runtime_data.plants.meta["plants"] == 17
    assert (await _call(hass, "search_plants", {"query": "tomato"}))["plants"]


async def test_catalogue_survives_reload_without_downloading(
    hass, entry, aioclient_mock
):
    await _setup(hass, entry)
    before = aioclient_mock.call_count
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == before
    assert entry.runtime_data.plants.meta["plants"] == 17


def test_care_profile_choices_match_the_profiles():
    root = Path(__file__).parents[2] / "custom_components" / "master_gardener"
    services = yaml.safe_load((root / "services.yaml").read_text(encoding="utf-8"))
    offered = services["add_planting"]["fields"]["care_profile"]["selector"]["select"][
        "options"
    ]
    from custom_components.master_gardener.engine.catalog import Catalog

    assert sorted(offered) == sorted(Catalog.load().profiles)
