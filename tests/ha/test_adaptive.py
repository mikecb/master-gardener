"""Adaptive maintenance: mowing follows what the lawn needs."""

from custom_components.master_gardener.const import DOMAIN

JUNE_1 = "2027-06-01 12:00:00+00:00"


async def _setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _call(hass, action, data=None):
    return await hass.services.async_call(
        DOMAIN, action, data or {}, blocking=True, return_response=True
    )


async def _mow(hass):
    """The real (due or overdue) mowing task on the front lawn."""
    tasks = await _call(hass, "list_tasks", {"area": "lawn", "show": "due"})
    (mow,) = [t for t in tasks["tasks"] if t["kind"] == "mow"]
    return mow


async def _next_mow(hass):
    tasks = await _call(hass, "list_tasks", {"area": "lawn", "show": "upcoming"})
    return next(t for t in tasks["tasks"] if t["kind"] == "mow")


async def test_complaints_mow_sooner(hass, entry, freezer):
    freezer.move_to(JUNE_1)
    await _setup(hass, entry)
    await _call(hass, "complete_task", {"task_id": (await _mow(hass))["id"]})
    assert (await _next_mow(hass))["start"] == "2027-06-08"

    # Weeds are not about mowing; grass is, twice.
    await _call(hass, "add_task", {"area": "lawn", "title": "Weeds by the path"})
    await _call(hass, "add_task", {"area": "lawn", "title": "Grass too long"})
    assert (await _next_mow(hass))["start"] == "2027-06-08"
    freezer.move_to("2027-06-04 12:00:00+00:00")
    await _call(hass, "add_task", {"area": "lawn", "title": "The lawn is a hayfield"})

    nxt = await _next_mow(hass)
    assert nxt["start"] == "2027-06-07"  # every 6 days now
    assert nxt["detail"].endswith("Every 6 days, usually 7: reported 2 times.")

    garden = await _call(hass, "get_garden")
    lawn = next(a for a in garden["areas"] if a["id"] == "lawn")
    (adjusted,) = lawn["schedule_adjustments"]
    assert adjusted["task"] == "Mow Front lawn"
    assert adjusted["interval_factor"] == 0.85
    assert adjusted["why"] == "reported 2 times"
    veg = next(a for a in garden["areas"] if a["id"] == "veg")
    assert "schedule_adjustments" not in veg

    # It survives a restart.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert (await _next_mow(hass))["start"] == "2027-06-07"

    # And can be put back.
    reset = await _call(hass, "reset_schedule", {"area": "Front lawn"})
    assert reset == {"reset": ["area:lawn:mow"]}
    assert (await _next_mow(hass))["start"] == "2027-06-08"
    assert (await _call(hass, "reset_schedule"))["reset"] == []


async def test_mowing_late_without_complaint_mows_less(hass, entry, freezer):
    freezer.move_to(JUNE_1)
    await _setup(hass, entry)
    await _call(hass, "complete_task", {"task_id": (await _mow(hass))["id"]})
    day = 1
    for _ in range(3):
        day += 14  # a week after the window closed
        freezer.move_to(
            f"2027-06-{day:02d} 12:00:00+00:00"
            if day <= 30
            else (f"2027-07-{day - 30:02d} 12:00:00+00:00")
        )
        mow = await _mow(hass)
        assert mow["state"] == "overdue"
        await _call(hass, "complete_task", {"task_id": mow["id"]})
    nxt = await _next_mow(hass)
    assert nxt["detail"].endswith("Every 8 days, usually 7: done late 3 times running.")
    assert nxt["start"] == "2027-07-21"  # July 13 + 8


async def test_mowing_early_counts(hass, entry, freezer):
    freezer.move_to(JUNE_1)
    await _setup(hass, entry)
    await _call(hass, "complete_task", {"task_id": (await _mow(hass))["id"]})
    freezer.move_to("2027-06-05 12:00:00+00:00")
    # Done before it was due (from list_tasks upcoming), then a complaint.
    await _call(hass, "complete_task", {"task_id": (await _next_mow(hass))["id"]})
    await _call(hass, "add_task", {"area": "lawn", "title": "Mow again, it's long"})
    assert "done early 1 time" in (await _next_mow(hass))["detail"]
