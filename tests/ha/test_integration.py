"""End-to-end tests of the Home Assistant layer."""

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.master_gardener.const import DOMAIN

FEB_20 = "2027-02-20 12:00:00+00:00"


async def _setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass, entry, platform, key, area=None):
    unique = f"{entry.entry_id}_{area}_{key}" if area else f"{entry.entry_id}_{key}"
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique)
    assert entity_id, f"missing {platform} {unique}"
    return entity_id


def _device_id(hass, entry, area):
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_{area}"), entry.entry_id
    )
    assert device
    return device.id


async def _add_tomato(hass, entry, **extra):
    return await hass.services.async_call(
        DOMAIN,
        "add_planting",
        {"area": _device_id(hass, entry, "veg"), "plant": "Tomatoes", **extra},
        blocking=True,
        return_response=True,
    )


async def _todo_items(hass, entity_id):
    result = await hass.services.async_call(
        "todo", "get_items", {"entity_id": entity_id}, blocking=True, return_response=True
    )
    return result[entity_id]["items"]


async def test_config_flow(hass, aioclient_mock):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    flow_id = result["flow_id"]
    configure = hass.config_entries.flow.async_configure

    result = await configure(flow_id, {"zip_code": "2290"})
    assert result["errors"] == {"zip_code": "invalid_zip"}

    aioclient_mock.get("https://phzmapi.org/00000.json", status=404)
    result = await configure(flow_id, {"zip_code": "00000"})
    assert result["errors"] == {"zip_code": "unknown_zip"}

    aioclient_mock.get("https://phzmapi.org/22901.json", status=500)
    result = await configure(flow_id, {"zip_code": "22901"})
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    aioclient_mock.get("https://phzmapi.org/22901.json", json={"zone": "7b"})
    result = await configure(flow_id, {"zip_code": " 22901 "})
    assert result["step_id"] == "frost"
    assert result["description_placeholders"] == {"zone": "7b", "zip_code": "22901"}

    result = await configure(
        flow_id, {"last_frost": "2026-10-25", "first_frost": "2026-04-15"}
    )
    assert result["errors"] == {"base": "frost_order"}

    # Accept the dates estimated for zone 7b.
    result = await configure(flow_id, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "zip_code": "22901",
        "zone": "7b",
        "last_frost": "04-05",
        "first_frost": "10-30",
    }


async def test_options_flow_changes_frost_dates(hass, entry):
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"last_frost": "2026-05-01", "first_frost": "2026-10-10"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.site.last_frost == (5, 1)


async def test_add_and_edit_area(hass, entry):
    await _setup(hass, entry)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "area"), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Cottage garden",
            "style": "cottage",
            "light": "part_shade",
            "adjacent": ["veg"],
            "character": "Loose, generous, scented",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    new = next(s for s in entry.subentries.values() if s.title == "Cottage garden")
    assert new.data["adjacent"] == ["veg"]
    # The entry reloaded and the new area has its entities.
    _entity_id(hass, entry, "todo", "todo", new.subentry_id)
    _entity_id(hass, entry, "calendar", "calendar", new.subentry_id)

    result = await entry.start_subentry_reconfigure_flow(hass, new.subentry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Front cottage garden", "style": "cottage", "light": "full_sun"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries[new.subentry_id].title == "Front cottage garden"


async def test_entities_and_devices(hass, entry):
    await _setup(hass, entry)
    for area in ("veg", "lawn"):
        _entity_id(hass, entry, "calendar", "calendar", area)
        _entity_id(hass, entry, "todo", "todo", area)
        for key in ("tasks_due", "tasks_overdue", "next_task", "plantings"):
            _entity_id(hass, entry, "sensor", key, area)
    _entity_id(hass, entry, "calendar", "calendar")
    _entity_id(hass, entry, "sensor", "tasks_due")

    garden = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    veg = dr.async_get(hass).async_get(_device_id(hass, entry, "veg"))
    assert veg.name == "Vegetable garden"
    assert veg.via_device_id == garden.id
    assert hass.states.get("calendar.vegetable_garden") is not None
    assert hass.states.get("todo.vegetable_garden") is not None


async def test_planting_calendar_todo_and_death(hass, entry, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, entry)
    todo = _entity_id(hass, entry, "todo", "todo", "veg")
    due = _entity_id(hass, entry, "sensor", "tasks_due", "veg")
    plantings = _entity_id(hass, entry, "sensor", "plantings", "veg")
    assert hass.states.get(due).state == "0"

    planting = await _add_tomato(hass, entry, name="Cherokee Purple")
    assert planting["in_catalog"] is True
    assert planting["plant"] == "tomato"
    await hass.async_block_till_done()

    # Calendar: the three tomato windows for 2027.
    calendar = _entity_id(hass, entry, "calendar", "calendar", "veg")
    events = (
        await hass.services.async_call(
            "calendar",
            "get_events",
            {
                "entity_id": calendar,
                "start_date_time": "2027-01-01 00:00:00",
                "end_date_time": "2027-12-31 00:00:00",
            },
            blocking=True,
            return_response=True,
        )
    )[calendar]["events"]
    starts = {e["summary"]: e["start"] for e in events}
    assert starts["Start Cherokee Purple indoors"] == "2027-02-18"
    assert starts["Plant Cherokee Purple outdoors"] == "2027-04-22"
    assert starts["Harvest Cherokee Purple"] == "2027-07-01"
    assert hass.states.get(calendar).attributes["message"] == "Start Cherokee Purple indoors"

    # To-do: sowing is due now; tick it off.
    assert hass.states.get(due).state == "1"
    assert hass.states.get(plantings).state == "1"
    items = await _todo_items(hass, todo)
    assert [i["summary"] for i in items] == ["Start Cherokee Purple indoors"]
    assert items[0]["status"] == "needs_action"
    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": todo, "item": items[0]["uid"], "status": "completed"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(due).state == "0"
    assert (await _todo_items(hass, todo))[0]["status"] == "completed"

    # Feedback: add a problem through the to-do list.
    await hass.services.async_call(
        "todo", "add_item", {"entity_id": todo, "item": "Bed 3 is full of weeds"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(due).state == "1"

    # The plant dies: no more tomato tasks, history kept.
    result = await hass.services.async_call(
        DOMAIN,
        "set_planting_status",
        {"planting_id": planting["id"], "status": "dead", "notes": "late frost"},
        blocking=True,
        return_response=True,
    )
    assert result["status"] == "dead"
    await hass.async_block_till_done()
    assert hass.states.get(plantings).state == "0"
    assert hass.states.get(plantings).attributes["plantings"][0]["notes"] == "late frost"
    assert [i["summary"] for i in await _todo_items(hass, todo)] == [
        "Bed 3 is full of weeds"
    ]


async def test_overdue_after_midnight_and_mowing(hass, entry, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, entry)
    await _add_tomato(hass, entry)
    overdue = _entity_id(hass, entry, "sensor", "tasks_overdue", "veg")
    assert hass.states.get(overdue).state == "0"

    # The sowing window (Feb 18 - Mar 3) passes without being ticked off.
    freezer.move_to("2027-03-10 12:00:00+00:00")
    entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(overdue).state == "1"
    assert hass.states.get(_entity_id(hass, entry, "sensor", "tasks_overdue")).state == "1"

    # Mowing: one live item in season; ticking it rolls it a week forward.
    freezer.move_to("2027-06-10 12:00:00+00:00")
    entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    lawn = _entity_id(hass, entry, "todo", "todo", "lawn")
    mows = [i for i in await _todo_items(hass, lawn) if i["summary"] == "Mow Front lawn"]
    assert len(mows) == 1
    await hass.services.async_call(
        "todo", "update_item",
        {"entity_id": lawn, "item": mows[0]["uid"], "status": "completed"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert not [i for i in await _todo_items(hass, lawn) if i["summary"] == "Mow Front lawn"]
    next_task = hass.states.get(_entity_id(hass, entry, "sensor", "next_task", "lawn"))
    assert next_task.state == "Mow Front lawn"
    assert next_task.attributes["start"] == "2027-06-17"


async def test_state_survives_reload(hass, entry, freezer):
    freezer.move_to(FEB_20)
    await _setup(hass, entry)
    planting = await _add_tomato(hass, entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    listed = await hass.services.async_call(
        DOMAIN, "list_plantings", {}, blocking=True, return_response=True
    )
    assert [p["id"] for p in listed["plantings"]] == [planting["id"]]


async def test_service_rejects_non_area_device(hass, entry):
    await _setup(hass, entry)
    garden = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    import pytest
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "add_task", {"area": garden.id, "title": "x"}, blocking=True
        )
